"""THE unified, hardened pipeline - not a pile of separate tier
experiments, one coherent system with the same save/retrieve/forget
API as system.py's VirtualStorage, but every piece protected by
everything built and tested in this project:

  Step 1 (split.py):        content / structure / metadata, by type
  Step 2+3 (CombinedSecureBox): each piece is ONE ciphertext buffer,
      ratcheted under a fresh key every hop, that key itself split
      byte-by-byte across 88 independently-timed, separately locked
      cells - mlock()'d and MADV_DONTDUMP'd throughout
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

Honest, tested scope and limits:
  - Small-to-medium files only (tested up to a few MB cleanly). A 50MB
    payload through CombinedSecureBox measured +250MB RAM per hop even
    with pacing - a real limit of whole-buffer encryption. For large
    files, use chunked_secure_box.py's ChunkedSecureBox instead (not
    yet wired in here - same RAM cost dropped to +52MB, bounded, in
    its own testing).
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

from .combined_secure_box import CombinedSecureBox, make_process_nondumpable
from .process_watchdog import ProcessWatchdog
from .splitter import SplitResult, split_bytes, split_file

Piece = str


@dataclass
class _Held:
    boxes: Dict[Piece, CombinedSecureBox]
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

    def __init__(self, watch_for_tampering: bool = True, kill_on_tamper: bool = True):
        make_process_nondumpable()
        self._held: Dict[str, _Held] = {}
        self._lock = threading.Lock()
        self._watchdog = None
        if watch_for_tampering:
            self._watchdog = ProcessWatchdog(target_pid=os.getpid(),
                                              kill_target=kill_on_tamper)

    # -- save --------------------------------------------------------

    def save(self, path: str) -> str:
        result = split_file(path)
        return self._hold(result, name=os.path.basename(path))

    def save_bytes(self, raw: bytes, name_hint: str) -> str:
        result = split_bytes(raw, name_hint)
        return self._hold(result, name=name_hint)

    def _hold(self, result: SplitResult, name: str) -> str:
        file_id = uuid.uuid4().hex
        boxes = {
            "content": CombinedSecureBox(result.content),
            "structure": CombinedSecureBox(result.structure),
            "metadata": CombinedSecureBox(result.metadata),
        }
        if self._watchdog:
            regions = []
            for box in boxes.values():
                regions.extend(box.regions())
            self._watchdog.add_regions(regions)
        original_size = (len(result.content) if result.reconstruct_from == "content"
                          else len(result.structure))
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
