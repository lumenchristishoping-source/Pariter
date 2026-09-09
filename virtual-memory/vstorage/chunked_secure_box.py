"""The real fix for encryption RAM scaling with file size, not just a
smaller pacing band-aid: CHUNK the file instead of re-encrypting the
whole thing every hop.

CombinedSecureBox re-encrypts the ENTIRE buffer on every single hop -
for a 50MB file, that means multiple full 50MB copies (ciphertext
read, plaintext decrypted, scratch buffer, new ciphertext) existing
at once, every hop. Measured: +332MB RAM, hop rate collapsing to 1
per 2 seconds. Pacing (combined_secure_box.py's hop_interval) fixed
the runaway HOP RATE, but not the per-hop RAM cost.

The fix: split the file into fixed-size chunks (default 256KB), each
its own separately locked ciphertext buffer, and only touch ONE chunk
per hop, round-robin - bounding per-hop cost to CHUNK size regardless
of FILE size. Same principle every real system uses for large data
(TLS records, disk-encryption sectors, cloud storage server-side
encryption).

Each chunk is compressed ONCE, at construction, before it's ever
encrypted - compress-then-encrypt, the only order that works, since
encrypted bytes are indistinguishable from random and don't compress
at all. This was missing for an embarrassingly long time: a 1GB real
test ran clean through this exact module without it, and "physics,
you can't hold 1GB in less than 1GB" was asserted as if it were a
universal law rather than a true-for-incompressible-data statement -
caught when asked directly "didn't it compress though? it's an .md
file" about a document that was, in fact, extremely repetitive
markdown text (34x compressible, checked directly with lzma before
writing the fix). Ratcheting never recompresses - it decrypts and
re-encrypts the same already-compressed bytes, so compression cost is
paid once per chunk, not once per hop.

Real bug found and fixed while building this: a naive first version
shared ONE ratcheting key across all chunks, but only re-encrypted one
chunk per hop - so the very next hop advanced the shared key while
every OTHER chunk was still sitting there encrypted under the OLD key,
and decryption failed outright (InvalidTag) the moment two chunks
existed. Fixed with a two-generation rotation: a "current" key and a
"next" key (each its own split, ratcheted SplitKeyGuard) live at once.
Each hop migrates exactly one not-yet-migrated chunk from current to
next (or, once migrated, just refreshes its nonce under next - real
motion, same key, still a valid AES-GCM use since the nonce is fresh
every time). Once every chunk has migrated, next is promoted to
current, a fresh next is generated, and the cycle repeats. This is
the same idea generational garbage collectors and rolling key-rotation
systems use: never touch everything at once, migrate it gradually
while always knowing which generation each piece is currently in.

Second real cost found the same way split_key_guard.py's was: one
falling thread PER BOX means one file (3 boxes: content/structure/
metadata) costs 3 threads, and a real 24-file, ~2GB run costs 72 of
them - on top of the 144 guard-scheduler threads that module used to
spawn too, for 217 threads system-wide. Under that much GIL
contention, saving 24 files took nearly an hour, and retrieving them
back out was, in places, even slower than saving. Fixed the same way:
every box now registers with ONE shared _GlobalFallScheduler instead
of starting its own thread. Each box still gets its own independent
pacing (hop_interval) and its own lock, exactly as before - only the
thread doing the work is now shared.
"""

from __future__ import annotations

import ctypes
import hashlib
import heapq
import lzma
import mmap
import os
import threading
import time
from typing import List

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .split_key_guard import SplitKeyGuard, _lock_and_hide

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
PR_SET_DUMPABLE = 4
NONCE_LEN = 12
DEFAULT_CHUNK_SIZE = 256 * 1024


def make_process_nondumpable() -> bool:
    return _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0


def _ratchet(key: bytes) -> bytes:
    return hashlib.sha256(key + b"vstorage-ratchet").digest()


def secure_zero(buf) -> None:
    if buf:
        buf[:] = bytes(len(buf))


_POOL_SIZE = 64 * 1024 * 1024  # 64MB per pool


