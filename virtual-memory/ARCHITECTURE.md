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

**Since superseded:** `CombinedSecureBox` is where all 3 pieces first
came together and proved out — but the whole-buffer re-encryption
underneath it is exactly what broke on a 50MB file (Part 7). Its
direct successor, `ChunkedSecureBox`, kept every property proven here
(ratcheted key, split-cell protection, forensics-clean) while fixing
the RAM cost, and is what the real pipeline (Part 10 onward) actually
uses today. `combined_secure_box.py` still lives in `vstorage/
superseded/` — kept, not deleted, as the tested proof this design
worked before it was made to scale.

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
    trust_mode="local",            # or "network" - see Part 14
)
file_id = vs.save("report.pdf")           # streams straight from disk (Part 11)
vs.retrieve_to_file(file_id, "out.pdf")    # streams straight back out (Part 11)
vs.forget(file_id)                        # wipe just this file
vs.collapse_all()                         # wipe everything, stop the watchdog
```

`save()` splits the file (streaming in via `ChunkedSecureBox.from_file()`
when the piece mirrors a real file on disk — Part 11, not the whole
thing read into RAM first), wraps each of the 3 pieces in a
`ChunkedSecureBox`, and registers every key cell's address with the
one shared watchdog process. Tested end-to-end on a real 49-page PDF
and, at real scale, a real 12GB file (~292MB peak RAM) and 24 mixed
real files held concurrently: byte-perfect, and a real `ptrace` attack
against the whole pipeline got every region verified wiped from
*outside* (not just trusted on the process's own say-so — the
attacking process's own `SIGSTOP` freezes it, so it can't even report
on itself; had to check from outside, the same way a real attacker
would look).

---

## Part 11 — streaming both directions: no more "the whole file has to fit in RAM first"

`ChunkedSecureBox` (Part 7's fix for the 50MB-file problem) always
held the file cheaply once it was in - but two real gaps stayed open
until this was pushed further:

- **Saving still needed the file's own size in RAM just to *start*.**
  `splitter.py`'s `split_file()` used to call `open(path).read()`
  before any of the cheap-to-hold chunking machinery kicked in. Fixed
  with a `FromFile` marker: a piece that mirrors a real file on disk
  streams straight in via `ChunkedSecureBox.from_file()`, 256KB at a
  time, instead of being materialized first. Verified on a real 5GB
  file through the actual `save()` call a caller uses (not a
  lower-level shortcut): **~195MB peak, 26x smaller than the file.**
- **Retrieving had the identical problem in reverse.** `retrieve()`
  built the whole reconstructed file as one object before handing it
  back. `ChunkedSecureBox.stream_to()` / `SecureVirtualStorage.
  retrieve_to_file()` decrypt one chunk at a time straight to a
  destination file instead. Verified on a real 3GB file: RAM moved
  from 91.7MB (holding it) to 93.8MB while streaming the *entire*
  file back out - not a multi-GB spike.

**Honest scope, not a bigger claim than earned:** streaming output
bounds how much plaintext is ever exposed at once (one chunk, not the
whole file) - it does not, and cannot, make plaintext never exist.
Retrieving a file always makes it usable somewhere, for any
encryption-at-rest system; that's what "retrieve" means.

---

## Part 12 — three ways to hand a file back out: `retrieve_to_destination(trust=...)`

Plain streaming (Part 11) still writes raw plaintext straight to the
destination the instant it's asked for. The real question underneath
that: how much do you trust *where* it's going? Built as three
distinct, selectable options rather than one merged answer:

- **`trust="trusted"`** — plain streaming, as above. The destination
  IS the readable file; that's the point.
- **`trust="untrusted"`** — `stream_to_wrapped()` decrypts each chunk
  and immediately re-encrypts it under a fresh transit key before it
  ever reaches the destination, so the file on disk there is never
  plaintext, confirmed directly (searched the wrapped file's bytes for
  the original plaintext - not found, across the whole file). The key
  needed to unwrap it is released only after `os.fsync()` confirms the
  bytes are durably written - not the instant the write call returns.
- **`trust="hostile"`** — same wrapped output, but instead of handing
  over the key at all, issues a single-use, time-boxed *token*
  (`DEFAULT_TOKEN_TTL = 60s`). The real key is only released by a
  separate `redeem_key(token)` call, and only once, before it expires.

**The honest limit underneath all three, stated plainly:** "the
process dies and everything with it" only covers what's still *inside*
the process boundary. A key that has already been handed to a caller -
released, returned, in their hands - has left that boundary. No
process dying afterward can revoke it retroactively. That's not a gap
in this design; it's what "handing over a key" means, in any system.

---

## Part 13 — watching the people you trust: the distributed-trust holders finally get a watchdog

Part 9's `DistributedTrustGroup` split the root secret across N real
OS processes - but a direct review found those N processes had **zero
tamper detection wired to them, anywhere.** A real `ptrace_attach` on
a holder produced no reaction at all, from anything. Two real fixes:

- **Each holder now gets its own `ProcessWatchdog`.** It can't spawn
  one itself - it's a multiprocessing daemon process, and Python
  refuses to let a daemon process have children (`AssertionError:
  daemonic processes are not allowed to have children`, hit directly).
  So the holder reports its own memory address back over its pipe, and
  the *parent* stands the watchdog up from the outside, pointed at the
  holder's pid.
- **A monitor thread reacts to a holder dying** outside a clean
  `stop()` call - but not by killing everything on the very first one.
  This system is RAM-only: killing the main process always means
  total, permanent loss of whatever file it's holding, and one
  compromised holder, alone, gives an attacker **zero** usable
  information (k=3 needed - proven directly: with k-1 captured shares,
  all 256 possible byte values remain equally consistent, not just
  "hard to guess"). So `kill_threshold` defaults to `k-1` - react one
  theft away from an attacker actually succeeding, not on the first
  one. `kill_threshold=1` restores the old instant-kill posture,
  `kill_threshold=0` disables auto-kill entirely.

Verified with real `ptrace_attack`s: an attacked holder dies in
6-60ms; tolerating 1-of-5 compromised holders below the threshold
keeps the system working (`fetch()` still succeeds from the remaining
4); 2-of-5 (meeting the default threshold) kills the main process, the
same way it always should have.

A genuine Linux ptrace quirk turned up while verifying this, worth
recording: a naive liveness check (`Process.is_alive()`, which calls
plain `os.waitpid()`) never sees a holder killed by a THIRD PARTY
attacker as dead - the kernel routes that stop/exit notification to
the attacker first, and the attacker has no reason to ever call
`wait()` on a process it doesn't own. Fixed by reading
`/proc/<pid>/stat`'s state field directly instead, which reflects real
kernel state regardless of who has or hasn't reaped it.

---

## Part 14 — holders on real separate machines: `network_trust.py` + `holder_server.py`

Part 9 and Part 13's holders are honest that they only "simulate N
separately-trusted machines" - real OS processes, but all under the
SAME kernel. A single root breach on that one machine could reach
every holder's `/proc/pid/mem` in turn, one after another - the k-of-n
math never even came into play, since one compromise got everything.
That gap is closed here for real, not just narrated around.

`holder_server.py` is a standalone `python3 -m vstorage.holder_server`
process - config arrives over stdin, never argv (which any local user
can read via `ps`). Not being a multiprocessing daemon child, it CAN
spawn its own `ProcessWatchdog` directly, no parent-side workaround
needed. It serves its share over a real TLS socket (a fresh 2048-bit
self-signed cert per group, client-pinned) authenticated with a shared
bearer token, checked constant-time.

`network_trust.py`'s `NetworkTrustGroup` is the client side - same
shape as `DistributedTrustGroup` (`k`, `n`, `fetch()`, `stop()`,
`kill_threshold`), wired in as `SecureVirtualStorage(trust_mode=
"network", trust_host=...)`. Point `trust_host` at a real remote
address and this is a genuine multi-machine deployment, unchanged -
what makes today's tests "only" localhost is the sandbox having one
machine available, not anything in the protocol.

**The one real architectural difference, stated plainly:** there is
no shared `/proc` across actual separate machines, so a dead holder
can't be seen by reading its kernel state directly anymore. Detection
became heartbeat-based instead - the client pings every holder over
its TLS connection and treats an unreachable one exactly like a
locally-dead one, same `kill_threshold` reaction. Verified with the
same real-`ptrace_attack` and tolerance test shapes as local mode:
attacked holder server self-protects and dies in ~10ms, main process
reacts in ~10-25ms, purely from the dead connection - no `/proc`
access to the holder used or needed anywhere in that path.

---

## The honest, one-paragraph summary

Data at rest is genuinely encrypted, not just moved, and streams in
and back out without ever needing to fit whole in RAM. Its key can't
be reconstructed from a partial capture, and old key material can't be
recovered from a newer one, or vice versa (with distributed trust on).
The system detects and kills itself the instant a real debugger or
memory-dump tool attaches - in under a millisecond for the main
process, tens of milliseconds for a distributed-trust holder, even
under load - and now reacts with a tunable threshold instead of
overreacting to a single, mathematically harmless compromise. The one
thing nothing here can stop is a fully privileged reader who
specifically avoids the one detectable signal (`ptrace_attach`) - and
the honest fix for *that* was never cleverer code, it's making sure
that even if they succeed, no single machine ever hands them the whole
secret. That promise is now real, not just simulated: holders can run
on genuinely separate machines, over real authenticated network
connections, so one root compromise reaches exactly one share, not all
of them.
