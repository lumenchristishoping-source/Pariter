#!/usr/bin/env python3
"""Real test: a real ~1GB markdown file, through ChunkedSecureBox (the
module actually built for 'huge things, little RAM'), tracking RAM at
every stage - construction, steady-state falling, and retrieval - not
just reporting one flattering number.
"""
import ctypes
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.chunked_secure_box import ChunkedSecureBox
from vstorage.measure_full import full_snapshot

PATH = "/dev/shm/big_test/report.md"


def rss():
    return full_snapshot()["RssAnon"]


def main() -> None:
    file_size = os.path.getsize(PATH)
    print(f"file size: {file_size:,} bytes ({file_size/1024/1024/1024:.3f} GB)\n")

    print("=== Reading the file into memory (this alone costs ~file size) ===")
    r0 = rss()
    with open(PATH, "rb") as f:
        data = f.read()
    r1 = rss()
    print(f"RssAnon before read: {r0:,} KB")
    print(f"RssAnon after read:  {r1:,} KB  (+{r1-r0:,} KB, ~{(r1-r0)/1024/1024:.2f} GB "
          f"- just holding the raw file, before the box exists at all)\n")

    original_hash = hashlib.sha256(data).hexdigest()

    print("=== Constructing ChunkedSecureBox (encrypts every 256KB chunk once) ===")
    t0 = time.time()
    r_before_construct = rss()
    box = ChunkedSecureBox(data, chunk_size=256 * 1024)
    construct_time = time.time() - t0
    r_after_construct = rss()
    print(f"construction time: {construct_time:.2f}s for {box.chunk_count:,} chunks")
    print(f"RssAnon before construct: {r_before_construct:,} KB")
    print(f"RssAnon after construct:  {r_after_construct:,} KB  "
          f"(+{r_after_construct-r_before_construct:,} KB, "
          f"~{(r_after_construct-r_before_construct)/1024/1024:.2f} GB - this is the "
          f"ONE-TIME peak: original file + all chunk ciphertexts existing at once)\n")

    # Drop our own reference to the raw file - a real caller wouldn't
    # keep holding a second full copy once the box exists.
    del data
    import gc
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass
    r_after_drop = rss()
    print(f"RssAnon after dropping our copy of the raw file: {r_after_drop:,} KB "
          f"(~{r_after_drop/1024/1024:.2f} GB)\n")

    print("=== Steady-state: is it bounded, or does it keep climbing? ===")
    readings = []
    for i in range(10):
        time.sleep(1.0)
        now = rss()
        readings.append(now)
        print(f"  {i+1}s: {now:,} KB (~{now/1024/1024:.2f} GB), hops={box.hops}")
    print(f"\n  min={min(readings):,} KB, max={max(readings):,} KB, "
          f"spread={max(readings)-min(readings):,} KB "
          f"(bounded if this stays small relative to the file size)\n")

    print("=== Retrieval: full round trip ===")
    t0 = time.time()
    out = box.snapshot()
    retrieve_time = time.time() - t0
    r_after_retrieve = rss()
    match = hashlib.sha256(bytes(out)).hexdigest() == original_hash
    print(f"retrieve time: {retrieve_time:.2f}s")
    print(f"byte-perfect: {match}")
    print(f"RssAnon after retrieve: {r_after_retrieve:,} KB "
          f"(~{r_after_retrieve/1024/1024:.2f} GB - now holding the box's ciphertext "
          f"AND the retrieved plaintext copy at once, honestly)\n")

    box.collapse()
    del out
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass
    r_final = rss()
    print(f"RssAnon after collapse(): {r_final:,} KB")


if __name__ == "__main__":
    main()
