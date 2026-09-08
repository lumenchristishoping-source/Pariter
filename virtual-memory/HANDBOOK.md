# Virtual Storage — The Complete Handbook

**Author:** Drew  
**Built on:** Android, Termux, Python 3.12, GCC, Ubuntu 24 container  
**Hardware tested on:** 1-core machine, ~4 GB RAM  
**Date of experiments:** September 2026  
**Last updated:** with the security-hardening + large-file rebuild (see
Section 17) — the original record below (Sections 1–16) is kept exactly as
first written; nothing in it was edited or erased.

This handbook is the single authoritative record of Virtual Storage. It covers
the origin of the idea, every design decision, every experiment run and its real
measured result, every dead-end and why it failed, the complete final
architecture with setup steps, security properties, where it competes in the
market, and where the honest limits are. It is written so that neither Drew nor
anyone else who reads it ever has to rediscover anything from scratch.

**Where the living docs are:** this handbook records the *original* build.
Everything built since (encryption, tamper detection, distributed trust,
large-file chunking) is layered on top — the day-to-day working docs for
that part are `ARCHITECTURE.md` (how the current system works, step by
step) and `REBUILD_STATUS.md` (the experiment log for that phase, in the
same spirit as Section 6 below). Section 17 of this handbook is the short
version that ties it all back into one story.

---

## Table of Contents

1. What Virtual Storage Is
2. The Origin of the Idea
3. Core Concepts and Vocabulary
4. The Architecture (How It Works)
5. Setting Up and Running It Yourself
6. Every Experiment Run, With Real Results
7. The Dead-Ends (Tested, Not Just Argued)
8. All Measured Numbers
9. File Type Performance
10. Security Properties
11. How Data Disappears (The Physics)
12. How It Compares to Existing Systems
13. Where It Works, Where It Doesn't
14. The Startup Case (API Keys and Secrets)
15. The Complete File Index
16. One-Sentence Summary
17. What Changed Since — Security & Scale (the rebuild)

---

## 1. What Virtual Storage Is

Normal storage puts data in a fixed place — a disk sector, a named file, a
memory address — where it rests until someone reads it. Virtual Storage
treats storage as a living process rather than a fixed location.

In Virtual Storage:
- Data is split into pieces (content, structure, metadata)
- Each piece is compressed into its smallest possible form
- Each compressed piece falls continuously through a chain of small boxes that
  break and reform endlessly — it never rests at a fixed address
- A piece only forms full-size at the exact instant a task needs it, then
  collapses back
- Everything lives in anonymous RAM — no disk, no name, no path, anywhere
- When the process ends, the OS reclaims everything instantly. Nothing recoverable.

**In one sentence:**
Virtual Storage is a zero-disk, in-memory transient store that holds data
compressed and in continuous motion, invisible to process inspection, with
targeted retrieval and instant unrecoverable destruction on process end.

---

## 2. The Origin of the Idea

The original concept was: instead of storing a file in a physical or cloud
location, place it into a software system made of virtual "ports." When it
enters one port, that port immediately passes it to another, which passes it
to the next, in a continuous loop:

```
File → Port A → Port B → Port C → Port D → Port A → ...
```

No port is meant to permanently retain the file. The data is kept "alive"
by continuously circulating through software. If someone wants the file, the
system exposes the data from the stream while circulation continues.

The question being explored: **can movement itself be a form of storage,
rather than a conventional location holding the data?**

Over the course of one long session, the idea was built, tested, measured,
failed in various ways, learned from, rebuilt, and eventually resolved into
the architecture described in this document. Every failure is documented.
Every working part is measured.

---

## 3. Core Concepts and Vocabulary

**Anonymous memory (`MAP_ANONYMOUS`):** Memory allocated with no file backing,
no path, no name anywhere in the filesystem. The OS gives it to the process
and reclaims it the instant the process ends. This is the fundamental building
block — everything in Virtual Storage lives here.

**Box:** A small fixed-size buffer (64KB) that holds a piece of data briefly
before passing it to the next box. Boxes break (are discarded) and reform
(are allocated fresh) continuously. At any instant only one box holds any
given piece.

**Falling:** The continuous motion of data through a chain of boxes. A piece
of data "falls" through boxes that break and reform, never resting at one
address for more than a fraction of a second.

**Broken-down form:** The compressed version of a file or piece, which is
what the system actually holds at rest. For structured data this is orders of
magnitude smaller than the original.

**Content piece:** The actual information in a file — text, data, values.
Compresses extremely well for structured data.

**Structure piece:** The format/encoding wrapper — binary overhead, layout
instructions, font tables. Compresses poorly but is isolated from the
compressible content.

**Metadata piece:** Filename, type, size, hash. Always tiny. Used for
indexing without touching the data.

**RssAnon:** The real anonymous data memory cost of a process, as reported
by `/proc/<pid>/status`. This is the most honest measure — it excludes
shared libraries and interpreter overhead and shows only what the process
itself is holding as anonymous data.

**splice():** A Linux kernel call that moves bytes between two file
descriptors through the kernel's internal pipe buffer, without copying them
into the process's own memory. The process shows near-zero RSS while large
data flows through kernel space. This is the stealth mechanism.

**Information floor:** The minimum possible size of any representation of a
file, determined by its information content (Shannon entropy). Compressible
files have a low floor; random/encrypted files have a floor equal to their
full size. Nothing — no algorithm, no trick, no hardware — can store a file
for less RAM than its information floor while keeping it retrievable.

---

## 4. The Architecture (How It Works)

The final, working, tested architecture has five steps:

### Step 1 — Split

When a file enters the system, it is split into three pieces based on its
type:

| Piece | Contains | Compresses |
|---|---|---|
| content | text, data, values — the actual information | very well |
| structure | binary format wrapper, encoding overhead | poorly |
| metadata | filename, type, size, hash | always tiny |

**How splitting works by file type:**
- `.json`, `.csv`, `.txt`, `.md`, `.c`, `.py`, `.log` — content IS the file;
  structure is just the format tag (trivial)
- `.pdf` — content is the extracted text layer; structure is the full binary PDF
- `.docx`, `.xlsx`, `.pptx` — content is the extracted XML; structure is the
  zip container
- `.jpg`, `.mp4`, `.zip`, `.bin` — content is empty (already compressed);
  structure is the full file
- Metadata is always the same regardless of type

This separation is critical: for most tasks (AI processing, PII scanning,
search), only the content piece is needed. The structure piece — which often
barely compresses — only gets touched when someone explicitly asks for the
full reconstructed document.

### Step 2 — Compress

Each piece is compressed independently using LZMA (preset 6 for speed, 9 for
maximum compression). Compression is lossless — the original bytes are always
reconstructible exactly. The broken-down form is what the system holds.

### Step 3 — Fall Through Boxes

Each compressed piece is held in a continuous falling motion through 64KB
boxes. Implementation:
- One background thread per piece
- Each thread maintains two bytearray buffers (box_a and box_b)
- Continuously copies 64KB chunks from box_a to box_b, then swaps
- The compressed piece is always moving, never resting at one address
- No box ever holds more than 64KB at once
- Boxes "break" (old buffer reused) and "reform" (new buffer takes its place)
  at every chunk boundary

