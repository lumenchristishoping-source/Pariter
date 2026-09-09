#!/usr/bin/env python3
"""Same proof as test_distributed_trust_watchdog.py, but for the real
network-separated holders (network_trust.py / holder_server.py)
instead of local multiprocessing Pipes.

Confirms:
  1. a real ptrace_attach on a holder_server.py process still gets it
     killed by its own ProcessWatchdog (same as local mode - it's a
     standalone process, not a daemon child, so it can protect
     itself directly).
  2. the main process, watching over a real TCP/TLS connection with
     no /proc access to the holder at all, still notices via its
     heartbeat and reacts according to kill_threshold - proving the
     network-only liveness signal (a dead connection) works as a
     real substitute for the local /proc check.
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
from vstorage.network_trust import NetworkTrustGroup

group = NetworkTrustGroup(k=3, n=5, kill_threshold=1, heartbeat_interval=0.05)
holder_pids = [p.pid for p in group._processes]
print(f"{{os.getpid()}} {{','.join(str(p) for p in holder_pids)}}", flush=True)
time.sleep(10)
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            content = f.read()
    except FileNotFoundError:
        return False
    return content[content.rfind(")") + 2] != "Z"


def main() -> None:
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir = tempfile.mkdtemp(prefix="net_trust_watchdog_test_")
    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(CHILD_SCRIPT.format(vstorage_dir=vstorage_dir))

    child = subprocess.Popen([sys.executable, script_path],
                              stdout=subprocess.PIPE, text=True,
                              start_new_session=True)
    line = child.stdout.readline()
    main_pid_s, holder_pids_s = line.split()
    main_pid = int(main_pid_s)
    holder_pids = [int(x) for x in holder_pids_s.split(",")]
    target_holder = holder_pids[2]
    print(f"main pid={main_pid}")
    print(f"holder server pids={holder_pids}")
    print(f"attacking holder server pid={target_holder} over the network link")

    time.sleep(0.3)

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    t_attach = time.time()
    ret = libc.ptrace(PTRACE_ATTACH, target_holder, None, None)
    print(f"ptrace_attach returned {ret}")

    holder_dead_at = None
    main_dead_at = None
    for _ in range(600):
        now = time.time()
        if holder_dead_at is None and not _alive(target_holder):
            holder_dead_at = now
        if main_dead_at is None and not _alive(main_pid):
            main_dead_at = now
        if holder_dead_at and main_dead_at:
            break
        time.sleep(0.01)

    if holder_dead_at:
        print(f"\nattacked holder server died at "
              f"+{(holder_dead_at - t_attach) * 1000:.1f}ms after attach "
              f"(its own ProcessWatchdog caught the attach and killed it)")
    else:
        print("\nFAIL: attacked holder server never died")

    if main_dead_at:
        print(f"main process died at "
              f"+{(main_dead_at - t_attach) * 1000:.1f}ms after attach "
              f"(NetworkTrustGroup's heartbeat noticed the dead connection "
              f"- no /proc access to the holder was used or needed)")
        print("\nRESULT: PASS - a compromised NETWORK holder still gets "
              "detected and reacted to, purely via connection health")
    else:
        print("\nFAIL: main process never reacted")

    try:
        os.killpg(os.getpgid(child.pid), 9)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


if __name__ == "__main__":
    main()
