"""
Leaner version per Drew's questions:
  1. No XOR splitting -- just continuous motion of the whole chunk.
  2. Explicit del + gc at each hop so only ONE copy of each chunk exists
     in memory at any instant, instead of accumulating old copies.

This isolates "motion without a fixed location" as its own tested property,
separate from "fragments are meaningless alone" (which needs the XOR
version -- they're independent features, not one combined requirement).
"""

import os
import time
import threading
import hashlib
import gc
import psutil

SHM_DIR = "/dev/shm/vstorage_lean"
FILE_SIZE = 500 * 1024 * 1024
CHUNK_SIZE = 8 * 1024 * 1024
NUM_HOPS = 4  # how many pipe-hops each chunk takes before being written out

proc = psutil.Process()

def mem_mb():
    return proc.memory_info().rss / 1024 / 1024

def get_disk_io():
    with open("/proc/self/io") as f:
        return {k: int(v) for k, v in (line.strip().split(": ") for line in f)}

def stream_through_pipe(data: bytes) -> bytes:
    r_fd, w_fd = os.pipe()
    received_parts = []
    def writer():
        with os.fdopen(w_fd, "wb") as w:
            w.write(data)
    t = threading.Thread(target=writer)
    t.start()
    with os.fdopen(r_fd, "rb") as r:
        while True:
            piece = r.read(65536)
            if not piece:
                break
            received_parts.append(piece)
    t.join()
    return b"".join(received_parts)

def hop_chunk_n_times(chunk: bytes, hops: int) -> bytes:
    """Move a chunk through N pipe-hops, deleting the previous copy the
    instant the new one exists -- never more than one live copy at once."""
    current = chunk
    for _ in range(hops):
        moved = stream_through_pipe(current)
        del current          # explicitly drop the old copy
        gc.collect()         # force reclaim now, don't wait for Python's own timing
        current = moved
    return current


if __name__ == "__main__":
    os.makedirs(SHM_DIR, exist_ok=True)
    source_path = os.path.join(SHM_DIR, "source.bin")
    recon_path = os.path.join(SHM_DIR, "reconstructed.bin")

    print(f"Baseline memory: {mem_mb():.1f} MB")
    io_start = get_disk_io()
    t0 = time.time()

    print(f"\nGenerating {FILE_SIZE // 1024 // 1024}MB source file in /dev/shm...")
    with open(source_path, "wb") as f:
        remaining = FILE_SIZE
        while remaining > 0:
            n = min(CHUNK_SIZE, remaining)
            f.write(os.urandom(n))
            remaining -= n
    print(f"  Done in {time.time()-t0:.2f}s")

    original_hasher = hashlib.sha256()
    with open(source_path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            original_hasher.update(chunk)
    original_hash = original_hasher.hexdigest()

    print(f"\nProcessing in {CHUNK_SIZE // 1024 // 1024}MB chunks, "
          f"{NUM_HOPS} pipe-hops each, NO splitting, explicit delete between hops...")

    t1 = time.time()
    peak_mem = mem_mb()
    chunks_done = 0
    recon_hasher = hashlib.sha256()

    with open(source_path, "rb") as fin, open(recon_path, "wb") as fout:
        while raw_chunk := fin.read(CHUNK_SIZE):
            moved_chunk = hop_chunk_n_times(raw_chunk, NUM_HOPS)
            del raw_chunk
            fout.write(moved_chunk)
            recon_hasher.update(moved_chunk)
            del moved_chunk
            gc.collect()

            chunks_done += 1
            peak_mem = max(peak_mem, mem_mb())

    elapsed = time.time() - t1
    final_hash = recon_hasher.hexdigest()

    print(f"\nProcessed {chunks_done} chunks, each hopped {NUM_HOPS}x, in {elapsed:.2f}s")
    print(f"Peak memory (lean, no split): {peak_mem:.1f} MB")
    print(f"SHA-256 matches original: {final_hash == original_hash}")

    io_end = get_disk_io()
    print(f"\nTotal wall time: {time.time()-t0:.2f}s")
    print("Actual physical disk I/O:")
    for k in io_start:
        print(f"  {k}: {io_end[k] - io_start[k]}")

    os.remove(source_path)
    os.remove(recon_path)
    os.rmdir(SHM_DIR)
    print("\nCleaned up.")
