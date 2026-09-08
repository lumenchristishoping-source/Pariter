# Virtual Storage

A software-only, RAM-based, ephemeral storage system - no disk
dependency. Files are split, compressed, and kept in continuous motion
through small falling boxes in anonymous memory, never written to disk.

On top of that, a hardened security layer: real AES-GCM encryption
(not just motion), an OS-level lockdown (no swap, no crash dumps), a
separate-process watchdog that detects and kills on a real attack, and
Shamir's Secret Sharing so no single machine ever holds a whole key.

**Start here: [`virtual-memory/ARCHITECTURE.md`](virtual-memory/ARCHITECTURE.md)**
- step-by-step, plain-language walkthrough of how all of it actually
works, including what was tried and disproven along the way.

See `virtual-memory/HANDBOOK.md` for the original design, and
`virtual-memory/REBUILD_STATUS.md` for the full, honest test log.

---

## Benchmarks & Accomplishments

Every number below is from a real run against the actual code path a
caller uses (not a lower-level primitive tested in isolation), on real
files, with retrieval verified byte-perfect via full SHA-256 comparison.
Full detail and raw logs for all of these: `virtual-memory/REBUILD_STATUS.md`.

### A 12GB file, held at ~292MB

A single real 12GB markdown file, streamed in chunk-by-chunk
(`ChunkedSecureBox.from_file()`), compressed, encrypted, and kept
continuously falling - on a sandbox with only ~15GB total RAM and
**no swap at all**.

| | |
|---|---|
| Peak RAM to hold it | **~292MB (41x smaller than the file)** |
| Steady state, 8s sampled | flat, ±8KB, while still hopping 12,663+ times |
| Safety abort (2.5GB ceiling) | never came close to triggering |
| Build time | 16.5 min (real LZMA compression, not a shortcut) |

### A 5GB file, through the real save() front door

Not a lower-level primitive - the actual `SecureVirtualStorage.save()`
call a caller uses, proving the fix that made this possible: `save()`
used to need roughly a file's own size in RAM just to *start*.

| | |
|---|---|
| Peak RAM during save | **~195MB (26x smaller than the file)** |
| Save time | 153.45s (real compression + encryption) |
| Correctness | byte-perfect |

### 3GB, streamed back out two ways - plain and fully encrypted-path

Retrieval had the identical problem in reverse (build the whole file
in RAM before handing it back) - fixed with streaming output, then
extended to a version where the destination itself never sees
plaintext at all, only re-encrypted ciphertext.

| | Plain streaming out | Fully encrypted-path out |
|---|---|---|
| RAM while holding it | 91.7 MB | 91.8 MB |
| RAM while streaming the ENTIRE 3GB back out | **93.8 MB (+2.1MB)** | **94.4 MB (+2.6MB)** |
| Time | 130.55s | 165.68s (one real extra AES-GCM pass per chunk) |
| Destination ever contains plaintext? | yes (by design - it's the file) | **no - confirmed directly, searched, not found** |
| Correctness | byte-perfect | byte-perfect after unwrapping |

### 24 real files at once (~2GB total: md, txt, json, geojson, csv, log, py, pdf, docx, zip)

Every file type the system recognizes, held and falling
*concurrently* - the test that found (and fixed) the real cost of
scaling to many files at once.

| | Before the fix | After the fix | Change |
|---|---|---|---|
| Background threads | 217 | **3** | 72x fewer |
| Save all 24 files | 55 min | **20.5 min** | 2.7x faster |
| Retrieve one file back | 153.6s | **16.3s** | 9.4x faster |
| All 25 retrievals (24 + a restored copy) | never finished | **100% byte-perfect** | - |
| Real cross-process attack trials (3, external process) | - | **0% detection** | - |
| Watchdog reaction to a real `ptrace_attach` | - | **151-271ms, real SIGKILL** | - |

---

`pariter/` holds an earlier, separate project (not part of Virtual
Storage) - kept together in its own folder.
