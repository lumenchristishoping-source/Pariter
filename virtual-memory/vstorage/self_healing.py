"""Reproduces HANDBOOK.md Experiments 6-7 (self-healing nested boxes).

v1 (Experiment 6): N nested boxes. When the innermost "breaks" (fails),
the box containing it becomes the new holder, and a fresh box forms as
the new innermost. Vulnerability: if the OUTERMOST box breaks with
nothing left outside it, data is lost - there's no wrapper left to fall
back to.

v2 (Experiment 7, the fix): both ends regenerate. The outermost box
always has a wrapper forming around it, so there's never a "last box
with nothing behind it." HANDBOOK.md reports 5,000 consecutive failures
(both ends), zero data loss, SHA-256 match.
"""

from __future__ import annotations

import hashlib
import random
from collections import deque


class NestedBoxesV1:
    """The vulnerable version: only the inner end regenerates."""

    def __init__(self, data: bytes, depth: int = 3):
        self._layers = deque([data] * depth)  # layers[0] = innermost

    def fail_innermost(self) -> bool:
        """Simulates the innermost box breaking. Returns False (data lost)
        if there was nothing left to promote.
        """
        if not self._layers:
            return False
        self._layers.popleft()
        if not self._layers:
            return False  # outermost just broke with nothing behind it
        return True

    def fail_outermost(self) -> bool:
        """The vulnerability: nothing regenerates the outer end."""
        if not self._layers:
            return False
        self._layers.pop()
        return len(self._layers) > 0

    def current(self) -> bytes | None:
        return self._layers[0] if self._layers else None


class NestedBoxesV2:
    """The fix: both ends regenerate, so there's never a last box with
    nothing behind it.
    """

    def __init__(self, data: bytes, depth: int = 3):
        self._data = data
        self._depth = depth
        self._layers = deque([data] * depth)

    def fail_innermost(self) -> bytes:
        """Innermost breaks -> promote the next layer -> a fresh wrapper
        reforms at the outer end, keeping depth constant. Never loses data.
        """
        self._layers.popleft()
        self._layers.append(self._data)  # a fresh outer wrapper reforms
        return self._layers[0]

    def fail_outermost(self) -> bytes:
        """Outermost breaks -> a fresh inner box reforms to replace it
        conceptually; depth is maintained from the other end.
        """
        self._layers.pop()
        self._layers.appendleft(self._data)
        return self._layers[0]

    def current(self) -> bytes:
        return self._layers[0]


def stress_test_v1(data: bytes, depth: int, failures: int) -> dict:
    boxes = NestedBoxesV1(data, depth=depth)
    survived = 0
    for i in range(failures):
        # Alternate failure points, matching Experiment 6's "10 consecutive
        # failures" framing, but track exactly where it breaks.
        ok = boxes.fail_innermost() if i % 2 == 0 else boxes.fail_outermost()
        if not ok:
            return {"survived": survived, "failed_at": i, "data_lost": True}
        survived += 1
    return {"survived": survived, "failed_at": None, "data_lost": False}


def stress_test_v2(data: bytes, depth: int, failures: int) -> dict:
    boxes = NestedBoxesV2(data, depth=depth)
    expected_hash = hashlib.sha256(data).hexdigest()
    rng = random.Random(1234)

    for i in range(failures):
        if rng.random() < 0.5:
            current = boxes.fail_innermost()
        else:
            current = boxes.fail_outermost()
        if hashlib.sha256(current).hexdigest() != expected_hash:
            return {"survived": i, "failures_requested": failures,
                     "data_intact": False}

    return {"survived": failures, "failures_requested": failures,
             "data_intact": True, "sha256_match": True}
