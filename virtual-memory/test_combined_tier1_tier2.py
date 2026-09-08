#!/usr/bin/env python3
"""Tier 1 + Tier 2 + split key, all together, tested as one system -
not three separate claims taken on faith.
"""
import ctypes
import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.superseded.combined_secure_box import (
    CombinedSecureBox, make_process_nondumpable, _mmap_addr,
)
from vstorage.measure_full import full_snapshot
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import lzma

NONCE_LEN_TEST = 12
MARKER = b"VSTORAGE_SECRET_MARKER_" + os.urandom(8).hex().encode()
PAD_BEFORE = os.urandom(300)
PAD_AFTER = os.urandom(300)
DATA = PAD_BEFORE + MARKER + PAD_AFTER


def read_self_mem(addr: int, length: int) -> bytes:
    with open("/proc/self/mem", "rb", buffering=0) as f:
        f.seek(addr)
        return f.read(length)


def check_dontdump(addr: int) -> bool:
    with open("/proc/self/smaps") as f:
        lines = f.readlines()
    current_range = None
    for line in lines:
        parts = line.split()
        if parts and "-" in parts[0] and parts[0][0] in "0123456789abcdef":
            try:
                s, e = parts[0].split("-")
                current_range = (int(s, 16), int(e, 16))
            except ValueError:
                current_range = None
        if current_range and current_range[0] <= addr < current_range[1]:
            if line.startswith("VmFlags:"):
                return "dd" in line.split()[1:]
    return False


def main() -> None:
    print("=== Setup ===")
    print(f"  process non-dumpable: {make_process_nondumpable()}")
    threads_before = threading.active_count()
    mem_before = full_snapshot()["RssAnon"]

    box = CombinedSecureBox(DATA)
    time.sleep(0.3)

    threads_after = threading.active_count()
    mem_after = full_snapshot()["RssAnon"]
    print(f"  threads: {threads_before} -> {threads_after} "
          f"(+{threads_after - threads_before} - 1 data thread + "
          f"{threads_after - threads_before - 1} key-byte-cell threads)")
    print(f"  RssAnon: {mem_before} KB -> {mem_after} KB "
          f"(+{mem_after - mem_before} KB - real, honest cost of 88 "
          f"separately-locked key-byte cells)\n")

    print("=== Correctness ===")
    out = box.snapshot()
    print(f"  round-trip byte-perfect: {bytes(out) == DATA}\n")

    print("=== Protections actually verified from /proc ===")
    data_addr = _mmap_addr(box._buf)
    print(f"  data buffer dontdump flag set: {check_dontdump(data_addr)}")
    with open("/proc/self/status") as f:
        vmlck = next(l for l in f if l.startswith("VmLck:"))
    print(f"  {vmlck.strip()} (should be well above 0 - data buffer + "
          f"176 key-cell pages, all locked)\n")

    print("=== Independent timing: do the 88 key-byte cells actually "
          "hop on DIFFERENT schedules? ===")
    hops1 = box.key_cell_hops()
    time.sleep(0.5)
    hops2 = box.key_cell_hops()
    deltas = [b - a for a, b in zip(hops1, hops2)]
    print(f"  hop counts across 88 cells in 0.5s - min={min(deltas)}, "
          f"max={max(deltas)}, distinct values seen={len(set(deltas))}")
    print(f"  (if they were synchronized, every cell would show the "
          f"exact same delta)\n")

    print("=== Attack: full dump of the DATA buffer (300 trials) ===")
    length = box._buf_len
    found = 0
    for _ in range(300):
        time.sleep(random.uniform(0, 0.001))
        if MARKER in read_self_mem(data_addr, length):
            found += 1
    print(f"  plaintext marker found: {found}/300\n")

    print("=== Attack: reconstruct the key from a PARTIAL capture "
          "(87 of 88 cells - missing just ONE byte) ===")
    # Pause the box first so the key cells and the ciphertext are read
    # as one consistent, matching pair - exactly like a real attacker's
    # "simultaneous" full dump would capture them, and exactly like
    # snapshot() does internally.
    box._paused.set()
    with box._lock:
        all_bytes = [cell.read() for cell in box._key_guard._cells]
        ciphertext_at_capture = bytes(
            box._buf[:NONCE_LEN_TEST + box._payload_len])
    box._paused.clear()

    tampered = list(all_bytes)
    tampered[40] = (tampered[40] + 1) % 256  # simulate 1 byte wrong/missing
    try:
        key = lzma.decompress(bytes(tampered))
        print(f"  reconstruction with 1 wrong byte: SUCCEEDED "
              f"(unexpected - key={key.hex()[:16]}...)")
    except Exception as e:
        print(f"  reconstruction with 1 wrong byte: FAILED as expected "
              f"({type(e).__name__}) - all 88 bytes are required, "
              f"missing/wrong even one breaks it entirely")

    correct_key = lzma.decompress(bytes(all_bytes))
    nonce = ciphertext_at_capture[:12]
    ct = ciphertext_at_capture[12:]
    pt = AESGCM(correct_key).decrypt(nonce, ct, None)
    print(f"  reconstruction with all 88 bytes CORRECT, captured "
          f"TOGETHER with the matching ciphertext: SUCCEEDED, "
          f"marker recoverable={MARKER in pt}")
    print("  (this is the honest limit again: catching ALL 88 cells AND "
          "the matching ciphertext together, at the same instant, still "
          "works - same physics as before, just now with 88 pieces "
          "instead of 1. A moment earlier, when I read them separately "
          "without pausing, the ratchet had already moved on between the "
          "two reads and this failed - that mismatch is itself proof the "
          "ratchet is really running in real time.)\n")

    print(f"data-hops during the whole test: {box.hops}")
    box.collapse()


if __name__ == "__main__":
    main()
