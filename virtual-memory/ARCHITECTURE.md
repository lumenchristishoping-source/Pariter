# How Virtual Storage actually works — step by step

This is the plain-language map of everything built and tested. If you
read nothing else, read this file.

## The starting idea

Store a file without ever writing it to disk. Keep it in RAM only,
broken into pieces, moving, so that when the process dies, the file
is unrecoverable. That's HANDBOOK.md's 5 steps:

1. **Split** — cut the file into `content` (the real information),
   `structure` (the file format's own wrapper), `metadata` (name,
   size, a fingerprint).
2. **Compress** — shrink each piece.
3. **Fall** — keep each piece moving in RAM, never sitting still.
4. **Retrieve** — pause, hand over a copy, resume.
5. **Death** — process ends, RAM is freed, gone.

Everything below is what got added on top of that basic idea, in the
order it was actually built and tested — including the parts that
didn't work, because those results shaped what got built next.

---

## Part 1 — the basic system (`splitter.py`, `falling_box.py`, `system.py`)

`VirtualStorage.save(path)` splits a file, compresses each piece, and
hands each one to a `FallingBox` — two buffers (`box_a`, `box_b`) that
bounce a piece back and forth, non-stop, in a background thread.
`retrieve()` pauses, copies out the current buffer, decompresses,
resumes.

**Tested and confirmed:** real files (PDFs, DOCX, JSON, code, maps),
byte-perfect every time, real compression on text (up to 7.8x on a
real book), near-1x on already-compressed formats like images (that's
physics, not a bug).

**Also tested and disproven:** the idea that this *motion* protects
the data from someone reading the process's memory. It doesn't — see
Part 2.

---

## Part 2 — the forensics tests, and why plain motion failed

Built 3 real attacks against a `FallingBox` holding a marker: a full
memory dump, a monitor watching one fixed address, and a slow
chunk-by-chunk read. **All three found the marker 100% of the time.**

Why: the motion never changes the data's *value* — it just copies the
same bytes to a new address, over and over, without deleting the old
copy first. At any instant, at least one of the two buffers holds a
complete, valid copy. Moving something without destroying the old
copy doesn't hide it — it's like moving a folder to the next desk
over. Whoever checks either desk still finds it.

Also tested "what if there were more than 2 hiding spots" (up to 64)
— still 100% found, for an even more decisive reason: since nothing
ever gets erased, every spot the data ever visited keeps a valid copy
forever. More hiding spots without cleanup just means more copies
lying around, not better hiding.

**Conclusion that shaped everything after this:** motion alone is not
security. Real security needed real cryptography and real OS-level
protections. That's Part 3 onward.

---

## Part 3 — Tier 1: OS-level hardening (`secure_falling_box.py`)

