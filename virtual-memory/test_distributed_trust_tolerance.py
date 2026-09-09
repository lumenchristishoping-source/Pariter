#!/usr/bin/env python3
"""Confirms the new default policy: a single compromised holder gives
an attacker literally zero information about the secret (k=3 needed),
and this system is RAM-only - killing the main process always means
total, permanent loss of whatever file it's holding. So the default
no longer kills on the first compromised holder; it kills once k-1
are gone (one compromise away from an attacker actually succeeding).

Two real scenarios, both using real ptrace_attach, not simulated:
  A. attack 1 of 5 holders (k=3, so default kill_threshold=2) ->
     main should SURVIVE, and fetch() should still work using the
     remaining 4 holders.
  B. attack 2 of 5 holders -> main should DIE, same as before.
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

group = DistributedTrustGroup(k=3, n=5)  # default kill_threshold (k-1=2)
holder_pids = [p.pid for p in group._processes]
print(f"{{os.getpid()}} {{','.join(str(p) for p in holder_pids)}}", flush=True)

if {fetch_after!r}:
    time.sleep(1.5)  # let the attack(s) land first
    try:
        secret = group.fetch()
        print(f"FETCH_OK {{len(secret)}}", flush=True)
    except Exception as e:
        print(f"FETCH_FAILED {{type(e).__name__}}: {{e}}", flush=True)

time.sleep(6)
"""


def _attack(pid: int) -> None:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    libc.ptrace(PTRACE_ATTACH, pid, None, None)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            content = f.read()
    except FileNotFoundError:
        return False
    return content[content.rfind(")") + 2] != "Z"


def run_scenario(num_attacked: int, fetch_after: bool) -> bool:
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir = tempfile.mkdtemp(prefix="dist_trust_tolerance_test_")
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
    print(f"main pid={main_pid}, holder pids={holder_pids}")
    print(f"attacking {num_attacked} holder(s): {targets}")

    time.sleep(0.3)
    for pid in targets:
        _attack(pid)

    main_survived = True
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not _alive(main_pid):
            main_survived = False
            break
        time.sleep(0.05)

    print(f"main process survived 3s after attack: {main_survived}")

    if fetch_after and main_survived:
        # give the fetch() call (issued 1.5s after start inside the
        # child) time to run and print its result
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
    print("=== Scenario A: attack 1 of 5 holders (below kill_threshold=2) ===")
    survived_a = run_scenario(num_attacked=1, fetch_after=True)
    a_pass = survived_a
    print(f"EXPECTED: main survives, fetch() still works -> "
          f"{'PASS' if a_pass else 'FAIL'}\n")

    print("=== Scenario B: attack 2 of 5 holders (meets kill_threshold=2) ===")
    survived_b = run_scenario(num_attacked=2, fetch_after=False)
    b_pass = not survived_b
    print(f"EXPECTED: main dies -> {'PASS' if b_pass else 'FAIL'}\n")

    print("=== RESULT ===")
    print(f"Scenario A (tolerate 1): {'PASS' if a_pass else 'FAIL'}")
    print(f"Scenario B (kill at 2):  {'PASS' if b_pass else 'FAIL'}")


if __name__ == "__main__":
    main()
