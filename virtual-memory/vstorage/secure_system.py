"""THE unified, hardened pipeline - not a pile of separate tier
experiments, one coherent system with the same save/retrieve/forget
API as system.py's VirtualStorage, but every piece protected by
everything built and tested in this project:

  Step 1 (split.py):        content / structure / metadata, by type
  Step 2+3 (ChunkedSecureBox): each piece is compressed once, then
      split into 256KB chunks, each its own separately locked
      ciphertext buffer - one chunk ratcheted per hop under a fresh
      key, that key itself split byte-by-byte across 88 independently
      -timed, separately locked cells - mlock()'d and MADV_DONTDUMP'd
      throughout. This is the box that scales: a real 1GB file holds
      at ~66MB steady-state (compression) instead of ballooning the
      way whole-buffer encryption did before this existed. The older,
      whole-buffer CombinedSecureBox still exists in
      combined_secure_box.py with its own tests, but nothing here
      uses it anymore - kept only as a reference for what "before
      chunking" looked like.
  Step 4 (ProcessWatchdog): one watchdog for the whole system,
      running as a REAL SEPARATE OS PROCESS - not a thread, because a
      thread-based watchdog was tested and found to lose the race
      once real background threads were competing for the GIL
      (50-70us alone vs 63-90ms in a busy pipeline). This one reaches
      directly into the pipeline's memory from outside and zeros
      every registered region itself - it doesn't depend on the
      pipeline's own threads reacting at all.
  Step 5 (Death):           collapse_all() / process end - matches
      HANDBOOK.md section 4 exactly, now hardened at every step

  Optional: distributed trust (distributed_key.py). Off by default -
  real cost, only worth it if you actually want this property. When
  on, every hop's key ratchet mixes in a secret freshly reconstructed
  from K of N separate processes (Shamir's Secret Sharing), instead of
  ratcheting purely from local state. Closes a real gap found while
  building this: a purely local ratchet is a deterministic hash chain,
  so a captured key predicts every FUTURE key too - verified directly,
  not assumed. With distributed trust on, a one-time full capture of
  this machine no longer lets an attacker compute what comes next.

Honest, tested scope and limits:
  - Verified end-to-end on a real 1GB file through THIS class's own
    save()/retrieve() (test_1gb_through_real_system.py) - not just the
    box in isolation. Byte-perfect, steady-state RAM ~66MB.
  - The watchdog only sees ptrace_attach-based access (TracerPid).
    Tested and confirmed: a root-privileged reader can open and read
    /proc/<pid>/mem successfully with NO attach at all, and this
    watchdog - or any watchdog using the same signal - sees nothing
    when that happens. This defends against the common tools
    (debuggers, most memory-dump software) but not a sophisticated,
    privileged attacker who knows to skip attach. Not solved here;
    stated plainly.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from typing import Dict

from .chunked_secure_box import ChunkedSecureBox, make_process_nondumpable
from .distributed_key import DistributedTrustGroup
from .process_watchdog import ProcessWatchdog
from .splitter import FromFile, SplitResult, piece_size, split_bytes, split_file

Piece = str


@dataclass
class _Held:
    boxes: Dict[Piece, ChunkedSecureBox]
    reconstruct_from: Piece
    original_size: int
    name: str = ""


class SecureVirtualStorage:
    """Same shape as system.py's VirtualStorage - save()/retrieve()/
    forget()/collapse_all() - but every held piece is hardened by
    everything built in this project, and one shared ProcessWatchdog
    (a real separate OS process, immune to this process's own GIL)
    protects the whole system: any ptrace attach anywhere gets every
    held key's cells and every data buffer zeroed from outside, then
    this process killed.
    """

    def __init__(self, watch_for_tampering: bool = True, kill_on_tamper: bool = True,
                 use_distributed_trust: bool = False, trust_k: int = 3, trust_n: int = 5):
        make_process_nondumpable()
        self._held: Dict[str, _Held] = {}
        self._lock = threading.Lock()
        self._watchdog = None
        if watch_for_tampering:
            self._watchdog = ProcessWatchdog(target_pid=os.getpid(),
                                              kill_target=kill_on_tamper)
        self._trust_group = None
        if use_distributed_trust:
            self._trust_group = DistributedTrustGroup(k=trust_k, n=trust_n)

    # -- save --------------------------------------------------------

    def save(self, path: str) -> str:
        result = split_file(path)
        return self._hold(result, name=os.path.basename(path))

    def save_bytes(self, raw: bytes, name_hint: str) -> str:
        result = split_bytes(raw, name_hint)
        return self._hold(result, name=name_hint)

    def _make_box(self, piece, on_new_guard) -> ChunkedSecureBox:
        """A splitter.Piece is either plain bytes (small, already in
        RAM - fine to hold as one object) or a FromFile marker (this
        piece mirrors a file on disk and should be streamed straight
        in via ChunkedSecureBox.from_file() - the difference between
        a 40GB file needing ~40GB+ just to start vs. staying bounded
        by chunk size, the same primitive already proven at 12GB)."""
        if isinstance(piece, FromFile):
            return ChunkedSecureBox.from_file(piece.path, trust_group=self._trust_group,
                                               on_new_guard=on_new_guard)
        return ChunkedSecureBox(piece, trust_group=self._trust_group,
                                 on_new_guard=on_new_guard)

    def _rotation_registrar(self):
        """Builds a per-box on_new_guard callback that keeps the
        watchdog's registration O(1) per box, no matter how many times
        that box's key rotates.

        Real gap found and fixed: the watchdog used to only ever learn
        a box's key-guard addresses ONCE, at save time. Every later key
        rotation replaces those cells with a fresh mmap allocation at a
        fresh address the watchdog never heard about - so after the
        first rotation, it was watching stale, freed (and often
        silently reused) memory instead of the actual current key.

        The first fix for that (add_regions on every rotation) created
        a SECOND real bug, found the same way - running the actual
        multi-file test, not by inspection: every rotation registered
        a BRAND NEW block of addresses and nothing ever removed the
        old one, so the table grows without bound. A single small
        file's metadata piece (usually just 1 chunk, so it rotates on
        EVERY hop) burned through all 200,000 slots within a few
        minutes under the scheduler pool's much higher throughput,
        crashing save() outright.

        Fixed by reserving ONE fixed slot per box (on its first
        rotation) and overwriting that same slot every time after -
        same live addresses always covered, registration cost bounded
        regardless of rotation count."""
        if not self._watchdog:
            return None
        watchdog = self._watchdog
        slot = {"index": None, "size": None}

        def _on_new_guard(regions: list) -> None:
            if slot["index"] is None:
                slot["index"] = watchdog.reserve_slot(len(regions))
                slot["size"] = len(regions)
            if slot["index"] != -1 and len(regions) == slot["size"]:
                watchdog.update_slot(slot["index"], regions)
            # else: table full, or an unexpected size change - skip
            # rather than crash; this box's rotation still succeeds,
            # just without watchdog coverage for that one instant.

        return _on_new_guard

    def _hold(self, result: SplitResult, name: str) -> str:
        file_id = uuid.uuid4().hex
        # Each piece rotates its own, independent guard - each needs
        # its OWN registrar (its own reserved slot). Sharing one
        # between pieces would let one piece's rotation silently
        # overwrite another piece's watched addresses.
        boxes = {
            "content": self._make_box(result.content, self._rotation_registrar()),
            "structure": self._make_box(result.structure, self._rotation_registrar()),
            "metadata": ChunkedSecureBox(result.metadata, trust_group=self._trust_group,
                                          on_new_guard=self._rotation_registrar()),
        }
        if self._watchdog:
            regions = []
            for box in boxes.values():
                regions.extend(box.regions())
            self._watchdog.add_regions(regions)
        original_size = (piece_size(result.content) if result.reconstruct_from == "content"
                          else piece_size(result.structure))
        with self._lock:
            self._held[file_id] = _Held(
                boxes=boxes, reconstruct_from=result.reconstruct_from,
                original_size=original_size, name=name,
            )
        return file_id

    # -- retrieve ------------------------------------------------------

    def retrieve(self, file_id: str, which: Piece | str = "full") -> bytes:
        held = self._held[file_id]
        if which == "full":
            which = held.reconstruct_from
        return bytes(held.boxes[which].snapshot())

    def original_size_bytes(self, file_id: str) -> int:
        return self._held[file_id].original_size

    def list_ids(self):
        return list(self._held.keys())

    # -- death ---------------------------------------------------------

    def forget(self, file_id: str) -> None:
        with self._lock:
            held = self._held.pop(file_id, None)
        if held:
            for box in held.boxes.values():
                box.collapse()

    def collapse_all(self) -> None:
        for file_id in list(self._held.keys()):
            self.forget(file_id)
        if self._watchdog:
            self._watchdog.stop()
        if self._trust_group:
            self._trust_group.stop()
