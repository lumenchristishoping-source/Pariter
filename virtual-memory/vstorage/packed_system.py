"""Same job as system.py's VirtualStorage, but using packed_box.py:
ALL files share just 3 falling boxes total (content/structure/metadata),
instead of each file getting its own 3. Files are packed together into
those shared boxes, kept apart by an index, not by separate boxes.
"""

from __future__ import annotations

import lzma
import os
import uuid
from typing import Dict, Tuple

from .packed_box import PackedFallingBox
from .splitter import split_bytes, split_file

Piece = str


class PackedVirtualStorage:
    def __init__(self, compression_preset: int = 6):
        self._preset = compression_preset
        self._content_box = PackedFallingBox()
        self._structure_box = PackedFallingBox()
        self._metadata_box = PackedFallingBox()
        self._meta: Dict[str, Tuple[Piece, str]] = {}  # file_id -> (reconstruct_from, name)

    def save(self, path: str) -> str:
        result = split_file(path)
        return self._hold(result, name=os.path.basename(path))

    def save_bytes(self, raw: bytes, name_hint: str) -> str:
        result = split_bytes(raw, name_hint)
        return self._hold(result, name=name_hint)

    def _hold(self, result, name: str) -> str:
        file_id = uuid.uuid4().hex
        self._content_box.add(file_id, self._compress(result.content))
        self._structure_box.add(file_id, self._compress(result.structure))
        self._metadata_box.add(file_id, self._compress(result.metadata))
        self._meta[file_id] = (result.reconstruct_from, name)
        return file_id

    def _compress(self, data: bytes) -> bytes:
        if not data:
            return b""
        return lzma.compress(data, preset=self._preset)

    def _decompress(self, data: bytes) -> bytes:
        if not data:
            return b""
        return lzma.decompress(data)

    def _box_for(self, which: Piece) -> PackedFallingBox:
        return {"content": self._content_box,
                "structure": self._structure_box,
                "metadata": self._metadata_box}[which]

    def retrieve(self, file_id: str, which: Piece | str = "full") -> bytes:
        reconstruct_from, _name = self._meta[file_id]
        if which == "full":
            which = reconstruct_from
        compressed = self._box_for(which).get(file_id)
        return self._decompress(compressed)

    def held_size_bytes(self, file_id: str) -> int:
        total = 0
        for which in ("content", "structure", "metadata"):
            total += len(self._box_for(which).get(file_id))
        return total

    def list_ids(self):
        return list(self._meta.keys())

    def thread_count(self) -> int:
        """How many falling threads exist right now - 3, no matter how
        many files are held, unlike system.py's VirtualStorage (3 per
        file)."""
        return 3

    def collapse_all(self) -> None:
        self._content_box.collapse()
        self._structure_box.collapse()
        self._metadata_box.collapse()
        self._meta = {}
