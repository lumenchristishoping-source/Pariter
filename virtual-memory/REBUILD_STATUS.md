# Rebuild status — independently verified against HANDBOOK.md

Everything below was actually run in this session, not assumed from the
handbook's numbers. Where a number differs from the original, that's noted
plainly rather than smoothed over.

## Core system (`vstorage/`) — `demo.py`

- 3 real files (JSON, Python, prose): **18.8x–84.7x compression**, all
  **byte-perfect** on retrieval (SHA-256 verified).
- Disk writes: confirmed **exactly 0** for save+retrieve+collapse, isolated
  three separate ways. (A first run showed 4096 bytes; traced to `print()`
  output itself registering as disk I/O in this sandboxed session — nothing
  to do with the storage system. Proved by printing 9 lines with zero
  storage activity in between: same 4096 bytes.)

## `splice_stealth.py` — Experiment 5

- **200MB** streamed through `splice()` chunk-by-chunk (read progressively
  from `/dev/shm`, never loaded as one Python object first — that would
  have defeated the test before it started).
- Process RSS: **flat at ~8.7–8.8MB throughout** (min/avg/max nearly
  identical — no creep). System RAM moved only ~15.7MB, not 200MB.
- Separately verified byte-perfect fidelity via a full round-trip
  (`splice_move()`) on a 1.28MB deterministic payload — SHA-256 match.
- Numbers differ from the handbook's own Experiment 5 (2.7MB process RSS,
  +1045MB system RAM for 1GB) — plausibly because this version discards
  each chunk immediately at the far end rather than the original's method;
  not a discrepancy investigated further, just noted honestly.

## `self_healing.py` — Experiments 6-7

- **v1 (vulnerable)**: depth 3, alternating inner/outer failures — data
  lost after 2 failures. The vulnerability is real and easy to trigger,
  matching the handbook's description of it.
- **v2 (fixed)**: **5,000 consecutive failures, zero data loss, SHA-256
  match every time** — exactly the number the handbook reports.

## PDF/DOCX splitting (`test_pdf_docx.py`) — Experiment 17

Real files generated fresh (via `reportlab` and `python-docx`), not
sourced from anywhere:

- **PDF**: text content correctly extracted as the `content` piece,
  full binary preserved as `structure`. Byte-perfect full reconstruction.
  Content-only retrieval returns just the extracted text, matching.
- **DOCX**: `word/document.xml` correctly extracted as `content`, full
  zip container preserved as `structure`. Byte-perfect full
  reconstruction. Content-only retrieval matches.

## `vstorage_c/anon_bounce.c` — the raw primitive underneath everything

- Two `MAP_ANONYMOUS | MAP_PRIVATE` regions (no file descriptor, no path),
  data bounced between them continuously.
- **50MB bounced 706 times in 3 seconds** (235 hops/sec), byte pattern
  verified intact across every hop.

## `layer_streaming.py` — Experiment 19 (model layer-streaming)

