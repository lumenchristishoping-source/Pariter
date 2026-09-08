"""FallingBox + the real, honest protections (Tier 1 from the security
discussion): the data still falls, exactly like falling_box.py - this
adds hardening AROUND that motion, it doesn't replace it.

Backed by mmap (real anonymous memory), not a plain bytearray - this
matters, not just style: mlock()/MADV_DONTDUMP need a page-aligned
address, and a bytearray's internal buffer almost never starts on a
page boundary (confirmed by testing: MADV_DONTDUMP failed with
"Invalid argument" on a bytearray-backed buffer). mmap always returns
page-aligned memory, so this actually works.

What each piece actually does:
  mlock()              - the OS is never allowed to swap these pages to
                          disk. Without this, a laptop suspend or memory
                          pressure could write your "never touches disk"
                          data to the swap file - defeating the whole
                          point silently.
  MADV_DONTDUMP        - if this process crashes, the OS may write a
                          core dump file to disk with a full copy of
                          its memory. This excludes these two buffers
                          from that file.
  PR_SET_DUMPABLE(0)   - stops other processes (running as the same
                          user) from attaching a debugger (ptrace) and
                          reading this process's memory live. Note:
                          root can generally still ptrace regardless -
                          this defends against a same-user, non-root
                          attacker, not root itself.

None of this makes the CURRENTLY ACTIVE, in-use copy invisible to a
full memory dump - we already proved nothing software-only can do
that. What it does is close off the other real, common leak paths:
swap files, crash dumps, and casual same-user snooping.
"""

from __future__ import annotations

import ctypes
import mmap
import os
import threading

_libc = ctypes.CDLL("libc.so.6", use_errno=True)

MADV_DONTDUMP = 16
PR_SET_DUMPABLE = 4
PAGE_SIZE = mmap.PAGESIZE


def make_process_nondumpable() -> bool:
    """Blocks other same-user processes from ptrace-attaching to us,
    and disables core dumps for this process (belt-and-suspenders on
    top of ulimit -c). Returns whether the call succeeded."""
    return _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0


def secure_zero(buf) -> None:
    """Overwrite a buffer with zeros - use this on any copy you pulled
    OUT of the falling box (via snapshot()) the moment you're done
    with it. Needs a mutable buffer (bytearray/mmap), not bytes."""
    if buf:
        buf[:] = bytes(len(buf))


def _round_up_page(n: int) -> int:
    return ((max(n, 1) + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE


def _mmap_addr(m: mmap.mmap) -> int:
    return ctypes.addressof((ctypes.c_char * len(m)).from_buffer(m))


def _lock_and_hide(buf: mmap.mmap) -> dict:
    addr = _mmap_addr(buf)
    length = len(buf)

    mlock_ok = _libc.mlock(ctypes.c_void_p(addr), ctypes.c_size_t(length)) == 0
    mlock_err = os.strerror(ctypes.get_errno()) if not mlock_ok else None

    dontdump_ok = _libc.madvise(ctypes.c_void_p(addr), ctypes.c_size_t(length),
                                 MADV_DONTDUMP) == 0
    dontdump_err = os.strerror(ctypes.get_errno()) if not dontdump_ok else None

    return {
        "mlock": mlock_ok, "mlock_error": mlock_err,
        "dontdump": dontdump_ok, "dontdump_error": dontdump_err,
        "addr": addr, "length": length,
    }


class SecureFallingBox:
    """Same bounce-between-two-buffers motion as FallingBox, backed by
    page-aligned anonymous memory so mlock()/MADV_DONTDUMP actually
    take effect (verified in test_tier1_security.py, not assumed)."""

    def __init__(self, data: bytes):
        length = _round_up_page(len(data))
        self._data_len = len(data)
        self._box_a = mmap.mmap(-1, length)
        self._box_b = mmap.mmap(-1, length)
        self._box_a[:len(data)] = data
        self._active = "a"
        self._lock = threading.Lock()
        self._hops = 0
        self._stop = threading.Event()
        self._paused = threading.Event()

        self.protection_status = {
            "box_a": _lock_and_hide(self._box_a),
            "box_b": _lock_and_hide(self._box_b),
        }

        self._thread = threading.Thread(target=self._fall_forever, daemon=True)
        self._thread.start()

    def _fall_forever(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.001)
                continue
            with self._lock:
                if self._active == "a":
                    self._box_b[:] = self._box_a[:]
                    self._active = "b"
                else:
                    self._box_a[:] = self._box_b[:]
                    self._active = "a"
                self._hops += 1

    @property
    def hops(self) -> int:
        return self._hops

    def snapshot(self) -> bytearray:
        self._paused.set()
        with self._lock:
            active = self._box_a if self._active == "a" else self._box_b
            out = bytearray(active[:self._data_len])
        self._paused.clear()
        return out

    def collapse(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._box_a.close()
        self._box_b.close()
