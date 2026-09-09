# Virtual Storage — To-do

Tracking what's next so nothing gets lost between sessions.

- [x] **File sharing** — built (`vstorage/sharing.py`) and proven: A
      falls a piece, hands it to B at the next break instead of
      bouncing back to itself, B continues falling it independently.
      Tested with real, separate processes; byte-perfect every time.
      Full RAM tracked through a run too (`sharing_tracked.py`).
- [x] **Running multiple files** — `test_multiple_files.py`: 6 real
      files (JSON, Python, prose, GeoJSON map, PDF, DOCX) held and
      falling at the same time (18 boxes at once). All 6 byte-perfect,
      nothing crossed between files.
- [x] **Security hardening** — full arc, see `ARCHITECTURE.md`: proved
      plain motion isn't security, built real encryption + OS
      hardening + a process-based attack watchdog + Shamir-based
      distributed trust, wired into one pipeline (`secure_system.py`).
      Honest remaining gap documented, not hidden: a root reader that
      skips `ptrace_attach` is undetectable from inside the process -
      no code fix exists for that; distributed trust is the real
      answer (no single machine ever holds the whole secret).
- [x] **Streaming ingestion from disk** — built
      (`ChunkedSecureBox.from_file()`): reads a file chunk-by-chunk,
      never holds the whole thing as one Python object. Stress
      tested on a real 12GB file: ~292MB peak RAM the whole way
      through (41x smaller than the file), flat steady state,
      cleaned up completely on collapse. See `REBUILD_STATUS.md`.
- [x] **Shared schedulers instead of one thread per file** — the
      24-file "ultimate test" found the real cost of one thread per
      guard + one per falling box: 217 threads, ~55 min to save 24
      files, retrieval sometimes slower than saving. Fixed with
      `_GlobalCellScheduler` / `_GlobalFallScheduler` - every guard
      and box now shares one process-wide scheduler each. Re-ran the
      same 24-file test after the fix: 3 threads, save 2.7x faster,
      full retrieval 9-10x faster per file, still 100% byte-perfect.
      See `REBUILD_STATUS.md`.
- [x] **Fall-scheduler throughput under heavy concurrent load** —
      the single-thread version above could be starved completely
      (measured: 29 of 75 boxes got 0 hops during a 75s window
      dominated by one heavy operation). Fixed with a small pool (4)
      of fall-scheduler workers, plus the same fix applied to the key
      -cell scheduler for the identical risk. Reproduced the exact
      failure directly and confirmed the fix: 0 of 30 boxes stalled
      across a 46.5s busy window, hop counts landing in a tight
      107-110 range. See `REBUILD_STATUS.md`.
- [x] **Watchdog stale-address gap** — the watchdog only ever learned
      a box's key-guard addresses ONCE, at save time; every later
      rotation replaced those cells at a fresh address the watchdog
      never heard about. Confirmed directly: attacking after several
      rotations left the CURRENT key fully readable 2000ms later on
      the old code. Fixed with an `on_new_guard` hook wired to the
      live watchdog on every rotation - re-verified the same attack:
      wiped in 5.7-9.3ms across 3 runs.
- [x] **Watchdog region-table growth** — the fix above then grew the
      watchdog's region table without bound (every rotation added a
      new block, nothing ever removed the old one), crashing save()
      with "region table full" after just 9 files in real use. Fixed
      with `reserve_slot()`/`update_slot()` - each box's rotating
      guard gets ONE fixed slot, overwritten in place forever after,
      instead of growing per rotation. Verified: region count stays
      flat as hop count climbs; post-rotation attack still wiped in
      2.3-3.6ms.
- [x] **Stream disk → storage all the way through the real front
      door** — `splitter.py`'s `split_file()` used to read a whole
      file into RAM before anything else could happen, meaning
      `save()` needed roughly a file's own size in RAM just to start,
      even though `ChunkedSecureBox.from_file()` could hold it
      cheaply afterward. Fixed with a `FromFile` marker: content/
      structure pieces that mirror the source file now stream
      straight from disk via `from_file()` instead of being
      materialized first. Verified on a real 5GB file through the
      actual `save()` call: ~195MB peak, not ~5GB. All 7 file-type
      branches (md, txt, json, csv, geojson, pdf, docx, zip) still
      byte-perfect.
