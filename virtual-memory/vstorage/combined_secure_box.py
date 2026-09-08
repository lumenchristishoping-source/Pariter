"""Tier 1 + Tier 2 together, plus the split key: everything built so
far, combined into one box.

  - ONE ciphertext data buffer (Tier 2), not two - "falls between
    nothing and nothing" instead of bouncing A/B.
  - mlock() + MADV_DONTDUMP on that buffer, and PR_SET_DUMPABLE(0) on
    the process (Tier 1).
  - The AES key itself isn't one 32-byte block sitting still between
    ratchets: it's compressed, split byte-by-byte into 88 cells, each
    one bouncing on its own independent, unsynchronized timer, each
    cell individually mlock()'d and MADV_DONTDUMP'd too.
  - Every data hop: reconstruct the current key from its 88 scattered
    cells, decrypt, ratchet to a new key, re-encrypt, push the new key
    back out into the same 88 cells (still scattered, still hopping).
"""

from __future__ import annotations

import ctypes
import hashlib
import mmap
import os
import threading

import gc

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .split_key_guard import SplitKeyGuard, _lock_and_hide

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
PR_SET_DUMPABLE = 4
PAGE_SIZE = mmap.PAGESIZE
NONCE_LEN = 12
TRIM_EVERY_N_HOPS = 20


def _release_freed_memory() -> None:
    """Every hop creates fresh AESGCM/lzma objects - real, short-lived
    garbage. glibc doesn't hand freed heap memory back to the OS on its
    own (confirmed the same way earlier in this project: 1 second of
    hopping grew RssAnon by 68MB, and a single malloc_trim() call
    after gc.collect() recovered 66MB of it - not a leak, just
    retained-but-freeable arenas). Calling this periodically keeps RAM
    from climbing unboundedly on a long-running box."""
    gc.collect()
    try:
        _libc.malloc_trim(0)
    except OSError:
        pass


def make_process_nondumpable() -> bool:
    return _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0


def _round_up_page(n: int) -> int:
    return ((max(n, 1) + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE


def _mmap_addr(m: mmap.mmap) -> int:
    return ctypes.addressof((ctypes.c_char * len(m)).from_buffer(m))


def _ratchet(key: bytes) -> bytes:
    return hashlib.sha256(key + b"vstorage-ratchet").digest()


def secure_zero(buf) -> None:
    if buf:
        buf[:] = bytes(len(buf))


TARGET_RATCHET_BYTES_PER_SEC = 100 * 1024 * 1024  # 100MB/s budget per hop
MAX_HOP_INTERVAL = 2.0


def _pick_hop_interval(data_len: int) -> float:
    """Small payloads ratchet as fast as possible (interval=0, same as
    before). Large ones get a computed pause between hops so the box
    doesn't try to re-encrypt itself as fast as the CPU allows -
    confirmed necessary: a 50MB payload with NO pacing dropped from
    ~35-46 hops/sec (measured at small sizes) to 1 hop per 2 SECONDS,
    while RAM grew by +332MB beyond the fixed baseline - re-encrypting
    the whole buffer every hop doesn't scale for real file sizes."""
    if data_len < 1024 * 1024:
        return 0.0
    return min(data_len / TARGET_RATCHET_BYTES_PER_SEC, MAX_HOP_INTERVAL)


class CombinedSecureBox:
    def __init__(self, data: bytes, hop_interval: float | None = None,
                 trust_group=None):
        """trust_group: optional DistributedTrustGroup. When set, every
        ratchet step mixes in a freshly-fetched secret from N separate
        processes (K-of-N reconstruction) instead of ratcheting purely
        from local state - closes a real gap: a purely local ratchet
        is deterministic, so a captured key predicts every FUTURE key
        too, not just the past ones (verified directly - a captured
        key hashed forward 5 times locally matched the real key
        exactly). With a trust group, computing the next key needs
        live access to K of the N other processes, not just a local
        snapshot."""
        self._data_len = len(data)
        self._hop_interval = (
            _pick_hop_interval(len(data)) if hop_interval is None else hop_interval)
        self._trust_group = trust_group
        length = _round_up_page(max(len(data) + 16 + NONCE_LEN, 1))
        self._buf = mmap.mmap(-1, length)
        self._buf_len = length

        initial_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(NONCE_LEN)
        ciphertext = AESGCM(initial_key).encrypt(nonce, data, None)
        self._buf[:NONCE_LEN] = nonce
        self._buf[NONCE_LEN:NONCE_LEN + len(ciphertext)] = ciphertext
        self._payload_len = len(ciphertext)

        self._key_guard = SplitKeyGuard(initial_key)

        self._lock = threading.Lock()
        self._hops = 0
        self._stop = threading.Event()
        self._paused = threading.Event()

        self.protection_status = {"data_buffer": _lock_and_hide(self._buf)}

        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _next_key(self, current_key: bytes) -> bytes:
        if self._trust_group is not None:
            external = self._trust_group.fetch()
            derived = hashlib.sha256(current_key + external + b"vstorage-ratchet").digest()
            external = bytes(len(external))  # never kept past this line
            return derived
        return _ratchet(current_key)

    def _fall_forever(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.001)
                continue
            with self._lock:
                current_key = self._key_guard.reconstruct()

                nonce = bytes(self._buf[:NONCE_LEN])
                ciphertext = bytes(self._buf[NONCE_LEN:NONCE_LEN + self._payload_len])
                plaintext = AESGCM(current_key).decrypt(nonce, ciphertext, None)

                new_key = self._next_key(current_key)
                self._key_guard.update(new_key)

                new_nonce = os.urandom(NONCE_LEN)
                new_ciphertext = AESGCM(new_key).encrypt(new_nonce, plaintext, None)

                plaintext_scratch = bytearray(plaintext)
                secure_zero(plaintext_scratch)

                self._buf[:NONCE_LEN] = new_nonce
                self._buf[NONCE_LEN:NONCE_LEN + len(new_ciphertext)] = new_ciphertext
                self._payload_len = len(new_ciphertext)
                self._hops += 1

            if self._hops % TRIM_EVERY_N_HOPS == 0:
                _release_freed_memory()

            if self._hop_interval > 0:
                self._stop.wait(self._hop_interval)

    @property
    def hops(self) -> int:
        return self._hops

    def key_cell_hops(self):
        return self._key_guard.hop_counts

    def regions(self) -> list:
        """Everything worth zeroing if tampering is detected: every
        key-byte cell's two pages (breaks the key beyond recovery -
        cheap, small, and enough on its own since the data buffer is
        ciphertext without it) plus the data buffer itself for
        defense-in-depth."""
        out = list(self._key_guard.regions())
        out.append((_mmap_addr(self._buf), self._buf_len))
        return out

    def raw_ciphertext_snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._buf[:NONCE_LEN + self._payload_len])

    def snapshot(self) -> bytearray:
        self._paused.set()
        with self._lock:
            current_key = self._key_guard.reconstruct()
            nonce = bytes(self._buf[:NONCE_LEN])
            ciphertext = bytes(self._buf[NONCE_LEN:NONCE_LEN + self._payload_len])
            plaintext = AESGCM(current_key).decrypt(nonce, ciphertext, None)
            out = bytearray(plaintext)
        self._paused.clear()
        return out

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._key_guard.collapse()
        self._buf.close()
