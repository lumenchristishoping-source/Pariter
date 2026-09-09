*Shock: holding a real 20GB file continuously falling can cost as
little as ~1.99GB of RAM - and it's never written to disk (ROM/local
storage) either. See
[Benchmarks & Accomplishments](#benchmarks--accomplishments).*

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

### The hardware these numbers were measured on

Timing numbers are only useful with the machine they came from attached
- so here it is, pulled directly from the box these tests actually ran
on, not estimated:

| | |
|---|---|
| CPU | Intel(R) Xeon(R) @ 2.10GHz, **4 cores**, 1 thread/core, KVM-virtualized cloud VM |
| Cache | 192 KiB L1d, 128 KiB L1i, 8 MiB L2, **260 MiB L3** |
| Hardware crypto | **AES-NI present**, plus AVX2, AVX-512 (incl. VAES), SHA-NI - real hardware acceleration for the AES-GCM this system uses everywhere |
| RAM | 16,461,028 KB (**~16.5GB**) total, **0 swap** |
| Disk | 252GB filesystem (not that it matters - nothing here ever gets written to it) |
| OS / kernel | Ubuntu 24.04.4 LTS, kernel 6.18.44, `PREEMPT_DYNAMIC`, x86_64 |
| Sandboxing | KVM hypervisor only - a real Linux kernel underneath, not a ptrace-based sandbox (see below for why that specifically matters) |

**Rough throughput, for comparing your own hardware against this
baseline** (derived directly from the save times above/below - real
measurements, not a formula):

| File size | Save time | Rate |
|---|---|---|
| 3GB | 153.45s | ~51 sec/GB |
| 5GB | 264.55s | ~53 sec/GB |
| 12GB | 991s (16.5 min) | ~83 sec/GB |

So on this hardware, a 1GB file should land somewhere around **45-90
seconds**. If yours takes meaningfully longer, that's not necessarily
a problem with the system - it's usually one or more of:

- **A ptrace-based sandbox (e.g. `proot`, common on Android/Termux).**
  It fakes a chroot by intercepting *every syscall* through `ptrace` -
  a real context-switch tax on every read/write/crypto call this
  system makes, on top of everything else. Measured directly: the
  same workload that takes ~1 minute here took 5+ minutes inside a
  proot Ubuntu on a phone.
- **No hardware AES acceleration.** If your `cryptography` package
  ends up linked against a generic (non-accelerated) build, AES-GCM
  runs in software instead of using the CPU's AES-NI/crypto
  extensions - a real difference, not a rounding error.
- **A weaker CPU under sustained load**, especially on a phone or
  laptop that thermal-throttles during a multi-minute compression run.

### Environments where the watchdog can't detect anything at all

This is separate from the speed issue above, and more important: the
watchdog's entire detection signal is `TracerPid` in
`/proc/<pid>/status`, which the kernel only sets when something
attaches via `ptrace`. That assumption breaks completely in any
environment that ITSELF uses `ptrace` to intercept or supervise your
process - Linux only allows **one** ptrace tracer per process at a
time, so if something else already holds that slot, `TracerPid` is
non-zero from the moment your process starts, permanently, not from
an attack. The watchdog has no way to tell the difference between
"that's just how this environment works" and "someone is reading my
memory right now." Not a slower reaction - a structural absence of
signal, the same category as the root-skips-`ptrace_attach`
limitation documented below.

**Confirmed broken, tested directly:**
- **`proot`** (how most people get a real Linux distro - Ubuntu, Debian
  - inside Termux on Android). Confirmed on a real device this
  session: `TracerPid` was already non-zero on a plain shell with
  nothing attached, before running anything of ours at all.

**Same mechanism, so almost certainly broken too - not independently
tested here, verify with the command below before relying on it:**
- **`strace` / `ltrace` / `rr`** wrapped around the whole process -
  these are ptrace-based tracing tools; running this system under one
  occupies the tracer slot itself.
- **gVisor (`runsc`) in ptrace-platform mode** - its KVM-platform mode
  does not have this problem, since that doesn't use ptrace; its
  ptrace-platform mode does.
- **Any other ptrace-based sandbox or debugger** - the mechanism is
  what matters, not the specific tool name. If it intercepts your
  syscalls via `ptrace`, it's occupying the exact signal this system
  watches for.

**Not affected, different mechanism, no known conflict:** plain
Docker/Podman/LXC (namespaces + cgroups, not ptrace), raw Termux
without proot (a different, separate problem - see `ARCHITECTURE.md`
if going that route), `qemu-user` cross-architecture emulation,
`fakeroot` (uses `LD_PRELOAD`, not ptrace).

**Check your own environment before trusting the watchdog in it** -
one line, works anywhere, takes a second:

```
cat /proc/self/status | grep TracerPid
```

Run it fresh, nothing else attached. Anything other than
`TracerPid: 0` means the watchdog's whole detection premise doesn't
hold there - the plain falling-box storage and the encryption still
work fine either way, it's specifically the tamper-detection layer
(main watchdog and distributed-trust holder watchdogs alike, since
they share this exact mechanism) that has nothing to react to.

### A 20GB file, genuinely on real disk - through 2 real bugs to a clean pass

The biggest, most rigorously tested file yet - and the only one whose
source genuinely lived on real disk (the 12GB test below actually used
`/dev/shm`, RAM-backed, not real disk). 83.6M-feature real GeoJSON,
streamed straight from disk, no retrieval. Two real bugs found and
fixed getting here, not glossed over: page cache from a single huge
sequential read growing unbounded (fixed - read in blocks at least as
large as the disk's own readahead window), then a second, different
crash - tens of thousands of separate per-chunk memory mappings
fragmenting the process's address space badly enough that even a
small allocation could fail (fixed - pool many chunks into shared
regions instead of one mapping each, cutting mapping count ~2,000x).

| | |
|---|---|
| File size | **20.002GB, all 81,930 chunks built** |
| RAM held, steady state | **~1.99GB, completely flat** (real content compresses ~10x here - coordinate floats, not prose) |
| Build time | 79.6 min (real LZMA + AES-GCM, no shortcuts) |
| RAM after collapse | **~33MB** - clean, near-total release |
| Safety abort / system-wide leak | neither - never triggered, `MemAvailable` delta -10.2MB across the whole run |

### A 12GB file, held at ~292MB

A single real 12GB markdown file, streamed in chunk-by-chunk
(`ChunkedSecureBox.from_file()`), compressed, encrypted, and kept
continuously falling - on a sandbox with only ~15GB total RAM and
**no swap at all**. Its source file lived in `/dev/shm` (RAM-backed
tmpfs), not on real disk - the 20GB test above is the one that
actually streamed from genuine disk.

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
| Save time | 264.55s (real compression + encryption) |
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
| Watchdog reaction to a real `ptrace_attach` | - | **270.7ms, real SIGKILL** | - |

### Distributed trust, actually watched, and actually on separate machines

Splitting the key across N processes (Shamir's Secret Sharing) is only
as strong as what happens when one of them gets attacked. Found, by
direct review, that nothing did - then fixed it, twice: once so each
holder is watched at all, and once so "separate machines" stopped
being a simulation.

| | |
|---|---|
| Attacked holder reacts (local mode, real `ptrace_attack`) | **6-60ms, self-protects and dies** |
| Main process reacts to a compromised holder | **5-25ms, purely from the holder dying** |
| Tolerates 1 of 5 holders compromised (below threshold) | **system keeps running, key still reconstructs** |
| 2 of 5 compromised (meets the default threshold) | **fails closed, same as before** |
| Holders on a real TLS-authenticated network connection, not shared memory | **same reaction times (~10ms / ~10-25ms), no `/proc` access to the holder needed at all** |

One compromised holder, on its own, gives an attacker **zero** usable
information about the key - proven directly (see
`test_shamir_secret_sharing.py`), which is why the system no longer
kills itself over the first one; this is RAM-only, so a kill always
means the file is gone completely, and that cost only makes sense once
an attacker is actually close to succeeding, not the instant a single
machine is touched.

---

`pariter/` holds an earlier, separate project (not part of Virtual
Storage) - kept together in its own folder.
