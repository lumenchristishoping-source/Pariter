#!/usr/bin/env python3
"""Tests distributed trust wired into the real pipeline: correctness,
the real hop-rate cost, and the actual property it closes - a
captured key at time T0 should NOT let an attacker compute the key at
T0+n anymore, unlike the pure-local ratchet (verified broken earlier:
a locally-hashed-forward key matched the real future key exactly).
"""
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.combined_secure_box import CombinedSecureBox
from vstorage.distributed_key import DistributedTrustGroup
from vstorage.secure_system import SecureVirtualStorage


def part1_pipeline_correctness() -> None:
    print("=== Part 1: real pipeline, distributed trust ON, real file ===\n")
    pdf_path = ("/root/.claude/uploads/9c6fcc6d-8c14-5ccc-a3a0-fd3c78528f86/"
                "fa1789a3-thechroniclesofnmachukwu.pdf")
    with open(pdf_path, "rb") as f:
        original = f.read()

    t0 = time.time()
    vs = SecureVirtualStorage(use_distributed_trust=True, trust_k=3, trust_n=5)
    file_id = vs.save(pdf_path)
    print(f"save() with distributed trust on: {time.time()-t0:.3f}s")

    time.sleep(1.0)
    full = vs.retrieve(file_id, "full")
    match = hashlib.sha256(full).digest() == hashlib.sha256(original).digest()
    print(f"byte-perfect retrieval: {match}")

    box = vs._held[file_id].boxes["structure"]
    print(f"hops with distributed trust on (1s window): {box.hops} "
          f"(compare to ~30-45/sec without it - real IPC cost per hop)")

    vs.collapse_all()
    print()


def part2_key_prediction_closed() -> None:
    print("=== Part 2: does a captured key still predict the future? ===\n")

    print("WITHOUT distributed trust (the old way):")
    box_plain = CombinedSecureBox(b"x" * 300, hop_interval=0.05)
    time.sleep(0.1)
    captured_key = box_plain._key_guard.reconstruct()
    hops_before = box_plain.hops
    time.sleep(1.0)
    hops_after = box_plain.hops
    n_hops = hops_after - hops_before

    predicted = captured_key
    for _ in range(n_hops):
        predicted = hashlib.sha256(predicted + b"vstorage-ratchet").digest()
    real_current = box_plain._key_guard.reconstruct()
    print(f"  captured key, then {n_hops} real hops happened")
    print(f"  attacker predicts the current key purely from the old "
          f"capture: {predicted == real_current}")
    box_plain.collapse()

    print("\nWITH distributed trust:")
    trust_group = DistributedTrustGroup(k=3, n=5)
    box_trusted = CombinedSecureBox(b"x" * 300, hop_interval=0.05,
                                     trust_group=trust_group)
    time.sleep(0.1)
    captured_key2 = box_trusted._key_guard.reconstruct()
    hops_before2 = box_trusted.hops
    time.sleep(1.0)
    hops_after2 = box_trusted.hops
    n_hops2 = hops_after2 - hops_before2

    # Attacker captured the key ONCE, at T0 - no ongoing access to the
    # trust group's holder processes after that (the realistic case:
    # they compromised this ONE machine, not the N others too).
    predicted2 = captured_key2
    for _ in range(n_hops2):
        predicted2 = hashlib.sha256(
            predicted2 + b"\x00" * 32 + b"vstorage-ratchet").digest()  # can't know the real external secret
    real_current2 = box_trusted._key_guard.reconstruct()
    print(f"  captured key, then {n_hops2} real hops happened")
    print(f"  attacker's best guess (can't fetch the real external "
          f"secret without compromising K of N other machines too): "
          f"matches real current key: {predicted2 == real_current2}")
    box_trusted.collapse()
    trust_group.stop()


if __name__ == "__main__":
    part1_pipeline_correctness()
    part2_key_prediction_closed()