- [x] **Streaming output** — `retrieve()` had the same problem in
      reverse: it built the whole reconstructed file as one object
      before returning it. `ChunkedSecureBox.stream_to()` +
      `SecureVirtualStorage.retrieve_to_file()` decrypt one chunk at
      a time straight to a destination file instead. Verified on the
      same 3GB file: RAM moved from 91.7MB (holding it) to only
      93.8MB while streaming the ENTIRE file back out - not a multi-
      GB spike. Byte-perfect. Honest scope, discussed with the user
      before building: this bounds exposure, it doesn't eliminate it
      - retrieving a file always makes it usable somewhere, for any
      encryption-at-rest system. The real win is shrinking what's
      ever exposed at once down to one chunk, briefly, rather than
      the whole file for as long as it's held.

- [x] **Wrap-for-transit output path** — the user's actual ask, which
      plain streaming output only partly answered: not just "stream
      out in bounded chunks" but "encrypt the path itself," so nothing
      written to the destination is ever raw plaintext, regardless of
      whether that destination is trusted. Built
      `ChunkedSecureBox.stream_to_wrapped()` / `SecureVirtualStorage.
      retrieve_to_encrypted_file()` - decrypt from storage, immediately
      re-encrypt under a transit key (real AES-GCM, fresh nonce per
      chunk), only THAT ciphertext ever reaches the destination.
      `decrypt_wrapped_file()` is the matching reader. Verified two
      ways: confirmed directly that the wrapped file's bytes on disk
      never contain the original plaintext (searched, not found), and
      compared head-to-head against plain streaming on the same 3GB
      file - RAM identical (~94MB either way), retrieval ~27% slower
      (165.68s vs 130.55s - one real extra AES-GCM pass per chunk,
      honest cost, not free) - byte-perfect after unwrapping either
      way.

