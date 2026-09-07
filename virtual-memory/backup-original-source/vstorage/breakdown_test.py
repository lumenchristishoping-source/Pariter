#!/usr/bin/env python3
"""
Test: break a REAL file down into the smallest reversible form, then rebuild
it exactly where asked. Measure how small the "broken down" form actually is
vs the original file.

The honest test: does a real file reduce to something tiny (near "nothing")
that rebuilds it perfectly? We try the strongest general-purpose reversible
reduction that exists: compression. If the file has real structure/repetition,
it shrinks a lot. If it's random, it won't. We measure the truth either way.
"""
import zlib, lzma, hashlib, os

os.chdir("/home/claude/vstorage")

with open("bigreal.txt", "rb") as f:
    original = f.read()
orig_hash = hashlib.sha256(original).hexdigest()
orig_size = len(original)

print(f"Original real file: {orig_size:,} bytes ({orig_size/1024:.1f} KB)")
print(f"Original hash: {orig_hash[:16]}...\n")

# "Break it down" -> smallest reversible form (max compression)
broken_down = lzma.compress(original, preset=9)
broken_size = len(broken_down)

print(f"Broken down (LZMA max): {broken_size:,} bytes ({broken_size/1024:.2f} KB)")
print(f"Reduction: {orig_size/broken_size:.1f}x smaller")
print(f"The 'broken down' form is {100*broken_size/orig_size:.2f}% of the original\n")

# Now "form it where asked" -> rebuild exactly
rebuilt = lzma.decompress(broken_down)
rebuilt_hash = hashlib.sha256(rebuilt).hexdigest()

print(f"Rebuilt file matches original exactly: {rebuilt_hash == orig_hash}")
print(f"Rebuilt size: {len(rebuilt):,} bytes\n")

# The honest question: is the broken-down form "nothing"? No -- but how small?
print("="*60)
print("WHAT THIS ACTUALLY SHOWS:")
print(f"  A real file CAN be broken down to a small reversible form.")
print(f"  This file: {orig_size:,} -> {broken_size:,} bytes, rebuilt PERFECTLY.")
print(f"  The small form is NOT 'nothing' -- it's {broken_size:,} bytes -- but")
print(f"  it's {orig_size/broken_size:.0f}x smaller, and rebuilds the exact file.")
print("="*60)

# Now test the LIMIT: try the same on RANDOM data (no structure)
print("\nSame test on RANDOM data (worst case, no pattern):")
random_data = os.urandom(orig_size)
random_broken = lzma.compress(random_data, preset=9)
print(f"  {orig_size:,} bytes random -> {len(random_broken):,} bytes 'broken down'")
print(f"  Reduction: {orig_size/len(random_broken):.2f}x  (barely any -- random can't reduce)")
print("\n  => Real files with structure break down small. Random data doesn't.")
print("     Your real files (docs, text, code) DO have structure.")
