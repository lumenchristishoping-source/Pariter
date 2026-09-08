#!/usr/bin/env python3
"""The test that was owed: a real 1GB file through the ACTUAL system
(SecureVirtualStorage.save()/retrieve()), not an isolated box tested
directly. Includes the watchdog, the split into content/structure/
metadata, everything a real caller would go through.
"""
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot
from vstorage.secure_system import SecureVirtualStorage

PATH = "/dev/shm/big_test/report.md"


def rss():
    return full_snapshot()["RssAnon"]


def main() -> None:
    file_size = os.path.getsize(PATH)
    print(f"file size: {file_size:,} bytes ({file_size/1024/1024/1024:.3f} GB)\n")

    with open(PATH, "rb") as f:
        original_hash = hashlib.sha256(f.read()).hexdigest()

    r_before = rss()
    print(f"RssAnon before touching the system at all: {r_before:,} KB\n")

    print("=== vs.save(path) - the REAL entry point, watchdog on ===")
    t0 = time.time()
    vs = SecureVirtualStorage()  # watch_for_tampering=True (default)
    file_id = vs.save(PATH)
    save_time = time.time() - t0
    r_after_save = rss()
    print(f"save() time: {save_time:.2f}s")
    print(f"RssAnon after save(): {r_after_save:,} KB "
          f"(+{r_after_save - r_before:,} KB, ~{(r_after_save-r_before)/1024/1024:.2f} GB)\n")

    print("=== Steady-state (10s) ===")
    readings = []
    for i in range(10):
        time.sleep(1.0)
        now = rss()
        readings.append(now)
        print(f"  {i+1}s: {now:,} KB (~{now/1024/1024:.2f} GB)")
    print(f"  min={min(readings):,} KB, max={max(readings):,} KB, "
          f"spread={max(readings)-min(readings):,} KB\n")

    print("=== vs.retrieve(file_id, 'full') ===")
    t0 = time.time()
    full = vs.retrieve(file_id, "full")
    retrieve_time = time.time() - t0
    match = hashlib.sha256(full).hexdigest() == original_hash
    r_after_retrieve = rss()
    print(f"retrieve time: {retrieve_time:.2f}s")
    print(f"byte-perfect through the real system: {match}")
    print(f"RssAnon after retrieve: {r_after_retrieve:,} KB "
          f"(~{r_after_retrieve/1024/1024:.2f} GB)\n")

    vs.collapse_all()
    time.sleep(0.3)
    r_final = rss()
    print(f"RssAnon after collapse_all() (still holding our own "
          f"retrieved copy in `full`): {r_final:,} KB")

    del full
    import gc
    import ctypes
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass
    time.sleep(0.2)
    r_after_drop = rss()
    print(f"RssAnon after ALSO dropping our own copy: {r_after_drop:,} KB "
          f"(this is the honest 'everything released' number)")


if __name__ == "__main__":
    main()