- [x] **Trust-tiered retrieval** — the user's own idea vs. mine,
      built as two distinct, selectable options rather than merged
      into one: `retrieve_to_destination(trust=...)` — `"trusted"`
      plain streaming, `"untrusted"` releases the key only after
      `os.fsync()` confirms the bytes are durably on the destination
      (the user's design), `"hostile"` issues a single-use,
      time-boxed token redeemed separately via `redeem_key()` instead
      of releasing the key at all. Wired into `secure_system.py`,
      documented in `HANDBOOK.md`.
- [x] **Distributed-trust holder processes had no watchdog at all** —
      found by direct review, not a test failing: `ProcessWatchdog`
      only ever watched the main process; the N holder processes in
      `DistributedTrustGroup` had zero tamper detection anywhere. A
      real `ptrace_attach` on one produced no reaction, from
      anything. Fixed: each holder gets its own `ProcessWatchdog`
      (stood up by the parent - a daemon process can't spawn its own
      children, hit that restriction directly), and a monitor thread
      in `DistributedTrustGroup` treats a holder dying outside a
      clean `stop()` as tamper evidence and kills the whole main
      process, fail-closed, without waiting for `fetch()`. Surfaced a
      real Linux ptrace quirk along the way: `Process.is_alive()`
      (plain `os.waitpid()`) never sees a holder killed by a THIRD
      PARTY attacker as dead, because the kernel routes that
      notification to the attacker first and the attacker never
      consumes it - fixed by reading `/proc/<pid>/stat` directly
      instead. Verified with a real `ptrace_attach`
      (`test_distributed_trust_watchdog.py`): holder dies in
      6-60ms, main reacts 5-25ms after that, 100% across repeated
      runs. See `REBUILD_STATUS.md`.
- [x] **Compromise tolerance, made a real switch instead of one fixed
      answer** — the user asked directly: should ANY compromised
      holder kill the whole system, or only once most are gone? Real
      trade-off: this system is RAM-only, so killing the main process
      ALWAYS means total, permanent loss of whatever file it's
      holding - and a single compromised holder, alone, gives an
      attacker zero usable information (k=3 needed). Changed the
      default: `DistributedTrustGroup(kill_threshold=...)` now kills
      only once `k-1` holders are gone (one compromise away from an
      attacker actually succeeding), not on the first one -
      `kill_threshold=1` restores the old instant-kill behavior,
      `kill_threshold=0` disables auto-kill entirely. Also fixed
      `fetch()` to use any `k` *currently alive* holders instead of
      always the first `k` by position, so tolerating a compromise
      below the threshold actually keeps the system working instead
      of permanently wedging on a dead holder. Verified with two real
      `ptrace_attack` scenarios (`test_distributed_trust_tolerance.py`):
      attacking 1 of 5 holders -> main survives, `fetch()` still
      works from the remaining 4; attacking 2 of 5 -> main dies, same
      as before.
- [x] **Real network-separated holder machines** — the user asked
      directly for this after the local-mode watchdog fix: local
      holders (`distributed_key.py`) only "simulate" separate
      machines - they're real OS processes, but all under the SAME
      kernel, so a sufficiently privileged local attacker could, in
      principle, reach all of them. Built `vstorage/holder_server.py`
      (a standalone `python3 -m vstorage.holder_server` process - not
      a multiprocessing daemon child, so unlike the local holder it
      CAN spawn its own `ProcessWatchdog` directly, no parent-side
      workaround needed) and `vstorage/network_trust.py`
      (`NetworkTrustGroup` - same public shape as
      `DistributedTrustGroup`, drop-in via
      `SecureVirtualStorage(trust_mode="network")`). Real TLS
      (fresh 2048-bit self-signed cert per group, client-pinned),
      real TCP, a shared bearer token checked with a constant-time
      compare. Point `trust_host=` at a real remote address and this
      is a genuine multi-machine deployment unchanged - what makes
      today's tests "only" localhost is the sandbox having one
      machine, not the protocol.

      Honest architectural difference from local mode, not hidden:
      there's no shared `/proc` across real machines, so the client
      can't read a remote holder's kernel state directly. Detection
      became heartbeat-based instead - `NetworkTrustGroup` pings
      every holder over its TLS connection and treats an unreachable
      one exactly like a locally-dead one (same `kill_threshold`
      machinery). Verified with two real `ptrace_attack` test
      scripts, same shape as the local-mode ones
      (`test_network_trust_watchdog.py`,
      `test_network_trust_tolerance.py`): attacked holder server dies
      in ~10ms (self-protects the same way local holders do), main
      process reacts in ~10-25ms via the dead connection alone - no
      `/proc` access to the holder used or needed. Tolerance scenarios
      (1-of-5 survives, 2-of-5 kills) match the local-mode results
      exactly.

      Two real bugs found and fixed while building this, both about
      process cleanup, not security: (1) holder servers were spawned
      with `start_new_session=True`, detaching them from the parent's
      process group - harmless for `NetworkTrustGroup.stop()` itself
      (which tracks and kills each by pid anyway) but meant an
      external cleanup (like a test harness's `killpg`) couldn't
      reach them, and a crashed test run left real, busy-spinning
      watchdog processes orphaned for minutes, driving load average
      past 10 on this 4-core sandbox before being found and killed.
      Fixed by removing it. (2) A holder's own self-spawned watchdog
      is its child via `multiprocessing(daemon=True)`, which only
      auto-cleans on a NORMAL interpreter exit (atexit) - a plain
      SIGTERM (what a clean `stop()` sends) doesn't trigger that,
      orphaning the watchdog every time. Fixed with an explicit
      SIGTERM handler in `holder_server.py` that stops its own
      watchdog before exiting.

## Not yet done

- [ ] PDF/DOCX text extraction still needs to open and parse the
      whole file (pypdf/zipfile) - the structure piece streams fine,
      but content extraction for those two types isn't RAM-bounded
      the same way text-like files now are. Not investigated further
      yet; likely bounded by what the parser libraries themselves do
      internally.

Update this list as items are explained, built, and verified.