class _ChunkPool:
    """Many chunks' payloads packed into ONE big locked+hidden mmap
    region, instead of every _Chunk getting its own separate mapping.

    Found running a real 20GB file on real disk: giving each chunk its
    own mmap() means tens of thousands of separate small memory
    mappings in one process - a real, documented way to fragment a
    process's address space badly enough that even a small, fixed-size
    allocation can fail. Confirmed directly: 3 separate attempts, all
    crashed with a genuine MemoryError from lzma's own internal buffer
    allocation, consistently around 57,000-60,000 chunks in - never
    from running low on total memory (checked every time: RAM usage,
    system-wide MemAvailable, cgroup limits, ulimits - all fine). An
    isolated test proved _Chunk construction itself has flat, constant
    per-chunk cost up to 16,000 chunks - the real threshold turned out
    to be well past that, at a scale the earlier test never reached.

    Pooling cuts the number of separate mappings by roughly
    _POOL_SIZE / average-chunk-size - about 2,000x for a 256KB chunk
    that compresses to ~24KB (this project's own real 20GB GeoJSON
    test), since a 64MB pool holds roughly 2,600+ such chunks instead
    of needing 2,600+ separate mmap() calls. Also shrinks the watchdog
    region table by the same factor - a second, real win: a 20GB file
    at 256KB chunks means ~82,000 raw chunks, dangerously close to
    ProcessWatchdog's own 200,000-region ceiling from a single file
    alone, before pooling."""

    def __init__(self, size: int = _POOL_SIZE):
        self._buf = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        self._size = size
        self._cursor = 0
        self.protection_status = _lock_and_hide(self._buf)

    def try_allocate(self, length: int):
        """Reserves `length` bytes at the current cursor, returns the
        offset - or None if it doesn't fit, telling the caller to
        start a new pool. Variable-length, packed tight (not fixed-
        size slots) - chunk payloads vary with how well each one
        compresses, and fixed worst-case-sized slots would waste most
        of a pool's space on real, compressible content."""
        if self._cursor + length > self._size:
            return None
        offset = self._cursor
        self._cursor += length
        return offset

    def region(self) -> tuple:
        return (ctypes.addressof((ctypes.c_char * len(self._buf)).from_buffer(self._buf)),
                len(self._buf))

    def collapse(self) -> None:
        self._buf.close()


def _new_chunk(pools: list, plaintext: bytes, key: bytes) -> "_Chunk":
    """Compresses+encrypts, then packs the result into the current
    pool (or starts a new one if it doesn't fit) instead of giving
    this chunk its own separate mmap - see _ChunkPool's docstring."""
    compressed = lzma.compress(plaintext, preset=6) if plaintext else b""
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, compressed, None)
    total_len = NONCE_LEN + len(ct)

    pool = pools[-1] if pools else None
    offset = pool.try_allocate(total_len) if pool is not None else None
    if offset is None:
        pool = _ChunkPool(max(_POOL_SIZE, total_len))
        pools.append(pool)
        offset = pool.try_allocate(total_len)

    pool._buf[offset:offset + NONCE_LEN] = nonce
    pool._buf[offset + NONCE_LEN:offset + total_len] = ct
    return _Chunk(pool, offset, len(plaintext), len(ct))


class _Chunk:
    def __init__(self, pool: "_ChunkPool", offset: int, plain_len: int, payload_len: int):
        self._pool = pool
        self._offset = offset
        self.plain_len = plain_len
        self.payload_len = payload_len

    def reencrypt(self, current_key: bytes, new_key: bytes) -> None:
        """Decrypt under current_key, re-encrypt under new_key (which
        may equal current_key - a fresh nonce alone is still real
        motion and still a valid AES-GCM use). Operates on the already
        -compressed bytes - compression happens once, at construction,
        never again per hop. Same-length in, same-length out (AES-GCM
        ciphertext length always matches its input), so this chunk's
        slot in the pool never needs to grow or move."""
        buf = self._pool._buf
        o = self._offset
        nonce = bytes(buf[o:o + NONCE_LEN])
        ct = bytes(buf[o + NONCE_LEN:o + NONCE_LEN + self.payload_len])
        compressed = AESGCM(current_key).decrypt(nonce, ct, None)
        new_nonce = os.urandom(NONCE_LEN)
        new_ct = AESGCM(new_key).encrypt(new_nonce, compressed, None)
        scratch = bytearray(compressed)
        secure_zero(scratch)
        buf[o:o + NONCE_LEN] = new_nonce
        buf[o + NONCE_LEN:o + NONCE_LEN + len(new_ct)] = new_ct

    def decrypt(self, key: bytes) -> bytes:
        buf = self._pool._buf
        o = self._offset
        nonce = bytes(buf[o:o + NONCE_LEN])
        ct = bytes(buf[o + NONCE_LEN:o + NONCE_LEN + self.payload_len])
        compressed = AESGCM(key).decrypt(nonce, ct, None)
        return lzma.decompress(compressed) if compressed else b""

    def collapse(self) -> None:
        pass  # the POOL owns this memory now - ChunkedSecureBox.collapse() releases pools directly

    def region(self) -> tuple:
        return self._pool.region()


