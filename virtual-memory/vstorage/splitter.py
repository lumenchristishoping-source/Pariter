"""Step 1 of the architecture (HANDBOOK.md section 4): split a file into
content / structure / metadata pieces, by file type.

- content:  the actual information. Compresses well for structured/text data.
- structure: the format wrapper (binary overhead, container). Compresses poorly.
- metadata: filename, type, size, hash. Always tiny.

Real gap found and fixed here: split_file() used to do `open(path).read()`
- one full read into a single Python bytes object - before anything else
could happen. ChunkedSecureBox.from_file() already proved a 12GB file
can be ingested at ~292MB peak RAM (see REBUILD_STATUS.md), but that
primitive was only ever reachable by calling it directly - the real
`SecureVirtualStorage.save()` front door still needed roughly the
file's own size in RAM just to get through split_file() first. A 40GB
file would need ~40GB free just to start, regardless of how cheaply it
could be held afterward.

Fixed with a FromFile marker: for the common case (content or
structure IS the source file, byte-for-byte - text-like files, and the
structure piece of everything else), split_file() now hands back a
FromFile(path) instead of materialized bytes, and secure_system.py
streams straight from disk into the box via ChunkedSecureBox.from_file()
- the same primitive already proven at 12GB, now reachable from the
real save() path. The whole-file hash (metadata) is computed the same
way, in fixed-size chunks, never holding the file whole either.

Honest limits, not hidden:
- This costs 2 full disk reads instead of 1 (one for the streaming
  hash, one for streaming ingestion) - a real trade of I/O time for
  bounded RAM, not free.
- PDF text extraction and Office XML extraction still need to open
  and parse the file (via pypdf/zipfile, now passed the path directly
  instead of a pre-loaded BytesIO, which avoids the double-buffering
  this module used to add on top - but the underlying parser's own
  memory behavior for a huge single PDF/DOCX isn't controlled by this
  module).
- Bounded RAM for INGESTION is not the same as bounded RAM for
  STORAGE: a compressible 40GB text file can genuinely be held in a
  small fraction of that once compressed (the 12GB test showed ~41x).
  An already-incompressible 40GB file (video, already-zipped data)
  will still need close to its own size once compressed+encrypted,
  because there is nothing left to compress - this module fixes the
  "needs 2x to start" problem, not the information floor
  (HANDBOOK.md section 11).
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from typing import Union

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

HASH_CHUNK_SIZE = 4 * 1024 * 1024  # streaming hash, never holds the file whole


@dataclass
class FromFile:
    """Marks a piece that should stream straight from disk into its
    box (ChunkedSecureBox.from_file()) instead of being materialized
    as one Python bytes object first."""
    path: str


Piece = Union[bytes, FromFile]


@dataclass
class SplitResult:
    content: Piece
    structure: Piece
    metadata: bytes
    # Which piece is the byte-for-byte original, for lossless reconstruction.
    reconstruct_from: str  # "content" or "structure"


def piece_size(piece: Piece) -> int:
    """Byte length of a piece, whichever form it's in - without ever
    reading a FromFile piece's bytes just to measure it."""
    if isinstance(piece, FromFile):
        return os.path.getsize(piece.path)
    return len(piece)


def _metadata_bytes_for_path(path: str) -> bytes:
    """Same fields as before (name, ext, size, sha256 of the whole
    original file), computed by streaming - reads the file in fixed-
    size chunks, never holds it whole, regardless of file size."""
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    text = f"name={name}\text={ext}\tsize={size}\tsha256={h.hexdigest()}"
    return text.encode("utf-8")


def split_file(path: str) -> SplitResult:
    """Streaming entry point - the real save() front door. Never reads
    the whole file into one Python object; content/structure pieces
    that mirror the source file stream straight from disk via
    FromFile, and the hash is computed by reading in chunks."""
    ext = os.path.splitext(path)[1].lower()
    metadata = _metadata_bytes_for_path(path)

    if ext == ".pdf":
        content = _extract_pdf_text_from_path(path)
        return SplitResult(content=content, structure=FromFile(path),
                            metadata=metadata, reconstruct_from="structure")

    if ext in ZIP_CONTAINER_EXTS:
        content = _extract_office_xml_from_path(path, ext)
        return SplitResult(content=content, structure=FromFile(path),
                            metadata=metadata, reconstruct_from="structure")

    if ext in ALREADY_COMPRESSED_EXTS:
        # Content is empty: nothing left to usefully separate out, and
        # already-compressed bytes don't shrink further (HANDBOOK.md
        # section 9). Structure carries the whole file - streamed.
        return SplitResult(content=b"", structure=FromFile(path),
                            metadata=metadata, reconstruct_from="structure")

    # Text-like and anything unrecognized: content IS the file - stream
    # it - structure is just the tiny format tag.
    return SplitResult(content=FromFile(path), structure=ext.encode("utf-8"),
                        metadata=metadata, reconstruct_from="content")


def split_bytes(raw: bytes, name_hint: str) -> SplitResult:
    """For data that's ALREADY in memory (e.g. re-storing bytes just
    retrieved from this same system - save_bytes()'s only caller
    today). No file on disk to stream from here, so this path keeps
    materializing pieces as plain bytes - that's fine, since the
    caller already paid the memory cost by having `raw` in hand at
    all, this doesn't add a second one."""
    ext = os.path.splitext(name_hint)[1].lower()
    digest = hashlib.sha256(raw).hexdigest()
    name = os.path.basename(name_hint)
    metadata = f"name={name}\text={ext}\tsize={len(raw)}\tsha256={digest}".encode("utf-8")

    if ext == ".pdf":
        content = _extract_pdf_text_from_bytes(raw)
        return SplitResult(content=content, structure=raw,
                            metadata=metadata, reconstruct_from="structure")

    if ext in ZIP_CONTAINER_EXTS:
        content = _extract_office_xml_from_bytes(raw, ext)
        return SplitResult(content=content, structure=raw,
                            metadata=metadata, reconstruct_from="structure")

    if ext in ALREADY_COMPRESSED_EXTS:
        return SplitResult(content=b"", structure=raw,
                            metadata=metadata, reconstruct_from="structure")

    return SplitResult(content=raw, structure=ext.encode("utf-8"),
                        metadata=metadata, reconstruct_from="content")


def _extract_pdf_text_from_path(path: str) -> bytes:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text.encode("utf-8")
    except Exception:
        # No pypdf, or an unparseable PDF: fall back to empty content.
        # Structure (the full binary) still guarantees exact reconstruction.
        return b""


def _extract_office_xml_from_path(path: str, ext: str) -> bytes:
    try:
        part = _OFFICE_MAIN_PART[ext]
        with zipfile.ZipFile(path) as z:
            return z.read(part)
    except Exception:
        return b""


def _extract_pdf_text_from_bytes(raw: bytes) -> bytes:
    try:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text.encode("utf-8")
    except Exception:
        return b""


def _extract_office_xml_from_bytes(raw: bytes, ext: str) -> bytes:
    try:
        from io import BytesIO
        part = _OFFICE_MAIN_PART[ext]
        with zipfile.ZipFile(BytesIO(raw)) as z:
            return z.read(part)
    except Exception:
        return b""
