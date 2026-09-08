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