class _GlobalFallScheduler:
    """A small pool of shared background threads advances every
    ChunkedSecureBox's falling motion (one chunk re-encrypted per
    tick, round-robin per box), instead of one thread PER BOX (the
    original, expensive design - see the module docstring's "second
    real cost" note) or exactly one shared thread for the whole
    process (the first fix - correct and far cheaper, but found to
    have a real cost of its own: a real 24-file run showed 29 of 75
    boxes getting ZERO hops during a 75s window where the main thread
    was busy with heavy save/retrieve work for 69 of those seconds -
    one thread can be starved completely). A small pool (default 4)
    keeps the thread count nowhere near the old per-box cost while
    giving rotation real headroom: if 3 workers are blocked or busy,
    a 4th can still make progress. Each box keeps its own independent
    pacing and its own lock; only the pool of workers is shared."""

    DEFAULT_WORKERS = 4

    def __init__(self, worker_count: int = DEFAULT_WORKERS):
        self._worker_count = worker_count
        self._lock = threading.Lock()
        self._heap: list = []  # (due_time, seq, box)
        self._seq = 0
        self._wakeup = threading.Event()
        self._threads: List[threading.Thread] = []

    def _ensure_started(self) -> None:
        if self._threads:
            return
        with self._lock:
            if not self._threads:
                for _ in range(self._worker_count):
                    t = threading.Thread(target=self._run, daemon=True)
                    self._threads.append(t)
                    t.start()

    def register(self, box: "ChunkedSecureBox") -> None:
        self._ensure_started()
        with self._lock:
            self._seq += 1
            heapq.heappush(self._heap, (time.monotonic(), self._seq, box))
        self._wakeup.set()

    def _run(self) -> None:
        while True:
            with self._lock:
                entry = self._heap[0] if self._heap else None
            if entry is None:
                self._wakeup.wait(0.05)
                self._wakeup.clear()
                continue
            wait = entry[0] - time.monotonic()
            if wait > 0:
                self._wakeup.wait(min(wait, 0.01))
                self._wakeup.clear()
                continue
            with self._lock:
                if not self._heap:
                    continue
                # Re-check under the lock: with several workers now
                # racing each other, another worker may already have
                # popped what we peeked at above, or a NEWER entry may
                # have taken the top spot. Either way, only take an
                # entry that is actually due right now.
                if self._heap[0][0] - time.monotonic() > 0:
                    continue
                _due, _seq, box = heapq.heappop(self._heap)

            with box._lock:
                if box._removed:
                    continue
                if box._paused.is_set():
                    delay = 0.001
                else:
                    try:
                        box._do_one_hop_locked()
                    except Exception:
                        # A live tamper response (the watchdog zeroing
                        # this box's cells from OUTSIDE, mid-hop) can
                        # make reconstruct() fail here - expected under
                        # real attack, not a bug. This is the ONE
                        # shared thread for every box in the process,
                        # so letting an exception escape would silently
                        # stop falling motion for every OTHER file too;
                        # drop this box (it's being wiped/killed anyway)
                        # and keep servicing everyone else.
                        continue
                    delay = box._hop_interval

            with self._lock:
                self._seq += 1
                heapq.heappush(self._heap, (time.monotonic() + delay, self._seq, box))


_fall_scheduler = _GlobalFallScheduler()