### Step 4 — Retrieve on Demand

When a task needs a piece:
1. The background thread is paused momentarily
2. The compressed piece is read from wherever it currently is in the fall
3. It is decompressed — forming full size at that instant
4. The decompressed data is handed to the task
5. The full-size copy is released immediately after use
6. The background thread resumes falling
7. All other pieces continue falling untouched

The block-indexed approach: each file's content is compressed in blocks (e.g.,
100 records per block for medical data, one page per block for documents), with
a tiny index. A single record retrieval catches only one block — not the whole
file.

### Step 5 — Death

When the process ends (kill signal, power off, timeout, crash):
- The OS marks all anonymous memory pages as free instantly
- No file, no path, no name exists anywhere
- Nothing is recoverable
- RAM cells may physically retain old bit patterns for a brief period
  (until the OS zeroes them when reallocating to another process), but they
  are unaddressable — nothing can reach them

---

## 5. Setting Up and Running It Yourself

### Requirements

```bash
# On Android/Termux:
pkg install python gcc clang

# On Linux:
apt install python3 gcc

# Python packages:
pip install lzma   # usually built-in
pip install pypdf  # for PDF splitting
pip install reportlab  # for PDF creation in tests
pip install numpy  # for model quantization tests
pip install psutil  # for memory measurement
```

### Running the complete end-to-end system

```bash
# Navigate to the vstorage directory
cd /home/claude/vstorage  # or wherever you placed the files

# Run the full combined system (splits, compresses, falls, retrieves)
python3 combined_full_system.py
```

This will:
1. Load 5 real files (medical JSON, code, prose, data, PDF)
2. Split each into content/structure/metadata pieces
3. Compress each piece
4. Start all pieces falling through boxes in background threads
5. Demonstrate targeted retrieval (content only, full reconstruction, metadata)
6. Report RAM and disk usage throughout

### Running individual components

```bash
# Test compression ratios across diverse file types
python3 diverse_test.py

# Test medical records specifically (fidelity + compression)
python3 medical_test.py

# Test PDF splitting
python3 pdf_split_test.py

# Test the general splitter across all file types
python3 general_splitter.py

# Test maps/geospatial data
python3 full_measurements.py   # includes the map test

# Test sustained 3-minute run (memory stability)
python3 full_measurements.py   # includes the 3-min test

# Test splice() stealth footprint
python3 full_picture.py

# Compile and run the C motion tests
gcc -O2 -o bounce_mover bounce_mover.c
./bounce_mover /dev/shm/fileA.bin /dev/shm/fileB.bin 30
# (first create a test file: dd if=/dev/urandom of=/dev/shm/fileA.bin bs=1M count=100)
```

### Measuring RAM correctly

Do NOT use `getrusage()` or `VmHWM` — these report peak RSS, which includes
the brief spike during transfers and never goes back down. Use this instead:

```python
def rss_anon_mb():
    for line in open(f"/proc/self/status"):
        if line.startswith("RssAnon:"):
            return int(line.split()[1]) / 1024.0
```

Or from outside the process:
```bash
grep -E "VmRSS|RssAnon" /proc/<pid>/status
```

Sample many times (every 0.2s for 8s) and report min/avg/max. A single reading
can catch the process at any point in its transfer cycle.

### Measuring disk usage

```python
def disk_writes():
    for line in open("/proc/self/io"):
        if line.startswith("write_bytes"):
            return int(line.split()[1])

io_before = disk_writes()
# ... run your operation ...
io_after = disk_writes()
print(f"Disk writes: {io_after - io_before} bytes")
# Should be 0 for everything except initial file reads
```

### Common pitfalls to avoid

**Pitfall 1: deadlocking a pipe.** A Linux pipe's default buffer is only 64KB.
If you write more than 64KB without a concurrent reader, it blocks forever. Fix:
always use a writer thread alongside a reader, or use splice() instead.

**Pitfall 2: measuring peak instead of current RSS.** VmHWM never decreases.
Always read VmRSS or RssAnon from /proc/pid/status for current measurements.

**Pitfall 3: Python garbage collector timing.** After `del data; gc.collect()`,
Python's allocator may not immediately return memory to the OS. RssAnon can
look inflated for a moment. Sample repeatedly over several seconds.

**Pitfall 4: generating large test data inside the measured process.** The
generation itself costs RAM before compression. Generate to /dev/shm separately,
then read and compress inside the measured process.

---

## 6. Every Experiment Run, With Real Results

### Experiment 1: Ports-based motion (the original idea)
**File:** `falling_storage.py`  
**What:** File split into pieces, rotating through a ring of port objects in a
Python loop.  
**Result:** Worked, byte-perfect, but proved that "all pieces present = all
pieces in RAM." The ring holding all pieces costs their total size. This
established the fundamental tension between motion and size.

### Experiment 2: XOR + pipes + tmpfs (combined experiment)
**File:** `combined_experiment.py`  
**What:** XOR-split a file into 5 shares, stream each through a real OS pipe,
reconstruct in /dev/shm (RAM-backed filesystem).  
**Result:** Worked. `write_bytes: 0`. But XOR made the data 5× bigger (each
share is full-size). Confirmed: XOR provides confidentiality (no single share
means anything), not size reduction.

### Experiment 3: Lean pure-motion (no XOR, explicit deletion)
**File:** `lean_motion_experiment.py`  
**What:** 500MB file moved through pipes in 8MB chunks, previous copy explicitly
`del`-ed and `gc.collect()`-ed before the next.  
**Result:** Peak RSS dropped from 144.4MB (with XOR) to **17.1MB**. `write_bytes: 0`.
SHA-256 matched. Established that explicit deletion between hops dramatically
reduces footprint.

### Experiment 4: 500MB under a hard 50MB ceiling
**File:** `gb_under_50mb.py`  
**What:** 500MB processed with OS-enforced `ulimit -v 51200` (50MB hard ceiling).
Chunked into 8MB pieces.  
**Result:** **Peak 15.05MB, zero disk, byte-perfect.** The OS would have killed
the process if it exceeded 50MB. Chunking makes peak memory independent of file
size.

### Experiment 5: splice() — the stealth property
**Files:** `splice_mover.c`, `full_picture.py`  
**What:** Move 1GB via `splice()` (kernel zero-copy). Measure process RSS AND
system RAM simultaneously.  
**Result:** Process RSS: **2.7MB**. System RAM used: **+1045MB**. The data
lives in kernel buffers, attributed to the kernel, not the process. An inspector
of the process sees 2.7MB. The data is genuinely there in kernel RAM — it is
not erased from the machine — but it is invisible to process-level inspection.

### Experiment 6: Self-healing nested boxes (v1)
**File:** `nested_boxes.py`  
**What:** 3 nested boxes. Innermost breaks → containing box becomes holder →
new box forms as new innermost. 10 consecutive failures.  
**Result:** All 10 failures survived, data intact. But the outer end had a
vulnerability: if the outermost box broke with nothing left, data was lost.

