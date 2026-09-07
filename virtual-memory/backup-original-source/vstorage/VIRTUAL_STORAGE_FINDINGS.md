# Virtual Storage — What We Actually Built and Measured

A record of real experiments, with real numbers, measured externally from
`/proc`. This exists so the findings aren't re-litigated from scratch or
dismissed as "impossible" — several things here genuinely work. The honest
limits are recorded too, because knowing exactly where the wall is is what
makes the working parts trustworthy.

Everything below was measured on a 1-core machine with ~4 GB total RAM.

---

## The core idea

Storage as **motion** instead of a fixed location. Data that exists by moving,
never resting in one findable place, holding no disk footprint, and vanishing
when the process ends. Built on anonymous memory, pipes, `splice()`, and
rotating "boxes."

---

## What works (measured, real)

### 1. Motion-based storage, RAM-only, zero disk
Data circulates continuously between anonymous memory regions. Never written to
disk, dies with the process, stays byte-perfect across thousands of moves.
- **Verified:** checksums intact across thousands of hops; `write_bytes: 0` to
  disk confirmed via `/proc/self/io`; data gone after `kill`.
- This is a real, legitimate technique — same family as in-memory secret stores.

### 2. `splice()` — tiny process footprint, big data in motion
Moving 1 GB continuously through kernel pipes:
- **Process RSS: ~2.7 MB** while 1 GB flows.
- **System RAM used: +1045 MB** — the 1 GB really lives in *kernel* buffers.
- **Meaning:** the data's footprint is invisible *to inspection of the process*
  (an inspector sees ~2.7 MB), but the bytes physically exist in kernel-owned
  RAM. Real, useful stealth property — the cost is hidden from the process, not
  erased from the machine.

### 3. On-demand generation — 1 GB "held" in ~4 MB
A 1 GB virtual file generated 1 MB at a time from a seed + position formula:
- **Process RSS: 2.8 MB, system delta: 4 MB, for a consistent 1 GB.**
- **Works ONLY for formula-derived data.** What's stored is the tiny rule, not
  arbitrary bytes. A real video/document can't be reduced to a seed — that's the
  information-theory limit (pigeonhole principle), not a coding limit.

### 4. Rotating boxes — cheap motion
3 boxes of 1 MB each, 200 MB flowing through them:
- **Process RSS: 4.8 MB, system: 4 MB, box RAM: 3 MB.**
- The boxes as *movers* are genuinely tiny. This part is real and cheap.

---

## The one wall we kept hitting (and exactly where it is)

**"Retrievable" and "present in RAM" are the same thing.**

When we required the 200 MB file to stay *retrievable while circulating through
3 boxes* (the actual goal: "retrievable as long as the process runs"):
- **Process RSS: 205.6 MB, system: 202 MB.**
- The 3 boxes moving = 3 MB. The 200 MB of pieces = 200 MB, because every piece
  has to exist somewhere reachable for the file to be retrievable.

So the trade, proven both ways in the same design:
- **Boxes flowing, nothing kept:** ~3–5 MB. Cheap. But the data flows through and
  is gone — not retrievable.
- **File kept retrievable:** costs the file's size. Because the bytes you can
  read back are the bytes taking up space.

You cannot have "only 3 MB exists" AND "all 200 MB retrievable" at once —
retrievable *means* the 200 MB is present. This isn't a limit of the code or of
cleverness; "the data you can get back" and "the data occupying memory" are the
same bytes.

---

## The honest summary, per goal

| What you want | Achievable cost | Status |
|---|---|---|
| Data in motion, no disk, dies with process | file size in RAM | ✅ real |
| Process footprint tiny while big data flows | ~3 MB process (data in kernel RAM) | ✅ real (`splice`) |
| Huge file from a formula, near-zero RAM | ~4 MB | ✅ real (generated data only) |
| Boxes moving cheaply | ~3 MB | ✅ real |
| Arbitrary file **retrievable** at tiny RAM cost | — | ❌ blocked by information theory |

---

## Why this isn't "impossible" — and where it honestly is

Most of what was aimed for **got built**: motion-as-storage, no disk footprint,
death-with-process, tiny *process* footprint via `splice()`, near-zero cost for
*generated* data. Those are real and measured.

The single thing that doesn't yield is: **keeping arbitrary (non-formula) data
retrievable for less RAM than the data's own size.** That specific claim is
blocked by information theory (you can't represent N distinct possible files in
fewer than N distinct states), not by any tooling limit. Everything *around*
that — where the bytes live, whether they rest, what an inspector sees, whether
they survive a crash — is fully in play and mostly already working.

## The genuinely valuable, real product here

Not "storage with no hardware." Rather:

> **Ephemeral, motion-based, disk-free storage whose footprint is invisible to
> process inspection and which vanishes completely when the process ends.**

Real uses: encryption keys / session secrets held only in motion; sensitive data
that must leave no disk trace and be unrecoverable after shutdown or seizure;
data whose presence is hidden from inspection of the owning process. All of that
is built and measured above.

## Files

- `anonymous_mmap_storage.c` — motion between two anonymous regions
- `bounce_mover.c` — sustained bounce, duration-based
- `splice_mover.c` / `full_picture.py` — the ~2.7 MB-process / 1 GB result
- `generate_on_demand.py` — 1 GB from a seed in ~4 MB (formula data only)
- `breaking_boxes.py` — 3 rotating 1 MB boxes, cheap flow
- `retrievable_test.py` — the proof that retrievable = full size in RAM