Three real, testable protections, layered onto the falling motion
(which stays — this doesn't replace it):

- **`mlock()`** — the OS is never allowed to swap these pages to disk.
  Confirmed: locked memory actually increases, checked in `/proc`.
- **`MADV_DONTDUMP`** — excludes the memory from a crash's core dump
  file. Confirmed via the memory region's own flags in `/proc/self/smaps`.
- **`PR_SET_DUMPABLE(0)`** — blocks a same-user process from
  `ptrace`-attaching and reading memory live. (Root generally bypasses
  this — noted honestly, not hidden.)

Found and fixed 2 real bugs building this: `MADV_DONTDUMP` failed
outright on a plain Python `bytearray` (not page-aligned — fixed by
switching to `mmap`, which always is), and `snapshot()` returned
immutable `bytes`, so a caller could never actually zero their own
retrieved copy — fixed to return a mutable `bytearray` everywhere.

---

## Part 4 — Tier 2: real encryption (`encrypted_falling_box.py`)

Instead of holding plaintext and moving it, hold **ciphertext**
(AES-GCM) and re-encrypt under a **freshly ratcheted key** every
single hop — the old key is destroyed the instant the new one exists.

Reran the exact same 3 attacks that beat plain motion: **all three
now come back 0% found.** There's no plaintext sitting anywhere to
find anymore.

**Tested and found the one honest remaining limit:** catching the
ciphertext *and* its matching key at the exact same instant still
works — that's unavoidable for anything currently in active use.
What ratcheting *does* kill is every other copy: old ciphertext in
swap, a stale page, an earlier crash dump — all permanently
undecryptable once the key has moved on.

Also tested and confirmed: dropping from 2 buffers to just 1 (your
"falls between nothing and nothing" question) works and is *better*
— no second buffer means no moment where two live copies exist at
once.

---

## Part 5 — splitting the key itself (`split_key_guard.py`)

The AES key doesn't sit as one 32-byte block. It's compressed (which,
honestly, makes it *bigger* — 32 bytes → 88, because a real key looks
exactly like random noise and compression can't do anything with
that) and split byte-by-byte into 88 separate cells, each bouncing
between its own two locked memory pages, each on its own independent,
unsynchronized timer.

This is a **different kind of split** than the earlier "many falling
boxes" idea, and it's why it actually works: the file-motion idea
failed because every copy was a *duplicate* — any one was enough. This
one is a genuine *split* — no single cell is useful alone, and even
one wrong byte out of 88 breaks reconstruction completely (tested:
LZMA decompression fails outright with 1 byte off).

Originally each cell had its own thread — 88 threads. Found this
caused real problems under load (see Part 7) and fixed it: one shared
scheduler thread now services all 88 cells at their own independent
times, keeping the 88 separate protected addresses (the part that
matters) without 88 separate threads (the part that was expensive).

---

## Part 6 — putting Tier 1 + Tier 2 + the split key together (`combined_secure_box.py`)

One class, `CombinedSecureBox`, that is all of the above at once: one
ciphertext buffer, locked and hidden from dumps, ratcheted every hop,
its key scattered across 88 independently-timed cells.

**Tested together, not just separately:** correctness held, the
forensics attacks still found nothing, and reconstructing the key
from 87 of 88 correct bytes (1 wrong) failed outright — real proof
the "need all pieces" property survives integration.

---

## Part 7 — the real costs, found by testing, not assumed

- **A 50MB file broke it.** Whole-buffer re-encryption on every hop
  meant multiple full-size copies existing at once — RAM jumped
  +332MB, hop rate collapsed to 1 every 2 seconds. Fixed two ways:
  **pacing** (`hop_interval` — small files stay instant, big ones get
  a sane, size-based pause) fixed the collapsing hop rate, and
  **chunking** (`chunked_secure_box.py` — split the file into 256KB
  pieces, touch one per hop instead of the whole thing) fixed the RAM
  cost too: the same 50MB file dropped from +332MB to +52MB, bounded,
  confirmed flat over 10 seconds and 7,500+ hops.
- **A mysterious ~65MB RAM ceiling**, traced (not guessed) to the
  `cryptography` library's own internal memory pool, specifically when
  AES-GCM runs on a background thread rather than the main one.
  Confirmed bounded (never grows past its first peak) — a real, fixed
  cost of doing this in Python, not a leak.
- **The watchdog (see Part 8) was 1000x slower inside a real, busy
  pipeline** than in isolation — traced to pure GIL contention (proven
  by reproducing the same delay with 7 threads doing nothing but
  counting). Fixed by making the watchdog a separate OS process
  instead of a thread (Part 8).

---

## Part 8 — detecting an attack: `process_watchdog.py`

**Your question — "did you implement kill the process at full dump" — the honest, complete answer:**

**Yes, for the common case.** `ProcessWatchdog` runs as a real,
separate OS process (not a thread — a thread-based version was tested
and found to lose the race once real background threads were
competing for it: 50-70 microseconds alone vs 63-90 *milliseconds* in
a busy pipeline). It watches for another process attaching via
`ptrace` (`TracerPid` changing) — the mechanism every debugger and
most memory-dumping tools actually use. The instant it sees that, it
reaches directly into the target's memory and zeros every registered
region **itself**, then kills the process. Tested against a real
attack, with the target under real load: detected and wiped in
97-520 **microseconds**, every trial, attacker got zero real reads.

**No, for the case that matters most.** Tested directly: a
root-privileged process can open `/proc/<pid>/mem` and read it
successfully **with no `ptrace_attach` call at all**, and `TracerPid`
never changes. This is completely invisible to the watchdog — not a
speed problem, a *detection* problem. There is no signal the kernel
gives the target process when this happens; nothing to catch. This is
not solved by making the watchdog faster or smarter. It's a structural
property of the Unix privilege model: root can read any process's
memory on that machine, by design.

Checked for a system-level fix (SELinux/AppArmor, which *can* in
principle restrict this even for root): this specific sandbox has
neither installed — no LSM at all, confirmed via
`/sys/kernel/security/lsm` not existing. Not solvable in software
alone here.

---

## Part 9 — the real answer to the unfixable case: `shamir.py` + `distributed_key.py`

Since no code running on one machine can hide from that machine's own
root, the fix is to make sure **no single machine ever holds the
whole secret**. Real Shamir's Secret Sharing, built from scratch (no
library existed): split a key into N pieces such that any K
reconstruct it, but fewer than K reveal **provably zero information**
— tested by checking that with K-1 pieces, all 256 possible values
for a secret byte remain equally valid, not just "hard to guess."

