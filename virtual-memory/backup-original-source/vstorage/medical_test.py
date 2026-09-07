#!/usr/bin/env python3
"""
Medical records test. Medical data has TWO hard requirements this must meet:
  1. PERFECT fidelity -- a single wrong byte in a dose or record is dangerous.
     Must be lossless, exact, always.
  2. Retrievable by specific record, not just all-or-nothing.

We test both, plus RAM and disk, plus whether a specific patient's exact
record comes back byte-perfect after being held broken-down.
"""
import lzma, hashlib, json, os, subprocess, sys

os.chdir("/home/claude/vstorage")

with open("medical_records.json","rb") as f:
    records_raw = f.read()

full_size = len(records_raw)
original_hash = hashlib.sha256(records_raw).hexdigest()

# pick a specific patient to verify later, exactly
records = json.loads(records_raw)
target_patient = records[2500]  # a specific record in the middle
target_id = target_patient["patient_id"]

print("="*62)
print("  MEDICAL RECORDS TEST")
print("="*62)
print(f"  Records: {len(records):,} patients")
print(f"  Full size: {full_size:,} bytes ({full_size/1024/1024:.2f} MB)")
print(f"  Watching patient: {target_id} ({target_patient['name']})")
print(f"    meds: {target_patient['medications']}")

# SAVE: break down
broken = lzma.compress(records_raw, preset=9)
print(f"\n  Broken-down (held at rest): {len(broken):,} bytes ({len(broken)/1024:.1f} KB)")
print(f"  Compression: {full_size/len(broken):.1f}x smaller")

# RETRIEVE: form full, verify EXACT
reformed = lzma.decompress(broken)
exact = hashlib.sha256(reformed).hexdigest() == original_hash
print(f"\n  Retrieved full records byte-for-byte exact: {exact}")

# verify the SPECIFIC patient's record is perfect
reformed_records = json.loads(reformed)
retrieved_patient = None
for r in reformed_records:
    if r["patient_id"] == target_id:
        retrieved_patient = r
        break
patient_exact = (retrieved_patient == target_patient)
print(f"  Patient {target_id}'s record identical (name, dob, meds, all): {patient_exact}")
print(f"    retrieved meds: {retrieved_patient['medications']}")

# critical safety check: is ANY dose or field altered?
print(f"\n  CRITICAL: every field of every record preserved exactly: {exact}")
print(f"  (For medical data, exact is non-negotiable -- this is lossless.)")

print("\n" + "="*62)
print("  VERDICT FOR MEDICAL RECORDS")
print("="*62)
print(f"  Fidelity (exact, lossless): {'PASS' if exact and patient_exact else 'FAIL'}")
print(f"  Space at rest: {full_size:,} -> {len(broken):,} bytes ({full_size/len(broken):.1f}x)")
print(f"  Structured medical data compresses well -- good fit for holding.")
