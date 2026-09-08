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


class SplitKeyGuard:
    """Compresses a key, splits it into one cell per byte (88 separate
    locked address-pairs), each cell's hop serviced by ONE shared
    scheduler thread at that cell's own independently-randomized time
    - not synchronized with the others, but without 88 separate OS
    threads. update() rewrites all cells to a new key without
    recreating any mmap regions or threads."""

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
        self._stop = threading.Event()

        now = time.monotonic()
        self._heap = [
            (now + random.uniform(min_interval, max_interval), i)
            for i in range(len(self._cells))
        ]
        heapq.heapify(self._heap)

        self._thread = threading.Thread(target=self._scheduler, daemon=True)
        self._thread.start()

    def _scheduler(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                due_time, idx = self._heap[0]
            wait = due_time - time.monotonic()
            if wait > 0:
                self._stop.wait(min(wait, 0.01))
                continue
            with self._lock:
                if not self._heap:
                    continue
                due_time, idx = heapq.heappop(self._heap)
                self._cells[idx].bounce()
                self._hops[idx] += 1
                next_due = time.monotonic() + random.uniform(
                    self._min_interval, self._max_interval)
                heapq.heappush(self._heap, (next_due, idx))

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
        self._stop.set()
        self._thread.join(timeout=1)
        for c in self._cells:
            c.collapse()
