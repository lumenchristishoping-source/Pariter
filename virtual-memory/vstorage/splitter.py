"""Step 1 of the architecture (HANDBOOK.md section 4): split a file into
content / structure / metadata pieces, by file type.

- content:  the actual information. Compresses well for structured/text data.
- structure: the format wrapper (binary overhead, container). Compresses poorly.
- metadata: filename, type, size, hash. Always tiny.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass

TEXT_LIKE_EXTS = {".json", ".csv", ".txt", ".md", ".c", ".py", ".log",
                   ".yaml", ".yml", ".toml", ".ini", ".html", ".htm",
                   ".geojson", ".jsonl", ".xml"}
ZIP_CONTAINER_EXTS = {".docx", ".xlsx", ".pptx"}
ALREADY_COMPRESSED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp",
                            ".mp4", ".mp3", ".aac", ".zip", ".gz", ".xz",
                            ".bin"}

# Which internal XML part to pull for a quick content preview of Office
# zip containers - not exhaustive, just the main document part per type.
_OFFICE_MAIN_PART = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/worksheets/sheet1.xml",
    ".pptx": "ppt/slides/slide1.xml",
}


@dataclass
class SplitResult:
    content: bytes
    structure: bytes
    metadata: bytes
    # Which piece is the byte-for-byte original, for lossless reconstruction.
    reconstruct_from: str  # "content" or "structure"


def _metadata_bytes(path: str, raw: bytes) -> bytes:
    digest = hashlib.sha256(raw).hexdigest()
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    text = f"name={name}\text={ext}\tsize={len(raw)}\tsha256={digest}"
    return text.encode("utf-8")


def split_file(path: str) -> SplitResult:
    with open(path, "rb") as f:
        raw = f.read()
    return split_bytes(raw, path)


def split_bytes(raw: bytes, name_hint: str) -> SplitResult:
    ext = os.path.splitext(name_hint)[1].lower()
    metadata = _metadata_bytes(name_hint, raw)

    if ext == ".pdf":
        content = _extract_pdf_text(raw)
        return SplitResult(content=content, structure=raw,
                            metadata=metadata, reconstruct_from="structure")

    if ext in ZIP_CONTAINER_EXTS:
        content = _extract_office_xml(raw, ext)
        return SplitResult(content=content, structure=raw,
                            metadata=metadata, reconstruct_from="structure")

    if ext in ALREADY_COMPRESSED_EXTS:
        # Content is empty: nothing left to usefully separate out, and
        # already-compressed bytes don't shrink further (HANDBOOK.md
        # section 9). Structure carries the whole file.
        return SplitResult(content=b"", structure=raw,
                            metadata=metadata, reconstruct_from="structure")

    # Text-like and anything unrecognized: content IS the file, structure
    # is just the format tag.
    return SplitResult(content=raw, structure=ext.encode("utf-8"),
                        metadata=metadata, reconstruct_from="content")


def _extract_pdf_text(raw: bytes) -> bytes:
    try:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text.encode("utf-8")
    except Exception:
        # No pypdf, or an unparseable PDF: fall back to empty content.
        # Structure (the full binary) still guarantees exact reconstruction.
        return b""


def _extract_office_xml(raw: bytes, ext: str) -> bytes:
    try:
        from io import BytesIO
        part = _OFFICE_MAIN_PART[ext]
        with zipfile.ZipFile(BytesIO(raw)) as z:
            return z.read(part)
    except Exception:
        return b""