Wired into the ratchet itself (`CombinedSecureBox(trust_group=...)`):
every hop now optionally mixes in a secret freshly fetched from K of
N separate processes, instead of ratcheting from purely local state.

This closed a real gap found while building it, previously untested:
the old ratchet was a deterministic hash chain, so a key captured
**once** let an attacker compute every *future* key too, forever, with
no further access needed — proven directly (a captured key hashed
forward 5 times locally matched the real key exactly). With
distributed trust on, that same prediction fails — proven the same
way, side by side.

**Real, honest cost:** roughly half the hop rate (18/sec vs 30-45/sec)
— the price of needing live network round-trips instead of local math.

---

## Part 10 — the actual, real pipeline you use: `secure_system.py`

Everything above, tied into one class with the same shape as the
original `VirtualStorage`:

```python
from vstorage.secure_system import SecureVirtualStorage

vs = SecureVirtualStorage(
    watch_for_tampering=True,      # ProcessWatchdog on (default)
    kill_on_tamper=True,           # actually kill on detection (default)
    use_distributed_trust=False,   # Shamir key rotation (opt-in, real cost)
)
file_id = vs.save("report.pdf")    # split, encrypt, protect, watch
text    = vs.retrieve(file_id, "content")
full    = vs.retrieve(file_id, "full")
vs.forget(file_id)                 # wipe just this file
vs.collapse_all()                  # wipe everything, stop the watchdog
```

`save()` splits the file, wraps each of the 3 pieces in a
`CombinedSecureBox`, and registers every key cell's address with the
one shared watchdog process. Tested end-to-end on a real 49-page PDF:
byte-perfect, and a real `ptrace` attack against the whole pipeline
got every region verified wiped from *outside* (not just trusted on
the process's own say-so — the attacking process's own `SIGSTOP`
freezes it, so it can't even report on itself; had to check from
outside, the same way a real attacker would look).

**Not yet wired in:** `ChunkedSecureBox` for large files (still a
separate, tested module — `secure_system.py` currently uses the
whole-buffer version, fine up to a few MB).

---

## The honest, one-paragraph summary

Data at rest is genuinely encrypted, not just moved. Its key can't be
reconstructed from a partial capture, and old key material can't be
recovered from a newer one, or vice versa (with distributed trust on).
The system detects and kills itself the instant a real debugger or
memory-dump tool attaches — in under a millisecond, even under load.
The one thing nothing here can stop is a fully privileged reader who
specifically avoids the one detectable signal (`ptrace_attach`) — and
the honest fix for *that* isn't cleverer code, it's making sure that
even if they succeed, no single machine ever hands them the whole
secret.
