# Virtual Storage — Complete Reference

**A full record of the concept, every experiment, real measured results, what
it is, what it does, where it works, and where the honest limits are.**

Written so nothing is lost and so the findings are never dismissed as
"impossible" — most of this was built and measured. Everything here was run and
measured externally from `/proc` on a 1-core machine with ~4 GB RAM.

---

## 1. The concept

Ordinary storage puts data in a fixed place (a disk sector, a file) where it
rests until read. **Virtual Storage treats storage as a living process instead
of a fixed location.** The core properties aimed for:

- Data lives in **RAM only** — never written to disk/ROM.
- Data is **held broken-down (compressed)** at rest, and **forms full-size only
  at the moment it is retrieved**, then collapses again.
- Data can be kept **in motion** (bouncing between memory regions / boxes) so it
  has no single fixed address.
- Everything **dies with the process** — power off or kill, and it's gone, with
  no recoverable trace.

The mental model that fits best: **a Pentagon-style archive that you read rather
than extract, but for live memory** — the system holds compact forms and
produces the real thing only on demand.

---

## 2. How it actually works (the mechanism)

**Save:** the user hands the system a file. The system compresses it into its
"broken-down" form and keeps only that. The full file is discarded.

**At rest:** the system holds only the small broken-down forms, in anonymous
RAM (memory with no filename, no disk backing). Nothing full exists anywhere.

**Retrieve:** the user names a file. It decompresses — forms full-size — at that
instant, is handed back (verified byte-for-byte exact), then the full copy is
released and collapses back to nothing.

**Motion / boxes (optional layer):** the broken-down forms can be continuously
moved between small memory "boxes." A box too small to hold the whole file just
passes pieces through, breaking and reforming continuously — so the data is
always flowing, never resting at one address. Three rotating boxes cost ~3 MB.

**Movement primitives proven to work:**
- `mmap(MAP_ANONYMOUS)` — RAM with no file, no name, reclaimed on process death.
- `pipe()` + a concurrent writer thread — moves data without deadlocking on the
  pipe's small buffer.
- `splice()` — moves bytes between file descriptors *through kernel buffers*
  without copying them into the process's own memory (near-zero process
  footprint).

---

## 3. Every experiment and its real result

### Motion storage (anonymous RAM, no disk)
`anonymous_mmap_storage.c`, `bounce_mover.c`
- 1 GB bounced between two RAM regions continuously for 3 real minutes: **303
  hops, flat 1.6 MB process footprint, checksum perfect, zero disk writes,
  data gone on kill.**
- **Result: works.** Motion-as-storage, RAM-only, dies with process — all real.

### `splice()` — big data, tiny process footprint
`splice_mover.c`, `full_picture.py`
- Single 1 GB move: **process peak ~1.7 MB.**
- 1 GB circulating continuously: **process RSS ~2.7 MB, but system RAM +1045 MB.**
- **Result: works, with a caveat.** The data's footprint is invisible *to
  inspection of the process* (~2.7 MB), but the full 1 GB physically lives in
  kernel-owned RAM. Stealth from process inspection is real; the bytes are not
  erased from the machine.

### Low-RAM ceiling test
`gb_under_50mb.py`
- 1 GB processed under a hard OS-enforced 50 MB ceiling: **peak 15 MB, zero disk,
  byte-perfect.** Chunking makes peak memory independent of file size.
- **Result: works.**

### XOR splitting
`xor_test.py`
- 200 MB split into 3 shares: **600 MB** (each share is full-size).
- **Result: XOR does NOT shrink data — it multiplies it.** Useful only for
  confidentiality (no single share is meaningful), never for saving space.

### On-demand generation (formula data)
`generate_on_demand.py`
- 1 GB generated from a seed+position formula: **process 2.8 MB, system +4 MB,
  consistent 1 GB.**
- **Result: works ONLY for formula-derived data.** What's stored is the tiny
  rule, not arbitrary bytes. Real files don't reduce to a seed.

### Rotating boxes — cheap motion
`breaking_boxes.py`
- 200 MB flowing through 3 × 1 MB boxes: **process 4.8 MB, box RAM 3 MB.**
- **Result: the boxes as movers are genuinely tiny** — but data flowing through
  and *not kept* is why it's cheap.

### Retrievable through boxes — the wall
`retrievable_test.py`
- 200 MB kept *retrievable* while circulating through 3 boxes: **process 205.6 MB.**
- **Result: "retrievable" and "present in RAM" are the same thing.** To get a
  byte back, that byte must exist. Boxes move data cheaply; keeping it
  retrievable costs the data's real size — unless it's compressible (below).

### Break-down (compression) — real files
`breakdown_test.py`, `diverse_test.py`
- Repetitive text: 256,000 → **372 bytes** (688×), rebuilt exact.
- Code: 16,600 → **200 bytes** (83×). JSON: 157 KB → 21.7 KB (7.2×). Prose:
  113 KB → 17.4 KB (6.5×). **Random binary: 200,000 → 200,072 (no shrink).**
