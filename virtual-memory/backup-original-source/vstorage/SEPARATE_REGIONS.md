# Virtual Storage — Separate Regions Version

A working, disk-free, in-motion storage mechanism. Data lives only in anonymous
RAM, moves continuously between two fixed regions, and vanishes permanently when
the process ends.

This document describes the **separate-regions** design specifically. A second
design (the **single self-destroying box**) trades away some of this version's
speed to roughly halve its memory footprint — see notes at the end.

## The core idea

Normal storage puts data in a fixed place — a disk sector, a file, an address —
and it rests there. This design never lets data rest. It is always moving between
two anonymous memory regions, and the motion itself is what keeps it "stored."
There is no fixed location to point to and say "the file is there."

## How it works

Two anonymous memory regions are allocated up front — call them **A** and **B**,
each the size of the file. "Anonymous" here is the exact technical term: memory
allocated with `mmap(MAP_ANONYMOUS)`, backed by **no file**, with **no path or
name anywhere** in the filesystem. It exists only while the process holds it.

The file starts in region A. A loop then continuously:

1. Streams the whole file from the current region to the other region,
   through a real OS pipe (`pipe()` + a concurrent writer thread).
2. Swaps which region is "current."
3. Repeats — forever, as long as the process runs.

At any instant the data is either in A, in B, or briefly in-flight inside the
pipe. None of those has a filename or a disk address.

```
        pipe (in-flight)
   A  ───────────────▶  B      (hop 1)
   A  ◀───────────────  B      (hop 2)
   A  ───────────────▶  B      (hop 3)
                ...forever
```

### Why a pipe, and why a writer thread

A Linux pipe's internal buffer is small (64 KB by default). You cannot shove a
whole multi-megabyte file into one in a single write with nobody reading — it
blocks forever waiting for room (a real deadlock we hit and fixed during
development). The fix is a **concurrent writer thread**: one thread feeds the
pipe in small chunks while the main thread drains it at the same time, so the
pipe never has to hold more than a chunk at once.

## What was actually measured

Tested in a Linux container (Ubuntu 24, single core, ~3.7 GB free RAM):

| Property | Result |
|---|---|
| Correctness | Data verified byte-identical (checksum) across thousands of hops |
| Memory stability | Resident memory stays **flat** over sustained runtime — no leak, no creep |
| Disk footprint | **Zero** bytes written to disk (`write_bytes: 0`, confirmed via `/proc/self/io`) |
| Runtime cost | Flat over time — hop rate steady, no slowdown as the run continues |
| On process death | Kernel reclaims both regions immediately; no file, no name, nothing recoverable |

A separate `splice()`-based single-move test moved a **1 GB** file with a peak
process footprint of **~1.7 MB**, because `splice()` moves bytes between file
descriptors through the kernel without copying them into user space at all. That
is the leanest movement primitive available and is a good candidate for the
motion step at large scale.

### An important measurement caveat (learned the hard way)

Measuring the memory of this design is subtle and easy to get wrong:

- `getrusage`/`VmHWM` report **peak** RSS, not current. Because the two regions
  briefly coexist during a transfer, the peak records that spike and then keeps
  displaying it forever — making the process look like it permanently uses ~2×
  the data size when it may not.
- Linux allocates pages **lazily**: `mmap` reserves address space, but a page
  only consumes real physical RAM once it is actually touched. So `RssAnon` can
  read far lower than the allocated region size, depending on exactly when in the
  transfer cycle you sample.

The honest summary: this design **allocates** two regions (≈2× the data size in
address space), but the **actual resident RAM at any instant depends on timing**
and is often much lower than 2×. Always measure current RSS from
`/proc/<pid>/status` (`VmRSS`/`RssAnon`), never peak, and sample repeatedly.

## Security properties

- **No fixed address.** The data relocates continuously. A single-instant memory
  snapshot of one address catches it a moment before it moves elsewhere.
- **No disk trace.** Nothing is ever written to disk or to a named tmpfs path, so
  disk imaging finds nothing.
- **Dies with the process.** On `kill` or power loss, the kernel reclaims the
  anonymous regions. There is no file to recover and no name to look it up by.

### Honest limits

- Not durable: the instant the process stops or power is lost, the data is gone.
  This is ephemeral, in-motion storage — not a replacement for permanent storage.
- Defeats a snapshot, not continuous observation: an attacker with ongoing,
  real-time access to the machine's full RAM could in principle follow the motion.
  Motion beats a single grab, not infinite watching.
- The composition of primitives here (anonymous mmap + pipe motion) is standard,
  but this specific arrangement has not been externally security-reviewed.

## Where it fits

Not a replacement for the disk/cloud storage a business runs on — a crash means
total loss, which is the opposite of what "storage" means to most companies. It
fits **ephemeral sensitive data that should disappear if anything goes wrong**:
encryption keys held only during active use, session secrets, decrypted content
that must never touch disk. This is the same principle secrets managers already
use for in-memory keys — this design pushes it further with continuous motion.

## Separate regions vs. the single box

- **Separate regions (this doc):** two fixed regions reused every hop. Faster
  hops (no per-hop allocation), but holds two regions' worth of address space
  for the whole run.
- **Single self-destroying box:** one box holds all files together; each hop
  allocates a fresh box, transfers everything in, then immediately `munmap`s the
  old one. Roughly halves the durably-held memory, at the cost of slower hops
  (allocating + freeing a full-size box every hop). Better when RAM is the
  binding constraint; worse when hop speed matters.

## Files in this project

- `anonymous_mmap_storage.c` — the separate-regions version (this doc)
- `bounce_mover.c` — sustained bounce between two files, duration-based
- `splice_mover.c` — zero-copy single move via `splice()` (the ~1.7 MB / 1 GB result)
- `one_box_all_files.c` — the single self-destroying box holding all files together
- `multi_file_test.c` — multiple files each bouncing independently
