#!/usr/bin/env python3
"""
The model: the system holds ONLY the broken-down (compressed) form. The full
file forms ONLY at the moment of retrieval, then collapses back.

Test across diverse REAL file types to get honest numbers on what each costs
in its stored (broken-down) form vs its momentary retrieved (full) form.
"""
import lzma, hashlib, os

os.chdir("/home/claude/vstorage")

files = ["sample_code.c", "sample_data.json", "sample_prose.txt", "sample_binary.bin"]

print(f"{'file':<20} {'full size':>10} {'stored(broken)':>15} {'ratio':>7} {'exact?':>7}")
print("-" * 65)

total_full = 0
total_stored = 0

for fn in files:
    with open(fn, "rb") as f:
        data = f.read()
    full = len(data)
    h = hashlib.sha256(data).hexdigest()

    # BROKEN DOWN form -- this is all the system holds
    stored = lzma.compress(data, preset=9)
    stored_size = len(stored)

    # RETRIEVAL -- full file forms only now, verify exact
    reformed = lzma.decompress(stored)
    exact = hashlib.sha256(reformed).hexdigest() == h

    ratio = full / stored_size
    total_full += full
    total_stored += stored_size

    print(f"{fn:<20} {full:>10,} {stored_size:>15,} {ratio:>6.1f}x {'YES' if exact else 'NO':>7}")

print("-" * 65)
print(f"{'TOTAL':<20} {total_full:>10,} {total_stored:>15,} {total_full/total_stored:>6.1f}x")
print()
print("What the system actually holds at rest (broken-down forms):")
print(f"  {total_stored:,} bytes ({total_stored/1024:.1f} KB)")
print(f"What it would be at full size (all files formed at once):")
print(f"  {total_full:,} bytes ({total_full/1024:.1f} KB)")
print()
print("Key point: full size only exists for ONE file, for the instant it's")
print("retrieved. At rest, only the broken-down forms exist. The binary file")
print("(already-random) barely shrinks -- that's the honest limit for data")
print("with no structure. Text/code/JSON shrink a lot.")
