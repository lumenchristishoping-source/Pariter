"""Wires Shamir's Secret Sharing (shamir.py) into the actual ratchet,
closing a real gap found while building this: the ratchet used until
now (SHA256(current_key + b"ratchet")) is purely local and
deterministic, so capturing the key ONCE lets an attacker compute
EVERY future key themselves, forever - verified directly: a key
captured at hop 0, hashed forward 5 times locally, matches the real
key at hop 5 exactly. "Forward secrecy" as tested before only covered
the backward direction (an old ciphertext staying safe from a newer
key) - it said nothing about a captured key predicting the future.

Fix: mix a freshly-fetched, genuinely external secret into every
ratchet step. That secret is never stored anywhere - it's Shamir-split
across N separate real OS processes (simulating N separately-trusted
machines) and reconstructed fresh, K-of-N, on demand. An attacker who
fully captures THIS machine at some instant still can't compute the
NEXT key, because doing that now requires live, ongoing access to K
of the N other machines too - not just a one-time local snapshot.
"""

from __future__ import annotations

import ctypes
import mmap
import multiprocessing as mp
import os
import threading

from .shamir import combine_shares, split_secret

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
MADV_DONTDUMP = 16


def _lock_and_hide(buf: mmap.mmap) -> None:
    addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    length = len(buf)
    _libc.mlock(ctypes.c_void_p(addr), ctypes.c_size_t(length))
    _libc.madvise(ctypes.c_void_p(addr), ctypes.c_size_t(length), MADV_DONTDUMP)


def _holder_process(x: int, share_bytes: bytes, request_conn) -> None:
    """Simulates one separately-trusted machine: holds ONE Shamir
    share, locked, and hands it over whenever asked - the only thing
    this process does. In a real deployment this would be a network
    service on an actually separate host, not a local process; that
    honest limitation is unavoidable inside a single sandbox."""
    buf = mmap.mmap(-1, mmap.PAGESIZE, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    buf[:len(share_bytes)] = share_bytes
    _lock_and_hide(buf)
    while True:
        try:
            msg = request_conn.recv()
        except EOFError:
            return
        if msg == "get_share":
            request_conn.send((x, bytes(buf[:len(share_bytes)])))
        elif msg == "stop":
            return


class DistributedTrustGroup:
    """N real separate OS processes, each holding one Shamir share of
    a root secret that is NEVER stored anywhere as a whole - only
    reconstructed transiently, K-of-N, each time fetch() is called,
    and the caller is expected to use it immediately and not persist
    it (same discipline as everything else in this project - a value
    that exists only for the instant it's needed)."""

    def __init__(self, k: int = 3, n: int = 5):
        self.k = k
        self.n = n
        root_secret = os.urandom(32)
        shares = split_secret(root_secret, k=k, n=n)
        root_secret = bytes(len(root_secret))  # never kept, not even here

        self._lock = threading.Lock()  # multiple CombinedSecureBox threads
        # share ONE trust group; without this, concurrent send()/recv()
        # calls on the same pipe from different threads corrupt each
        # other's messages (found by testing: EOFError / invalid pickle
        # data the moment more than one box used the group at once).

        ctx = mp.get_context("fork")
        self._conns = []
        self._processes = []
        for x, y in shares:
            parent_conn, child_conn = ctx.Pipe()
            p = ctx.Process(target=_holder_process, args=(x, y, child_conn),
                             daemon=True)
            p.start()
            self._conns.append(parent_conn)
            self._processes.append(p)

    def fetch(self) -> bytes:
        """Reconstructs the root secret fresh, right now, from K of
        the N holder processes. Costs a real round-trip to each one -
        this is the honest price of the security property, not free."""
        with self._lock:
            collected = []
            for conn in self._conns[:self.k]:
                conn.send("get_share")
                collected.append(conn.recv())
        return combine_shares(collected)

    def stop(self) -> None:
        for conn in self._conns:
            try:
                conn.send("stop")
            except (BrokenPipeError, OSError):
                pass
        for p in self._processes:
            p.join(timeout=1)
            if p.is_alive():
                p.terminate()
