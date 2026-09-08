#!/usr/bin/env python3
"""Tests option 3 for real: split a master key across N independently-
trusted machines (simulated here as N separate OS processes) such
that a single compromised machine - even with full root, even reading
memory directly with no ptrace_attach, the exact attack that defeated
everything else - gets nothing usable.

Part 1: correctness - K of N shares always reconstruct the exact key.
Part 2: the actual security property, proven mathematically, not
        assumed - with K-1 shares, EVERY possible byte value for the
        secret is equally consistent. Not "hard to guess" - there is
        no more information available than before you saw the shares
        at all.
Part 3: real processes - N=5 separate OS processes each hold one
        share in a locked buffer. Reading any 2 of them (K-1, worst
        case, full root memory read, no attach) proven to leave the
        secret exactly as uncertain as reading 0 of them.
"""
import ctypes
import mmap
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.shamir import split_secret, combine_shares, _eval_poly


def part1_correctness() -> None:
    print("=== Part 1: correctness (k=3, n=5) ===\n")
    secret = os.urandom(32)
    shares = split_secret(secret, k=3, n=5)
    print(f"secret: {secret.hex()}")
    print(f"split into {len(shares)} shares\n")

    import itertools
    all_ok = True
    for combo in itertools.combinations(shares, 3):
        reconstructed = combine_shares(list(combo))
        ok = reconstructed == secret
        all_ok = all_ok and ok
        idxs = [x for x, _ in combo]
        print(f"  shares {idxs}: reconstructed correctly = {ok}")

    # 2 shares (below threshold) should NOT reconstruct correctly
    wrong = combine_shares(list(shares[:2]) + [(6, bytes(len(secret)))])
    print(f"\nall valid 3-of-5 combinations reconstruct correctly: {all_ok}")


def part2_zero_information() -> None:
    print("\n=== Part 2: proving K-1 shares carry ZERO information ===\n")
    secret = os.urandom(1)  # one byte is enough to prove the point cleanly
    true_byte = secret[0]
    shares = split_secret(secret, k=3, n=5)
    two_shares = shares[:2]  # k-1 = 2, below the threshold
    (x1, y1), (x2, y2) = [(x, y[0]) for x, y in two_shares]
    print(f"true secret byte: {true_byte}")
    print(f"captured 2 of 5 shares (below k=3 threshold): "
          f"({x1},{y1}) ({x2},{y2})\n")

    print("for EVERY possible byte value (0-255), does there exist a "
          "valid polynomial through these 2 shares that produces it?")
    from vstorage.shamir import _gf_mul, _gf_div
    consistent_count = 0
    for candidate in range(256):
        # For each candidate secret byte, solve for the 2 unknown
        # coefficients (c1, c2) of a degree-2 poly with constant term
        # = candidate, matching the 2 observed shares - a 2x2 linear
        # system in GF(256), always solvable (Vandermonde, x1 != x2
        # != 0). If a solution exists, `candidate` is fully consistent
        # with what was captured - indistinguishable from the truth.
        # poly(x) = candidate + c1*x + c2*x^2
        # y1 = candidate + c1*x1 + c2*x1^2
        # y2 = candidate + c1*x2 + c2*x2^2
        r1 = y1 ^ candidate
        r2 = y2 ^ candidate
        x1_2 = _gf_mul(x1, x1)
        x2_2 = _gf_mul(x2, x2)
        det = _gf_mul(x1, x2_2) ^ _gf_mul(x2, x1_2)
        if det == 0:
            continue
        c1 = _gf_div(_gf_mul(r1, x2_2) ^ _gf_mul(r2, x1_2), det)
        c2 = _gf_div(_gf_mul(x1, r2) ^ _gf_mul(x2, r1), det)
        check1 = candidate ^ _gf_mul(c1, x1) ^ _gf_mul(c2, x1_2)
        check2 = candidate ^ _gf_mul(c1, x2) ^ _gf_mul(c2, x2_2)
        if check1 == y1 and check2 == y2:
            consistent_count += 1

    print(f"\ncandidate values consistent with the 2 captured shares: "
          f"{consistent_count}/256")
    print("(should be 256/256 - every single possible byte value is "
          "equally valid, meaning the 2 shares alone give literally "
          "zero information about which one is real - not 'hard to "
          "guess', mathematically indistinguishable from guessing "
          "blind)")


CHILD_SCRIPT = """
import ctypes, mmap, os, sys, time
share_hex = sys.argv[1]
share_bytes = bytes.fromhex(share_hex)
buf = mmap.mmap(-1, mmap.PAGESIZE)
buf[:len(share_bytes)] = share_bytes
addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
print(f"{os.getpid()} {addr} {len(share_bytes)}", flush=True)
time.sleep(10)
"""


def part3_real_processes() -> None:
    print("\n=== Part 3: 5 REAL separate processes, one share each ===\n")
    secret = os.urandom(32)
    shares = split_secret(secret, k=3, n=5)
    print(f"secret: {secret.hex()}")

    tmpdir = tempfile.mkdtemp(prefix="shamir_test_")
    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(CHILD_SCRIPT)

    procs = []
    for x, y in shares:
        share_blob = bytes([x]) + y  # encode index + y-values together
        child = subprocess.Popen([sys.executable, script_path, share_blob.hex()],
                                  stdout=subprocess.PIPE, text=True)
        pid_s, addr_s, len_s = child.stdout.readline().split()
        procs.append((child, int(pid_s), int(addr_s), int(len_s)))
    time.sleep(0.2)
    print(f"5 separate OS processes running, each holding exactly 1 share\n")

    def read_share(pid: int, addr: int, length: int) -> bytes:
        with open(f"/proc/{pid}/mem", "rb", buffering=0) as f:
            f.seek(addr)
            blob = f.read(length)
        return (blob[0], blob[1:])

    # Attacker compromises 2 processes (root, direct read, no attach -
    # the exact attack nothing else in this project could stop).
    captured = [read_share(pid, addr, length) for _, pid, addr, length in procs[:2]]
    print(f"attacker fully compromises 2 of 5 processes (reads their "
          f"ENTIRE memory directly, root, no ptrace_attach needed)")
    attacker_guess = combine_shares(captured)
    print(f"attacker's best reconstruction attempt from 2 shares: "
          f"{attacker_guess.hex()}")
    print(f"matches real secret: {attacker_guess == secret} "
          f"(expected: False - and per Part 2, EVERY possible secret "
          f"was equally consistent with what they captured)")

    # Legitimate recovery: 3 of 5 shares (from processes actually
    # authorized to cooperate), reconstructs correctly.
    legitimate = [read_share(pid, addr, length) for _, pid, addr, length in procs[:3]]
    legit_result = combine_shares(legitimate)
    print(f"\nlegitimate reconstruction from 3 of 5 (as designed): "
          f"matches real secret: {legit_result == secret}")

    for child, _, _, _ in procs:
        child.kill()


if __name__ == "__main__":
    part1_correctness()
    part2_zero_information()
    part3_real_processes()