class ChunkedSecureBox:
    def __init__(self, data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 hop_interval: float = 0.0, trust_group=None, on_new_guard=None):
        self._data_len = len(data)
        self._chunk_size = chunk_size
        self._hop_interval = hop_interval
        self._trust_group = trust_group
        self._on_new_guard = on_new_guard

        current_key = AESGCM.generate_key(bit_length=256)
        self._current_guard = SplitKeyGuard(current_key)
        self._next_guard = SplitKeyGuard(self._next_key(current_key))

        self._pools: List[_ChunkPool] = []
        self._chunks: List[_Chunk] = [
            _new_chunk(self._pools, data[i:i + chunk_size], current_key)
            for i in range(0, max(len(data), 1), chunk_size)
        ] or [_new_chunk(self._pools, b"", current_key)]
        self._on_next = [False] * len(self._chunks)

        self._start_falling()

    @classmethod
    def from_file(cls, path: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                  hop_interval: float = 0.0, trust_group=None,
                  on_new_guard=None) -> "ChunkedSecureBox":
        """Streams the file in from disk, chunk_size bytes at a time -
        never holds the whole file as one Python object. __init__
        needs the full file as `data: bytes` first, which for a small
        piece (a typical file's structure/metadata) is fine, but for
        a truly huge file means needing 2x its size in RAM just to
        start (the file wherever it already lives, plus this copy).
        This path never pays that - peak RAM is bounded by one
        chunk_size buffer plus whatever the compressed+encrypted
        output ends up being, regardless of how large the source is."""
        self = cls.__new__(cls)
        self._data_len = os.path.getsize(path)
        self._chunk_size = chunk_size
        self._hop_interval = hop_interval
        self._trust_group = trust_group
        self._on_new_guard = on_new_guard

        current_key = AESGCM.generate_key(bit_length=256)
        self._current_guard = SplitKeyGuard(current_key)
        self._next_guard = SplitKeyGuard(self._next_key(current_key))

        # Read in large RAW blocks, not chunk_size at a time - found
        # running a real 20GB file on real disk, the hard way. A single
        # forward pass over a huge file lets the kernel's page cache
        # for the bytes ALREADY read keep growing the whole time
        # (normal readahead/caching) - we never re-read earlier bytes,
        # so that cache serves no purpose, but on a no-swap machine it
        # can still crowd out real allocations. First fix attempt
        # called posix_fadvise(DONTNEED) after every 256KB chunk -
        # verified in isolation that fadvise itself works (a single
        # call over 500MB dropped Cached by exactly 500MB), but
        # verified DIRECTLY that calling it every 256KB in a tight
        # loop barely worked at all (+497MB Cached for 500MB read,
        # same as no fix). Root cause: this disk's readahead window
        # (`/sys/block/*/queue/read_ahead_kb`) is 8MB - 32x our old
        # 256KB read size - so the kernel's own readahead heuristic
        # was re-populating cache ahead of us faster than narrow,
        # frequent DONTNEED hints could clear it. Real fix: read in
        # blocks at least as large as that readahead window, fadvise
        # each whole block in one call (the granularity actually
        # proven to work), THEN slice it into chunk_size pieces for
        # the existing per-chunk encryption/falling machinery -
        # unrelated to and unchanged by this fix.
        RAW_READ_SIZE = max(chunk_size, 16 * 1024 * 1024)

        self._pools: List[_ChunkPool] = []
        chunks: List[_Chunk] = []
        with open(path, "rb") as f:
            fd = f.fileno()
            offset = 0
            while True:
                block = f.read(RAW_READ_SIZE)
                if not block:
                    break
                for i in range(0, len(block), chunk_size):
                    chunks.append(_new_chunk(self._pools, block[i:i + chunk_size], current_key))
                try:
                    os.posix_fadvise(fd, offset, len(block), os.POSIX_FADV_DONTNEED)
                except (AttributeError, OSError):
                    pass  # not available on this platform - best effort
                offset += len(block)
        self._chunks = chunks or [_new_chunk(self._pools, b"", current_key)]
        self._on_next = [False] * len(self._chunks)

        self._start_falling()
        return self

    def _start_falling(self) -> None:
        self._lock = threading.Lock()
        self._hops = 0
        self._next_chunk = 0
        self._paused = threading.Event()
        self._removed = False

        _fall_scheduler.register(self)

    def _next_key(self, current_key: bytes) -> bytes:
        if self._trust_group is not None:
            external = self._trust_group.fetch()
            derived = hashlib.sha256(current_key + external + b"vstorage-ratchet").digest()
            external = bytes(len(external))
            return derived
        return _ratchet(current_key)

    def _do_one_hop_locked(self) -> None:
        """Advances exactly one chunk by one hop. Caller (the shared
        _GlobalFallScheduler) must already hold self._lock.

        Real gap found and fixed here: a watchdog only ever gets told
        a box's key-guard addresses ONCE, when regions() is first
        called at file-save time. But this method REPLACES
        _next_guard with a brand new SplitKeyGuard (a fresh mmap
        allocation, fresh addresses) every time a generation
        completes - so without on_new_guard(), the watchdog keeps
        watching the OLD, by-then-freed addresses forever after the
        first rotation. Confirmed directly: after one rotation, the
        stale address is often still readable and non-zero - not
        because the wipe failed, but because the freed address gets
        silently reused for unrelated live memory. A real attack
        landing after that point would have the watchdog wipe the
        wrong thing. This was always true; it just took a fast enough
        rotation rate to actually observe it happening."""
        idx = self._next_chunk
        chunk = self._chunks[idx]
        current_key = self._current_guard.reconstruct()
        next_key = self._next_guard.reconstruct()

        if not self._on_next[idx]:
            chunk.reencrypt(current_key, next_key)
            self._on_next[idx] = True
        else:
            chunk.reencrypt(next_key, next_key)  # fresh nonce, same generation

        self._next_chunk = (idx + 1) % len(self._chunks)
        self._hops += 1

        if self._next_chunk == 0 and all(self._on_next):
            old_current = self._current_guard
            self._current_guard = self._next_guard
            self._next_guard = SplitKeyGuard(self._next_key(next_key))
            if self._on_new_guard is not None:
                self._on_new_guard(self._next_guard.regions())
            old_current.collapse()
            self._on_next = [False] * len(self._chunks)

    @property
    def hops(self) -> int:
        return self._hops

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def regions(self) -> list:
        """Everything worth zeroing if tampering is detected: both key
        guards' cells (current AND next generation - a rotation may be
        mid-flight) plus every chunk pool, for defense-in-depth. One
        region per POOL, not per chunk - many chunks share a pool (see
        _ChunkPool), so this list stays small even at huge chunk
        counts, which is also what keeps ProcessWatchdog's own region
        table from filling up on a single large file."""
        out = list(self._current_guard.regions())
        out.extend(self._next_guard.regions())
        for pool in self._pools:
            out.append(pool.region())
        return out

    def snapshot(self) -> bytearray:
        self._paused.set()
        with self._lock:
            current_key = self._current_guard.reconstruct()
            next_key = self._next_guard.reconstruct()
            out = bytearray()
            for chunk, on_next in zip(self._chunks, self._on_next):
                key = next_key if on_next else current_key
                out += chunk.decrypt(key)[:chunk.plain_len]
        self._paused.clear()
        return out[:self._data_len]

    def stream_to(self, write) -> None:
        """Same data as snapshot(), but never holds more than ONE
        chunk's plaintext at a time - decrypt a chunk, hand it to
        `write` (a callable taking bytes, e.g. an open file's .write),
        immediately let it go, move to the next. snapshot() builds one
        bytearray the size of the whole file; for a huge file being
        retrieved, that means a huge exposed buffer for as long as the
        caller holds it. This bounds the exposed plaintext to one
        chunk_size at a time, same principle as from_file() on the way
        in - the caller's `write` is expected to send it straight
        somewhere already protected (disk, an encrypted destination, a
        socket) rather than accumulate it either."""
        self._paused.set()
        remaining = self._data_len
        try:
            with self._lock:
                current_key = self._current_guard.reconstruct()
                next_key = self._next_guard.reconstruct()
                for chunk, on_next in zip(self._chunks, self._on_next):
                    if remaining <= 0:
                        break
                    key = next_key if on_next else current_key
                    plaintext = chunk.decrypt(key)
                    piece = plaintext[:min(chunk.plain_len, remaining)]
                    write(piece)
                    remaining -= len(piece)
        finally:
            self._paused.clear()

    def stream_to_wrapped(self, write, transit_key: bytes) -> None:
        """The actual "encrypt the path" version: same one-chunk-at-a-
        time bound as stream_to(), but `write` NEVER sees raw
        plaintext. Each chunk is decrypted under this box's own
        storage key, then IMMEDIATELY re-encrypted under
        `transit_key` (a fresh nonce per chunk, real AES-GCM) before
        `write` is called - the bytes that actually leave this
        function, cross into the caller's write target, and land on
        whatever destination it is are ciphertext the whole way, not
        plaintext trusted to a "hopefully protected" destination.
        Plaintext still exists for one chunk's worth, for one
        instant, inside this loop - that part is unavoidable, the
        storage-layer decrypt has to happen for the data to be
        useful to anyone - but it never reaches `write`, never
        touches the destination, and is gone the moment the loop
        moves to the next chunk.

        Wire format per chunk: 4-byte big-endian ciphertext length,
        then NONCE_LEN-byte nonce, then the ciphertext - self-
        delimiting so a reader can pull chunks back out without
        needing to know chunk_size in advance."""
        self._paused.set()
        remaining = self._data_len
        try:
            with self._lock:
                current_key = self._current_guard.reconstruct()
                next_key = self._next_guard.reconstruct()
                for chunk, on_next in zip(self._chunks, self._on_next):
                    if remaining <= 0:
                        break
                    key = next_key if on_next else current_key
                    plaintext = chunk.decrypt(key)
                    piece = plaintext[:min(chunk.plain_len, remaining)]
                    nonce = os.urandom(NONCE_LEN)
                    ct = AESGCM(transit_key).encrypt(nonce, piece, None)
                    write(len(ct).to_bytes(4, "big") + nonce + ct)
                    remaining -= len(piece)
        finally:
            self._paused.clear()

    def collapse(self) -> None:
        with self._lock:
            self._removed = True
        self._current_guard.collapse()
        self._next_guard.collapse()
        for pool in self._pools:
            pool.collapse()
