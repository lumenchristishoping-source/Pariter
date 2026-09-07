#!/usr/bin/env python3
"""Demonstrates the rebuilt system end-to-end and measures it the same way
HANDBOOK.md does: RssAnon from /proc, write_bytes from /proc/self/io.
Not a re-run of the original 22 experiments - a fresh, honest check that
this independent rebuild behaves the way the handbook describes.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure import disk_write_bytes, rss_anon_mb
from vstorage.system import VirtualStorage


def make_sample_files(tmpdir: str) -> list[str]:
    paths = []

    # Structured/repetitive text - expected to compress heavily.
    json_path = os.path.join(tmpdir, "log.json")
    with open(json_path, "w") as f:
        f.write('{"records":[')
        f.write(",".join('{"level":"info","msg":"heartbeat ok","seq":%d}' % i
                          for i in range(4000)))
        f.write("]}")
    paths.append(json_path)

    # Source code.
    py_path = os.path.join(tmpdir, "sample.py")
    with open(py_path, "w") as f:
        f.write("def add(a, b):\n    return a + b\n\n" * 200)
    paths.append(py_path)

    # Prose.
    txt_path = os.path.join(tmpdir, "prose.txt")
    with open(txt_path, "w") as f:
        f.write("The quick brown fox jumps over the lazy dog. " * 500)
    paths.append(txt_path)

    return paths


def main() -> None:
    # Per HANDBOOK.md's own Pitfall 4: generate test data to /dev/shm
    # (RAM-backed) BEFORE starting the measured region, and outside the
    # disk-write count entirely - generation cost isn't the system's cost.
    tmpdir = "/dev/shm/vstorage_demo"
    os.makedirs(tmpdir, exist_ok=True)
    sample_paths = make_sample_files(tmpdir)
    originals = {p: open(p, "rb").read() for p in sample_paths}

    io_before = disk_write_bytes()
    rss_before = rss_anon_mb()

    try:
        vs = VirtualStorage()
        ids = {}
        total_raw = 0
        for p in sample_paths:
            fid = vs.save(p)
            ids[p] = fid
            total_raw += os.path.getsize(p)
            print(f"saved  {os.path.basename(p):14s} "
                  f"raw={os.path.getsize(p):>7d}B  "
                  f"held={vs.held_size_bytes(fid):>6d}B  "
                  f"ratio={os.path.getsize(p) / max(vs.held_size_bytes(fid), 1):6.1f}x")

        total_held = sum(vs.held_size_bytes(fid) for fid in ids.values())
        print(f"\ntotal: {total_raw}B raw -> {total_held}B held "
              f"({total_raw / max(total_held, 1):.1f}x overall)\n")

        # Targeted retrieval + fidelity check for every file.
        ok = True
        for p, fid in ids.items():
            full = vs.retrieve(fid, "full")
            match = hashlib.sha256(full).digest() == hashlib.sha256(originals[p]).digest()
            ok = ok and match
            print(f"retrieve {os.path.basename(p):14s} "
                  f"byte-perfect={match}")

        rss_after = rss_anon_mb()
        io_after = disk_write_bytes()

        print(f"\nRssAnon before: {rss_before:.2f}MB  after: {rss_after:.2f}MB")
        print(f"Disk writes during this run: {io_after - io_before} bytes")
        print(f"All files byte-perfect on retrieval: {ok}")

        vs.collapse_all()
        print("\ncollapse_all() called - nothing held, nothing recoverable.")
    finally:
        for p in sample_paths:
            os.remove(p)
        os.rmdir(tmpdir)


if __name__ == "__main__":
    main()
