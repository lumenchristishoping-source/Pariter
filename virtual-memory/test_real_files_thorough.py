#!/usr/bin/env python3
"""Answers two fair questions the user raised about the last test:

1. Did it actually FALL, or did we just compress it and grab it straight
   back? Last time, save() and retrieve() happened back-to-back with no
   wait - the background thread had barely started. This time we sleep
   in between and print the hop count from each box, so there's proof
   real motion happened, not just a compress/decompress round trip.

2. Is the file that comes back not just byte-identical, but actually the
   SAME CONTENT when opened for real? SHA256 matching only proves the
   bytes line up - it doesn't prove the PDF still opens correctly or
   that the text inside still reads the same. So this re-opens the
   retrieved bytes as a real PDF with pypdf and compares page count and
   extracted text, page by page, against the original file opened fresh
   from disk (not from what we already had in memory).
"""
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.system import VirtualStorage

FILES = [
    ("/root/.claude/uploads/9c6fcc6d-8c14-5ccc-a3a0-fd3c78528f86/7f188850-FUTO_SM_Core_Portal1.PDF", "portal.pdf"),
    ("/root/.claude/uploads/9c6fcc6d-8c14-5ccc-a3a0-fd3c78528f86/fa1789a3-thechroniclesofnmachukwu.pdf", "book.pdf"),
]

FALL_TIME_SECONDS = 2.0


def pdf_text_pages(raw: bytes) -> list[str]:
    from io import BytesIO
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(raw))
    return [page.extract_text() or "" for page in reader.pages]


def main() -> None:
    vs = VirtualStorage()

    for src_path, name in FILES:
        print(f"\n=== {name} ===")
        with open(src_path, "rb") as f:
            original_raw = f.read()

        file_id = vs.save(src_path)
        held = vs._held[file_id]

        print(f"saved, now falling for {FALL_TIME_SECONDS}s "
              f"(3 boxes: content, structure, metadata)...")
        time.sleep(FALL_TIME_SECONDS)

        for piece_name, box in held.boxes.items():
            print(f"  {piece_name:10s} box hops during that wait: {box.hops}")

        # --- Question 1 answered: hops > 0 above means it was actually
        # moving on its own in the background, not sitting still.

        retrieved_raw = vs.retrieve(file_id, "full")

        # --- Question 2: byte check first (cheap, exact) ---
        orig_hash = hashlib.sha256(original_raw).hexdigest()
        retr_hash = hashlib.sha256(retrieved_raw).hexdigest()
        bytes_match = orig_hash == retr_hash
        print(f"  byte-for-byte identical: {bytes_match}  "
              f"(sha256 {orig_hash[:16]}... vs {retr_hash[:16]}...)")

        # --- Question 2, the real check: open both as actual PDFs and
        # compare the content a reader would actually see. ---
        orig_pages = pdf_text_pages(original_raw)
        retr_pages = pdf_text_pages(retrieved_raw)
        pages_match = len(orig_pages) == len(retr_pages)
        text_match = orig_pages == retr_pages
        print(f"  page count: original={len(orig_pages)} "
              f"retrieved={len(retr_pages)} match={pages_match}")
        print(f"  extracted text identical, page by page: {text_match}")
        if orig_pages:
            print(f"  page 1 opens and reads (first 120 chars): "
                  f"{orig_pages[0][:120].strip()!r}")

        vs.forget(file_id)


if __name__ == "__main__":
    main()
