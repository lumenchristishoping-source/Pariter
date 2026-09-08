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
"""

from __future__ import annotations

import ctypes
import hashlib
import lzma
import mmap
import os
import threading
from typing import List

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .split_key_guard import SplitKeyGuard, _lock_and_hide

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
PR_SET_DUMPABLE = 4
PAGE_SIZE = mmap.PAGESIZE
NONCE_LEN = 12
DEFAULT_CHUNK_SIZE = 256 * 1024


def make_process_nondumpable() -> bool:
    return _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0


def _round_up_page(n: int) -> int:
    return ((max(n, 1) + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE


def _ratchet(key: bytes) -> bytes:
    return hashlib.sha256(key + b"vstorage-ratchet").digest()


def secure_zero(buf) -> None:
    if buf:
        buf[:] = bytes(len(buf))


class _Chunk:
    def __init__(self, plaintext: bytes, key: bytes):
        compressed = lzma.compress(plaintext, preset=6) if plaintext else b""
        length = _round_up_page(len(compressed) + 16 + NONCE_LEN)
        self._buf = mmap.mmap(-1, length, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        self.plain_len = len(plaintext)
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(key).encrypt(nonce, compressed, None)
        self._buf[:NONCE_LEN] = nonce
        self._buf[NONCE_LEN:NONCE_LEN + len(ct)] = ct
        self.payload_len = len(ct)
        self.protection_status = _lock_and_hide(self._buf)

    def reencrypt(self, current_key: bytes, new_key: bytes) -> None:
        """Decrypt under current_key, re-encrypt under new_key (which
        may equal current_key - a fresh nonce alone is still real
        motion and still a valid AES-GCM use). Operates on the already
        -compressed bytes - compression happens once, at construction,
        never again per hop."""
        nonce = bytes(self._buf[:NONCE_LEN])
        ct = bytes(self._buf[NONCE_LEN:NONCE_LEN + self.payload_len])
        compressed = AESGCM(current_key).decrypt(nonce, ct, None)
        new_nonce = os.urandom(NONCE_LEN)
        new_ct = AESGCM(new_key).encrypt(new_nonce, compressed, None)
        scratch = bytearray(compressed)
        secure_zero(scratch)
        self._buf[:NONCE_LEN] = new_nonce
        self._buf[NONCE_LEN:NONCE_LEN + len(new_ct)] = new_ct
        self.payload_len = len(new_ct)

    def decrypt(self, key: bytes) -> bytes:
        nonce = bytes(self._buf[:NONCE_LEN])
        ct = bytes(self._buf[NONCE_LEN:NONCE_LEN + self.payload_len])
        compressed = AESGCM(key).decrypt(nonce, ct, None)
        return lzma.decompress(compressed) if compressed else b""

    def collapse(self) -> None:
        self._buf.close()

    def region(self) -> tuple:
        return (ctypes.addressof((ctypes.c_char * len(self._buf)).from_buffer(self._buf)),
                len(self._buf))


class ChunkedSecureBox:
    def __init__(self, data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 hop_interval: float = 0.0, trust_group=None):
        self._data_len = len(data)
        self._chunk_size = chunk_size
        self._hop_interval = hop_interval
        self._trust_group = trust_group

        current_key = AESGCM.generate_key(bit_length=256)
        self._current_guard = SplitKeyGuard(current_key)
        self._next_guard = SplitKeyGuard(self._next_key(current_key))

        self._chunks: List[_Chunk] = [
            _Chunk(data[i:i + chunk_size], current_key)
            for i in range(0, max(len(data), 1), chunk_size)
        ] or [_Chunk(b"", current_key)]
        self._on_next = [False] * len(self._chunks)

        self._start_falling()

    @classmethod
    def from_file(cls, path: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                  hop_interval: float = 0.0, trust_group=None) -> "ChunkedSecureBox":
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

        current_key = AESGCM.generate_key(bit_length=256)
        self._current_guard = SplitKeyGuard(current_key)
        self._next_guard = SplitKeyGuard(self._next_key(current_key))

        chunks: List[_Chunk] = []
        with open(path, "rb") as f:
            while True:
                piece = f.read(chunk_size)
                if not piece:
                    break
                chunks.append(_Chunk(piece, current_key))
        self._chunks = chunks or [_Chunk(b"", current_key)]
        self._on_next = [False] * len(self._chunks)

        self._start_falling()
        return self

    def _start_falling(self) -> None:
        self._lock = threading.Lock()
        self._hops = 0
        self._next_chunk = 0
        self._stop = threading.Event()
        self._paused = threading.Event()

        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _next_key(self, current_key: bytes) -> bytes:
        if self._trust_group is not None:
            external = self._trust_group.fetch()
            derived = hashlib.sha256(current_key + external + b"vstorage-ratchet").digest()
            external = bytes(len(external))
            return derived
        return _ratchet(current_key)

    def _fall_forever(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.001)
                continue
            with self._lock:
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
                    old_current.collapse()
                    self._on_next = [False] * len(self._chunks)

            if self._hop_interval > 0:
                self._stop.wait(self._hop_interval)

    @property
    def hops(self) -> int:
        return self._hops

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def regions(self) -> list:
        """Everything worth zeroing if tampering is detected: both key
        guards' cells (current AND next generation - a rotation may be
        mid-flight) plus every chunk's own buffer, for defense-in-depth."""
        out = list(self._current_guard.regions())
        out.extend(self._next_guard.regions())
        for chunk in self._chunks:
            out.append(chunk.region())
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

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._current_guard.collapse()
        self._next_guard.collapse()
        for chunk in self._chunks:
            chunk.collapse()
