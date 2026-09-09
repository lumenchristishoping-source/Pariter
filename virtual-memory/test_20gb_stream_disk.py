#!/usr/bin/env python3
"""20GB real file, through ChunkedSecureBox.from_file() (streaming
ingestion), tracking every form of RAM at every stage.

Deliberately different from test_12gb_stream.py in one important way:
that test's source file lived in /dev/shm - tmpfs, RAM-backed, not
real disk. This one's source file lives on the real ext4 root
filesystem, so the read side genuinely exercises disk I/O, not RAM
pretending to be disk. Also deliberately NOT retrieving/rebuilding the
file, same reasoning as before - that alone would need to hold the
full ~20GB reconstructed plaintext, and the point of this test is
ingestion + steady-state cost, not retrieval cost (already known from
smaller tests and doesn't change in kind at this size).

Runs construction in a CHILD process, monitored from outside with a
safety abort - this content (GeoJSON with many quasi-random
coordinate floats) has a real, structural reason to expect a WORSE
compression ratio than the 12GB test's prose-like markdown: high-
entropy decimal numbers compress less than natural-language text.
Set generously higher than the 12GB test's 2.5GB ceiling specifically
because of that uncertainty - protects the whole session from an OOM
crash if the real ratio comes in far worse than hoped, rather than
assuming it'll match the earlier, differently-shaped test.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot, system_snapshot

PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/report_20gb.geojson"
SAFETY_LIMIT_KB = 6_000_000  # ~6GB - well under the ~15.9GB free, no swap

CHILD_SCRIPT = f'''
import sys, os, json, time
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from vstorage.chunked_secure_box import ChunkedSecureBox
from vstorage.measure_full import full_snapshot

path = {PATH!r}
print(json.dumps({{"stage": "start", "snapshot": full_snapshot()}}), flush=True)

t0 = time.time()
box = ChunkedSecureBox.from_file(path, chunk_size=256*1024)
build_time = time.time() - t0

print(json.dumps({{
    "stage": "constructed",
    "snapshot": full_snapshot(),
    "build_time": build_time,
    "chunk_count": box.chunk_count,
}}), flush=True)

for i in range(8):
    time.sleep(1.0)
    print(json.dumps({{"stage": f"steady_{{i}}", "snapshot": full_snapshot(),
                        "hops": box.hops}}), flush=True)

box.collapse()
import gc, ctypes
gc.collect()
try:
    ctypes.CDLL("libc.so.6").malloc_trim(0)
except OSError:
    pass
time.sleep(0.2)
print(json.dumps({{"stage": "collapsed", "snapshot": full_snapshot()}}), flush=True)
'''


def main() -> None:
    file_size = os.path.getsize(PATH)
    print(f"file size: {file_size:,} bytes ({file_size/1024/1024/1024:.3f} GB)\n")

    print("=== System-wide RAM before touching anything ===")
    sys_before = system_snapshot()
    for k, v in sys_before.items():
        print(f"  {k:15s} {v:>12,} KB")
    print()

    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    print("=== Child process output (real-time RAM at every stage) ===\n")
    events = []
    aborted = False
    for line in child.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            print("  [non-json line]", line)
            continue
        events.append(evt)
        snap = evt["snapshot"]
        rss_anon = snap.get("RssAnon", 0)
        vm_rss = snap.get("VmRSS", 0)
        rss_file = snap.get("RssFile", 0)
        rss_shmem = snap.get("RssShmem", 0)
        vm_swap = snap.get("VmSwap", 0)
        extra = ""
        if "build_time" in evt:
            extra = f"  build_time={evt['build_time']:.2f}s chunks={evt['chunk_count']}"
        if "hops" in evt:
            extra = f"  hops={evt['hops']}"
        print(f"  [{evt['stage']:12s}] RssAnon={rss_anon:>10,} KB  "
              f"VmRSS={vm_rss:>10,} KB  RssFile={rss_file:>9,} KB  "
              f"RssShmem={rss_shmem:>9,} KB  VmSwap={vm_swap:>7,} KB{extra}")

        if rss_anon > SAFETY_LIMIT_KB:
            print(f"\n!!! SAFETY ABORT: child RssAnon {rss_anon:,} KB exceeded "
                  f"the {SAFETY_LIMIT_KB:,} KB limit - killing it now.")
            child.kill()
            aborted = True
            break

    child.wait(timeout=10)
    print(f"\nchild exit code: {child.returncode} (aborted by safety limit: {aborted})")

    print("\n=== System-wide RAM after ===")
    sys_after = system_snapshot()
    for k, v in sys_after.items():
        before_v = sys_before.get(k, 0)
        print(f"  {k:15s} {v:>12,} KB   (delta from before: {v-before_v:+,} KB)")


if __name__ == "__main__":
    main()
