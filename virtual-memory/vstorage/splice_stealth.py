"""Reproduces HANDBOOK.md Experiment 5 (splice() - the stealth property).

splice() moves bytes between two pipes through the kernel's own pipe
buffers, without the calling process ever holding the data in its own
heap. Python 3.8+ exposes this directly as os.splice(); no C extension
needed.

Two different, honestly-separated claims live here, because they are not
the same measurement (this distinction is exactly HANDBOOK.md's Dead-End
9/10 conclusion: "retrievable" and "held" are the same fact):

  - `measure_stealth_flow()` - data flows through and is DISCARDED at the
    far end (never accumulated into a Python object). This is the stealth
    claim: process RSS stays tiny while the data is in transit, because
    the kernel's pipe buffers hold it, not this process's heap.
  - `splice_move()` - data flows through and IS collected back into a
    Python bytes object, for a fidelity check. This one, honestly, ends
    with the process holding the full-size result - that's unavoidable if
    you want the data back, and is the same wall Experiment 9 hit.

A pipe's buffer is only 64KB (HANDBOOK.md Pitfall 1) - writing or
splicing more than that with no concurrent reader on the other end
blocks forever. Fixed here exactly as the handbook prescribes: a writer
thread feeding the source pipe, concurrent with the splice loop.
"""

from __future__ import annotations

import os
import threading
import time

from .measure import rss_anon_mb

CHUNK = 64 * 1024


def _system_ram_used_mb() -> float:
    total = avail = None
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
    if total is None or avail is None:
        return 0.0
    return (total - avail) / 1024.0


def _feed(write_fd: int, data: bytes, errors: list) -> None:
    try:
        view = memoryview(data)
        pos = 0
        while pos < len(view):
            n = os.write(write_fd, view[pos:pos + CHUNK])
            pos += n
    except Exception as e:  # pragma: no cover
        errors.append(e)
    finally:
        os.close(write_fd)


def splice_move(data: bytes) -> bytes:
    """Kernel-to-kernel transit via splice(), then collected back into a
    Python bytes object for a fidelity check. See module docstring: this
    call, by design, ends with the process holding the full result.
    """
    src_r, src_w = os.pipe()
    dst_r, dst_w = os.pipe()
    received = bytearray()
    errors: list = []

    writer = threading.Thread(target=_feed, args=(src_w, data, errors), daemon=True)
    writer.start()

    def drain_and_keep():
        while True:
            chunk = os.read(dst_r, CHUNK)
            if not chunk:
                break
            received.extend(chunk)

    reader = threading.Thread(target=drain_and_keep, daemon=True)
    reader.start()

    try:
        remaining = len(data)
        while remaining > 0:
            n = os.splice(src_r, dst_w, min(CHUNK, remaining))
            if n == 0:
                break
            remaining -= n
    finally:
        os.close(src_r)
        os.close(dst_w)

    writer.join()
    reader.join()
    os.close(dst_r)

    if errors:
        raise errors[0]
    return bytes(received)


def measure_stealth_flow_from_file(path: str, sample_interval: float = 0.005) -> dict:
    """The actual stealth claim, tested honestly: stream `path` (e.g. a
    large file on /dev/shm) through splice() chunk by chunk - reading it
    progressively with os.sendfile-style chunked I/O, NOT loading it as
    one big Python bytes object first (that would already cost its full
    size in this process before the test even starts). Each chunk is
    discarded at the far end. If the kernel pipe buffers are really
    holding the data in flight, this process's own RSS stays flat and
    tiny throughout, independent of the file's size.
    """
    file_size = os.path.getsize(path)
    src_r, src_w = os.pipe()
    dst_r, dst_w = os.pipe()
    errors: list = []
    done = threading.Event()
    samples: list = []

    def feed_from_file():
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    pos = 0
                    view = memoryview(chunk)
                    while pos < len(view):
                        pos += os.write(src_w, view[pos:])
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            os.close(src_w)

    def drain_and_discard():
        while True:
            chunk = os.read(dst_r, CHUNK)
            if not chunk:
                break
            # deliberately not kept anywhere - this is the discard path

    def sampler():
        while not done.is_set():
            samples.append(rss_anon_mb())
            time.sleep(sample_interval)

    writer = threading.Thread(target=feed_from_file, daemon=True)
    reader = threading.Thread(target=drain_and_discard, daemon=True)
    sampler_thread = threading.Thread(target=sampler, daemon=True)

    sys_before = _system_ram_used_mb()
    writer.start()
    reader.start()
    sampler_thread.start()

    try:
        remaining = file_size
        while remaining > 0:
            n = os.splice(src_r, dst_w, min(CHUNK, remaining))
            if n == 0:
                break
            remaining -= n
    finally:
        os.close(src_r)
        os.close(dst_w)

    writer.join()
    reader.join()
    os.close(dst_r)
    done.set()
    sampler_thread.join()
    sys_after = _system_ram_used_mb()

    if errors:
        raise errors[0]

    samples = samples or [rss_anon_mb()]
    return {
        "bytes_moved": file_size,
        "rss_min_mb": min(samples),
        "rss_avg_mb": sum(samples) / len(samples),
        "rss_max_mb": max(samples),
        "system_ram_delta_mb": sys_after - sys_before,
        "samples_taken": len(samples),
    }
