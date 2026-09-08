#!/usr/bin/env python3
"""Follow-up to test_forensics_motion.py, answering two direct
questions: (1) what if there were many spots instead of just 2, and
(2) does destroying the old copy as you go actually help?

Part 1 - MANY SPOTS: builds NFallingBox with N buffers (not just 2)
and runs two different attacks against it:
  - a FULL dump of ALL N buffers (what a real memory-image tool does)
  - a RESOURCE-LIMITED monitor that can only check K random addresses
    out of the N that exist (can't afford to scan everything)
This separates two very different attacker capabilities that "more
hiding spots" affects completely differently.

Part 2 - ZERO THE OLD COPY: builds ZeroingFallingBox (destroys the
previous buffer right after each hop) and checks: does a full dump
still find the live data? And does it cut down how many REDUNDANT
copies exist in memory at once, compared to the plain version?
"""
import ctypes
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.falling_box import FallingBox
from vstorage.superseded.nbox_falling import NFallingBox
from vstorage.superseded.zeroing_falling import ZeroingFallingBox

MARKER = b"VSTORAGE_SECRET_MARKER_" + os.urandom(8).hex().encode()
PAD_BEFORE = os.urandom(300)
PAD_AFTER = os.urandom(300)
DATA = PAD_BEFORE + MARKER + PAD_AFTER


def buf_addr(ba: bytearray) -> int:
    return ctypes.addressof((ctypes.c_char * len(ba)).from_buffer(ba))


def read_self_mem(addr: int, length: int) -> bytes:
    with open("/proc/self/mem", "rb", buffering=0) as f:
        f.seek(addr)
        return f.read(length)


def part1_many_spots() -> None:
    print("=== Part 1: what if there were many spots, not just 2? ===\n")
    for n_boxes in (2, 8, 64):
        box = NFallingBox(DATA, n_boxes=n_boxes)
        time.sleep(0.05)
        length = len(box.boxes[0])
        addrs = [buf_addr(b) for b in box.boxes]

        # Attack A: FULL dump of every single buffer that exists.
        trials = 100
        found = 0
        for _ in range(trials):
            time.sleep(random.uniform(0, 0.001))
            hit = False
            for addr in addrs:
                if MARKER in read_self_mem(addr, length):
                    hit = True
                    break
            found += hit
        full_dump_rate = 100 * found / trials

        # Attack B: resource-limited monitor - can only check a SMALL
        # fixed number of addresses (K), not all N, each time it looks.
        K = 2
        found_limited = 0
        for _ in range(trials):
            time.sleep(random.uniform(0, 0.001))
            sample = random.sample(addrs, min(K, len(addrs)))
            hit = any(MARKER in read_self_mem(a, length) for a in sample)
            found_limited += hit
        limited_rate = 100 * found_limited / trials

        print(f"  N={n_boxes:3d} buffers:  "
              f"full dump of ALL of them finds it {full_dump_rate:5.1f}% of the time   |   "
              f"a monitor that can only check {K} of the {n_boxes} finds it {limited_rate:5.1f}% of the time")

        box.collapse()

    print("""
  What this shows: a FULL dump always finds it, no matter how many
  spots there are (100 or 2 - same result) - because a full dump reads
  EVERY spot, so having more spots to hide in doesn't matter; they all
  get read anyway. Physical RAM is finite - "unlimited places" isn't
  actually unlimited, and a complete capture captures all of it.

  But a monitor that can only afford to check a FEW spots at a time
  (can't scan everything) finds it far LESS often as N grows - because
  it's gambling on checking the right 2 out of 64 spots. That's a real,
  measurable protection - but only against an attacker who CAN'T look
  everywhere, not one who eventually can.
""")


def part2_zero_the_old_copy() -> None:
    print("=== Part 2: does destroying the old copy actually help? ===\n")

    plain = FallingBox(DATA)
    zeroing = ZeroingFallingBox(DATA)
    time.sleep(0.05)

    length = len(DATA)
    plain_a, plain_b = buf_addr(plain._box_a), buf_addr(plain._box_b)
    zero_a, zero_b = buf_addr(zeroing._box_a), buf_addr(zeroing._box_b)

    print("Q1: does a full dump still find the LIVE data with zeroing on?")
    trials = 200
    found = 0
    for _ in range(trials):
        time.sleep(random.uniform(0, 0.001))
        d1 = read_self_mem(zero_a, length)
        d2 = read_self_mem(zero_b, length)
        if MARKER in d1 or MARKER in d2:
            found += 1
    print(f"    yes - found {found}/{trials} times "
          f"({100*found/trials:.1f}%). Makes sense: the data has to be\n"
          f"    usable somewhere for the system to work at all - zeroing\n"
          f"    the OLD copy doesn't hide the CURRENT one.\n")

    print("Q2: how many REDUNDANT live copies exist at once - plain vs zeroing?")
    plain_double = 0
    zero_double = 0
    for _ in range(trials):
        time.sleep(random.uniform(0, 0.001))
        p1 = read_self_mem(plain_a, length)
        p2 = read_self_mem(plain_b, length)
        if MARKER in p1 and MARKER in p2:
            plain_double += 1
        z1 = read_self_mem(zero_a, length)
        z2 = read_self_mem(zero_b, length)
        if MARKER in z1 and MARKER in z2:
            zero_double += 1
    print(f"    plain (no zeroing):  both buffers held a full live copy "
          f"at the same time in {plain_double}/{trials} checks")
    print(f"    zeroing old copy:    both buffers held a full live copy "
          f"at the same time in {zero_double}/{trials} checks")
    print("""
  What this shows: zeroing the old copy does NOT stop a full dump from
  finding the data while it's in active use - nothing software-only
  can, since the program needs a usable copy to exist to work at all.
  What it DOES do: cut the number of redundant, simultaneous copies
  lying around from 2 down to 1 - less leftover residue, but not
  invisibility.
""")

    plain.collapse()
    zeroing.collapse()


if __name__ == "__main__":
    part1_many_spots()
    part2_zero_the_old_copy()
