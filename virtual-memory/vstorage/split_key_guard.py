"""The user's addition to Tier 2: instead of the encryption key sitting
as one 32-byte block, compress it and split it character by character
(byte by byte) - each byte gets its OWN independent hop, on its own
timer, not synchronized with the others.

Honest note up front: compressing a real cryptographic key doesn't
shrink it. A proper key is indistinguishable from random noise, and
compression can't find any pattern in true randomness to exploit -
lzma consistently turns a 32-byte AES key into 88 bytes (verified:
20 different random keys, always exactly 88 bytes). It's still done
here, exactly as asked - it just doesn't get smaller, it gets a little
bigger, because of the container format's own overhead.

Each byte lives in its own locked, dontdump-protected pair of memory
pages - 88 separate addresses, which is the part that actually matters
(no single one holds anything useful; all 88 are needed, at their
correct positions, to reconstruct anything - a genuinely different
property than the earlier "many falling boxes" experiment, which
failed because every box held a full, independent copy).

Version history, and why this isn't 88 threads anymore: the first
version gave each byte its own OS thread. Tested under real combined
load (with the data box's crypto running too), that pushed RssAnon
from a real ~200KB of actual data up to +67MB - not from the crypto
(measured separately: flat, +220KB over 150 iterations with no extra
threads) and not from the threads existing (88 idle threads alone:
+1.3MB). It was glibc spinning up a separate memory arena per
concurrently-allocating thread, and a malloc_trim() call from one
thread doesn't reliably reach garbage sitting in another thread's
arena. Fixed by keeping the 88 separate LOCATIONS (the real security
property) but servicing them all from ONE scheduler thread instead of
88 - each cell still gets its own independent, randomized next-hop
time, just serviced by a shared mover instead of a dedicated thread.

Second round, found the same way: that fix was "one thread per GUARD,"
which is fine for one file, but a real 24-file, ~2GB run (2 guards x
3 pieces x 24 files = 144 guards) meant 144 of these scheduler threads
alone, contributing to 217 total background threads system-wide - and
under that much GIL contention, saving 24 files took nearly an hour,
and RETRIEVING them back out was, in places, even slower than saving.
Same fix, one level up: every guard in the whole process now registers
its cells with ONE shared _GlobalCellScheduler instead of starting its
own thread. Every cell keeps its own separately-mlocked, separately-
hidden pair of pages (the real security property, still fully intact -
this only changes which thread does the checking-in, not how many
places the key lives or how independently each byte moves).
"""

from __future__ import annotations

import ctypes
import heapq
import lzma
import mmap
import random
import threading
import time
from typing import List

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
MADV_DONTDUMP = 16
PAGE_SIZE = mmap.PAGESIZE


def _lock_and_hide(buf: mmap.mmap) -> dict:
    addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    length = len(buf)
    mlock_ok = _libc.mlock(ctypes.c_void_p(addr), ctypes.c_size_t(length)) == 0
    dontdump_ok = _libc.madvise(ctypes.c_void_p(addr), ctypes.c_size_t(length),
                                 MADV_DONTDUMP) == 0
    return {"mlock": mlock_ok, "dontdump": dontdump_ok}


class _RawCell:
    """One byte's own two locked pages. No thread of its own - the
    shared scheduler in SplitKeyGuard calls bounce()/write() on it at
    that cell's own independently-scheduled moment."""

    def __init__(self, value: int):
        # MAP_PRIVATE|MAP_ANONYMOUS explicitly - mmap.mmap(-1, n) alone
        # defaults to MAP_SHARED, backed by a deleted /dev/zero (visible
        # as a real path in /proc/pid/maps, counted as RssFile not
        # RssAnon, and - more seriously - inherited as truly SHARED
        # rather than copy-on-write across a fork()). Found via a real
        # 1GB test where RssAnon suspiciously barely moved.
        _anon = mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS
        self._page_a = mmap.mmap(-1, PAGE_SIZE, flags=_anon)
        self._page_b = mmap.mmap(-1, PAGE_SIZE, flags=_anon)
        self._page_a[0:1] = bytes([value])
        self._active = "a"
        self.protection_status = {
            "a": _lock_and_hide(self._page_a), "b": _lock_and_hide(self._page_b),
        }

    def bounce(self) -> None:
        if self._active == "a":
            self._page_b[0:1] = self._page_a[0:1]
            self._active = "b"
        else:
            self._page_a[0:1] = self._page_b[0:1]
            self._active = "a"

    def read(self) -> int:
        active = self._page_a if self._active == "a" else self._page_b
        return active[0]

    def write(self, value: int) -> None:
        active = self._page_a if self._active == "a" else self._page_b
        active[0:1] = bytes([value])

    def regions(self) -> list:
        """Both pages' addresses - stable for this cell's whole life
        (bounce() only ever rewrites the 1 byte inside them, never
        reallocates), so a watchdog can register them once and they
        stay valid targets to zero for as long as the cell exists."""
        a = ctypes.addressof((ctypes.c_char * len(self._page_a)).from_buffer(self._page_a))
        b = ctypes.addressof((ctypes.c_char * len(self._page_b)).from_buffer(self._page_b))
        return [(a, len(self._page_a)), (b, len(self._page_b))]

    def collapse(self) -> None:
        self._page_a.close()
        self._page_b.close()


