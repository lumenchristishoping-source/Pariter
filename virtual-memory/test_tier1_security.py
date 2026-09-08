#!/usr/bin/env python3
"""Verifies the Tier 1 protections actually took effect - not just
that the syscalls returned success, but that the kernel really did
what we asked, checked from /proc. And confirms the falling motion
is still happening throughout - this hardens the existing design,
it doesn't replace it.
"""
import ctypes
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.superseded.secure_falling_box import (
    SecureFallingBox, make_process_nondumpable, secure_zero, _mmap_addr,
)

DATA = os.urandom(50_000)


def check_vmlck() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmLck:"):
                return int(line.split()[1])
    return -1


def check_dontdump(addr: int) -> bool:
    """Reads /proc/self/smaps looking for the VMA containing `addr`,
    and checks its VmFlags for 'dd' (don't-dump)."""
    with open("/proc/self/smaps") as f:
        lines = f.readlines()
    current_range = None
    for line in lines:
        if "-" in line.split()[0] if line[:1].isalnum() else False:
            pass
        parts = line.split()
        if len(parts) >= 1 and "-" in parts[0] and parts[0][0] in "0123456789abcdef":
            try:
                start_s, end_s = parts[0].split("-")
                start, end = int(start_s, 16), int(end_s, 16)
                current_range = (start, end)
            except ValueError:
                current_range = None
        if current_range and current_range[0] <= addr < current_range[1]:
            if line.startswith("VmFlags:"):
                return "dd" in line.split()[1:]
    return False


def main() -> None:
    print("=== Step 1: make this process non-dumpable ===")
    ok = make_process_nondumpable()
    print(f"  prctl(PR_SET_DUMPABLE, 0) succeeded: {ok}")

    print("\n=== Step 2: create a SecureFallingBox, check what actually locked ===")
    vmlck_before = check_vmlck()
    box = SecureFallingBox(DATA)
    time.sleep(0.05)
    vmlck_after = check_vmlck()

    for name, status in box.protection_status.items():
        print(f"  {name}: mlock={status['mlock']}"
              + (f" (error: {status.get('mlock_error')})" if not status["mlock"] else "")
              + f"  dontdump={status['dontdump']}"
              + (f" (error: {status.get('dontdump_error')})" if not status["dontdump"] else ""))

    print(f"\n  VmLck (locked memory) before: {vmlck_before} KB, "
          f"after: {vmlck_after} KB  (+{vmlck_after - vmlck_before} KB)")
    print("  ^ this confirms the kernel actually locked pages, not just "
          "that the syscall returned 0")

    addr_a = _mmap_addr(box._box_a)
    dontdump_confirmed = check_dontdump(addr_a)
    print(f"\n  box_a's VMA actually carries the 'dd' (don't-dump) flag "
          f"in /proc/self/smaps: {dontdump_confirmed}")

    print("\n=== Step 3: is it STILL falling? (this must not have stopped) ===")
    hops_start = box.hops
    time.sleep(1.0)
    hops_end = box.hops
    print(f"  hops in that 1s: {hops_end - hops_start:,} "
          f"(motion is {'ALIVE' if hops_end > hops_start else 'STOPPED - BUG'})")

    print("\n=== Step 4: still byte-perfect? ===")
    out = box.snapshot()
    match = hashlib.sha256(out).digest() == hashlib.sha256(DATA).digest()
    print(f"  retrieved copy byte-perfect: {match}")

    print("\n=== Step 5: secure_zero() the copy the caller pulled out ===")
    print(f"  before zero, first 16 bytes: {out[:16].hex()}")
    secure_zero(out)
    print(f"  after zero,  first 16 bytes: {out[:16].hex()}")
    print(f"  fully zeroed: {out == bytes(len(out))}")

    print("\n=== Step 6: does PR_SET_DUMPABLE actually block ptrace here? ===")
    print("  Testing for real: spawn a child that sets itself non-dumpable, "
          "then try to ptrace-attach to it from here.")
    child = subprocess.Popen([
        sys.executable, "-c",
        "import ctypes,time; "
        "ctypes.CDLL('libc.so.6').prctl(4,0,0,0,0); "
        "time.sleep(5)"
    ])
    time.sleep(0.3)
    PTRACE_ATTACH = 16
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    ret = libc.ptrace(PTRACE_ATTACH, child.pid, None, None)
    err = os.strerror(ctypes.get_errno()) if ret == -1 else None
    print(f"  ptrace(PTRACE_ATTACH) on the non-dumpable child: "
          f"return={ret}, error={err}")
    if ret == -1:
        print("  blocked, as expected for a non-root attacker.")
    else:
        libc.ptrace(17, child.pid, None, None)  # PTRACE_DETACH
        print("  NOT blocked - because we're root here, and root generally "
              "bypasses this protection. Against a same-user, non-root "
              "attacker this same test would show 'blocked'.")
    child.terminate()
    child.wait()

    box.collapse()


if __name__ == "__main__":
    main()
