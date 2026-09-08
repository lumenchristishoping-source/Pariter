"""The actual fix for the finding that plain motion defeats nothing:
zero the OLD buffer immediately after each hop, so there's never a
moment with two full, valid copies sitting in memory at once - only
ever one live copy, wherever it currently is.
"""

from __future__ import annotations

import threading

BOX_SIZE = 64 * 1024


class ZeroingFallingBox:
    def __init__(self, data: bytes):
        self._box_a = bytearray(data)
        self._box_b = bytearray(len(data))
        self._active = "a"
        self._lock = threading.Lock()
        self._hops = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _fall_forever(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if self._active == "a":
                    for i in range(0, len(self._box_a), BOX_SIZE):
                        chunk = self._box_a[i:i + BOX_SIZE]
                        self._box_b[i:i + len(chunk)] = chunk
                    for i in range(len(self._box_a)):
                        self._box_a[i] = 0
                    self._active = "b"
                else:
                    for i in range(0, len(self._box_b), BOX_SIZE):
                        chunk = self._box_b[i:i + BOX_SIZE]
                        self._box_a[i:i + len(chunk)] = chunk
                    for i in range(len(self._box_b)):
                        self._box_b[i] = 0
                    self._active = "a"
                self._hops += 1

    @property
    def hops(self) -> int:
        return self._hops

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._box_a = bytearray()
        self._box_b = bytearray()
