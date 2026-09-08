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

`pariter/` holds an earlier, separate project (not part of Virtual
Storage) - kept together in its own folder.
