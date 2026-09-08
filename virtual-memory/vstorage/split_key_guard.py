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

Each byte then lives in its own locked, dontdump-protected pair of
memory pages, bouncing between them on ITS OWN random schedule -
different interval, different phase, unrelated to every other byte's
timer. This is a genuinely different property from the earlier
"many falling boxes" experiment for the whole file: that failed
because every box held a FULL, independent COPY (any one of them was
enough). Here, no single cell holds anything useful by itself - all
88 have to be read, at their correct positions, to reconstruct
anything. Missing even one breaks the whole key.
"""

from __future__ import annotations

import ctypes
import lzma
import mmap
import random
import threading
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


class KeyByteCell:
    """One byte of the key, bouncing between its own two locked pages
    on its own independent, randomly-jittered schedule."""

    def __init__(self, value: int, min_interval: float = 0.001,
                 max_interval: float = 0.01):
        self._page_a = mmap.mmap(-1, PAGE_SIZE)
        self._page_b = mmap.mmap(-1, PAGE_SIZE)
        self._page_a[0:1] = bytes([value])
        self._active = "a"
        self._lock = threading.Lock()
        self._hops = 0
        self._stop = threading.Event()
        self._min_interval = min_interval
        self._max_interval = max_interval

        self.protection_status = {
            "a": _lock_and_hide(self._page_a), "b": _lock_and_hide(self._page_b),
        }

        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _fall_forever(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if self._active == "a":
                    self._page_b[0:1] = self._page_a[0:1]
                    self._active = "b"
                else:
                    self._page_a[0:1] = self._page_b[0:1]
                    self._active = "a"
                self._hops += 1
            # independent timer per byte - not synchronized with the others
            self._stop.wait(random.uniform(self._min_interval, self._max_interval))

    def read(self) -> int:
        with self._lock:
            active = self._page_a if self._active == "a" else self._page_b
            return active[0]

    def write(self, value: int) -> None:
        with self._lock:
            active = self._page_a if self._active == "a" else self._page_b
            active[0:1] = bytes([value])

    @property
    def hops(self) -> int:
        return self._hops

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._page_a.close()
        self._page_b.close()


class SplitKeyGuard:
    """Compresses a key, splits it into one cell per byte, each
    hopping independently. reconstruct() re-assembles it on demand;
    update() rewrites all cells to a new key without tearing down and
    recreating 176 mmap regions and 88 threads on every ratchet."""

    def __init__(self, key: bytes):
        compressed = lzma.compress(key, preset=9)
        self.original_len = len(key)
        self.compressed_len = len(compressed)
        self._cells: List[KeyByteCell] = [KeyByteCell(b) for b in compressed]

    def reconstruct(self) -> bytes:
        compressed = bytes(cell.read() for cell in self._cells)
        return lzma.decompress(compressed)

    def update(self, new_key: bytes) -> bool:
        """Rewrites every cell to the new key's compressed bytes.
        Returns False (and does nothing) if the compressed length
        changed - callers should handle that by building a fresh
        guard instead, though in practice this doesn't happen for
        same-length AES keys (verified: always 88 bytes for 32)."""
        compressed = lzma.compress(new_key, preset=9)
        if len(compressed) != len(self._cells):
            return False
        for cell, b in zip(self._cells, compressed):
            cell.write(b)
        return True

    @property
    def hop_counts(self) -> List[int]:
        return [c.hops for c in self._cells]

    def collapse(self) -> None:
        for c in self._cells:
            c.collapse()
