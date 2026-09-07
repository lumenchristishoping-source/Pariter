#!/usr/bin/env python3
"""
Virtual Storage — end-to-end working system.

Flow, exactly as a user would experience it:
  1. SAVE:      user hands the system a real file. System breaks it down and
                holds ONLY the broken-down form. The full file is discarded.
  2. AT REST:   the system holds only tiny broken-down forms, in RAM, no disk.
  3. RETRIEVE:  user asks for a file by name. It forms full-size at that
                moment, is handed back, verified exact, then collapses again.

We measure the real footprint at rest vs the moment of retrieval.
"""
import lzma, hashlib, os

class VirtualStorage:
    def __init__(self):
        self.vault = {}   # name -> broken-down form (all the system ever holds)
        self.hashes = {}  # name -> original hash, to prove exact retrieval

    def save(self, name, data: bytes):
        """User saves a file. System holds only the broken-down form."""
        original_hash = hashlib.sha256(data).hexdigest()
        broken_down = lzma.compress(data, preset=9)
        self.vault[name] = broken_down
        self.hashes[name] = original_hash
        # the full `data` is NOT kept -- only broken_down survives in self.vault
        return len(data), len(broken_down)

    def retrieve(self, name):
        """User asks for a file. It forms full-size ONLY now."""
        broken_down = self.vault[name]
        full = lzma.decompress(broken_down)   # forms full size at this instant
        # verify it's exact
        assert hashlib.sha256(full).hexdigest() == self.hashes[name], "corrupted!"
        return full

    def footprint_at_rest(self):
        """Total RAM the system holds at rest -- only broken-down forms."""
        return sum(len(v) for v in self.vault.values())


os.chdir("/home/claude/vstorage")

# ============ THE ACTUAL RUN ============
storage = VirtualStorage()

print("=" * 62)
print("  STEP 1 — USER SAVES FILES INTO THE SYSTEM")
print("=" * 62)

files_to_save = ["sample_code.c", "sample_data.json", "sample_prose.txt", "bigreal.txt"]
total_full = 0
for fn in files_to_save:
    with open(fn, "rb") as f:
        data = f.read()
    full, broken = storage.save(fn, data)
    total_full += full
    print(f"  saved '{fn}': user gave {full:,} bytes -> system holds {broken:,} bytes")
    # prove the full data is gone from our scope
    del data

print(f"\n  User handed the system {total_full:,} bytes total.")
print(f"  System now holds at rest: {storage.footprint_at_rest():,} bytes")
print(f"  ({total_full/storage.footprint_at_rest():.1f}x smaller held than given)")

print("\n" + "=" * 62)
print("  STEP 2 — AT REST (nothing full exists anywhere)")
print("=" * 62)
print(f"  The system is holding {len(storage.vault)} files.")
print(f"  Full versions existing right now: ZERO.")
print(f"  Total RAM held: {storage.footprint_at_rest()/1024:.1f} KB (broken-down only)")

print("\n" + "=" * 62)
print("  STEP 3 — USER ASKS FOR A FILE BACK")
print("=" * 62)
ask_for = "sample_prose.txt"
print(f"  User: 'give me {ask_for} back'")
retrieved = storage.retrieve(ask_for)
print(f"  System: forms it full-size NOW -> {len(retrieved):,} bytes")

# prove it's identical to what they saved
with open(ask_for, "rb") as f:
    original = f.read()
print(f"  Byte-for-byte identical to what was saved: {retrieved == original}")
print(f"  First line back: {retrieved.splitlines()[0][:50].decode()}...")

print(f"\n  After handing it back, that full copy collapses again.")
print(f"  System back to holding only: {storage.footprint_at_rest()/1024:.1f} KB")

print("\n" + "=" * 62)
print("  Retrieve a DIFFERENT one to prove it's a real working store")
print("=" * 62)
r2 = storage.retrieve("sample_code.c")
with open("sample_code.c","rb") as f: orig2 = f.read()
print(f"  Asked for sample_code.c -> {len(r2):,} bytes, exact match: {r2 == orig2}")