class _GlobalCellScheduler:
    """A small pool of shared background threads services every
    SplitKeyGuard's cells in the whole process, instead of each guard
    running its own scheduler thread - see the module docstring's
    "second round" note for why. A real multi-file run found that a
    single shared thread here (or in the matching fall-motion pool in
    chunked_secure_box.py) can be starved completely by a sustained
    heavy operation elsewhere - a small pool (default 4) gives real
    headroom against that without coming close to the old per-guard
    thread count. Every cell keeps its own independently-randomized
    next-hop time; this only changes who does the checking-in.

    Safety: a guard's own `_lock` (already used by reconstruct() and
    update()) is reused here too - the scheduler only ever touches a
    guard's cells while holding THAT guard's lock, so collapse() can
    safely set `_removed` under the same lock and know no bounce is
    either in flight or will start again afterward, before it closes
    the underlying mmap pages."""

    DEFAULT_WORKERS = 4

    def __init__(self, worker_count: int = DEFAULT_WORKERS):
        self._worker_count = worker_count
        self._lock = threading.Lock()
        self._heap: list = []  # (due_time, seq, guard, cell_index)
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

    def register(self, guard: "SplitKeyGuard") -> None:
        self._ensure_started()
        now = time.monotonic()
        with self._lock:
            for idx in range(len(guard._cells)):
                due = now + random.uniform(guard._min_interval, guard._max_interval)
                self._seq += 1
                heapq.heappush(self._heap, (due, self._seq, guard, idx))
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
                # Several workers now race each other here - only take
                # an entry that is actually due right now (see the
                # matching note in chunked_secure_box.py's fall pool).
                if self._heap[0][0] - time.monotonic() > 0:
                    continue
                _due, _seq, guard, idx = heapq.heappop(self._heap)
            next_due = None
            with guard._lock:
                if not guard._removed:
                    try:
                        guard._cells[idx].bounce()
                        guard._hops[idx] += 1
                        next_due = time.monotonic() + random.uniform(
                            guard._min_interval, guard._max_interval)
                    except Exception:
                        # This is the ONE shared thread for every
                        # guard's cells in the process - an uncaught
                        # exception here (e.g. a race with an external
                        # tamper response touching this same memory)
                        # would silently stop key rotation for every
                        # OTHER guard too. Drop this cell, keep going.
                        next_due = None
            if next_due is not None:
                with self._lock:
                    self._seq += 1
                    heapq.heappush(self._heap, (next_due, self._seq, guard, idx))


_cell_scheduler = _GlobalCellScheduler()


class SplitKeyGuard:
    """Compresses a key, splits it into one cell per byte (88 separate
    locked address-pairs), each cell's hop serviced by the process-wide
    _GlobalCellScheduler at that cell's own independently-randomized
    time - not synchronized with the others, and without one OS thread
    per guard (let alone one per cell). update() rewrites all cells to
    a new key without recreating any mmap regions or threads."""

    def __init__(self, key: bytes, min_interval: float = 0.001,
                 max_interval: float = 0.01):
        compressed = lzma.compress(key, preset=9)
        self.original_len = len(key)
        self.compressed_len = len(compressed)
        self._min_interval = min_interval
        self._max_interval = max_interval

        self._cells: List[_RawCell] = [_RawCell(b) for b in compressed]
        self._hops = [0] * len(self._cells)
        self._lock = threading.Lock()
        self._removed = False

        _cell_scheduler.register(self)

    def reconstruct(self) -> bytes:
        with self._lock:
            compressed = bytes(cell.read() for cell in self._cells)
        return lzma.decompress(compressed)

    def update(self, new_key: bytes) -> bool:
        """Rewrites every cell to the new key's compressed bytes.
        Returns False (and does nothing) if the compressed length
        changed - in practice this doesn't happen for same-length AES
        keys (verified: always 88 bytes for 32)."""
        compressed = lzma.compress(new_key, preset=9)
        if len(compressed) != len(self._cells):
            return False
        with self._lock:
            for cell, b in zip(self._cells, compressed):
                cell.write(b)
        return True

    @property
    def hop_counts(self) -> List[int]:
        return list(self._hops)

    def regions(self) -> list:
        out = []
        for cell in self._cells:
            out.extend(cell.regions())
        return out

    def collapse(self) -> None:
        with self._lock:
            self._removed = True
        for c in self._cells:
            c.collapse()