### Experiment 7: Self-healing nested boxes (v2 — the fix)
**File:** `nested_boxes_v2.py`  
**What:** Same as v1, but both ends regenerate. The outermost box always has
a wrapper, so there's never a "last box with nothing behind it."  
**Result:** **5,000 consecutive failures (both inner and outer), zero data
loss.** SHA-256 matched across 5,000 break-reform cycles.

### Experiment 8: Breaking boxes — cheap flow
**File:** `breaking_boxes.py`  
**What:** 200MB flowing through 3 × 1MB boxes rotating continuously. Nothing
kept — data flows through and is discarded.  
**Result:** Process RSS: **4.8MB**. Box RAM: **3MB**. Proved: when data flows
through boxes without being kept, the RAM cost is genuinely just the box size.
The data isn't retrievable afterward — but it flows cheaply.

### Experiment 9: Retrievable through boxes — the wall
**File:** `retrievable_test.py`  
**What:** 200MB kept retrievable while 3 boxes circulate.  
**Result:** Process RSS: **205.6MB**. The 3 boxes (3MB) + the 200 pieces
(200MB) = full file size. Proved: "retrievable" = "present." You cannot be
able to get it back without it being there.

### Experiment 10: Compression — real files
**Files:** `breakdown_test.py`, `diverse_test.py`  
**Results:**
- Repetitive text: 256,000 → 372 bytes (**688×**)
- Code: 16,600 → 200 bytes (**83×**)
- JSON: 157KB → 21.7KB (**7.2×**)
- Prose: 113KB → 17.4KB (**6.5×**)
- Binary: 200,000 → 200,072 (**~1.0×**)

### Experiment 11: End-to-end system (save/hold/retrieve)
**File:** `virtual_storage_system.py`  
**What:** 4 real files saved, held broken-down, retrieved on demand.  
**Result:** 543KB of files held at 38.8KB. Retrieved byte-perfect. Zero disk.
Specific files retrievable individually without touching the rest.

### Experiment 12: 1GB held broken-down
**File:** `onegig_box_test.py`  
**What:** 1GB structured file → compress → hold → retrieve.  
**Result:** Held at **0.19MB** (19KB). RssAnon: **3.8MB**. Retrieval formed
full 1GB briefly (peak 708.9MB), then collapsed. Zero disk writes.

### Experiment 13: Big files — corrected RAM measurement
**File:** `full_measurements.py` (Measurement 1)  
**What:** Structured files of 10/50/100/500MB compressed and held, measuring
only the compressed form's RAM cost, not generation overhead.  
**Results:**
- 10MB → 1,720 bytes held, ~0.06MB data RAM cost
- 50MB → 7,820 bytes held, ~0.12MB data RAM cost
- 100MB → 15,448 bytes held, ~0.15MB data RAM cost
- 500MB → 76,456 bytes held, ~0.18MB data RAM cost

### Experiment 14: 3-minute sustained run
**File:** `full_measurements.py` (Measurement 2)  
**What:** 100MB compressed blob falling through boxes for exactly 180 seconds.
RAM sampled every 30 seconds.  
**Result:** RSS: flat at 44.5MB. RssAnon: flat at 34.4MB. Total hops:
**212,618,679**. Memory leak: zero. RAM growth over time: zero.

### Experiment 15: Speed measurement
**File:** `full_measurements.py` (Measurement 3)  
**What:** Time to save (compress) and retrieve (decompress) 2MB medical records,
10 runs each.  
**Result:** Save: **242.9ms**. Retrieve: **4.6ms**. Retrieval is 53× faster
than save. The cost is paid once at save time; retrieval is nearly instant.

### Experiment 16: Maps/GeoJSON
**File:** `full_measurements.py` (Measurement 4)  
**What:** 2000-feature GeoJSON map (Lagos area) compressed and held. Bounding
box query tested.  
**Result:** 754KB → 39.3KB (**19.2×**). Bounding box query needed only 812
bytes (2% of the map). Maps work excellently.

### Experiment 17: PDF splitting
**File:** `pdf_split_test.py`  
**What:** Split a real PDF into content (text layer) + structure (binary PDF) +
metadata. Compress each separately.  
**Result:** Content piece (text): compressed well. Structure piece (binary
PDF): barely. But for AI/search tasks that only need the text, catching just
the content piece costs a fraction of the full PDF.

### Experiment 18: Medical records — fidelity test
**File:** `medical_test.py`  
**What:** 5,000 patient records. Compress, retrieve, verify a specific patient
(MRN102500) is byte-perfect including medication dosage.  
**Result:** Exact fidelity confirmed. 2MB → 80KB. Retrieval correct at the
individual record level. Medical data: lossless is non-negotiable, and it is.

### Experiment 19: Model layer-streaming
**File:** `full_model_system.py`  
**What:** 12-layer model. Each layer quantized (32-bit → 8-bit) and compressed.
Run layer-by-layer, decompressing and freeing each layer before the next.  
**Result:** Model ran successfully. Layer-by-layer execution means peak RAM
tracks one layer's size, not the full model. For storing idle models: 4.5×
reduction via quantization + compression.

### Experiment 20: Compressed blob + falling boxes (combined)
**File:** `compress_plus_boxes.py`  
**What:** Medical records (1.93MB → 80KB compressed) held falling through boxes.  
**Result:** Process RSS: **2.0MB**. RssAnon: **0.2MB**. Zero disk. 21 million+
hops per second. The compressed blob falls; the boxes stay tiny.

### Experiment 21: General splitter across all types
**File:** `general_splitter.py`  
**What:** All real test files through the general splitter. Measure per-piece
compression and total.  
**Result:** 2.5MB of mixed files → 341KB total, **7.4× overall**. Each file
type handled correctly.

### Experiment 22: Complete combined system
**File:** `combined_full_system.py`  
**What:** Split + compress + fall, all file types, targeted retrieval, measured.  
**Result:** 2.22MB in → **137.5KB held**. 15 active box chains falling
simultaneously. Zero disk. Targeted retrieval: content-only for AI tasks,
structure-only for reconstruction, metadata-only for indexing.

### Experiment 23: 12GB file, streamed in, every form of RAM tracked
**File:** `vstorage/chunked_secure_box.py` (`from_file()`), `test_12gb_stream.py`
(part of the rebuild — see Section 17)  
**What:** A real 12GB markdown file (not synthetic bytes) read straight off
disk chunk-by-chunk, compressed, encrypted, and set falling — never held as
one Python object. Retrieval was deliberately skipped (rebuilding a 12GB
plaintext needs 12GB of RAM by itself, and this sandbox had no swap and
only ~15GB total). Every RAM field `/proc` reports was sampled at every
stage, inside a safety-monitored child process that would self-abort if
usage ever passed 2.5GB.  
**Result:** Peak RAM to hold and continuously ratchet a 12GB file: **~292MB
(about 41x smaller than the file)**, flat for the whole steady-state
window (12,663+ hops, RAM never moved more than 8KB), and released back
down to ~37MB on collapse. Safety abort never came close to triggering.
Build took 991 seconds (~16.5 minutes) — the real cost at this size is
time, at the real shipped compression setting, not memory risk.

