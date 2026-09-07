#!/usr/bin/env python3
"""Tests the user's proposed alternative: instead of every file getting
its own 3 falling boxes (3 background threads per file), pack all files
into just 3 SHARED falling boxes total, kept apart by an index inside
each box, not by separate boxes/threads.

Runs the exact same 4 real files as test_real_multiple.py, same content
checks (not just hashes), and directly compares thread count + RAM
against the old per-file design so the trade-off is honest, not assumed.
"""
import ctypes
import gc
import hashlib
import io
import os
import sys
import threading
import time
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot
from vstorage.packed_system import PackedVirtualStorage
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
    if name.endswith(".pdf"):
        ok = pdf_pages(original) == pdf_pages(retrieved)
        print(f"    PDF re-opened, text identical page-by-page: {ok}")
        return ok
    if name.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(retrieved)) as z:
                bad = z.testzip()
            ok = bad is None
            print(f"    zip re-opened, testzip() corruption: {bad}, valid: {ok}")
            return ok
        except zipfile.BadZipFile:
            print("    zip FAILED to open")
            return False
    ok = original.decode("utf-8") == retrieved.decode("utf-8")
    print(f"    text re-decoded, string-identical: {ok}")
    return ok


def release_freed_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


def run_packed(originals: dict) -> dict:
    print("\n=== PACKED design: 4 files sharing just 3 boxes total ===")
    threads_before = threading.active_count()
    snap_before = full_snapshot()

    vs = PackedVirtualStorage()
    ids = {}
    for path, name in FILES:
        ids[name] = vs.save(path)

    threads_while_holding = threading.active_count()
    print(f"background threads: before={threads_before} "
          f"while holding all 4 files={threads_while_holding} "
          f"(+{threads_while_holding - threads_before})")

    print(f"falling for {FALL_TIME_SECONDS}s...")
    time.sleep(FALL_TIME_SECONDS)
    hops = {
        "content": vs._content_box.hops,
        "structure": vs._structure_box.hops,
        "metadata": vs._metadata_box.hops,
    }
    print(f"hops on the 3 shared boxes: {hops} "
          f"(total {sum(hops.values()):,})")

    snap_while_holding = full_snapshot()

    all_ok = True
    for name, fid in ids.items():
        retrieved = vs.retrieve(fid, "full")
        original = originals[name]
        byte_match = hashlib.sha256(retrieved).digest() == hashlib.sha256(original).digest()
        print(f"  {name}: byte-for-byte identical={byte_match}")
        content_ok = verify_content(name, original, retrieved)
        all_ok = all_ok and byte_match and content_ok

    vs.collapse_all()
    release_freed_memory()
    snap_after_collapse = full_snapshot()
    threads_after = threading.active_count()

    return {
        "all_ok": all_ok,
        "threads_while_holding": threads_while_holding,
        "threads_after": threads_after,
        "rss_before": snap_before["RssAnon"],
        "rss_while_holding": snap_while_holding["RssAnon"],
        "rss_after_collapse": snap_after_collapse["RssAnon"],
    }


def run_per_file(originals: dict) -> dict:
    print("\n=== OLD design: 4 files, each with its own 3 boxes (12 total) ===")
    threads_before = threading.active_count()
    snap_before = full_snapshot()

    vs = VirtualStorage()
    ids = {}
    for path, name in FILES:
        ids[name] = vs.save(path)

    threads_while_holding = threading.active_count()
    print(f"background threads: before={threads_before} "
          f"while holding all 4 files={threads_while_holding} "
          f"(+{threads_while_holding - threads_before})")

    print(f"falling for {FALL_TIME_SECONDS}s...")
    time.sleep(FALL_TIME_SECONDS)

    snap_while_holding = full_snapshot()

    all_ok = True
    for name, fid in ids.items():
        retrieved = vs.retrieve(fid, "full")
        original = originals[name]
        byte_match = hashlib.sha256(retrieved).digest() == hashlib.sha256(original).digest()
        all_ok = all_ok and byte_match

    vs.collapse_all()
    release_freed_memory()
    snap_after_collapse = full_snapshot()
    threads_after = threading.active_count()

    print(f"  all 4 byte-perfect: {all_ok}")

    return {
        "all_ok": all_ok,
        "threads_while_holding": threads_while_holding,
        "threads_after": threads_after,
        "rss_before": snap_before["RssAnon"],
        "rss_while_holding": snap_while_holding["RssAnon"],
        "rss_after_collapse": snap_after_collapse["RssAnon"],
    }


def main() -> None:
    originals = {}
    for path, name in FILES:
        with open(path, "rb") as f:
            originals[name] = f.read()

    packed = run_packed(originals)
    time.sleep(0.5)
    release_freed_memory()
    old = run_per_file(originals)

    print("\n=== Side by side ===")
    print(f"{'':28s} {'packed (shared)':>18s} {'old (per-file)':>18s}")
    print(f"{'all files correct':28s} {str(packed['all_ok']):>18s} {str(old['all_ok']):>18s}")
    print(f"{'threads while holding 4':28s} "
          f"{packed['threads_while_holding']:>18d} {old['threads_while_holding']:>18d}")
    print(f"{'RssAnon while holding (KB)':28s} "
          f"{packed['rss_while_holding']:>18,d} {old['rss_while_holding']:>18,d}")
    print(f"{'RssAnon after collapse (KB)':28s} "
          f"{packed['rss_after_collapse']:>18,d} {old['rss_after_collapse']:>18,d}")


if __name__ == "__main__":
    main()