- **Result: real files with structure break down small and rebuild exactly.
  Random / already-compressed data does not shrink at all.**

### Full end-to-end system
`virtual_storage_system.py`, `measured_system.py`, `onegig_box_test.py`
- Save 4 real files (543 KB) → **held at 38.8 KB**, retrieve any one byte-perfect,
  **zero disk writes.**
- **1 GB structured file → held broken-down at 0.19 MB, RssAnon ~3.8 MB, zero
  disk. Retrieval briefly forms the full 1 GB (peak ~709 MB), then collapses
  back to ~27 MB.**
- **Result: the complete system works.** Tiny at rest, full-size only for the
  instant of retrieval, ROM never used.

---

## 4. What it is, in one paragraph

Virtual Storage is an **in-memory, disk-free store that holds data in a
broken-down (compressed) form and produces the full file only at the moment of
retrieval, then collapses it again** — optionally keeping the broken-down forms
in continuous motion so they have no fixed address, and losing everything when
the process ends. For structured data it holds enormous logical volumes in a few
megabytes; it never touches ROM/disk; and its footprint can be made nearly
invisible to inspection of the process itself.

---

## 5. What works vs. the honest limits

| Goal | Result | Status |
|---|---|---|
| RAM-only, zero disk/ROM | write_bytes = 0 across all tests | ✅ real |
| Dies with the process, no trace | confirmed after kill | ✅ real |
| Data in continuous motion, no fixed address | 303 hops, flat RAM | ✅ real |
| Tiny *process* footprint while big data flows | ~2.7 MB / 1 GB via `splice` | ✅ real |
| Hold structured 1 GB in single-digit MB | 1 GB → 3.8 MB RssAnon | ✅ real |
| Form full file only on retrieval, then collapse | peak on retrieval, drops after | ✅ real |
| Hold **arbitrary/random** data for less than its size | — | ❌ information theory |
| Keep a file **retrievable** for less RAM than its size | — | ❌ retrievable = present |
| Run a live model in tiny RAM | needs all weights live continuously | ❌ (see below) |

**The two hard walls, stated plainly:**
1. **Retrievable = present.** The bytes you can read back are the bytes taking up
   space. You can compress them (if structured) or hide where they live, but you
   cannot make retrievable data cost less than the information it contains.
2. **Compression depends on structure.** Text, code, JSON, logs shrink hugely.
   Random or already-compressed data (video, JPEG, encrypted, raw model weights)
   does not shrink at all.

---

## 6. Where it genuinely helps

- **Ephemeral sensitive data** — keys, session secrets, decrypted content that
  must leave no disk trace and vanish on shutdown/seizure.
- **Stealth from process inspection** — data whose footprint is hidden from
  anyone examining the owning process (`splice` property).
- **Compressed-at-rest storage for structured data** — documents, code, logs,
  JSON, config, chat history: held at a fraction of size, formed only on access.
- **Cold storage / transport of models** (checkpoints, idle variants) — but NOT
  the memory cost of a *running* model, which needs all weights live full-size
  continuously. Raw weights are high-entropy and barely compress; quantization
  (lossy, keeps model usable) is the right tool for *that* problem, not this.

## 7. Where it does NOT replace normal storage

- Not durable: process stops or power lost = data gone. This is ephemeral
  in-motion storage, not a permanent disk/cloud replacement.
- Defeats a snapshot, not continuous live observation of the machine's RAM.
- Already-compressed / random data gets no size benefit.

---

## 8. File index

**Working end-to-end system:**
- `virtual_storage_system.py` — save / hold-broken-down / retrieve, in plain form
- `measured_system.py` — same, with full RAM + disk measurement
- `onegig_box_test.py` — 1 GB held broken-down, measured (the headline result)

**Motion & footprint:**
- `anonymous_mmap_storage.c`, `bounce_mover.c` — motion in anonymous RAM
- `splice_mover.c`, `full_picture.py` — the tiny-process-footprint result
- `gb_under_50mb.py` — 1 GB under a hard 50 MB ceiling

**Boxes:**
- `breaking_boxes.py` — cheap flow through 3 × 1 MB boxes
- `retrievable_test.py` — the "retrievable = present" wall
- `one_box_all_files.c` — one box holding all files, self-destroying

**Break-down (compression):**
- `breakdown_test.py`, `diverse_test.py` — real files, diverse types

**Instructive dead-ends (kept so they're not retried blindly):**
- `xor_test.py` — XOR multiplies, doesn't shrink
- `generate_on_demand.py` — near-zero RAM, but formula data only

**Prior notes:** `VIRTUAL_STORAGE_FINDINGS.md`, `SEPARATE_REGIONS.md`
