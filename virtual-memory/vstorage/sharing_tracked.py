"""Same hand-off as sharing.py, rerun with full memory tracking - every
figure /proc exposes, at every key moment, captured from INSIDE each
process (not guessed from outside), plus the whole machine's state.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time

from .measure_full import full_snapshot, system_snapshot

BOX_SIZE = 64 * 1024


def _bounce_once(box_a: bytearray, box_b: bytearray, active: str) -> str:
    if active == "a":
        for i in range(0, len(box_a), BOX_SIZE):
            box_b[i:i + BOX_SIZE] = box_a[i:i + BOX_SIZE]
        return "b"
    else:
        for i in range(0, len(box_b), BOX_SIZE):
            box_a[i:i + BOX_SIZE] = box_b[i:i + BOX_SIZE]
        return "a"


def _sender_process(data: bytes, write_fd: int, hops_before_handoff: int,
                     result_queue: "mp.Queue") -> None:
    snapshots = [("A: start", full_snapshot())]

    box_a = bytearray(data)
    box_b = bytearray(len(data))
    active = "a"
    snapshots.append(("A: holding piece, before falling", full_snapshot()))

    for _ in range(hops_before_handoff):
        active = _bounce_once(box_a, box_b, active)
    snapshots.append(("A: right before hand-off", full_snapshot()))

    current = bytes(box_a if active == "a" else box_b)
    pos = 0
    view = memoryview(current)
    while pos < len(view):
        pos += os.write(write_fd, view[pos:pos + BOX_SIZE])
    os.close(write_fd)

    box_a = bytearray()
    box_b = bytearray()
    snapshots.append(("A: right after hand-off (boxes cleared)", full_snapshot()))

    result_queue.put(("A", snapshots))


def _receiver_process(read_fd: int, expected_size: int, fall_duration: float,
                       result_queue: "mp.Queue") -> None:
    snapshots = [("B: start, before receiving anything", full_snapshot())]

    received = bytearray()
    while len(received) < expected_size:
        chunk = os.read(read_fd, BOX_SIZE)
        if not chunk:
            break
        received.extend(chunk)
    os.close(read_fd)
    snapshots.append(("B: right after receiving", full_snapshot()))

    box_a = bytearray(received)
    box_b = bytearray(len(received))
    active = "a"
    start = time.time()
    hops = 0
    while time.time() - start < fall_duration:
        active = _bounce_once(box_a, box_b, active)
        hops += 1
    snapshots.append(("B: after falling independently", full_snapshot()))

    final = bytes(box_a if active == "a" else box_b)
    result_queue.put(("B", snapshots, final, hops))


def share_via_handoff_tracked(data: bytes, hops_before_handoff: int = 5,
                               fall_duration_after: float = 1.0) -> dict:
    ctx = mp.get_context("fork")
    read_fd, write_fd = os.pipe()
    result_queue = ctx.Queue()

    sys_before = system_snapshot()

    sender = ctx.Process(target=_sender_process,
                          args=(data, write_fd, hops_before_handoff, result_queue))
    receiver = ctx.Process(target=_receiver_process,
                            args=(read_fd, len(data), fall_duration_after, result_queue))

    receiver.start()
    sender.start()
    os.close(read_fd)
    os.close(write_fd)

    a_snapshots = b_snapshots = None
    final = None
    b_hops = None
    for _ in range(2):
        payload = result_queue.get()
        if payload[0] == "A":
            a_snapshots = payload[1]
        else:
            _, b_snapshots, final, b_hops = payload

    sender.join()
    receiver.join()

    sys_after = system_snapshot()

    return {
        "byte_perfect": final == data,
        "bytes_moved": len(data),
        "hops_in_b_after_handoff": b_hops,
        "system_before": sys_before,
        "system_after": sys_after,
        "a_snapshots": a_snapshots,
        "b_snapshots": b_snapshots,
    }