- 12-layer model (matching the handbook's "12-layer model" framing),
  quantized 32-bit → 8-bit + LZMA compressed: **192MB raw → 40.66MB held
  (4.72x)** — close to the handbook's own "4.5x via quantization +
  compression."
- RSS during `run()` (decompress → use → free, layer by layer): **flat
  at 56.36MB across all 12 layers** — matching almost exactly `baseline
  + total held size` (14.8MB + 40.66MB ≈ 55.4MB), meaning each layer's
  cycle adds zero net residual, not growing with layer count.
- Getting to that clean number required real diagnosis, not just
  reporting the first number: an initial run showed RSS oscillating
  35–75MB per layer. Traced to a well-known glibc malloc quirk (dynamic
  mmap threshold retaining freed arenas across repeated large alloc/free
  cycles) — confirmed by explicit `malloc_trim(0)` calls flattening it
  immediately. Not a leak in this code; a documented allocator behavior,
  now handled explicitly (`_release_freed_memory()`).

## `secrets_api.py` — section 14's product layer

The four things the handbook names as missing, all built and tested:

- **API surface**: `store(key, value, token)` / `retrieve(key, token)` -
  same shape as a Redis or Vault call.
- **Auth**: retrieval by a non-owning token is correctly denied
  (`AuthError`); `share()` grants another token access without ever
  exposing the secret in the grant call itself.
- **Key rotation**: `rotate()` replaces a secret's value with no window
  where neither the old nor new value exists - verified old value is
  gone, new value retrievable immediately.
- **Replication**: `ReplicatedSecretsStore` holds secrets across N
  independent instances; killing one (`kill_replica()`, simulating a
  process crash - that replica's data collapses completely, matching
  section 4's Step 5) still serves reads correctly from the survivor.

All four verified working together in one test run: store, deny,
share, rotate, revoke, then a 2-replica crash-survival test - correct
result at every step.

---

## Security hardening arc — full walkthrough in `ARCHITECTURE.md`

Everything below has its own test file in this directory; the numbers
here are the headline results. Read `ARCHITECTURE.md` for the
step-by-step "how it works" version.

### Forensics testing (`test_forensics_motion.py`, `test_forensics_more.py`)

- Plain falling motion (2 buffers, no encryption): **100% detection**
  across 3 real attack models (full dump, fixed-address monitor, slow
  chunked acquisition) - motion alone is not security, proven not
  assumed.
- Scaling to 64 hiding spots: still 100% detected, and for a worse
  reason than expected - nothing ever gets erased, so every spot ever
  visited keeps a live copy forever.

### Tier 1 — OS hardening (`secure_falling_box.py`, `test_tier1_security.py`)

- `mlock()`, `MADV_DONTDUMP`, `PR_SET_DUMPABLE(0)` - all verified from
  `/proc`, not trusted from return codes alone.
- 2 real bugs found and fixed: `MADV_DONTDUMP` needs page-aligned
  memory (a `bytearray` isn't; switched to `mmap`), and `snapshot()`
  returned immutable `bytes` (couldn't be zeroed by a caller; now
  `bytearray` everywhere).

### Tier 2 — real encryption (`encrypted_falling_box.py`, `test_tier2_encryption.py`)

- Same 3 forensics attacks, rerun against AES-GCM ciphertext ratcheted
  every hop: **0% detection**, all three.
- Forward secrecy checked directly: an old ciphertext snapshot fails
  to decrypt under the current (ratcheted-forward) key.
- One buffer instead of two ("falls between nothing and nothing") -
  works, and removes the "two live copies at once" risk entirely.

### Split key (`split_key_guard.py`)

- AES key compressed (32B → 88B - real keys don't compress, verified)
  and split byte-by-byte across 88 independently-timed, separately
  locked cells.
- Reconstruction with 87 of 88 correct bytes: fails outright (LZMA
  error) - a genuine "need all pieces" property, unlike duplicated
  falling boxes.
- 88 threads → 1 shared scheduler thread: fixed a real RAM/CPU cost
  found under real combined load, with no loss of the security
  property (still 88 separate locked addresses).

### Real-world costs found and fixed

- 50MB payload, whole-buffer ratchet: hop rate collapsed to 1 per 2s,
  RAM +332MB. Fixed with adaptive pacing (hop rate) and chunking
  (`chunked_secure_box.py`, RAM down to +52MB, bounded over 10s/7,593
  hops).
- A ~65MB RAM ceiling traced to the `cryptography` library's own
  internal per-thread memory pool (only appears when AES-GCM runs on
  a background thread) - confirmed bounded, not a leak.

### Tamper detection (`tamper_watchdog.py` → `process_watchdog.py`)

- Thread-based watchdog: wins a race in isolation (~50-70us) but loses
  badly (~63-90ms) once real background threads compete for the GIL -
  reproduced deliberately with 7 dummy busy threads to confirm the
  cause.
- Process-based watchdog: reaches into the target's memory directly
  (`/proc/<pid>/mem` write, no `ptrace_attach` needed) instead of
  asking the target to react. Retested under the same busy-GIL
  conditions: 97-520us, wins every trial.
- Wired into `secure_system.py`: every held file's key cells and data
  buffers registered with one shared watchdog. Verified against a real
  attack from OUTSIDE the target (the target's own main thread freezes
  the instant `ptrace_attach` lands via its own `SIGSTOP`, so it can
  never confirm anything about itself) - real key material before,
  all zero after.
- **Honest, unfixed limit**: `TracerPid` (what any such watchdog
  watches) never changes if the reader skips `ptrace_attach` entirely
  - confirmed a root reader can open and read `/proc/<pid>/mem`
  directly with no attach call and no detectable signal at all. Not a
  speed problem - nothing to catch.

### SELinux/AppArmor investigated, real dead end here

- Checked before attempting anything: no LSM present in this sandbox
  at all (`/sys/kernel/security/lsm` doesn't exist, no AppArmor
  module, empty SELinux pseudo-fs, capability set nearly full). Not a
  theoretical dead end - confirmed for this specific environment.

### Distributed trust (`shamir.py`, `distributed_key.py`, `test_shamir_secret_sharing.py`, `test_distributed_trust.py`)

- Real Shamir's Secret Sharing from scratch (GF(256), no library
  available). Found and fixed a real bug in the field arithmetic
  first: the log/exp tables were generated assuming 2 is a primitive
  element - verified it isn't (order 51, not 255); fixed using 3
  (verified primitive).
- Security property proven, not assumed: with k-1 shares, all 256
  possible values for a secret byte are equally consistent (256/256) -
  provably zero information, not "hard to guess."
- Real processes: 5 separate OS processes, one share each. Fully
  compromising 2 of them (root, direct memory read, no attach - same
  attack that beat everything else) yields garbage; legitimate 3-of-5
  reconstruction works.
- Wired into `CombinedSecureBox`'s ratchet as `trust_group=`, exposed
  on `SecureVirtualStorage(use_distributed_trust=True)`.
- Closed a real gap found while building this: the old local-only
  ratchet is a deterministic hash chain, so a key captured once
  predicts every FUTURE key too (verified: hashed forward 5 times
  locally, matched the real key exactly). With distributed trust on,
  that same prediction fails - proven side by side, same test.
- Real cost: hop rate roughly halves (18/sec vs 30-45/sec) - the price
  of live round-trips instead of local math.

### The unified pipeline (`secure_system.py`, `test_secure_pipeline_e2e.py`)

- `SecureVirtualStorage`: same `save()`/`retrieve()`/`forget()`/
  `collapse_all()` shape as the original, every piece hardened by
  everything above, one shared `ProcessWatchdog` for the whole system.
- Tested end-to-end on a real 49-page PDF: byte-perfect, PDF re-opens
  and reads correctly, real attack detected and wiped, verified
  externally.
- Not yet wired in: `ChunkedSecureBox` for large files (tested
  separately, works, just not the default path yet).

### Streaming ingestion + the 12GB stress test (`chunked_secure_box.py`'s `from_file()`, `test_12gb_stream.py`)

The open question was simple: can this system actually hold a real,
large file - not a synthetic in-RAM `bytes` object - without needing
2x its size in RAM just to start? `ChunkedSecureBox.from_file()` was
built to answer that: it reads a file chunk-by-chunk (default 256KB)
straight off disk, compressing and encrypting each chunk as it goes,
never holding the whole file as one Python object.

Tested against a real 12GB markdown file (12,885,321,036 bytes, real
structured text, not random bytes) in a sandbox with **no swap at
all** and only ~15GB total RAM. Retrieval/rebuild was deliberately
skipped for this run - that alone would need to hold the full ~12GB
plaintext, and the point of this test was ingestion + steady-state
cost, not retrieval cost (retrieval cost is already known from
earlier, smaller tests and doesn't change in kind at this size).

Every form of RAM tracked via `/proc/<pid>/status` and
`/proc/meminfo` at every stage, run in a safety-monitored child
process (auto-killed if it ever crossed 2.5GB, as a guard against
trusting the compression-ratio estimate blindly):

| Stage | RssAnon | VmRSS | Notes |
|---|---|---|---|
| Before touching the file | 13.1 MB | 25.1 MB | baseline |
| After building (ingest + compress + encrypt all 49,154 chunks) | 291.7 MB | 303.9 MB | took 991s (~16.5 min) - real LZMA preset=6, not a shortcut |
| Steady state (8 samples, 1s apart, falling the whole time) | 291.7-291.7 MB (flat, 8KB spread) | 303.9-304.0 MB (flat) | 1,406 → 12,663 hops during the window - real continuous motion, not stalled |
| After collapse (`box.collapse()` + `gc.collect()` + `malloc_trim(0)`) | 36.7 MB | 49.1 MB | clean release, close to baseline |

- **Peak RAM for a 12GB file: ~292MB — about 41x smaller than the
  source file.** That number is bounded by chunk size and chunk
  count, not file size, so it doesn't grow if the file were 50GB or
  500GB - only the time to ingest it would.
- Safety abort (2.5GB) never triggered - actual peak was ~8.5x below
  that line.
- System-wide `MemAvailable` was actually **higher** after the run
  than before (+32MB) - confirms nothing leaked into the system
  either, not just that this one process released cleanly.
- The only real cost at this scale is **time** (16.5 minutes, at the
  real shipped compression preset), not memory risk.
- Honest gap this test exposes, not fixed here: this bypassed
  `splitter.py` entirely and called `ChunkedSecureBox.from_file()`
  directly. The real `SecureVirtualStorage.save()` path still routes
  through `splitter.py`'s `split_file()`, which does a full
  `open(path).read()` first - so the *actual* front door isn't yet
  safe at this scale for a text-like file, only this
  lower-level primitive is. Wiring a streaming path through
  `splitter.py` is the next real step (see `TODO.md`).

### The "ultimate test" - 24 concurrent files, ~2GB, and the real cost it found (`test_ultimate_multifile.py`)

Everything above tested ONE file at a time (even the 12GB one). This
test asked a different question: what happens when the system holds
MANY real files at once, through the real `SecureVirtualStorage`, not
a lower-level primitive? 24 files (md, txt, json, geojson, csv, log,
py, pdf, docx, zip - every branch `splitter.py` has), ~2.0GB total,
generated as real content (not random bytes), 3 with unique markers
planted in their plaintext for later attack trials.

**First run (before any fix) - stopped intentionally partway through:**
save phase completed in full (24/24, all correct) but took **3,287s
(~55 minutes)** - and got SLOWER as more files piled up (14s for the
1st file, 464s for the 22nd), regardless of file size. 217 background
threads by the end. Retrieval was, in places, even slower than saving
(`data_1.json`, 190MB: 444s). Root cause found by inspection, not
guessing: every file spawned 6 key-guard-scheduler threads + 3
falling-motion threads (9/file), all competing for Python's one GIL.
Killed deliberately once the pattern was clear and the fix was
understood, rather than waiting ~2.5 hours for a result already known.

**The fix** (`vstorage/chunked_secure_box.py`, `vstorage/split_key_guard.py`):
every guard and every falling box now registers with ONE process-wide
shared scheduler each (`_GlobalCellScheduler`, `_GlobalFallScheduler`)
instead of spawning its own thread. Same security property - every
cell/chunk still separately hidden and independently timed - just
serviced by 2 background threads total instead of hundreds. Verified
first with a direct 6-file old-vs-new comparison on identical files:
threads 55 -> 3, retrieve time 181.5s -> 84.4s (>2x), both fully
correct. Also found and fixed a real robustness gap along the way: a
live tamper response (the watchdog zeroing a box's memory mid-hop)
threw an uncaught exception inside the new shared thread during
`test_secure_pipeline_e2e.py` - harmless with one thread per box, but
with ONE shared thread for the whole process, that exception would
have silently stopped falling motion for every OTHER file too. Both
schedulers now catch per-item exceptions and keep servicing everyone
else.

**Second run (with the fix) - completed in full, ~29.6 minutes total:**

| Phase | Before | After | Speedup |
|---|---|---|---|
| Save all 24 files | 3,287s | **1,230s** | 2.7x |
| Mid-run retrieve (1 file) | 153.6s | **16.3s** | 9.4x |
| Send it back in | 261.3s | **53.2s** | 4.9x |
| Retrieve all 25 (incl. restored dup) | never finished | **467.8s** | - |
| Background threads | 217 | **3** | 72x |

- **All 25 final retrievals byte-perfect** (24 files + the one
  retrieved-and-restored duplicate), 2,079.3MB total, 0 mismatches.
- **Real attack trials, run by a genuinely separate attacker process**
  (not the target scanning itself - a first version did that and
  trivially "found" its own search strings, caught before the full
  run): 3 full memory-dump trials, 22,774 regions / 517.8MB scanned
  each, **0 of 3 markers found, every trial**.
- **The finale**: a real `ptrace_attach` from outside the process,
  same technique a debugger or memory-dump tool uses. Watchdog
  detected it and killed the process in **270,741 microseconds**,
  confirmed via a real `SIGKILL` wait status (a subtler bug caught
  first: because the attacker and the real parent process were the
  same process, the naive detection loop was catching the
  ptrace-induced `SIGSTOP` and mistaking it for the kill - fixed by
  explicitly skipping `WIFSTOPPED` results and waiting for a real
  terminal status).
- RAM: ~485MB holding all 24 files after save, peaking at ~620MB
  during full retrieval (briefly reconstructing several large files),
  for ~2GB of source data.

**The honest remaining cost, found in this same run, not smoothed
over:** the hop-concurrency check - sampled across the mid-run
retrieve-and-restore window - showed **29 of 75 boxes stalled (0 hops)**
in a 75.8s window. Traced precisely, not guessed: 69 of those 75.8
seconds were the main thread doing sustained heavy compression/
encryption work (the retrieve + re-save of `table_2.csv`), competing
directly with the one shared falling-motion thread for the GIL - and
losing. Before the fix, 75 *separate* threads meant a busy main thread
couldn't starve all of them; after the fix, one thread can be starved
completely. A real trade: foreground save/retrieve speed for
background rotation resilience under heavy concurrent load. Next step
under consideration: a small pool (3-4) of fall-scheduler threads
instead of exactly one, to recover rotation throughput without
bringing back the full per-box thread count.

### Closing the gaps found above: scheduler pool, watchdog fixes, and full streaming both ways

Four more real bugs found and fixed in direct succession, each caught
by actually running the system at scale rather than by inspection -
same discipline as everything above.

**1. Fall-scheduler starvation, reproduced and fixed.** Built a direct
repro of the 29/75-stalled scenario: 30 boxes falling, main thread
busy for 46.5s on one heavy operation. Confirmed the failure on the
single-thread scheduler, then fixed both `_GlobalFallScheduler` and
`_GlobalCellScheduler` (the identical risk existed there too) with a
small worker pool (4 each) instead of exactly one thread. Same repro
after the fix: **0 of 30 boxes stalled**, hops landing in a tight
107-110 range across all of them. Both pools also gained per-item
exception handling - a live tamper response can make a box's own hop
fail mid-flight, and with only a handful of shared threads now
servicing every box in the process, one uncaught exception would have
silently stopped falling motion for every other file too.

**2. The watchdog was watching stale addresses after every rotation.**
Found while re-testing fix #1: `SecureVirtualStorage` registered a
box's key-guard addresses with the watchdog exactly once, at save
time. Every later key rotation replaces those cells with a fresh mmap
allocation the watchdog never heard about. Proven directly: attacking
deliberately AFTER several rotations left the CURRENT key readable
2000ms later on the unfixed code - not because the watchdog failed,
but because it was zeroing an address that had since been silently
reused for unrelated live memory. Fixed with an `on_new_guard` hook,
called on every rotation, wired straight to the live watchdog. Same
attack, re-run 3x on the fixed code: wiped in **5.7-9.3ms**, every
time.

**3. That fix then grew the watchdog's region table without bound.**
Running the real 10-file test (not a synthetic one) crashed save()
outright: `RuntimeError: ProcessWatchdog region table full`, after
just 9 files (~279MB). Root cause: fix #2 registered a BRAND NEW block
of addresses on every rotation and never removed the old one - a
single small file's metadata piece (usually 1 chunk, so it rotates on
EVERY hop) burned through all 200,000 slots within minutes under the
scheduler pool's much higher throughput. Fixed properly, not by
raising the ceiling: `ProcessWatchdog.reserve_slot()`/`update_slot()`
give each box's rotating guard ONE fixed slot, overwritten in place
forever after instead of growing. A second bug caught before shipping
this fix: an early version shared one registrar closure across all 3
pieces of a file, which would have let one piece's rotation silently
overwrite another piece's watched addresses - each piece now gets its
own independent slot. Verified: watchdog region count stayed flat at
1587 as a box's hop count climbed from 8 to 13 over 4 seconds of real
rotation; the post-rotation attack (fix #2's proof) still wiped the
current key in 2.3-3.6ms across 3 more runs.

**4. Streaming, both directions, through the real front door.** The
12GB test proved `ChunkedSecureBox.from_file()` could ingest a huge
file cheaply, but only `splitter.py`'s `split_file()` - the ACTUAL
`save()` entry point - still did `open(path).read()` first, needing
roughly a file's own size in RAM just to start. Fixed with a
`FromFile` marker: text-like content and the structure piece of every
type now stream straight from disk instead of being materialized.
Verified on a real 5GB file through the real `save()` call: **~195MB
peak** (not ~5GB), all 7 file-type branches still byte-perfect.
`retrieve()` had the identical problem in reverse - building the whole
reconstructed file as one object before returning it. Added
`ChunkedSecureBox.stream_to()` / `SecureVirtualStorage.
retrieve_to_file()`: decrypt one chunk, write it straight to a
destination file, discard, repeat. Verified on the same file, scaled
to 3GB: RAM went from 91.7MB (holding it) to 93.8MB while streaming
the ENTIRE 3GB back out - a 2.1MB delta to retrieve the whole file,
not a multi-GB spike. Byte-perfect (full SHA-256 match, source vs.
retrieved copy).

Honest scope on the streaming-output piece, discussed with the user
before building it: this bounds exposure, it does not eliminate it -
retrieving a file always means it becomes usable somewhere, true of
any encryption-at-rest system, not a gap specific to this one. The
real win is shrinking what's ever exposed in the open at once from
"the whole file, for as long as the caller holds it" down to "one
chunk, briefly" - and it fully eliminates local exposure specifically
when the destination the caller streams into is itself already
protected (an encrypted volume, a secured upload target).

### The attacker-found-it investigation - not a regression, a test bug

The 10-file post-fix run's external attacker found all 3 planted
markers (`any_marker_found: true`), where the earlier 24-file run had
found none. Investigated properly rather than assumed either way:

- Two isolated repros (a single marked file scanned immediately after
  save, and 7 files saved sequentially with no retrieval at all)
  both found nothing - ruled out ingestion-time or multi-file-
  sequential residue as the cause.
- Traced the real explanation directly: located the exact memory
  address of a legitimately-retrieved file's plaintext, confirmed it
  lived in a normal `[heap]` mapping, and read it successfully three
  separate ways (address lookup, direct read, self-scan) - the
  plaintext genuinely is there, exactly as documented
  (`HANDBOOK.md`: "forms full-size at the exact instant a task needs
  it"). Cross-checked against the real run's own event timestamps:
  the attacker's scan window did overlap the test's own retrieval of
  the marked files.
- Along the way, found and fixed a real bug in the diagnostic
  scripts themselves (not the storage system): using the same
  placeholder filename for multiple markers in a test's `_markers.json`
  caused a dict-key collision that silently discarded the real marker,
  producing false "not found" results in three earlier isolated tests.

Conclusion: not a regression. The 0%-detection claim was always
specifically about data *at rest*; this confirms, correctly, that
data actively being retrieved is real plaintext in memory, which no
encryption-at-rest system can avoid.

### The actual encrypted-output path - not just streaming, genuinely encrypted the whole way

The user's response to the investigation above: since plaintext has to
exist *somewhere* the instant a file is used (true of any system,
confirmed above, not a flaw), why not encrypt the path the output
streams through, so nothing ever lands anywhere in the open? The first
streaming-output fix (`retrieve_to_file()`) only bounded HOW MUCH was
exposed at once; it still wrote raw plaintext straight to the
destination. Built the real version:
`ChunkedSecureBox.stream_to_wrapped()` decrypts a chunk from storage
and immediately re-encrypts it under a transit key (real AES-GCM,
fresh nonce per chunk) before it's ever handed to the destination -
`SecureVirtualStorage.retrieve_to_encrypted_file()` wires this to a
real file, `decrypt_wrapped_file()` reads it back for whoever holds
the transit key.

Verified two ways:
- Directly confirmed the wrapped file's bytes on disk never contain
  the original plaintext - searched for it, not found, across the
  whole file.
- Ran the identical 3GB file through both paths for a clean head-to-
  head:

| | Plain streaming | Wrapped (encrypted path) |
|---|---|---|
| Save (identical either way) | 91.7MB, 153.45s | 91.8MB, 147.09s |
| Retrieve | 93.8MB, **130.55s** | 94.4MB, **165.68s** |
| Collapse | 20.8MB | 20.8MB |

RAM: no meaningful difference (still one chunk in flight at a time
either way). Time: wrapped took **~27% longer** - one real extra
AES-GCM encrypt per chunk, honest cost, not hidden. Both byte-perfect
end to end (unwrapped copy's SHA-256 matches the original exactly).

Same honest scope as the plain version, stated plainly to the user
before and after building: this does not make plaintext never exist -
nothing can, decrypting is what makes data usable at all. What it
actually buys is that the DESTINATION itself never sees plaintext,
regardless of whether that destination is trusted - a different,
real property from "bounded exposure," not a bigger version of it.

### Distributed-trust holder processes had no watchdog at all - found by review, not by a test

The user asked directly: if a distributed-trust holder machine is
compromised, does the watchdog kill anything? Checked
`distributed_key.py` and `process_watchdog.py` directly rather than
answer from memory - the honest answer was no. `ProcessWatchdog` only
ever watches one `target_pid`, always the main process
(`ProcessWatchdog(target_pid=os.getpid(), ...)` in
`secure_system.py`). The N holder processes in `DistributedTrustGroup`
had zero tamper detection wired to them anywhere - a real
`ptrace_attach` on one produced no reaction, from anything, ever.

Fixed:
- Each holder now gets its own real `ProcessWatchdog`. It can't spawn
  one itself - it runs `daemon=True` (so it never outlives whoever
  started it), and Python refuses to let a daemon process have
  children at all (`AssertionError: daemonic processes are not
  allowed to have children` - hit this directly on the first attempt).
  Fix: the holder sends its share buffer's address back over its
  pipe right after starting; the PARENT (which can have children)
  stands up the watchdog from the outside, pointed at the holder's
  pid.
- `DistributedTrustGroup` runs a background monitor thread that
  treats a holder dying outside a clean `stop()` call as tamper
  evidence and immediately kills the whole main process
  (`kill_main_on_compromise=True`, wired to `kill_on_tamper` in
  `SecureVirtualStorage`) - fail-closed, without waiting for the next
  `fetch()` call to stumble into it.
- `fetch()` itself also treats a dead pipe (`EOFError` /
  `BrokenPipeError` / `OSError`) as compromise and raises
  `TrustGroupCompromised` - defense in depth alongside the monitor
  thread, in case the monitor hasn't caught up yet.

Building this surfaced a real, non-obvious Linux ptrace bug worth
recording in its own right: the first implementation used
`multiprocessing.Process.is_alive()` (which calls plain
`os.waitpid()`) to detect a holder dying, and it silently never
worked - `is_alive()` kept reporting the attacked holder as alive
forever, even though `/proc/<pid>/stat` showed it as a confirmed,
permanent zombie. Root cause: when a THIRD PARTY (the attacker, not
the holder's real parent) is the one who `ptrace_attach`es, the
kernel routes that stop/exit notification to the ATTACKER first. The
attacker has no reason to ever call `wait()` on a process it doesn't
own, so it never consumes that notification - and the real parent's
own `waitpid()` on the same pid then just returns "no change",
forever. Fixed by reading `/proc/<pid>/stat`'s state field directly
instead (`Z` = zombie = dead), which reflects real kernel state
regardless of who has or hasn't reaped it. Same category of gotcha as
the earlier SIGSTOP-vs-death misdetection bug in the ptrace finale
test, different mechanism.

Verified end to end with a real `ptrace_attach`, not simulated
(`test_distributed_trust_watchdog.py`), 3 runs:

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Attacked holder dies | +43.9ms | +59.9ms | +39.9ms |
| Main process dies | +67.9ms | +83.9ms | +55.9ms |
| Main ever called `fetch()`? | No | No | No |

Also confirmed: a clean `stop()` call does NOT falsely trigger the
fail-closed kill, and a real end-to-end save/retrieve through
`SecureVirtualStorage(use_distributed_trust=True)` still works and is
byte-perfect with the new watchdogs wired in.

Honest scope: this closes "a compromised holder produces zero
reaction," not "compromising fewer than k holders is dangerous" - the
k-of-n math (Part 2/3 above) already made that safe. What was missing
was any REACTION at all when a holder gets attacked, whether or not
that attack alone would have succeeded.

One more honest note, found while cleaning up after test runs: the
fail-closed `SIGKILL` to the main process is uncatchable by design -
that's the whole point, an attacker mid-exploit can't intercept and
suppress it. The cost is that the main process's OWN daemon children
(the other, un-attacked holders and their watchdogs) never get an
`atexit` chance to clean up either, and become orphans instead of
dying with it. In a real attack this is moot - the whole application
is going down anyway. It only matters for repeatedly testing this in
a sandbox, where it can leak real processes across runs (hit this
directly: ~140 orphaned, some busy-spinning, accumulated across this
session's debugging runs, cleaned up with `pkill`). Not a security
issue, just documented so it isn't a surprise later.

### Compromise tolerance - the user's own question, made a real switch

Immediate follow-up question from the user: if a distributed-trust
holder machine is compromised and the whole system gets killed, does
that mean the file is lost? And should the system really kill on the
FIRST compromised holder, or tolerate a few before reacting that
hard?

Answered honestly: yes, the file is lost either way - this system is
RAM-only, killing the main process always means total, permanent loss
of whatever it's holding, no fallback, no "it's saved somewhere
safe." Given that, killing everything over the FIRST compromised
holder is a real cost worth questioning, because (re-confirmed from
`test_shamir_secret_sharing.py` Part 2) one compromised holder alone
gives an attacker literally zero usable information about the secret
- every possible byte value is equally consistent with what they've
captured, below the k=3 threshold. So the user's instinct (tolerate
some, kill only once almost all are gone) was right, and became the
new default rather than staying a hypothetical.

Changed:
- `DistributedTrustGroup(kill_threshold=...)` - defaults to `k-1`
  (one compromise away from an attacker actually succeeding), not 1.
  `kill_threshold=1` restores the old instant-kill posture,
  `kill_threshold=0` disables auto-kill entirely (fetch() still fails
  on its own once too few holders remain alive - that part was never
  a policy choice, just math).
- `_monitor()` no longer stops after the first compromised holder -
  it keeps counting every one that goes down, so the threshold logic
  has real data to react to.
- `fetch()` was quietly broken for tolerance to even work: it always
  queried `self._conns[:self.k]`, the first k BY POSITION, not by
  liveness. A holder tolerated below the threshold would permanently
  wedge every future fetch() if it happened to be among the first k -
  fixed to use any k *currently alive* holders instead.
- `SecureVirtualStorage(trust_kill_threshold=...)` wires this through,
  kept deliberately independent from `kill_on_tamper` (the main
  process's own watchdog) - "how hard to react to MY OWN process being
  attacked" and "how hard to react to a HOLDER's zero-information
  compromise" are different questions with different right answers,
  though setting `kill_on_tamper=False` disables auto-kill on the
  trust side too by default, to match "I don't want automatic kills"
  meaning that everywhere unless overridden explicitly.

Verified with two real `ptrace_attack` scenarios, not simulated
(`test_distributed_trust_tolerance.py`):

| | Scenario A: attack 1 of 5 | Scenario B: attack 2 of 5 |
|---|---|---|
| kill_threshold (default, k=3) | 2 | 2 |
| Main process | **survived** | **died** |
| `fetch()` after the attack | **still works** (from the 4 remaining) | n/a |

Both matched the intended policy exactly. Also re-ran the original
instant-kill test (`test_distributed_trust_watchdog.py`, explicitly
passing `kill_threshold=1`) and the full Shamir suite to confirm
nothing regressed - both still pass, holder death still detected in
single-digit-to-tens of ms.