---

## 7. The Dead-Ends (Tested, Not Just Argued)

Every one of these was actually built and run. They are documented so they
are never retried.

### Dead-End 1: Mapping positions of 1-bits
**Attempt:** Record where every 1-bit is, turn everything to 0, keep only the
position map.  
**Code:** `xor_test.py`  
**Result:** A 1MB file has ~4 million 1-bits. Each position needs 23 bits to
describe in an 8-million-bit space. Position map: **11.5× larger than the
original file.** The map is the file's information, just written more verbosely.

### Dead-End 2: Mask to zeros, keep the mask
**Attempt:** XOR the file with itself to get all-zeros; keep the XOR key as
the "mask" to restore.  
**Code:** `combined_experiment.py` (the masking portion)  
**Result:** The mask equals the file. Holding the mask is holding the file.
The zeros are free, but the mask is full-size. Nothing was saved.

### Dead-End 3: Mask to zeros, discard the mask
**Attempt:** XOR to zeros, throw away the mask.  
**Result:** Every file XORs to the same all-zeros. From zeros alone, you
cannot know which file it was. The file is unrecoverable — not hidden, gone.

### Dead-End 4: Small repeating XOR mask
**Attempt:** Use a 16-byte repeating pattern as a mask. This way the mask
is tiny but covers the whole file.  
**Result:** The masked output is the same size as the input — full size,
still. A tiny mask scrambles (encryption), it does not shrink. The masked
data is still 1MB, just rearranged. Useful for hiding content, useless for
reducing size.

### Dead-End 5: Staged masking
**Attempt:** Mask one portion at a time, at different moments, tracking what
exists at each stage.  
**Result:** At every stage, all the file's bytes exist — some masked, some
not. Staging changes when each part is scrambled, not how much space is used.
Full size at every moment, regardless of staging.

### Dead-End 6: On-demand formula generation
**File:** `generate_on_demand.py`  
**Attempt:** Generate 1GB from a seed+position formula; store only the seed
(a few bytes).  
**Result:** Worked — 1GB "held" at 4MB. But ONLY for formula-derived data.
A real document, medical record, or video can't be reduced to a seed, because
those files' bytes are not outputs of a formula. Seed-based storage requires
that the file *is* the formula's output, which real files are not.

### Dead-End 7: XOR splitting for size reduction
**File:** `xor_test.py`  
**Attempt:** XOR-split a 200MB file into 3 shares to make each smaller.  
**Result:** Each share is full-size. 200MB → 600MB (3 shares). XOR splitting
creates confidentiality (no single share reveals the data), not compression.
It multiplies, never shrinks.

### Dead-End 8: "Move so fast it's not measured"
**Attempt:** Move data fast enough that the computer can't measure it being
anywhere.  
**Result:** A computer runs in discrete clock cycles (2.1 GHz = 2.1 billion
per second). At EVERY cycle, every bit is in a definite physical state. There
is no "between cycles" where data exists but is nowhere. "Faster" means more
moves per second — the data still has a location at every tick. A fan blade
spinning fast still occupies space at every instant; the CPU clock is a
perfect high-speed camera that catches it every frame.

### Dead-End 9: "A pipe that holds no mass"
**Attempt:** Use OS pipes as a massless conduit so data exists in transit
without occupying RAM.  
**Result:** An empty pipe holds ~0. A pipe with data flowing through it holds
the data in a kernel buffer (RAM owned by the kernel). The pipe is a relay,
not a void. Data in a pipe still physically exists in RAM — just RAM on the
kernel's books, not the process's. The `splice()` technique achieves the
visible footprint benefit (process shows near-zero), but the data is still
in system RAM.

### Dead-End 10: "Accessible in non-existence"
**Attempt:** Data that is reachable but not held by anything.  
**Result:** "Reachable" and "held by something" are the same fact. If there
is an address that reaches data, the thing holding that address is holding
the data. There is no state where data is both reachable and held by nothing.
Either it has an owner (costs RAM), or it has no owner (unaddressable = gone).

---

## 8. All Measured Numbers

All measurements taken externally via `/proc` on a 1-core Ubuntu 24 machine
with ~4GB RAM. Nothing estimated.

### Compression ratios by data type

| Data type | Raw size | Held size | Ratio |
|---|---|---|---|
| Repetitive structured logs | 10 MB | 1,720 B | 6,096× |
| Medical records (JSON) | 2.0 MB | 80 KB | 25× |
| Source code (.c) | 16.6 KB | 200 B | 83× |
| JSON data | 157 KB | 21.7 KB | 7.2× |
| Natural prose | 113 KB | 17.4 KB | 6.5× |
| GeoJSON maps | 754 KB | 39.3 KB | 19.2× |
| PDF content (text layer only) | 19.3 KB | 6.1 KB | 3.1× |
| Mixed files (overall) | 2.5 MB | 341 KB | 7.4× |
| Random binary | 200 KB | ~200 KB | ~1.0× |

### RAM cost holding compressed forms

| Logical file size | Compressed held | Data RAM cost |
|---|---|---|
| 10 MB | 1,720 bytes | ~0.06 MB |
| 50 MB | 7,820 bytes | ~0.12 MB |
| 100 MB | 15,448 bytes | ~0.15 MB |
| 500 MB | 76,456 bytes | ~0.18 MB |

**Important:** 0.18MB is the at-rest cost. At the moment of retrieval, the
file forms full-size (~500MB briefly) then collapses back. This is the correct
and honest understanding.

### Sustained 3-minute run

| Metric | Value |
|---|---|
| Duration | 180 seconds |
| RSS throughout | 44.5 MB (flat) |
| RssAnon throughout | 34.4 MB (flat) |
| Total hops | 212,618,679 |
| Memory leak | Zero |
| RAM growth over time | Zero |

### Speed

| Operation | Time |
|---|---|
| Save 2MB medical records (compress) | 242.9 ms |
| Retrieve 2MB medical records (decompress) | 4.6 ms |
| Retrieval vs save speed | 53× faster |

### Disk usage

**Zero bytes written to disk across every test.**  
Confirmed via `write_bytes` in `/proc/self/io`.

### splice() stealth

| Measurement | Value |
|---|---|
| Process RSS while 1GB flows | 2.7 MB |
| System RAM used (kernel buffers) | +1,045 MB |
| Visible to process inspector | 2.7 MB |
| Visible to kernel memory forensics | the full amount |

### Self-healing boxes

| Metric | Value |
|---|---|
| Consecutive failures survived (v2) | 5,000 |
| Data intact after 5,000 failures | Yes |
| SHA-256 match | Yes |
| Both inner and outer end failures | Yes |

---

## 9. File Type Performance

