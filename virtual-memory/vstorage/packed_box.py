"""Packed falling boxes - the user's proposed alternative to one set of
3 boxes PER FILE: instead, ALL files share just 3 boxes total (one for
all contents, one for all structures, one for all metadata). Every
file's piece gets packed into the same shared buffer, back to back -
but each file's own bytes stay exactly its own, found again by a small
index (name -> offset, length), not mixed together.

Trade being tested: fewer boxes and fewer background threads (3 total,
no matter how many files), against the earlier design (3 threads PER
file). Same falling motion, same pause-snapshot-resume pattern as
falling_box.py - just one shared buffer instead of many small ones.
"""

from __future__ import annotations

import threading
from typing import Dict, Tuple

BOX_SIZE = 64 * 1024


class PackedFallingBox:
    """One falling buffer shared by many files' same-type piece. `add()`
    pauses the fall, appends the new file's bytes, resumes. `get()`
    pauses, reads just that file's slice out (leaving everyone else's
    bytes untouched), resumes.
    """

    def __init__(self):
        self._box_a = bytearray()
        self._box_b = bytearray()
        self._active = "a"
        self._index: Dict[str, Tuple[int, int]] = {}
        self._lock = threading.Lock()
        self._hops = 0
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _fall_forever(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.001)
                continue
            with self._lock:
                length = len(self._box_a)
                if length:
                    if self._active == "a":
                        for i in range(0, length, BOX_SIZE):
                            chunk = self._box_a[i:i + BOX_SIZE]
                            self._box_b[i:i + len(chunk)] = chunk
                        self._active = "b"
                    else:
                        for i in range(0, length, BOX_SIZE):
                            chunk = self._box_b[i:i + BOX_SIZE]
                            self._box_a[i:i + len(chunk)] = chunk
                        self._active = "a"
                    self._hops += 1
            if not length:
                # Nothing to fall yet (box just created, or just emptied
                # by remove()) - without this wait, the loop above spins
                # as fast as possible doing zero work, and that tight
                # spin starves any other thread trying to grab the same
                # lock (add()/get()/remove()) for seconds at a time.
                self._stop.wait(0.001)

    def add(self, name: str, data: bytes) -> None:
        """Pack one more file's piece onto the end of the shared buffer."""
        self._paused.set()
        with self._lock:
            active = self._box_a if self._active == "a" else self._box_b
            other = self._box_b if self._active == "a" else self._box_a
            offset = len(active)
            active.extend(data)
            other.extend(bytes(len(data)))  # keep both buffers same length
            self._index[name] = (offset, len(data))
        self._paused.clear()

    def get(self, name: str) -> bytes:
        """Pause, pull just this file's slice out of the shared buffer,
        resume. Everyone else's bytes are untouched and still falling
        the instant this returns."""
        self._paused.set()
        with self._lock:
            offset, length = self._index[name]
            active = self._box_a if self._active == "a" else self._box_b
            out = bytes(active[offset:offset + length])
        self._paused.clear()
        return out

    def remove(self, name: str) -> None:
        """Pause, cut this file's slice clean out of the shared buffer
        (both copies), shift everyone after it back to close the gap,
        resume. Nothing recoverable of this file after - matches
        collapse()'s "death" idea, just for one file instead of all."""
        self._paused.set()
        with self._lock:
            offset, length = self._index.pop(name)
            del self._box_a[offset:offset + length]
            del self._box_b[offset:offset + length]
            for other_name, (o, l) in list(self._index.items()):
                if o > offset:
                    self._index[other_name] = (o - length, l)
        self._paused.clear()

    @property
    def hops(self) -> int:
        return self._hops

    @property
    def file_count(self) -> int:
        return len(self._index)

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._box_a = bytearray()
        self._box_b = bytearray()
        self._index = {}
