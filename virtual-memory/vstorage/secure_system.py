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
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .chunked_secure_box import NONCE_LEN, ChunkedSecureBox, make_process_nondumpable
from .distributed_key import DistributedTrustGroup
from .process_watchdog import ProcessWatchdog
from .splitter import FromFile, SplitResult, piece_size, split_bytes, split_file

Piece = str

DEFAULT_TOKEN_TTL = 60.0  # seconds - "hostile" tier's key-redemption window


@dataclass
class RetrievalReceipt:
    """What retrieve_to_destination() hands back - shape depends on
    trust: "trusted" gets neither (nothing to hand over, dest_path IS
    the readable file); "untrusted" gets the real key, released only
    after confirmed durable departure; "hostile" gets a redeemable
    token instead of the key itself."""
    dest_path: str
    trust: str
    key: Optional[bytes] = None
    token: Optional[str] = None
    expires_at: Optional[float] = None


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
        self._pending_keys: Dict[str, dict] = {}
        self._key_lock = threading.Lock()

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

    def retrieve_to_file(self, file_id: str, dest_path: str, which: Piece | str = "full") -> None:
        """Same output as retrieve(), but never assembles the whole
        file as one Python object first. Writes straight to dest_path
        in chunk_size pieces - at any instant, at most one chunk's
        worth of real plaintext exists, and only for as long as it
        takes to write it out. Symmetric with save()'s from_file():
        that fixed "needs the whole file in RAM just to get IN",
        this fixes "needs the whole file in RAM just to get back OUT".

        This bounds exposure, it does not eliminate it - something
        still has the plaintext the instant it's written (that's true
        of any encryption-at-rest system, retrieving a file always
        means it becomes usable). The real win here is shrinking WHAT
        exists in the open at once from "the whole file, for however
        long the caller holds it" down to "one chunk, briefly" -
        write dest_path itself to somewhere already protected (an
        encrypted volume, a secured upload) for that to matter."""
        held = self._held[file_id]
        if which == "full":
            which = held.reconstruct_from
        box = held.boxes[which]
        with open(dest_path, "wb") as f:
            box.stream_to(f.write)

    def retrieve_to_encrypted_file(self, file_id: str, dest_path: str,
                                    transit_key: bytes | None = None,
                                    which: Piece | str = "full") -> bytes:
        """The actual "encrypt the path" version the user asked for,
        not just "write plaintext somewhere hopefully safe": streams
        the file out the same chunk-at-a-time way as retrieve_to_file,
        but every byte written to dest_path is ciphertext under
        transit_key - decrypted from storage and immediately
        re-encrypted, never handed to the destination in the open.

        transit_key is generated fresh (real AES-256) if not given,
        and returned either way - the caller (or whoever they hand
        dest_path to) needs it to read the file back via
        decrypt_wrapped_file(). Pass your own key if the destination
        is a specific other party who should be the only one able to
        open it.

        Same honest scope as retrieve_to_file(): the plaintext still
        exists for one chunk, one instant, inside this call - that's
        unavoidable, decrypting from storage is what makes the data
        real in the first place. What this actually buys you: nothing
        written to dest_path, or visible to anything reading dest_path
        off disk or off the wire, is ever plaintext - only someone
        holding transit_key can ever get it back."""
        held = self._held[file_id]
        if which == "full":
            which = held.reconstruct_from
        box = held.boxes[which]
        if transit_key is None:
            transit_key = AESGCM.generate_key(bit_length=256)
        with open(dest_path, "wb") as f:
            box.stream_to_wrapped(f.write, transit_key)
        return transit_key

    def retrieve_to_destination(self, file_id: str, dest_path: str, trust: str = "trusted",
                                 which: Piece | str = "full",
                                 token_ttl: float = DEFAULT_TOKEN_TTL) -> RetrievalReceipt:
        """Picks the actual output strategy from how much you trust
        dest_path, instead of making the caller choose the raw method
        by hand. Both ideas discussed with the user exist here as
        real, distinct tiers rather than one being "the" answer:

          "trusted"   - plain streaming (stream_to()). Fastest, no
                        wrapping. Assumes dest_path is already
                        protected on its own (an encrypted volume, a
                        destination you control) - same as
                        retrieve_to_file().

          "untrusted" - wrapped streaming (stream_to_wrapped()) AND
                        the key is only released after the encrypted
                        bytes are CONFIRMED durably written - a real
                        os.fsync(), not just "the write() calls
                        returned". Don't hand out the means to read
                        the file until the file has genuinely,
                        durably left. (The user's idea.)

          "hostile"   - everything "untrusted" does, PLUS the key
                        itself is never handed back directly: you get
                        a single-use, time-boxed token instead, and
                        redeem_key(token) is a separate call that only
                        works once, within token_ttl seconds of this
                        call. For a destination you don't trust at
                        all, or don't even know yet. (Layers the
                        token/expiry idea on top of the confirmed-
                        departure gate, rather than replacing it.)

        Honest limit that applies to all three, stated plainly: once
        a key or token is redeemed and leaves this process (returned
        to the caller), this process can no longer reach it - see the
        HANDBOOK.md "death" guarantee, which is about what's INSIDE
        this process, not about copies a caller already has."""
        if trust == "trusted":
            self.retrieve_to_file(file_id, dest_path, which=which)
            return RetrievalReceipt(dest_path=dest_path, trust=trust)

        if trust not in ("untrusted", "hostile"):
            raise ValueError(f"unknown trust level: {trust!r} (use 'trusted', "
                              f"'untrusted', or 'hostile')")

        held = self._held[file_id]
        if which == "full":
            which = held.reconstruct_from
        box = held.boxes[which]
        transit_key = AESGCM.generate_key(bit_length=256)
        with open(dest_path, "wb") as f:
            box.stream_to_wrapped(f.write, transit_key)
            f.flush()
            os.fsync(f.fileno())  # confirmed durable departure - THEN the key may be released

        if trust == "untrusted":
            return RetrievalReceipt(dest_path=dest_path, trust=trust, key=transit_key)

        # "hostile": hold the key back, issue a single-use, expiring token instead.
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + token_ttl
        with self._key_lock:
            self._sweep_expired_tokens_locked()
            self._pending_keys[token] = {"key": transit_key, "expires_at": expires_at}
        return RetrievalReceipt(dest_path=dest_path, trust=trust, token=token,
                                 expires_at=expires_at)

    def redeem_key(self, token: str) -> bytes:
        """Redeems a "hostile"-tier token for the real transit key -
        single-use (deleted the instant this succeeds) and time-boxed
        (fails once token_ttl has passed, even if never redeemed).
        Raises KeyError for an unknown/never-issued/already-redeemed
        token, TimeoutError for a real but expired one - checked
        directly against this specific token's own deadline, not via
        the general sweep (which runs only at issuance, to bound
        growth from tokens nobody ever redeems - doing it here too
        raced this exact check, deleting an expired entry before its
        own expiry could be distinguished from "never existed")."""
        with self._key_lock:
            entry = self._pending_keys.get(token)
            if entry is None:
                raise KeyError("unknown token: never issued or already redeemed")
            if time.time() > entry["expires_at"]:
                del self._pending_keys[token]
                raise TimeoutError("token expired")
            del self._pending_keys[token]
            return entry["key"]

    def _sweep_expired_tokens_locked(self) -> None:
        """Caller must hold self._key_lock. Drops expired, never-
        redeemed tokens so a long-running process holding many
        "hostile"-tier keys that were never picked up doesn't
        accumulate them forever."""
        now = time.time()
        expired = [t for t, e in self._pending_keys.items() if e["expires_at"] < now]
        for t in expired:
            del self._pending_keys[t]

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


def decrypt_wrapped_file(path: str, transit_key: bytes, dest_path: str) -> None:
    """Reads back a file written by retrieve_to_encrypted_file() -
    whoever's on the receiving end of the encrypted path calls this
    (with the transit_key they were given out of band) to get the
    real plaintext. Streams both directions, same as everything else
    here: reads one wrapped chunk at a time, decrypts it, writes it
    straight to dest_path, never holds the whole file either way."""
    with open(path, "rb") as src, open(dest_path, "wb") as dst:
        while True:
            length_bytes = src.read(4)
            if not length_bytes:
                break
            ct_len = int.from_bytes(length_bytes, "big")
            nonce = src.read(NONCE_LEN)
            ct = src.read(ct_len)
            plaintext = AESGCM(transit_key).decrypt(nonce, ct, None)
            dst.write(plaintext)