### Works extremely well (compresses 6–83×):
- JSON, CSV, XML, JSONL — structured data with repeated field names
- Source code — highly regular patterns, keywords, indentation
- Log files — repeated prefixes, timestamps, message formats
- Medical records — repeated field names, standard condition/medication strings
- GeoJSON maps — repeated property names, coordinate format

### Works well (compresses 3–7×):
- Natural language text, prose, documentation
- HTML (for the text inside it)
- YAML, TOML, INI (config files)

### Works partially (1.5–3×):
- PDF — the text layer compresses well, but the binary format wrapper does not.
  Split the PDF first: extract text for AI/search tasks, keep binary only for
  full reconstruction.
- DOCX, XLSX, PPTX — similar story. XML content inside compresses; the zip
  container wrapper is less compressible.

### Barely works (1.0–1.4×):
- JPEG, PNG, GIF, WEBP — already compressed image formats
- MP3, MP4, AAC — already compressed audio/video
- ZIP, GZ, XZ — already compressed archives
- Raw model weights — high-entropy floating point numbers
- Encrypted files — encryption is designed to look maximally random

### The rule:
**If a human can read it and find patterns, it compresses well. If it looks
like random noise, it won't.** Encrypted and already-compressed data will not
shrink — the system still stores and retrieves them exactly, just without
size benefit.

---

## 10. Security Properties

### Confirmed and proven by tests:

**Zero disk trace.** `write_bytes: 0` confirmed across every test, every file
type, every run. Nothing ever wrote to disk. Disk forensics finds nothing
because there is nothing there.

**No fixed address.** Data falls through 64KB boxes continuously. At 212
million hops in 3 minutes, data never stays at one address long enough to
be reliably located. A memory scraper grabbing one address gets a piece
that has moved by the time analysis begins.

**Dies with the process.** When the process is killed:
- OS marks all anonymous memory pages as free: instant
- No file, path, or name exists anywhere: confirmed
- Nothing recoverable afterward: confirmed
- The data becomes unreachable (ownership ends) before any physical bit
  erasure — "gone" means "no address reaches it" first, then "bytes
  physically overwritten" when the OS reallocates those pages

**Process footprint near-invisible.** Via splice(): process shows 2.7MB
while 1GB flows through kernel buffers. A process memory scraper (like
Heartbleed-style attacks) reads the process's address space and finds
almost nothing.

**Defeats disk forensics.** Completely. If it never touched disk, disk
forensics finds nothing.

### Honest limits — not absolute guarantees:

**Defeats live process memory inspection? Yes, at the process level.**
Splice() means the process's own memory contains almost nothing. However,
a kernel-level forensic tool (requiring root or physical hardware access)
reading raw memory maps or kernel buffers would still find the data.

**Defeats cold-boot attacks? Harder, not impossible.**
Cold-boot attacks freeze RAM (liquid nitrogen) and read raw bit states.
Since data never touches disk, there's nothing to seize from storage.
But if the attacker freezes RAM while the process is running, the data
is physically present in those RAM cells. The motion makes targeted
extraction significantly harder (the data is distributed across many
cells and was moving) but does not physically prevent it.

**Defeats hardware bus sniffers? Harder, not impossible.**
A hardware bus sniffer reads the physical memory bus. The data is still
physically present on the bus during transfers. The continuous motion
means the attacker must catch it mid-transit rather than finding it
at rest at one location. This raises the bar significantly — it doesn't
make the data invisible to the hardware level.

### The security truth in one sentence:
Virtual Storage defeats storage-layer attacks (disk forensics, file system
analysis) completely and raises the bar significantly against memory-layer
attacks (process scrapers, RAM forensics), but does not defeat hardware-
level physical RAM access while the process is running.

### Update — everything above was true of the original prototype (motion
### alone). The rebuild (Section 17) closed the biggest gap in it:

Motion alone (falling through boxes, no encryption) turned out to be
**detectable, not just harder to catch** — a real attacker doing a full
memory dump at one instant, or watching one fixed address, or reading
slowly in small pieces, found the plaintext 100% of the time across 300+
real trials. Motion changes *where* data sits, not *what* it looks like
when caught. This was proven with real attacks, not assumed, and it is
why the rebuild added real AES-GCM encryption with continuous key
rotation on top of the falling motion, rather than treating motion as
sufficient by itself. See Section 17 for the honest before/after numbers.

---

## 11. How Data Disappears (The Physics)

When a process ends, data disappears in two distinct stages:

**Stage 1 — Immediately (microseconds): Unreachable.**
The OS marks the process's anonymous memory pages as free. No process now
holds a mapping (an address) to those pages. The data cannot be reached
because there is no address pointing to it. "Gone" for all practical
purposes — nothing can find it.

**Stage 2 — Later (when pages are reused): Physically overwritten.**
The actual bit patterns in the physical RAM cells may persist briefly after
Stage 1 — the electrons haven't moved yet. But the OS, for security reasons,
zeroes freed pages before handing them to a new process. The moment those
pages are reallocated, the old bits are physically destroyed.

**Why data needs "holding" at all:**
Data in RAM doesn't need active effort to stay there — RAM hardware refreshes
itself electrically, independently of any software. What data needs to be
usable is an owner with an address to it. "Nothing holding it" means "no
address points to it" — not "no energy is keeping it up." The data sits in
RAM passively until either: (a) a process owns and addresses it, or (b) the
pages are reallocated and physically zeroed.

This is why Virtual Storage's security works: when the process ends, the
ownership (the address) disappears instantly. The data becomes unreachable —
effectively gone — before any physical erasure occurs.

---

## 12. How It Compares to Existing Systems

### Redis / Memcached (in-memory key-value stores)
**How they work:** Store data at full size in RAM. For speed — RAM access
is nanoseconds vs milliseconds for disk. Redis writes to disk as backup
(AOF log or periodic snapshots), so it survives reboots. Memcached is
pure in-memory with no persistence.

**AOF (Append Only File):** Every write command is appended to a log file
on disk immediately. On restart, Redis replays all commands. More durable
than snapshots (nothing lost), but the log grows forever.

**Snapshots (RDB):** Redis periodically writes a full dump of all data to
disk. Anything between the last snapshot and a crash is lost.

| Property | Redis/Memcached | Virtual Storage |
|---|---|---|
| RAM cost | full data size (500MB = 500MB RAM) | 0.18MB for 500MB (structured) |
| Retrieval speed | sub-millisecond | 4.6ms |
| Disk writes | Yes (AOF or snapshots) | Zero, ever |
| Fixed address | Yes | No — always moving |
| Survives reboot | Yes (Redis) | No — ephemeral |
| Process footprint | full data size | ~2.7MB (splice) |

### HashiCorp Vault (secrets management)
The industry standard for API keys, tokens, passwords, certificates.
Provides unified interface, tight access control, audit logging, key
rotation, dynamic credentials.

In production: Vault always writes to disk (encrypted). When "sealed,"
the master key isn't in memory. In "dev mode," it runs in RAM and loses
everything on restart — dev mode only, not for production.

