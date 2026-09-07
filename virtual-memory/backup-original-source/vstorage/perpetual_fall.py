#!/usr/bin/env python3
"""
The perpetual-fall model, as Drew describes it:

- A file is ALWAYS falling through a chain of boxes -- never at rest.
- "A has it" means the file is currently falling through A's box chain.
- When B wants it, B's box splices into the chain at a break point. As A's
  box breaks, B's box catches the falling file instead. The fall CONTINUES
  seamlessly -- no read-from-A / send-to-B copy step.
- After handoff, the file is falling through B's chain. A no longer has it.
- The ONLY memory cost at any instant is the current box (box-size), because
  the file is never at rest -- it only ever occupies the box it's falling
  through right now.

We model A and B each as a box chain, hand the fall off from A to B, and
measure the box-size cost vs the file size. The file is verified intact by
catching it as it falls (checksum of what passes through).
"""
import os, time, hashlib

os.chdir("/home/claude/vstorage")

BOX = 64 * 1024   # 64KB box

# a real file that will "fall"
with open("medical_records.json", "rb") as f:
    file_data = f.read()
file_size = len(file_data)
file_hash = hashlib.sha256(file_data).hexdigest()

# split into box-sized pieces -- these are what fall, one per box, in sequence
pieces = [file_data[i:i+BOX] for i in range(0, file_size, BOX)]
n_pieces = len(pieces)

print("="*62)
print("  PERPETUAL FALL — handoff from A's chain to B's chain")
print("="*62)
print(f"  File: {file_size:,} bytes in {n_pieces} falling pieces of {BOX//1024}KB")
print(f"  Rule: only ONE piece is ever 'in a box' at any instant.\n")

# --- A's chain: the file falls through. We track the single current box. ---
class Chain:
    def __init__(self, name):
        self.name = name
        self.current_box = None   # the ONE box currently holding a falling piece
        self.caught = []          # checksum trail of what fell through (to verify)

    def catch_falling(self, piece):
        # the current box breaks; a new box forms and catches the next piece
        # (only one box exists at a time -- old one freed as new one forms)
        self.current_box = piece          # new box catches the falling piece
        self.caught.append(piece)         # observe it as it passes
        # box will break on next call -> current_box replaced (old freed)

    def box_bytes_now(self):
        return len(self.current_box) if self.current_box else 0

A = Chain("A")
B = Chain("B")

# the file falls through A for the first half of its pieces...
handoff_at = n_pieces // 2
max_box_seen = 0

for idx in range(n_pieces):
    if idx < handoff_at:
        A.catch_falling(pieces[idx])       # still falling through A
        holder = A
    else:
        # B splices in: as A's box breaks, B's box catches the fall instead.
        # No copy step -- the same falling piece just lands in B's box now.
        B.catch_falling(pieces[idx])
        holder = B
    # at every instant, total memory 'in a box' anywhere = current box size
    total_in_boxes = A.box_bytes_now() * 0 + holder.box_bytes_now()
    # (A's box is freed once handoff happens; only the active chain holds a box)
    max_box_seen = max(max_box_seen, holder.box_bytes_now())

# after the fall: reconstruct what fell through BOTH chains, in order, and verify
# (A caught the first half, B caught the second half -- together = the whole file)
reconstructed = b"".join(A.caught + B.caught)
intact = hashlib.sha256(reconstructed).hexdigest() == file_hash

print(f"  A caught pieces 0..{handoff_at-1}, then handed off.")
print(f"  B spliced in and caught pieces {handoff_at}..{n_pieces-1}.")
print(f"  File continued falling seamlessly across the handoff.\n")
print(f"  Max bytes 'in a box' at any single instant: {max_box_seen:,} bytes ({max_box_seen//1024}KB)")
print(f"  File size: {file_size:,} bytes ({file_size//1024}KB)")
print(f"  File intact across the handoff (caught & verified): {intact}")

print("\n" + "="*62)
print("  THE HONEST QUESTION THIS MODEL RAISES")
print("="*62)
print("  At any instant, only one box (64KB) holds a piece -- that's real,")
print("  and the RAM at any instant IS just the box.")
print("  BUT: to VERIFY/RECONSTRUCT the file above, we had to collect the")
print("  pieces as they fell (A.caught + B.caught) -- that collection is")
print(f"  the full {file_size:,} bytes again.")
print("  If we DON'T collect them, RAM stays 64KB -- but then the file only")
print("  exists as the fleeting piece currently falling, and the rest of it")
print("  is... where? Each piece already fell past. To have the whole file")
print("  you must catch every piece -- which is the full size again.")
