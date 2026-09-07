#!/usr/bin/env python3
"""Actually tests the claim I made without testing: does the falling
motion defeat memory forensics? Three different, real attack models,
against this process's OWN memory (legitimate self-inspection via
/proc/self/mem - no privilege escalation, no other process touched).

A unique marker is planted inside the data held by a real FallingBox.
The falling thread runs for real, in the background, the whole time.

Attack 1 - FULL DUMP, single instant: read BOTH internal buffers
  (box_a and box_b) completely, at a random moment. This is what a
  real memory-image tool captures - the WHOLE process, not just one
  buffer. Question: does motion hide the marker from this?

Attack 2 - FIXED-ADDRESS MONITOR: attacker learned box_a's address
  once (e.g. from an earlier leak) and only re-checks that ONE address
  repeatedly, never re-scanning box_b. Question: does motion make the
  marker intermittently absent at that one fixed address?

Attack 3 - SLOW FULL ACQUISITION: real forensic imaging tools take
  real wall-clock time to copy a full memory image (reading it in many
  small chunks, page by page) - they are not instantaneous. While that
  slow read is happening, does the data keep falling underneath it and
  tear the captured image, or does the marker still survive intact?
"""
import ctypes
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.falling_box import FallingBox

MARKER = b"VSTORAGE_SECRET_MARKER_" + os.urandom(8).hex().encode()
PAD_BEFORE = os.urandom(300)
PAD_AFTER = os.urandom(300)
DATA = PAD_BEFORE + MARKER + PAD_AFTER
MARKER_OFFSET = len(PAD_BEFORE)


def buf_addr(ba: bytearray) -> int:
    """Real address of a bytearray's underlying buffer - not a copy,
    the actual memory this process has mapped."""
    return ctypes.addressof((ctypes.c_char * len(ba)).from_buffer(ba))


def read_self_mem(addr: int, length: int) -> bytes:
    with open("/proc/self/mem", "rb", buffering=0) as f:
        f.seek(addr)
        return f.read(length)


def read_self_mem_chunked(addr: int, length: int, chunk: int, delay: float) -> bytes:
    """Same read, but done the way a real memory-imaging tool actually
    works: many small reads over real wall-clock time, not one instant
    call. This is what gives motion an actual chance to interfere."""
    out = bytearray()
    with open("/proc/self/mem", "rb", buffering=0) as f:
        for off in range(0, length, chunk):
            n = min(chunk, length - off)
            f.seek(addr + off)
            out += f.read(n)
            time.sleep(delay)
    return bytes(out)


def attack1_full_dump(box: FallingBox, trials: int) -> None:
    print(f"\n=== Attack 1: full dump, single instant ({trials} trials) ===")
    print("Reads BOTH box_a and box_b entirely, at a random moment each "
          "time - what a real memory-image tool actually captures.")
    addr_a = buf_addr(box._box_a)
    addr_b = buf_addr(box._box_b)
    length = len(box._box_a)

    found = 0
    for _ in range(trials):
        time.sleep(random.uniform(0, 0.002))
        dump_a = read_self_mem(addr_a, length)
        dump_b = read_self_mem(addr_b, length)
        if MARKER in dump_a or MARKER in dump_b:
            found += 1

    print(f"  marker found: {found}/{trials} full dumps "
          f"({100 * found / trials:.1f}%)")


def attack2_fixed_address(box: FallingBox, trials: int) -> None:
    print(f"\n=== Attack 2: fixed-address monitor ({trials} trials) ===")
    print("Only ever reads box_a's address - never re-scans box_b, "
          "the way a monitor that cached one address once would.")
    addr_a = buf_addr(box._box_a)
    length = len(box._box_a)

    found = 0
    for _ in range(trials):
        time.sleep(random.uniform(0, 0.002))
        dump_a = read_self_mem(addr_a, length)
        if MARKER in dump_a:
            found += 1

    print(f"  marker found at that one fixed address: {found}/{trials} "
          f"({100 * found / trials:.1f}%)")


def attack3_slow_acquisition(box: FallingBox, trials: int) -> None:
    print(f"\n=== Attack 3: slow full acquisition ({trials} trials) ===")
    print("Reads the full buffer in small chunks with real delay between "
          "chunks - like a real forensic imaging tool, not an instant call. "
          "Long enough per trial for many hops to happen underneath it.")
    addr_a = buf_addr(box._box_a)
    addr_b = buf_addr(box._box_b)
    length = len(box._box_a)

    intact = 0
    torn_but_found = 0
    missed = 0
    for _ in range(trials):
        image_a = read_self_mem_chunked(addr_a, length, chunk=16, delay=0.0005)
        image_b = read_self_mem_chunked(addr_b, length, chunk=16, delay=0.0005)
        combined_found = (MARKER in image_a) or (MARKER in image_b)
        clean_a = image_a == bytes(DATA)
        clean_b = image_b == bytes(DATA)
        if combined_found and (clean_a or clean_b):
            intact += 1
        elif combined_found:
            torn_but_found += 1
        else:
            missed += 1

    print(f"  clean, complete capture with marker intact: {intact}/{trials}")
    print(f"  buffer was torn mid-read, but marker still recoverable: "
          f"{torn_but_found}/{trials}")
    print(f"  marker NOT found anywhere in either slow image: "
          f"{missed}/{trials}")


def main() -> None:
    print(f"Marker planted: {MARKER!r}")
    print(f"Held inside {len(DATA)} bytes, at offset {MARKER_OFFSET}, "
          f"falling for real in a background thread the whole time.")

    box = FallingBox(DATA)
    time.sleep(0.05)
    print(f"box_a address: {hex(buf_addr(box._box_a))}   "
          f"box_b address: {hex(buf_addr(box._box_b))}")
    print(f"hops so far: {box.hops}")

    attack1_full_dump(box, trials=300)
    attack2_fixed_address(box, trials=300)
    attack3_slow_acquisition(box, trials=100)

    print(f"\ntotal hops during the whole test: {box.hops}")
    box.collapse()


if __name__ == "__main__":
    main()
