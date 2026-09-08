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

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .split_key_guard import SplitKeyGuard, _lock_and_hide

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
PR_SET_DUMPABLE = 4
PAGE_SIZE = mmap.PAGESIZE
NONCE_LEN = 12


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


class CombinedSecureBox:
    def __init__(self, data: bytes):
        self._data_len = len(data)
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

                new_key = _ratchet(current_key)
                self._key_guard.update(new_key)

                new_nonce = os.urandom(NONCE_LEN)
                new_ciphertext = AESGCM(new_key).encrypt(new_nonce, plaintext, None)

                plaintext_scratch = bytearray(plaintext)
                secure_zero(plaintext_scratch)

                self._buf[:NONCE_LEN] = new_nonce
                self._buf[NONCE_LEN:NONCE_LEN + len(new_ciphertext)] = new_ciphertext
                self._payload_len = len(new_ciphertext)
                self._hops += 1

    @property
    def hops(self) -> int:
        return self._hops

    def key_cell_hops(self):
        return self._key_guard.hop_counts

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
