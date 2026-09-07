"""THE MAIN SYSTEM (equivalent of HANDBOOK.md's combined_full_system.py):
split + compress + fall + targeted retrieval, tied together.

    from vstorage.system import VirtualStorage

    vs = VirtualStorage()
    file_id = vs.save("report.pdf")
    text = vs.retrieve(file_id, "content")     # just the text layer
    full = vs.retrieve(file_id, "full")        # exact original bytes
    vs.forget(file_id)                         # explicit early collapse
    vs.collapse_all()                          # at process end / on demand
"""

from __future__ import annotations

import lzma
import os
import uuid
from dataclasses import dataclass, field
from typing import Dict

from .falling_box import FallingBox
from .splitter import SplitResult, split_bytes, split_file

Piece = str  # "content" | "structure" | "metadata"


@dataclass
class _Held:
    boxes: Dict[Piece, FallingBox]
    reconstruct_from: Piece
    original_size: int
    name: str = field(default="")


class VirtualStorage:
    """Holds files in the broken-down, falling, RAM-only form described in
    HANDBOOK.md section 4. Nothing here ever touches disk; nothing here
    survives the process - see `collapse_all()` / `forget()`.
    """

    def __init__(self, compression_preset: int = 6):
        self._preset = compression_preset
        self._held: Dict[str, _Held] = {}

    # -- Step 1+2: split, compress, start falling ------------------------

    def save(self, path: str) -> str:
        result = split_file(path)
        return self._hold(result, name=os.path.basename(path))

    def save_bytes(self, raw: bytes, name_hint: str) -> str:
        result = split_bytes(raw, name_hint)
        return self._hold(result, name=name_hint)

    def _hold(self, result: SplitResult, name: str) -> str:
        file_id = uuid.uuid4().hex
        boxes = {
            "content": FallingBox(self._compress(result.content)),
            "structure": FallingBox(self._compress(result.structure)),
            "metadata": FallingBox(self._compress(result.metadata)),
        }
        original_size = len(result.content) if result.reconstruct_from == "content" else len(result.structure)
        self._held[file_id] = _Held(
            boxes=boxes,
            reconstruct_from=result.reconstruct_from,
            original_size=original_size,
            name=name,
        )
        return file_id

    def _compress(self, data: bytes) -> bytes:
        if not data:
            return b""
        return lzma.compress(data, preset=self._preset)

    def _decompress(self, data: bytes) -> bytes:
        if not data:
            return b""
        return lzma.decompress(data)

    # -- Step 4: retrieve on demand ---------------------------------------

    def retrieve(self, file_id: str, which: Piece | str = "full") -> bytes:
        held = self._held[file_id]

        if which == "full":
            which = held.reconstruct_from

        box = held.boxes[which]
        compressed = box.snapshot()          # pause -> copy -> resume
        data = self._decompress(compressed)  # full-size, briefly
        return data                          # caller drops the reference

    def held_size_bytes(self, file_id: str) -> int:
        """Bytes currently held (compressed, at rest) for a file - all
        three pieces combined."""
        held = self._held[file_id]
        return sum(len(box.snapshot()) for box in held.boxes.values())

    def original_size_bytes(self, file_id: str) -> int:
        return self._held[file_id].original_size

    def list_ids(self):
        return list(self._held.keys())

    # -- Step 5: death ------------------------------------------------------

    def forget(self, file_id: str) -> None:
        """Explicit early collapse of one file - nothing recoverable after."""
        held = self._held.pop(file_id, None)
        if held:
            for box in held.boxes.values():
                box.collapse()

    def collapse_all(self) -> None:
        for file_id in list(self._held.keys()):
            self.forget(file_id)