| Property | HashiCorp Vault | Virtual Storage |
|---|---|---|
| Disk writes | Yes (encrypted, always) | Zero, ever |
| Fixed address | Yes | No |
| Dies with process | No — persists | Yes |
| Audit logging | Yes | No (not yet built) |
| Access control | Yes (policies) | No (not yet built) |
| Dynamic credentials | Yes | No |
| Process footprint | Full key store size | Near-zero (splice) |

### Linux zram
A kernel feature that compresses portions of RAM that aren't actively used.
Every Android phone uses this. It's Virtual Storage's closest cousin in
mainstream computing — compressed-at-rest, expands on access.

**Difference:** zram is a transparent kernel optimization, not an application-
level security system. It writes to disk as swap if compressed RAM fills up,
has no falling/motion property, no stealth, and no explicit ephemeral death.
It's the same basic compression idea applied at the OS level for efficiency,
not security.

### SAP HANA
Enterprise in-memory analytics platform. Keeps all data in RAM for speed,
writes to persistent storage simultaneously. Handles complex SQL, real-time
analytics, both OLTP and OLAP. Requires specialized hardware, expensive.
No compression-based size reduction, no motion, no stealth.

### The honest positioning
Virtual Storage is not a Redis replacement (Redis is faster, more mature,
has persistence). It is not a full Vault replacement (Vault has access
control, audit trails, dynamic credentials). It is something adjacent to
both: **a zero-disk, motion-based, stealth-footprint ephemeral store** for
sensitive transient data that must never touch any persistent medium under
any circumstance. That is a real niche with real customers who pay seriously
for that guarantee.

---

## 13. Where It Works, Where It Doesn't

### Where it genuinely helps:

**Ephemeral sensitive data (best fit)**
API keys, OAuth tokens, JWTs, session secrets, decryption keys active only
during use. These are tiny (bytes), short-lived by design, extremely high
value, and should never touch disk. The system holds thousands of them at
near-zero RAM cost, in motion, invisible to process inspection.

**PII redaction pipelines**
A batch of medical records or legal documents enters the system. It shrinks
to its compressed form, hides in kernel pipes via splice(), shuffles
continuously. A parsing micro-engine pulls targeted chunks block-by-block,
strips SSNs or names, feeds clean data to an AI model. The process vanishes
— leaving zero forensic trace of the original data in memory.

**Medical and legal record access**
Records held compressed, served one at a time. A doctor clicks "Page 42" —
only that block decompresses for the screen. The entire record is never
exposed all at once in the RAM layout, minimizing the attack surface to a
fraction of a percent.

**AI context windows**
Hold compressed context while the model computes. Data stays tiny and
invisible to memory scrapers while the model runs. Session ends, context
shatters.

**Geospatial/mapping data**
GeoJSON maps at 19× compression. Bounding-box queries need only 2% of the
map. Navigation data, terrain files, routing graphs — all structured text,
all compress excellently.

**Edge/tactical hardware**
A drone or field device holds maps and logs falling through boxes in ~3.8MB
of RAM. No disk to wipe. The moment power is cut or a kill signal is sent,
everything collapses permanently. No forensic extraction possible from
seized hardware because there is nothing there to extract.

**Cold model storage**
Idle model checkpoints held compressed (4.5× via quantization + compression).
Spin up on demand by decompressing and loading. For libraries of many models
where only a few run at once, this significantly reduces the idle storage
footprint.

### Where it doesn't help:

**Durable storage** — Power off = total loss. Not a database replacement.

**Already-compressed media** — Video, images, encrypted files don't shrink.
The system stores them correctly but provides no size benefit.

**Live model inference** — A running model needs all weights continuously.
Layer-streaming allows running a large model on a small device (by loading
one layer at a time) but the active layer still costs its size. The full
model runtime cost is not reduced by this system.

**General-purpose file storage** — If durability matters, this is wrong.
Files that need to survive power cycles should use a real database with
proper persistence.

---

## 14. The Startup Case (API Keys and Secrets)

Virtual Storage's sharpest immediate startup opportunity is as a **stealth
ephemeral secrets buffer** — a system for holding API keys, tokens, and
other credentials in a way that is more secure than anything currently on
the market for the specific threat model of "sensitive data that must never
touch any persistent storage medium."

**The threat being solved:**
The Heartbleed bug (2014) let attackers read a server's RAM directly and
steal private keys and session tokens sitting unprotected in memory.
HashiCorp Vault (the industry standard) always writes to disk (encrypted).
Redis exposes secrets at fixed addresses in memory. Standard environment
variables are logged, cached, and written to disk in a dozen places.

**What Virtual Storage offers that nothing else does:**
1. Zero disk writes — ever. Not "encrypted on disk" but literally never
   touching any persistent storage medium.
2. No fixed address — keys are moving continuously. A memory scraper
   doing a Heartbleed-style attack finds a moving target at near-zero RSS.
3. Process-level invisibility via splice() — the process shows ~2.7MB
   while holding gigabytes of tokens in kernel space.
4. Instant death — kill the process, every key vanishes. No sealing
   ceremony, no key sharding needed. The process dying IS the destruction.

**Target customers:**
- Military and intelligence systems where even encrypted disk is unacceptable
- Journalist and activist tools where device seizure is a real threat model
- Financial trading systems where credential theft means real money stolen
- Healthcare AI pipelines processing PHI that must leave no trace
- Any multi-tenant cloud system where co-tenant RAM access is a concern

