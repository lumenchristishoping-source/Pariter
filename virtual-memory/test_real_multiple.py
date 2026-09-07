#!/usr/bin/env python3
"""Multiple REAL files held and falling at the SAME time, with every
form of RAM tracked (not just RssAnon), and content checked properly -
not just byte-hash, but actually opened and read back for the types
where that means something (PDF: pages + text; zip: still a valid
archive; text: exact string match).

Files used (all real, all previously supplied by the user - nothing
synthetic here):
  - FUTO_SM_Core_Portal1.PDF        (real PDF, mostly image)
  - thechroniclesofnmachukwu.pdf    (real PDF, mostly text, 49 pages)
  - VIRTUAL_STORAGE_HANDBOOK.md     (real markdown, the project's own spec)
  - virtual_storage_source.zip      (real zip, already-compressed container)

4 files x 3 pieces each (content/structure/metadata) = 12 boxes falling
in the same process at the same time, none touching each other.
"""
import ctypes
import gc
import hashlib
import io
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot, system_snapshot
from vstorage.system import VirtualStorage

UPLOAD_DIR = "/root/.claude/uploads/9c6fcc6d-8c14-5ccc-a3a0-fd3c78528f86"
FILES = [
    (f"{UPLOAD_DIR}/7f188850-FUTO_SM_Core_Portal1.PDF", "portal.pdf"),
    (f"{UPLOAD_DIR}/fa1789a3-thechroniclesofnmachukwu.pdf", "book.pdf"),
    (f"{UPLOAD_DIR}/811bf743-VIRTUAL_STORAGE_HANDBOOK.md", "handbook.md"),
    (f"{UPLOAD_DIR}/50e10a20-virtual_storage_source.zip", "source.zip"),
]
FALL_TIME_SECONDS = 2.0


def pdf_pages(raw: bytes) -> list[str]:
    from io import BytesIO
    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(BytesIO(raw)).pages]


def verify_content(name: str, original: bytes, retrieved: bytes) -> bool:
    """Real content check per type, not just a hash comparison."""
    if name.endswith(".pdf"):
        orig_pages = pdf_pages(original)
        retr_pages = pdf_pages(retrieved)
        ok = orig_pages == retr_pages
        print(f"    PDF re-opened: {len(retr_pages)} pages, "
              f"text identical page-by-page: {ok}")
        return ok
    if name.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(retrieved)) as z:
                bad = z.testzip()
                names = z.namelist()
            ok = bad is None
            print(f"    zip re-opened: {len(names)} entries, "
                  f"testzip() found corruption: {bad}, valid archive: {ok}")
            return ok
        except zipfile.BadZipFile:
            print("    zip re-opened: FAILED to open as a zip at all")
            return False
    ok = original.decode("utf-8") == retrieved.decode("utf-8")
    print(f"    text re-decoded as utf-8, string-identical: {ok}")
    return ok


def release_freed_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


def main() -> None:
    originals = {}
    for path, name in FILES:
        with open(path, "rb") as f:
            originals[name] = f.read()

    print(f"{len(FILES)} real files, about to hold and fall at the SAME time:")
    for name, raw in originals.items():
        print(f"  {name:14s} {len(raw):>9,} B")

    snap_before = full_snapshot()
    sys_before = system_snapshot()

    vs = VirtualStorage()
    ids = {}
    for path, name in FILES:
        fid = vs.save(path)
        ids[name] = fid
        held = vs.held_size_bytes(fid)
        raw = len(originals[name])
        print(f"  saved {name:14s} held={held:>7,} B  ratio={raw/max(held,1):5.2f}x")

    print(f"\n{len(FILES) * 3} boxes falling simultaneously "
          f"({len(FILES)} files x 3 pieces) - waiting {FALL_TIME_SECONDS}s "
          "to prove they're really moving, not just sitting there...")
    time.sleep(FALL_TIME_SECONDS)

    total_hops = 0
    for name, fid in ids.items():
        held = vs._held[fid]
        hops = {piece: box.hops for piece, box in held.boxes.items()}
        total_hops += sum(hops.values())
        print(f"  {name:14s} hops -> {hops}")
    print(f"  total hops across all 12 boxes in {FALL_TIME_SECONDS}s: {total_hops:,}")

    snap_while_holding = full_snapshot()

    print("\nretrieving and verifying all 4, interleaved:")
    all_ok = True
    for name, fid in ids.items():
        retrieved = vs.retrieve(fid, "full")
        original = originals[name]
        byte_match = hashlib.sha256(retrieved).digest() == hashlib.sha256(original).digest()
        print(f"  {name}: byte-for-byte identical={byte_match}")
        content_ok = verify_content(name, original, retrieved)
        all_ok = all_ok and byte_match and content_ok

    snap_after_retrieve = full_snapshot()

    vs.collapse_all()
    release_freed_memory()
    snap_after_collapse = full_snapshot()
    sys_after = system_snapshot()

    print(f"\nALL 4 real files correct (bytes AND actual content) "
          f"while all were falling together: {all_ok}")

    print(f"\nRssAnon (this process's own heap data) through each stage:")
    print(f"  before holding anything:  {snap_before['RssAnon']:>8,} KB")
    print(f"  while all 4 falling:      {snap_while_holding['RssAnon']:>8,} KB "
          f"(+{snap_while_holding['RssAnon'] - snap_before['RssAnon']:,} KB)")
    print(f"  after retrieving all:     {snap_after_retrieve['RssAnon']:>8,} KB")
    print(f"  after collapse_all():     {snap_after_collapse['RssAnon']:>8,} KB")

    print(f"\nEvery other RAM figure Linux tracks for THIS PROCESS, "
          f"before holding anything vs after collapse (should end close "
          f"to where it started):")
    all_keys = sorted(set(snap_before) | set(snap_after_collapse))
    for key in all_keys:
        b = snap_before.get(key, "-")
        a = snap_after_collapse.get(key, "-")
        print(f"  {key:20s} before={b!s:>10s} KB   after={a!s:>10s} KB")

    print(f"\nSystem-wide (whole machine, /proc/meminfo), "
          f"before holding anything vs after collapse:")
    all_sys_keys = sorted(set(sys_before) | set(sys_after))
    for key in all_sys_keys:
        b = sys_before.get(key, "-")
        a = sys_after.get(key, "-")
        print(f"  {key:15s} before={b!s:>10s} KB   after={a!s:>10s} KB")


if __name__ == "__main__":
    main()
