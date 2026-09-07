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

## Not yet rebuilt

- The C-level experiments (`anonymous_mmap_storage.c`, `bounce_mover.c`,
  the `one_box_*.c` variants) — the Python `FallingBox` covers the same
  concept at the architecture level; the C versions test raw
  `MAP_ANONYMOUS` behavior specifically, which Python's own allocator
  already relies on under the hood for large objects.
- Model layer-streaming (Experiment 19) and the startup-case API surface
  (section 14: `store()`/`retrieve()` auth, replication, key rotation) -
  product-layer work, not the core mechanism.
