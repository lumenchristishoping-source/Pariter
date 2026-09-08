"""Tests the user's actual question: what if there weren't just 2
spots (box_a/box_b), but MANY - N buffers, data cycling through all of
them in sequence, at N different addresses instead of 2?

Same idea as falling_box.py, generalized from 2 buffers to N.
"""

from __future__ import annotations

import threading
from typing import List

BOX_SIZE = 64 * 1024


class NFallingBox:
    def __init__(self, data: bytes, n_boxes: int = 8):
        self._boxes: List[bytearray] = [bytearray(len(data)) for _ in range(n_boxes)]
        self._boxes[0][:] = data
        self._active = 0
        self._lock = threading.Lock()
        self._hops = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _fall_forever(self) -> None:
        n = len(self._boxes)
        while not self._stop.is_set():
            with self._lock:
                nxt = (self._active + 1) % n
                src = self._boxes[self._active]
                dst = self._boxes[nxt]
                for i in range(0, len(src), BOX_SIZE):
                    chunk = src[i:i + BOX_SIZE]
                    dst[i:i + len(chunk)] = chunk
                self._active = nxt
                self._hops += 1

    @property
    def hops(self) -> int:
        return self._hops

    @property
    def boxes(self) -> List[bytearray]:
        return self._boxes

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._boxes = []
