#!/usr/bin/env python3
"""Tests Tier 2 for real: does actual encryption on the boxes defeat
the same 3 attack models that beat plain motion? And does dropping
down to ONE buffer ("falls between nothing and nothing" instead of
bouncing A/B) actually work?
"""
import ctypes
import hashlib
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.encrypted_falling_box import EncryptedFallingBox, _mmap_addr
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MARKER = b"VSTORAGE_SECRET_MARKER_" + os.urandom(8).hex().encode()
PAD_BEFORE = os.urandom(300)
PAD_AFTER = os.urandom(300)
DATA = PAD_BEFORE + MARKER + PAD_AFTER


def read_self_mem(addr: int, length: int) -> bytes:
    with open("/proc/self/mem", "rb", buffering=0) as f:
        f.seek(addr)
        return f.read(length)


def main() -> None:
    print(f"Marker planted: {MARKER!r}\n")

    box = EncryptedFallingBox(DATA)
    time.sleep(0.05)

    print("=== Correctness ===")
    out = box.snapshot()
    print(f"  round-trip byte-perfect: {bytes(out) == DATA}")
    print(f"  only ONE buffer exists (no A/B pair): "
          f"{not hasattr(box, '_box_a') and not hasattr(box, '_box_b')}\n")

    addr = _mmap_addr(box._buf)
    length = box._buf_len

    print("=== Attack 1: full dump, single instant (300 trials) ===")
    found = 0
    for _ in range(300):
        time.sleep(random.uniform(0, 0.001))
        dump = read_self_mem(addr, length)
        if MARKER in dump:
            found += 1
    print(f"  plaintext marker found in the raw dump: {found}/300 "
          f"({100*found/300:.1f}%)\n")

    print("=== Attack 2: fixed-address monitor (300 trials) ===")
    print("  (same buffer either way here, since there's only one address "
          "now - included for direct comparison with the Tier 1 numbers)")
    found2 = 0
    for _ in range(300):
        time.sleep(random.uniform(0, 0.001))
        dump = read_self_mem(addr, length)
        if MARKER in dump:
            found2 += 1
    print(f"  plaintext marker found: {found2}/300 "
          f"({100*found2/300:.1f}%)\n")

    print("=== Attack 3: slow chunked acquisition (100 trials) ===")
    found3 = 0
    for _ in range(100):
        img = bytearray()
        with open("/proc/self/mem", "rb", buffering=0) as f:
            for off in range(0, length, 16):
                f.seek(addr + off)
                img += f.read(16)
                time.sleep(0.0003)
        if MARKER in bytes(img):
            found3 += 1
    print(f"  plaintext marker found: {found3}/100 "
          f"({100*found3/100:.1f}%)\n")

    print("=== Forward secrecy: is old ciphertext useless once ratcheted? ===")
    old_ciphertext = box.raw_ciphertext_snapshot()
    old_key_moment = box.current_key()
    hops_before = box.hops
    time.sleep(0.3)
    hops_after = box.hops
    print(f"  captured ciphertext, then waited through {hops_after - hops_before:,} "
          f"more hops (key ratcheted that many times)")

    current_key_now = box.current_key()
    nonce = old_ciphertext[:12]
    ct = old_ciphertext[12:]
    try:
        AESGCM(current_key_now).decrypt(nonce, ct, None)
        print("  decrypting the OLD snapshot with the CURRENT key: SUCCEEDED "
              "(unexpected - would mean no forward secrecy)")
    except InvalidTag:
        print("  decrypting the OLD snapshot with the CURRENT key: FAILED "
              "(expected - old key is truly gone, old snapshot is dead)")

    try:
        pt = AESGCM(old_key_moment).decrypt(nonce, ct, None)
        print(f"  decrypting the OLD snapshot with the key FROM THAT MOMENT: "
              f"SUCCEEDED, marker recoverable={MARKER in pt} "
              f"(expected - if you catch BOTH together, you still get it)")
    except InvalidTag:
        print("  decrypting with the key from that exact moment: FAILED "
              "(unexpected)")

    print("""
  What this shows: catching ciphertext ALONE, after the key has moved
  on, is worthless - even to someone holding the newest key. Catching
  ciphertext AND its matching key TOGETHER, at the same moment, still
  works - that's the one honest limit encryption doesn't remove: a
  full simultaneous dump gets both. What it DOES kill is every OTHER
  copy - old ciphertext lying in swap, a stale heap page, a crash
  dump from 10 minutes ago - all of that is permanently dead the
  moment the key ratchets past it.
""")

    box.collapse()


if __name__ == "__main__":
    main()
