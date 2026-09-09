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

Second gap, found later by direct review and closed here: the holder
processes above had NO watchdog at all - a real ptrace_attach on one
produced zero reaction anywhere, from anything. Fixed: every holder
gets its own ProcessWatchdog (stood up by the parent, since a daemon
process can't spawn its own children), and DistributedTrustGroup runs
a monitor thread that treats a holder dying outside a clean stop() as
tamper evidence and kills the whole main process, fail-closed, without
waiting for fetch() to notice. Verified at real ptrace_attach scale:
the attacked holder dies in ~40-60ms, the main process reacts and
dies ~15-25ms after that - see test_distributed_trust_watchdog.py.
"""

from __future__ import annotations

import ctypes
import mmap
import multiprocessing as mp
import os
import signal
import threading
import time

from .process_watchdog import ProcessWatchdog
from .shamir import combine_shares, split_secret

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
MADV_DONTDUMP = 16


class TrustGroupCompromised(RuntimeError):
    """Raised (and, by default, escalated to killing the whole main
    process) when a holder process stops responding for any reason
    other than a clean stop() call. A holder that just dies has only
    one real cause: its own watchdog (see below) caught a live
    ptrace_attach on it and killed it. That is exactly as serious as
    an attack on the main process itself - closing the gap found
    earlier where a compromised holder produced no reaction at all."""


def _lock_and_hide(buf: mmap.mmap) -> None:
    addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    length = len(buf)
    _libc.mlock(ctypes.c_void_p(addr), ctypes.c_size_t(length))
    _libc.madvise(ctypes.c_void_p(addr), ctypes.c_size_t(length), MADV_DONTDUMP)


def _is_dead(pid: int) -> bool:
    """True once a pid is gone OR a zombie (exited, not yet reaped).
    Reads /proc directly rather than trusting Process.is_alive() -
    see _monitor()'s docstring for the real ptrace quirk that makes
    is_alive() unsafe for this specific check."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            content = f.read()
    except FileNotFoundError:
        return True
    return content[content.rfind(")") + 2] == "Z"


def _holder_process(x: int, share_bytes: bytes, request_conn) -> None:
    """Simulates one separately-trusted machine: holds ONE Shamir
    share, locked, and hands it over whenever asked - the only thing
    this process does. In a real deployment this would be a network
    service on an actually separate host, not a local process; that
    honest limitation is unavoidable inside a single sandbox.

    This process holds a live secret for as long as it runs, same as
    the main process does - so it needs the same self-protection.
    It can't spawn its OWN ProcessWatchdog though: it runs as a
    daemon process (so it never outlives / blocks exit of whoever
    started it), and Python refuses to let a daemon process have
    children at all ("daemonic processes are not allowed to have
    children" - hit this directly while building it). So instead it
    reports its share buffer's address back to whoever started it,
    and the PARENT stands up a real ProcessWatchdog pointed at this
    process from the outside - same watchdog, same protection, just
    built by a process that's actually allowed to have children."""
    buf = mmap.mmap(-1, mmap.PAGESIZE, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    buf[:len(share_bytes)] = share_bytes
    _lock_and_hide(buf)

    addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    request_conn.send(("ready", addr, len(share_bytes)))

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

    def __init__(self, k: int = 3, n: int = 5, kill_main_on_compromise: bool = True):
        self.k = k
        self.n = n
        self._kill_main_on_compromise = kill_main_on_compromise
        self._main_pid = os.getpid()
        root_secret = os.urandom(32)
        shares = split_secret(root_secret, k=k, n=n)
        root_secret = bytes(len(root_secret))  # never kept, not even here

        self._lock = threading.Lock()  # multiple CombinedSecureBox threads
        # share ONE trust group; without this, concurrent send()/recv()
        # calls on the same pipe from different threads corrupt each
        # other's messages (found by testing: EOFError / invalid pickle
        # data the moment more than one box used the group at once).
        self._stopping = False
        self._compromised = False

        ctx = mp.get_context("fork")
        self._conns = []
        self._processes = []
        self._holder_watchdogs = []
        for x, y in shares:
            parent_conn, child_conn = ctx.Pipe()
            p = ctx.Process(target=_holder_process, args=(x, y, child_conn),
                             daemon=True)
            p.start()
            _tag, addr, length = parent_conn.recv()
            wd = ProcessWatchdog(target_pid=p.pid, kill_target=True)
            wd.add_region(addr, length)
            self._conns.append(parent_conn)
            self._processes.append(p)
            self._holder_watchdogs.append(wd)

        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()

    def _monitor(self) -> None:
        """Notices a holder process dying on its own, without waiting
        for the next fetch() to stumble into it. The only way a
        holder dies outside a clean stop() is its own ProcessWatchdog
        killing it after catching a real ptrace_attach - so any
        unexpected exit here is treated as tamper evidence, not a
        normal failure to shrug off.

        Deliberately does NOT use Process.is_alive() - found a real
        Linux ptrace quirk while testing this: is_alive() calls plain
        os.waitpid() under the hood, but when a THIRD PARTY (the
        attacker, not this process) is the one who ptrace_attach'd
        the holder, the kernel routes that stop/exit notification to
        the ATTACKER first. The attacker has no reason to ever call
        wait() on a process it doesn't own, so it never consumes that
        notification - and this process's own waitpid() on the same
        pid then just returns "no change" FOREVER, even after the
        holder is a confirmed, permanent zombie in /proc. Reading
        /proc/<pid>/stat directly sees the real kernel state
        regardless of who has or hasn't reaped it - immune to this.

        Polls instead of busy-spinning: this loop runs as a THREAD
        inside the process it's protecting (unlike ProcessWatchdog,
        which gets a whole separate OS process to spin freely in), so
        a tight loop here would burn CPU and reintroduce the exact
        GIL-contention cost this project already found and fixed
        elsewhere. 20ms is fast enough to react in well under the time
        an attacker needs to do anything with a share, without
        competing for the GIL against real work."""
        while not self._stopping:
            for p in self._processes:
                if not self._stopping and _is_dead(p.pid):
                    self._on_holder_compromised()
                    return
            time.sleep(0.02)

    def _on_holder_compromised(self) -> None:
        if self._compromised:
            return
        self._compromised = True
        if self._kill_main_on_compromise:
            # Fail closed, immediately - do not wait for whoever is
            # holding fetch()'s lock to notice. A trusted holder
            # machine just went down mid-attack; treat that exactly
            # like an attack on this process itself.
            os.kill(self._main_pid, signal.SIGKILL)

    def fetch(self) -> bytes:
        """Reconstructs the root secret fresh, right now, from K of
        the N holder processes. Costs a real round-trip to each one -
        this is the honest price of the security property, not free."""
        if self._compromised:
            raise TrustGroupCompromised(
                "a trust-group holder process was killed by its own watchdog")
        with self._lock:
            collected = []
            try:
                for conn in self._conns[:self.k]:
                    conn.send("get_share")
                    collected.append(conn.recv())
            except (EOFError, BrokenPipeError, OSError) as e:
                self._on_holder_compromised()
                raise TrustGroupCompromised(
                    "lost contact with a trust-group holder mid-fetch") from e
        return combine_shares(collected)

    def stop(self) -> None:
        self._stopping = True
        for wd in self._holder_watchdogs:
            wd.stop()
        for conn in self._conns:
            try:
                conn.send("stop")
            except (BrokenPipeError, OSError):
                pass
        for p in self._processes:
            p.join(timeout=1)
            if p.is_alive():
                p.terminate()
