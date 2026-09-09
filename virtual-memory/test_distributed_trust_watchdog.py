#!/usr/bin/env python3
"""Confirms the fix for the gap found while reviewing this project: a
distributed-trust holder process used to have NO watchdog at all - a
real ptrace_attach on one produced zero reaction anywhere. Now each
holder self-protects, and the main process reacts to a holder dying.

Runs a real DistributedTrustGroup inside a CHILD process (so this test
can kill it without killing itself), ptrace_attaches one of its real
holder processes from the outside, and confirms:
  1. that holder process gets killed by its OWN watchdog
  2. the child ("main") process gets killed too, fast, without ever
     calling fetch() itself - proving this is detected proactively,
     not just the next time someone happens to ask for the secret
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time

CHILD_SCRIPT = """
import sys, os, time
sys.path.insert(0, {vstorage_dir!r})
from vstorage.distributed_key import DistributedTrustGroup

group = DistributedTrustGroup(k=3, n=5, kill_main_on_compromise=True)
holder_pids = [p.pid for p in group._processes]
print(f"{{os.getpid()}} {{','.join(str(p) for p in holder_pids)}}", flush=True)
time.sleep(10)
"""


def main() -> None:
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir = tempfile.mkdtemp(prefix="dist_trust_watchdog_test_")
    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(CHILD_SCRIPT.format(vstorage_dir=vstorage_dir))

    # new session/process group: lets cleanup below kill the WHOLE
    # tree in one shot. Needed because kill_main_on_compromise's
    # SIGKILL is uncatchable - the main process's daemon children
    # (the other holders + their watchdogs) never get an atexit
    # chance to clean up and would otherwise leak as orphans. Found
    # this the hard way: an earlier version of this test only killed
    # main_pid + target_holder and left ~140 busy-spinning orphan
    # processes behind after repeated runs.
    child = subprocess.Popen([sys.executable, script_path],
                              stdout=subprocess.PIPE, text=True,
                              start_new_session=True)
    line = child.stdout.readline()
    main_pid_s, holder_pids_s = line.split()
    main_pid = int(main_pid_s)
    holder_pids = [int(x) for x in holder_pids_s.split(",")]
    target_holder = holder_pids[2]  # one of the k=3 needed to reconstruct
    print(f"main pid={main_pid}")
    print(f"holder pids={holder_pids}")
    print(f"attacking holder pid={target_holder} (never called fetch())")

    time.sleep(0.3)  # let each holder's own watchdog finish starting

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    t_attach = time.time()
    ret = libc.ptrace(PTRACE_ATTACH, target_holder, None, None)
    print(f"ptrace_attach returned {ret}")
    try:
        with open(f"/proc/{target_holder}/status") as f:
            for line in f:
                if line.startswith("TracerPid"):
                    print(f"  {line.strip()}")
    except FileNotFoundError:
        print("  holder already gone")

    holder_dead_at = None
    main_dead_at = None
    for _ in range(400):
        now = time.time()
        if holder_dead_at is None and not _alive(target_holder):
            holder_dead_at = now
        if main_dead_at is None and not _alive(main_pid):
            main_dead_at = now
        if holder_dead_at and main_dead_at:
            break
        time.sleep(0.005)

    if holder_dead_at:
        print(f"\nattacked holder process died at "
              f"+{(holder_dead_at - t_attach) * 1000:.1f}ms after attach "
              f"(its own ProcessWatchdog caught the attach and killed it)")
    else:
        print("\nFAIL: attacked holder process never died")

    if main_dead_at:
        print(f"main process died at "
              f"+{(main_dead_at - t_attach) * 1000:.1f}ms after attach "
              f"(DistributedTrustGroup's monitor thread reacted to the "
              f"holder dying - main NEVER called fetch())")
        print("\nRESULT: PASS - a compromised holder now kills the "
              "whole system, fail-closed, without waiting for fetch()")
    else:
        print("\nFAIL: main process never reacted to the holder dying")

    try:
        os.killpg(os.getpgid(child.pid), 9)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _alive(pid: int) -> bool:
    """os.kill(pid, 0) alone isn't enough: a zombie process (already
    dead, just not yet reaped by its parent) still answers that check
    as if it were alive - hit this directly running this test. Read
    /proc/<pid>/stat's state field instead; 'Z' means it's really
    gone, whether or not anyone has reaped it yet."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            content = f.read()
    except FileNotFoundError:
        return False
    state = content[content.rfind(")") + 2]
    return state != "Z"


if __name__ == "__main__":
    main()
