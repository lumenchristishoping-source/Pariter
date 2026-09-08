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

## Not yet done

- [ ] Wire `ChunkedSecureBox` (large-file support) into
      `secure_system.py` as the default for big payloads - tested
      separately and works, just not connected to the main pipeline
      yet.
- [ ] **Stream disk → storage all the way through the real front
      door.** `from_file()` proves the underlying box can ingest a
      huge file cheaply, but `SecureVirtualStorage.save()` still
      goes through `splitter.py`'s `split_file()`, which does a full
      `open(path).read()` first. For plain content-is-the-file types
      (`.md`, `.json`, `.log`, `.csv`, `.txt`) this is a real,
      scoped fix - route straight to `from_file()`, skipping the
      full read. For `.pdf`/`.docx` the text-extraction step itself
      needs the whole file, so that path stays bounded by the
      parser library regardless.

Update this list as items are explained, built, and verified.
