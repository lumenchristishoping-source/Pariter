"""File sharing via hand-off, per Drew's design:

A holds a piece falling through its boxes, same as always. B "stretches
out its box" and places it right after the box that's currently about to
break. When that box breaks, instead of reforming back into A's own
other buffer (the normal bounce), the piece flows into B's box instead -
the natural break IS the hand-off point, not a new pause. From there,
the piece continues falling - just now inside B's own boxes, not A's.

A and B are two separate, real OS processes here (not two threads
pretending) - a pipe between them is the path the piece crosses.
After the hand-off, A no longer holds anything: this is a transfer, not
a copy.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time

BOX_SIZE = 64 * 1024


def _bounce_once(box_a: bytearray, box_b: bytearray, active: str) -> str:
    """One hop of the normal internal bounce - same motion as
    falling_box.py's FallingBox, inlined here so each side (A and B)
    can run it as a plain loop in its own process."""
    if active == "a":
        for i in range(0, len(box_a), BOX_SIZE):
            box_b[i:i + BOX_SIZE] = box_a[i:i + BOX_SIZE]
        return "b"
    else:
        for i in range(0, len(box_b), BOX_SIZE):
            box_a[i:i + BOX_SIZE] = box_b[i:i + BOX_SIZE]
        return "a"


def _sender_process(data: bytes, write_fd: int, hops_before_handoff: int) -> None:
    box_a = bytearray(data)
    box_b = bytearray(len(data))
    active = "a"

    # Falls normally inside A for a while, exactly like any held piece.
    for _ in range(hops_before_handoff):
        active = _bounce_once(box_a, box_b, active)

    # The NEXT break: instead of reforming back into A's own other
    # buffer, the piece goes out through the pipe to B instead.
    current = bytes(box_a if active == "a" else box_b)
    pos = 0
    view = memoryview(current)
    while pos < len(view):
        pos += os.write(write_fd, view[pos:pos + BOX_SIZE])
    os.close(write_fd)

    # A's boxes are cleared - the piece has left, not been duplicated.
    box_a = bytearray()
    box_b = bytearray()


def _receiver_process(read_fd: int, expected_size: int, fall_duration: float,
                       result_queue: "mp.Queue") -> None:
    received = bytearray()
    while len(received) < expected_size:
        chunk = os.read(read_fd, BOX_SIZE)
        if not chunk:
            break
        received.extend(chunk)
    os.close(read_fd)

    # B now owns the piece - it starts falling in B's own boxes,
    # continuing the same journey, just on this side now.
    box_a = bytearray(received)
    box_b = bytearray(len(received))
    active = "a"
    start = time.time()
    hops = 0
    while time.time() - start < fall_duration:
        active = _bounce_once(box_a, box_b, active)
        hops += 1

    final = bytes(box_a if active == "a" else box_b)
    result_queue.put((final, hops))


def share_via_handoff(data: bytes, hops_before_handoff: int = 5,
                       fall_duration_after: float = 1.0) -> dict:
    """A falls `data` for `hops_before_handoff` hops, hands it to B at
    the next break, B falls it independently for `fall_duration_after`
    seconds. Two real OS processes, connected by one pipe.
    """
    ctx = mp.get_context("fork")
    read_fd, write_fd = os.pipe()
    result_queue = ctx.Queue()

    sender = ctx.Process(target=_sender_process, args=(data, write_fd, hops_before_handoff))
    receiver = ctx.Process(target=_receiver_process,
                            args=(read_fd, len(data), fall_duration_after, result_queue))

    receiver.start()
    sender.start()

    # The parent's own copies aren't needed once the children have theirs.
    os.close(read_fd)
    os.close(write_fd)

    sender.join()
    final, b_hops = result_queue.get()
    receiver.join()

    return {
        "byte_perfect": final == data,
        "hops_in_a_before_handoff": hops_before_handoff,
        "hops_in_b_after_handoff": b_hops,
        "bytes_moved": len(data),
    }
