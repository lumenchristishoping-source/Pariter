"""Step 3 of the architecture (HANDBOOK.md section 4): a compressed piece
held in continuous motion through small 64KB "boxes" that break and reform,
so it never rests at one address for more than a fraction of a second.

Vocabulary (HANDBOOK.md section 3):
  Box     - a small fixed-size buffer (64KB) holding a piece briefly.
  Falling - the continuous motion of data through a chain of boxes.
"""

from __future__ import annotations

import threading

BOX_SIZE = 64 * 1024


class FallingBox:
    """Keeps `data` continuously moving between two buffers in a background
    thread. At any instant, the data lives in whichever buffer is currently
    "active" - the other one is being overwritten (broken) chunk by chunk
    and about to become active again (reformed).
    """

    def __init__(self, data: bytes):
        self._box_a = bytearray(data)
        self._box_b = bytearray(len(data))
        self._active = "a"  # which box currently holds the real data
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
                if self._active == "a":
                    for i in range(0, len(self._box_a), BOX_SIZE):
                        chunk = self._box_a[i:i + BOX_SIZE]
                        self._box_b[i:i + len(chunk)] = chunk
                    self._active = "b"
                else:
                    for i in range(0, len(self._box_b), BOX_SIZE):
                        chunk = self._box_b[i:i + BOX_SIZE]
                        self._box_a[i:i + len(chunk)] = chunk
                    self._active = "a"
                self._hops += 1

    def snapshot(self) -> bytearray:
        """Pause falling, copy out the currently-active buffer, resume.
        Returns a bytearray, not bytes - bytes is immutable, so a
        caller could never actually zero their own copy after using
        it (secure_falling_box.secure_zero() needs this to be
        mutable)."""
        self._paused.set()
        with self._lock:
            active = self._box_a if self._active == "a" else self._box_b
            out = bytearray(active)
        self._paused.clear()
        return out

    @property
    def hops(self) -> int:
        return self._hops

    def collapse(self) -> None:
        """Stop falling and drop both buffers. Nothing recoverable after
        this - matches HANDBOOK.md section 4, Step 5 (Death): once nothing
        holds an address to the memory, it's unreachable, then physically
        overwritten whenever the OS reallocates those pages.
        """
        self._stop.set()
        self._thread.join(timeout=1)
        self._box_a = bytearray()
        self._box_b = bytearray()
