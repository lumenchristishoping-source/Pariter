#!/usr/bin/env python3
"""Runs the system with several real, different files held AT THE SAME
TIME (not one after another) - JSON, Python, prose, PDF, DOCX, and a map
- all falling simultaneously, then retrieves and verifies every one is
still byte-perfect. Tracks full RAM (not just RssAnon) before, during,
and after.
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot, system_snapshot
from vstorage.system import VirtualStorage


def make_files(tmpdir: str) -> list[str]:
    paths = []

    p = os.path.join(tmpdir, "log.json")
    with open(p, "w") as f:
        f.write('{"records":[')
        f.write(",".join('{"level":"info","seq":%d}' % i for i in range(3000)))
        f.write("]}")
    paths.append(p)

    p = os.path.join(tmpdir, "sample.py")
    with open(p, "w") as f:
        f.write("def add(a, b):\n    return a + b\n\n" * 150)
    paths.append(p)

    p = os.path.join(tmpdir, "prose.txt")
    with open(p, "w") as f:
        f.write("The quick brown fox jumps over the lazy dog. " * 400)
    paths.append(p)

    p = os.path.join(tmpdir, "map.geojson")
    features = [{"type": "Feature", "properties": {"id": i},
                 "geometry": {"type": "Point", "coordinates": [3.37 + i * 0.001, 6.52 + i * 0.001]}}
                for i in range(1500)]
    with open(p, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    paths.append(p)

    from reportlab.pdfgen import canvas
    p = os.path.join(tmpdir, "report.pdf")
    c = canvas.Canvas(p)
    for i in range(30):
        c.drawString(80, 750 - i * 20, f"PDF line {i}: quick brown fox jumps over lazy dog.")
    c.save()
    paths.append(p)

    import docx
    p = os.path.join(tmpdir, "memo.docx")
    d = docx.Document()
    d.add_heading("Test memo", level=1)
    for i in range(30):
        d.add_paragraph(f"Paragraph {i}: quick brown fox jumps over lazy dog.")
    d.save(p)
    paths.append(p)

    return paths


def main() -> None:
    tmpdir = "/dev/shm/vstorage_multi_test"
    os.makedirs(tmpdir, exist_ok=True)
    paths = make_files(tmpdir)
    originals = {p: open(p, "rb").read() for p in paths}
    time.sleep(0.2)

    print(f"{len(paths)} files, held at the SAME time:\n")

    rss_before = full_snapshot()["RssAnon"]
    sys_before = system_snapshot()

    vs = VirtualStorage()
    ids = {}
    total_raw = 0
    for p in paths:
        fid = vs.save(p)
        ids[p] = fid
        raw = os.path.getsize(p)
        held = vs.held_size_bytes(fid)
        total_raw += raw
        print(f"  saved  {os.path.basename(p):14s} raw={raw:>7d}B  "
              f"held={held:>6d}B  ratio={raw / max(held, 1):6.1f}x  "
              f"(3 boxes falling for this file: content, structure, metadata)")

    total_held = sum(vs.held_size_bytes(fid) for fid in ids.values())
    print(f"\n  {len(paths) * 3} boxes falling simultaneously "
          f"({len(paths)} files x 3 pieces each)")
    print(f"  total: {total_raw}B raw -> {total_held}B held "
          f"({total_raw / max(total_held, 1):.1f}x overall)\n")

    rss_while_holding = full_snapshot()["RssAnon"]

    # Retrieve ALL of them, interleaved, to make sure nothing crosses
    # between files while everything is falling at once.
    ok = True
    for p, fid in ids.items():
        full = vs.retrieve(fid, "full")
        match = hashlib.sha256(full).digest() == hashlib.sha256(originals[p]).digest()
        ok = ok and match
        print(f"  retrieve {os.path.basename(p):14s} byte-perfect={match}")

    rss_after_retrieve = full_snapshot()["RssAnon"]

    vs.collapse_all()
    import ctypes
    import gc
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass
    rss_after_collapse = full_snapshot()["RssAnon"]
    sys_after = system_snapshot()

    print(f"\nAll {len(paths)} files byte-perfect while all were falling at once: {ok}")
    print(f"\nRssAnon (this process's own data):")
    print(f"  before holding anything:     {rss_before:>7d} KB")
    print(f"  while all {len(paths)} files falling: {rss_while_holding:>7d} KB "
          f"(+{rss_while_holding - rss_before} KB)")
    print(f"  after retrieving all of them: {rss_after_retrieve:>7d} KB")
    print(f"  after collapse_all():         {rss_after_collapse:>7d} KB")
    print(f"\nSystem-wide MemAvailable: {sys_before['MemAvailable']} KB -> "
          f"{sys_after['MemAvailable']} KB "
          f"(moved {sys_before['MemAvailable'] - sys_after['MemAvailable']} KB)")

    for p in paths:
        os.remove(p)
    os.rmdir(tmpdir)


if __name__ == "__main__":
    main()
