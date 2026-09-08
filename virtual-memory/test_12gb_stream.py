#!/usr/bin/env python3
"""12GB real file, through ChunkedSecureBox.from_file() (streaming
ingestion - required at this size, see the from_file() docstring),
tracking EVERY form of RAM at every stage. Deliberately NOT retrieving
/ rebuilding the file, per instruction - that alone would need to hold
the full reconstructed ~12GB plaintext, and this sandbox only has
~15GB total with no swap.

Runs the actual construction in a CHILD process, monitored from
outside with a safety abort - protects the whole session from an OOM
crash if the real compression ratio ends up worse than expected,
rather than trusting the estimate blindly.
"""
import ctypes
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot, system_snapshot

PATH = "/dev/shm/report_12gb.md"
SAFETY_LIMIT_KB = 2_500_000  # abort the child well before the ~3.1GB ceiling

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