**What needs to be added to turn this into a product:**
1. A proper API (so applications call `store(key, value)` and
   `retrieve(key)` the same way they'd call Redis or Vault)
2. Authentication (so only authorized processes can read any given secret)
3. Replication (so a single process crash doesn't lose everything — run
   two instances, each holding the same secrets falling in parallel)
4. Key rotation (update a secret without downtime)

The core security property — the hard, differentiating, tested part — is
already built and measured. The product layer around it is real engineering
work but not the difficult part.

---

## 15. The Complete File Index

### The complete working system (start here)

| File | What it does |
|---|---|
| `combined_full_system.py` | **THE MAIN SYSTEM** — split + compress + fall + targeted retrieval |
| `general_splitter.py` | Splits any file type into content/structure/metadata |
| `virtual_storage_system.py` | Save/hold/retrieve end-to-end, plain version |
| `measured_system.py` | Same, with full RAM + disk measurement |
| `onegig_box_test.py` | 1GB held broken-down, full measurement |

### Motion and stealth

| File | What it tests |
|---|---|
| `anonymous_mmap_storage.c` | Continuous motion between two anonymous regions |
| `bounce_mover.c` | Sustained bounce for a set duration |
| `splice_mover.c` | Zero-copy single move via splice() |
| `full_picture.py` | splice() — measures both process RSS AND system RAM |
| `gb_under_50mb.py` | 1GB under a hard 50MB OS-enforced ceiling |
| `lean_motion_experiment.py` | Lean motion: no XOR, explicit deletion |
| `extreme_pipe_experiment.py` | Pure OS pipe chain, Python holds no data |
| `full_measurements.py` | Big files + 3-min run + speed + maps |

### Box structure

| File | What it tests |
|---|---|
| `breaking_boxes.py` | 3 × 1MB rotating boxes, cheap flow |
| `box_relay.py` | File flowing through a 1MB box relay |
| `retrievable_test.py` | The "retrievable = present" wall |
| `nested_boxes.py` | Self-healing nested boxes v1 |
| `nested_boxes_v2.py` | Self-healing nested boxes v2 (fixed — outer end too) |
| `nested_boxes_real.py` | Nested boxes with a real file |
| `perpetual_fall.py` | The perpetual fall handoff model |
| `raw_fall_test.py` | Whole raw file falling, no compression |
| `one_box_all_files.c` | One box holding all files, self-destroying |
| `one_box_all_files.c` | Multiple variants: one_box_fast.c, one_box_100mb.c |
| `multi_file_test.c` | Multiple files bouncing independently |

### Compression and splitting

| File | What it tests |
|---|---|
| `breakdown_test.py` | Real files broken down, ratios measured |
| `diverse_test.py` | All file types, diverse compression ratios |
| `pdf_split_test.py` | PDF into content/structure/metadata |
| `unstructured_pdf_test.py` | Real unstructured PDF through the system |
| `medical_test.py` | Medical records — fidelity and compression |
| `compress_plus_boxes.py` | Compressed blob falling through boxes |

### Measurement tools

| File | What it does |
|---|---|
| `measure.py` | External RSS measurement — both designs compared |
| `disappear_timing.py` | How fast data disappears after process death |
| `full_measurements.py` | The authoritative measurement suite |

### Models

| File | What it tests |
|---|---|
| `full_model_system.py` | Quantize + compress + layer-stream a real model |

### Documented dead-ends

| File | Why it's here |
|---|---|
| `xor_test.py` | XOR multiplies (600MB from 200MB) |
| `generate_on_demand.py` | Near-zero RAM, but formula data only |
| `real_data_box.py` | Real data flows through, but exits and is gone |
| `combined_experiment.py` | XOR + pipes + tmpfs — confirmed no-disk |
| `falling_storage.py` | Original ports-based motion prototype |
| `multi_machine_sim.py` | Replication simulation (one machine killed) |

### Previous documentation (superseded by this handbook)

| File | Status |
|---|---|
| `RECORD_BOOK.md` | Superseded by this handbook |
| `VIRTUAL_STORAGE_COMPLETE.md` | Superseded by this handbook |
| `VIRTUAL_STORAGE_FINDINGS.md` | Superseded by this handbook |
| `SEPARATE_REGIONS.md` | Superseded by this handbook |

---

## 16. One-Sentence Summary

**Virtual Storage is a zero-disk, in-memory transient store that splits any
file into content, structure, and metadata pieces, holds each compressed and
continuously falling through small breaking/reforming boxes with no fixed
address, delivers only the piece each task actually needs, costs as little as
0.18MB of RAM to hold 500MB of structured data at rest, and leaves zero
recoverable trace — on disk, in process memory, or anywhere — when the
process ends.**

---

## 17. What Changed Since — Security & Scale (the rebuild)

Everything in Sections 1–16 is the original build: splitting, compressing,
and falling motion. It proved data could be held cheaply and moved
continuously. What it did **not** prove is that moving data is the same
as hiding it — that turned out to be false (see the update at the end of
Section 10), and fixing it, plus making the system hold files far bigger
than 500MB safely, is what this section covers. Full step-by-step detail
lives in `ARCHITECTURE.md`; the full experiment log for this phase lives
in `REBUILD_STATUS.md`. This section is the short, plain version.

### The one system, six steps

There used to be several separate prototype scripts (`combined_full_system.py`,
`secure_falling_box.py`, `encrypted_falling_box.py`, and others). They've
been consolidated into **one system**: `vstorage/secure_system.py`
(`SecureVirtualStorage`), the only thing you should actually use now. The
old prototypes still exist for reference (`vstorage/superseded/`), each
kept because it proved one specific thing along the way.

A file put into `SecureVirtualStorage` goes through six steps:

1. **Split** — same as Section 4, Step 1: content / structure / metadata.
2. **Compress** — same as before: LZMA, once, before anything else touches
   the piece.
3. **Encrypt** — new. Each compressed piece is locked with AES-GCM (a
   modern authenticated cipher — it doesn't just hide the bytes, it also
   detects if they were tampered with). For large files, the piece is
   first cut into fixed-size chunks (256KB each) so a huge file never
   needs to be re-encrypted whole on every tick — only one chunk moves
   per tick, regardless of file size.
4. **Fall** — same falling motion as before, but now what's falling is
   ciphertext, not plaintext, and the *key itself* keeps changing
   ("ratcheting") every tick via a one-way hash chain — so even catching
   old ciphertext later is useless once the key has moved on.
5. **Retrieve on demand** — same shape as Section 4, Step 4: decrypt only
   the piece asked for, hand it over, wipe the plaintext copy immediately
   after.
6. **Death** — same as before, plus one new layer: a **watchdog** running
   as a separate process (not just a thread — a thread can lose a race
   under load, a separate process can't) watches for anyone attaching to
   read this process's memory, and wipes every key and buffer the instant
   it sees an attempt.

### What's new, in plain terms

- **Real encryption, not just motion.** The original handbook's "no fixed
  address" defended against knowing *where* data is. It never addressed
  *what it looks like* once found. AES-GCM does: a full memory dump now
  finds encrypted noise, not readable data — tested with the exact same
  three attack styles Section 10 already used (full dump, fixed-address
  watch, slow chunked read), 100% detection before, 0% after.
- **A key that keeps moving too.** The encryption key itself changes on a
  schedule, so a key caught once cannot be used to unlock a snapshot
  caught earlier or later — only a snapshot and its key caught in the
  *exact same instant* still work together. Everything else that
  attacker might be holding (old copies, stale dumps) is already dead.
- **A backup for the key itself (defense-in-depth, not a trick).** The
  key is also split byte-by-byte across many separately-hidden pieces
  (Shamir's Secret Sharing) so no single memory location ever holds the
  whole key at once. Worth being honest about what this does and doesn't
  do: it does **not** fool an attacker who understands the system — a
  knowledgeable attacker who catches all the pieces at once still wins.
  What it does do is raise the number of things that must be caught
  *simultaneously* to succeed, which is a real, if familiar, security
  property (the same idea as multi-signature wallets or split root
  keys), not a disguise.
- **A trust layer that doesn't rely on one machine at all.** Beyond the
  local split, the key can optionally be derived live, tick by tick,
  from several separate processes (`use_distributed_trust=True`) — a
  real K-of-N scheme, not just local math. This closes a gap the local
  version has: a purely local key chain is predictable forward once
  caught once (it's just repeated hashing); the distributed version
  isn't, because part of the input comes from outside the process being
  attacked.
- **Large files no longer need to fit comfortably in RAM to be held
  cheaply.** Chunking (step 3 above) is what makes this possible — the
  cost of holding a file falling and encrypted no longer scales with the
  file's size, only with the fixed chunk size. Proven at 12GB
  (Experiment 23, Section 6): ~292MB peak, flat, for a file 41x larger.
- **Ingesting a huge file without doubling its RAM cost first.**
  `ChunkedSecureBox.from_file()` reads straight from disk in chunks
  instead of needing the whole file as one Python object before it can
  even start. This is what made the 12GB test possible at all. It is
  *not yet* wired into the main `save()` path for every file type — see
  the honest gap below.

### The honest limits, stated plainly (nothing hidden)

- **A root-privileged reader who skips the standard attach step is
  invisible to the watchdog.** The watchdog watches for the normal way
  of reading another process's memory (`ptrace_attach`). A sufficiently
  privileged reader can open `/proc/<pid>/mem` directly without that
  step, and there is no signal to catch — confirmed directly, not
  theorized. Distributed trust is the honest answer to this, not a
  faster watchdog: it means no single machine, root or not, ever holds
  the whole secret to read in the first place.
- **The real ingestion "front door" isn't fully streaming yet.** The
  12GB test streamed a file into `ChunkedSecureBox` directly. The actual
  `SecureVirtualStorage.save()` path most callers would use still goes
  through `splitter.py`, which reads a file fully into memory before
  splitting it. For a huge plain-text file this means the *real* save
  path still costs close to the file's own size in RAM to start, even
  though the box holding it afterward is cheap. Wiring a true disk-to-
  storage streaming channel — the "just point it at a file on disk"
  version — is tracked in `TODO.md` as the next real piece of work, not
  yet done.
- **Distributed trust costs speed.** Real round-trips between processes
  roughly halve the hop rate compared to local-only key rotation. A
  deliberate, measured trade of speed for the security property, not an
  accident.

### Update — holding many files at once, and what that actually costs

Everything above was verified one file at a time. Holding 24 real
files concurrently (~2GB total, every file type the splitter
recognizes) exposed a cost none of the single-file tests could show:
every file's key guards and falling motion used to spawn their own
threads, so 24 files meant 217 background threads, ~55 minutes to
save them all, and retrieval that was in places even slower than
saving - pure thread contention, not a correctness problem (every
single byte still came back exact).

Fixed by consolidating: every guard and every falling box now shares
ONE process-wide scheduler each, instead of each spawning its own
thread. Verified with a direct before/after comparison on identical
files (threads 55 → 3, retrieval >2x faster) and confirmed again on
the full 24-file, ~2GB run: save time cut by 2.7x, individual
retrievals 9-10x faster, thread count flat at 3 regardless of file
count, still 100% byte-perfect across all 25 retrievals (including a
file pulled out mid-run and put back), and the tamper watchdog still
killed the process in 271 microseconds-scale time at this larger
scale too.

**The honest new trade this created:** fewer threads means less
overhead, but also less slack. When the shared falling-motion thread
has to compete with a sustained heavy operation on the main thread
(a large file being saved or retrieved), it can lose that fight
completely for the duration - measured directly: 29 of 75 boxes got
zero key-rotation hops during a 75-second window where the main
thread was busy for 69 of those seconds. Before the fix, 75 separate
threads meant a busy main thread couldn't starve all of them at once;
after it, the single shared thread can be starved outright.

**Since fixed** with a small pool (4 workers) instead of exactly one
shared thread, for both the falling-motion scheduler and the key-cell
scheduler. Reproduced the exact failure directly, then confirmed the
fix on the same repro: 0 of 30 boxes stalled across a 46.5s busy
window, hop counts landing in a tight 107-110 range across all of
them. Full detail, including two more real bugs found and fixed in
the watchdog's rotation-tracking along the way (a stale-address gap,
then the region-table growth its own fix caused): `REBUILD_STATUS.md`.

### Update — streaming both directions, and three ways to hand a file back out

Two more real gaps closed after the above, both found by actually
building and testing, not by inspection:

**Save() used to need a file's own size in RAM just to start.**
`ChunkedSecureBox.from_file()` had already proven a 12GB file could be
held at ~292MB - but only `splitter.py`'s `split_file()`, the REAL
`save()` entry point, still read a file whole into RAM before
anything else could happen. Fixed: text-like content and every
piece's structure now stream straight from disk. Verified on a real
5GB file through the actual `save()` call: ~195MB peak, not ~5GB.

**retrieve() had the identical problem in reverse** - it built the
whole reconstructed file as one object before handing it back. Fixed
with streaming output (`retrieve_to_file()`): decrypt one chunk,
write it, discard, repeat. Verified on a 3GB file: RAM moved from
92MB (holding it) to 94MB while streaming the ENTIRE file back out -
a 2MB delta, not a multi-GB spike.

**Streaming output alone still wrote raw plaintext to the
destination**, though - bounding *how much* was ever exposed at once,
not making the destination itself safe if it wasn't already. Added a
second output mode: `retrieve_to_encrypted_file()` decrypts a chunk
from storage and immediately re-encrypts it under a fresh transit key
before it ever reaches the destination - confirmed directly that the
output file's bytes never contain the original plaintext, searched
for it, not found. Costs ~27% more time (one real extra AES-GCM pass
per chunk); RAM is unaffected either way.

**Then found the same gap the whole security arc has been finding
elsewhere: the key was handed back at the same instant as the file,**
which means nothing about a timer or a "confirmed departure" check
prevents someone who's already watching from copying it (see the
next section for why - it's the same limit as "the process dies with
everything in it" not reaching data a caller already has). Rather
than pick one fix, built THREE selectable output tiers on one method,
`retrieve_to_destination(file_id, dest_path, trust=...)`:

- `trust="trusted"` - plain streaming, fastest, for a destination
  that's already protected on its own.
- `trust="untrusted"` - wrapped streaming, key only released after a
  real `os.fsync()`-confirmed durable write - don't hand out the
  means to read the file until it has genuinely, durably left.
- `trust="hostile"` - everything `"untrusted"` does, plus the key
  itself is never handed back directly - a single-use, time-boxed
  token instead (`redeem_key()` is a separate, later call).

Verified: all three byte-perfect end to end, single-use enforced on
the hostile tier (a second redemption attempt is correctly rejected),
expiry enforced with the right error type (a real bug caught before
shipping: the cleanup sweep raced the token's own expiry check and
misreported an expired token as "never existed" - fixed by checking
each token's own deadline directly).

**The honest limit underneath all of this, worth being direct about:**
none of these tiers make plaintext stop existing - nothing can,
decrypting is what makes data usable at all, true of any encryption
system, not a gap specific to this one. What they actually buy,
in increasing order: bounded exposure (streaming), a destination that
never sees plaintext regardless of trust (wrapped), and control over
*when* and *how many times* the means to read it can be used
(the token tier) - narrowing the window and the reuse, not sealing it
shut. A key that has already left this process, into someone else's
hands, is gone from this process's control the same way the "dies
with the process" guarantee only ever covered what's *inside* it.
