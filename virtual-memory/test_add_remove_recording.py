#!/usr/bin/env python3
"""Adds and removes files WHILE everything is still falling (not
paused for the test, not one-at-a-time-then-wait) - and records RAM
continuously through the whole thing, not just at a few checkpoints.

A background thread samples every RAM figure that matters every 100ms
while the main thread runs a live sequence of add/remove/add/remove on
the packed boxes. At the end, prints the full recording as a timeline,
with the add/remove events marked against it, plus a plain-language
explanation of which numbers actually matter and why.
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

UPLOAD_DIR = "/root/.claude/uploads/9c6fcc6d-8c14-5ccc-a3a0-fd3c78528f86"
FILES = {
    "portal": f"{UPLOAD_DIR}/7f188850-FUTO_SM_Core_Portal1.PDF",
    "book": f"{UPLOAD_DIR}/fa1789a3-thechroniclesofnmachukwu.pdf",
    "handbook": f"{UPLOAD_DIR}/811bf743-VIRTUAL_STORAGE_HANDBOOK.md",
    "source": f"{UPLOAD_DIR}/50e10a20-virtual_storage_source.zip",
}
SAMPLE_INTERVAL = 0.1
FIELDS = ("RssAnon", "VmRSS", "RssFile", "RssShmem", "smaps_Pss")


class Recorder:
    """Samples RAM in the background, on its own thread, independent of
    whatever the main thread is doing - so the recording covers moments
    the main thread is busy (mid-add, mid-remove), not just idle gaps."""

    def __init__(self):
        self._samples = []
        self._events = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t0 = time.time()
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            snap = full_snapshot()
            with self._lock:
                self._samples.append((time.time() - self._t0,
                                       {k: snap.get(k, 0) for k in FIELDS}))
            self._stop.wait(SAMPLE_INTERVAL)

    def event(self, label: str):
        with self._lock:
            self._events.append((time.time() - self._t0, label))
        print(f"  [{time.time() - self._t0:5.2f}s] {label}")

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1)

    def timeline(self):
        with self._lock:
            return list(self._samples), list(self._events)


def pdf_pages(raw: bytes) -> list[str]:
    from io import BytesIO
    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(BytesIO(raw)).pages]


def content_ok(name: str, original: bytes, retrieved: bytes) -> bool:
    if name.lower().endswith(".pdf"):
        return pdf_pages(original) == pdf_pages(retrieved)
    if name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(retrieved)) as z:
                return z.testzip() is None
        except zipfile.BadZipFile:
            return False
    return original.decode("utf-8") == retrieved.decode("utf-8")


def release_freed_memory():
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


def main() -> None:
    originals = {}
    basenames = {}
    for key, path in FILES.items():
        with open(path, "rb") as f:
            originals[key] = f.read()
        basenames[key] = os.path.basename(path)

    rec = Recorder()
    rec.start()

    vs = PackedVirtualStorage()
    ids = {}

    print("Live sequence - add/remove happening WHILE the boxes keep falling:\n")

    ids["portal"] = vs.save(FILES["portal"])
    rec.event("added portal.pdf")
    time.sleep(0.4)

    ids["book"] = vs.save(FILES["book"])
    rec.event("added book.pdf  (portal.pdf still falling alongside it)")
    time.sleep(0.4)

    ids["handbook"] = vs.save(FILES["handbook"])
    rec.event("added handbook.md  (3 files now sharing the same boxes)")
    time.sleep(0.4)

    vs.forget(ids["portal"])
    del ids["portal"]
    rec.event("REMOVED portal.pdf mid-fall  (book.pdf + handbook.md keep falling)")
    time.sleep(0.4)

    ids["source"] = vs.save(FILES["source"])
    rec.event("added source.zip")
    time.sleep(0.4)

    vs.forget(ids["handbook"])
    del ids["handbook"]
    rec.event("REMOVED handbook.md mid-fall  (book.pdf + source.zip keep falling)")
    time.sleep(0.4)

    ids["portal2"] = vs.save(FILES["portal"])
    rec.event("re-added portal.pdf (proves the system still works cleanly after a remove)")
    time.sleep(0.4)

    rec.event("retrieving everything still held, verifying content")
    all_ok = True
    remaining = {"book": ids["book"], "source": ids["source"], "portal2": ids["portal2"]}
    for key, fid in remaining.items():
        retrieved = vs.retrieve(fid, "full")
        original = originals[key if key != "portal2" else "portal"]
        byte_match = hashlib.sha256(retrieved).digest() == hashlib.sha256(original).digest()
        ok = byte_match and content_ok(basenames[key if key != "portal2" else "portal"], original, retrieved)
        all_ok = all_ok and ok
        print(f"    {key}: byte-perfect={byte_match} content-correct={ok}")

    rec.event(f"all remaining files correct: {all_ok}")

    vs.collapse_all()
    release_freed_memory()
    rec.event("collapse_all() + malloc_trim")
    time.sleep(0.3)

    rec.stop()
    samples, events = rec.timeline()

    print("\n=== Full RAM recording (sampled every 100ms) ===")
    print(f"{'t(s)':>6s} {'RssAnon':>9s} {'VmRSS':>9s} {'RssFile':>9s} "
          f"{'RssShmem':>9s} {'smaps_Pss':>10s}   event")
    event_idx = 0
    for t, vals in samples:
        label = ""
        while event_idx < len(events) and events[event_idx][0] <= t:
            label = events[event_idx][1]
            event_idx += 1
        print(f"{t:6.2f} {vals['RssAnon']:9,d} {vals['VmRSS']:9,d} "
              f"{vals['RssFile']:9,d} {vals['RssShmem']:9,d} "
              f"{vals['smaps_Pss']:10,d}   {label}")

    print("\n=== What these numbers actually mean, and which ones matter ===")
    print("""
  RssAnon   - THE MAIN ONE. This is your own data: the compressed
              pieces sitting in the falling boxes. Goes up when you
              add a file, comes down when you remove one. This is
              "how much is the storage system actually costing you."

  VmRSS     - total resident memory: RssAnon + RssFile + RssShmem
              added together. The full physical-RAM footprint of the
              whole process, not just the storage system's own data.
              Always a bit bigger than RssAnon because Python itself
              and its libraries take some too.

  RssFile   - memory-mapped CODE (Python itself, pypdf, lzma, etc).
              This should stay basically FLAT the whole time - it's
              not your data, it's the program. If this climbs while
              you're just adding/removing files, something's wrong.

  RssShmem  - shared memory (tmpfs, /dev/shm). Should be 0 here since
              nothing in this test touches shared memory - the boxes
              live in ordinary process memory, not shared pages.

  smaps_Pss - the "fair share" number: if any of this memory were
              shared with another process, this divides it fairly
              between them. With just one process holding everything,
              this tracks RssAnon closely - useful mainly when you
              have MULTIPLE processes (like the sharing.py hand-off).
""")


if __name__ == "__main__":
    main()
