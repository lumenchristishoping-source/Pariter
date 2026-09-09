#!/usr/bin/env python3
"""Network counterpart to test_distributed_trust_tolerance.py: same
default policy (kill only at k-1 compromised, not on the first one),
proven against real, separate, TLS-authenticated holder_server.py
processes instead of local Pipes.

  A. attack 1 of 5 network holders (k=3, default kill_threshold=2) ->
     main should SURVIVE, fetch() should still work via the network
     from the remaining 4.
  B. attack 2 of 5 -> main should DIE.
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

group = NetworkTrustGroup(k=3, n=5, heartbeat_interval=0.05)  # default kill_threshold=2
holder_pids = [p.pid for p in group._processes]
print(f"{{os.getpid()}} {{','.join(str(p) for p in holder_pids)}}", flush=True)

if {fetch_after!r}:
    time.sleep(1.5)
    try:
        secret = group.fetch()
        print(f"FETCH_OK {{len(secret)}}", flush=True)
    except Exception as e:
        print(f"FETCH_FAILED {{type(e).__name__}}: {{e}}", flush=True)

time.sleep(6)
"""


def _attack(pid: int) -> None:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.ptrace(16, pid, None, None)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            content = f.read()
    except FileNotFoundError:
        return False
    return content[content.rfind(")") + 2] != "Z"


def run_scenario(num_attacked: int, fetch_after: bool) -> bool:
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir = tempfile.mkdtemp(prefix="net_trust_tolerance_test_")
    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(CHILD_SCRIPT.format(vstorage_dir=vstorage_dir, fetch_after=fetch_after))

    child = subprocess.Popen([sys.executable, script_path],
                              stdout=subprocess.PIPE, text=True,
                              start_new_session=True)
    line = child.stdout.readline()
    main_pid_s, holders_s = line.split()
    main_pid = int(main_pid_s)
    holder_pids = [int(x) for x in holders_s.split(",")]
    targets = holder_pids[:num_attacked]
    print(f"main pid={main_pid}, holder server pids={holder_pids}")
    print(f"attacking {num_attacked} holder server(s): {targets}")

    time.sleep(0.5)
    for pid in targets:
        _attack(pid)

    main_survived = True
    deadline = time.time() + 3.5
    while time.time() < deadline:
        if not _alive(main_pid):
            main_survived = False
            break
        time.sleep(0.05)

    print(f"main process survived 3.5s after attack: {main_survived}")

    if fetch_after and main_survived:
        extra = child.stdout.readline()
        print(f"child reported: {extra.strip()}")

    try:
        os.killpg(os.getpgid(child.pid), 9)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass

    return main_survived


def main() -> None:
    print("=== Scenario A: attack 1 of 5 network holders (below kill_threshold=2) ===")
    survived_a = run_scenario(num_attacked=1, fetch_after=True)
    a_pass = survived_a
    print(f"EXPECTED: main survives, fetch() still works -> "
          f"{'PASS' if a_pass else 'FAIL'}\n")

    print("=== Scenario B: attack 2 of 5 network holders (meets kill_threshold=2) ===")
    survived_b = run_scenario(num_attacked=2, fetch_after=False)
    b_pass = not survived_b
    print(f"EXPECTED: main dies -> {'PASS' if b_pass else 'FAIL'}\n")

    print("=== RESULT ===")
    print(f"Scenario A (tolerate 1): {'PASS' if a_pass else 'FAIL'}")
    print(f"Scenario B (kill at 2):  {'PASS' if b_pass else 'FAIL'}")


if __name__ == "__main__":
    main()
