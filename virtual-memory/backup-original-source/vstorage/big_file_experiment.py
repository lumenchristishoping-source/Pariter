"""
Same experiment, scaled to a real 500MB file.

Changes needed at this size:
  - numpy for XOR instead of pure Python loops (would be far too slow at 500MB)
  - streaming through OS pipes needs a writer thread, because a pipe's kernel
    buffer is only ~64KB -- writing 500MB in one os.write() call would just
    block forever waiting for a reader that hasn't started yet (deadlock)
  - the 500MB source file itself is generated directly in /dev/shm, so it
    never touches disk even at the start
  - memory tracked at each step so we can see the real cost, not guess at it
"""

import os
import time
import threading
import hashlib
import numpy as np
import psutil

SHM_DIR = "/dev/shm/vstorage_bigtest"
FILE_SIZE = 500 * 1024 * 1024  # 500 MB
NUM_SHARES = 3

proc = psutil.Process()

def mem_mb():
    return proc.memory_info().rss / 1024 / 1024

def get_disk_io():
    with open("/proc/self/io") as f:
        return {k: int(v) for k, v in (line.strip().split(": ") for line in f)}

def xor_split_numpy(data: np.ndarray, num_shares: int):
    shares = [np.random.randint(0, 256, size=data.shape, dtype=np.uint8)
              for _ in range(num_shares - 1)]
    last = data.copy()
    for s in shares:
        last ^= s
    shares.append(last)
    return shares

def xor_combine_numpy(shares):
    result = shares[0].copy()
    for s in shares[1:]:
        result ^= s
    return result

def stream_through_pipe_threaded(data: bytes) -> bytes:
    """Move data through a real OS pipe safely at any size, using a writer
    thread so the pipe's small kernel buffer never causes a deadlock."""
    r_fd, w_fd = os.pipe()
    chunks = []

    def writer():
        with os.fdopen(w_fd, "wb") as w:
            w.write(data)  # writes in the OS's own chunk size under the hood

    t = threading.Thread(target=writer)
    t.start()

    with os.fdopen(r_fd, "rb") as r:
        while True:
            chunk = r.read(1024 * 1024)  # read 1MB at a time
            if not chunk:
                break
            chunks.append(chunk)
    t.join()
    return b"".join(chunks)


if __name__ == "__main__":
    os.makedirs(SHM_DIR, exist_ok=True)

    print(f"Baseline memory: {mem_mb():.1f} MB")
    io_start = get_disk_io()
    t0 = time.time()

    # generate the 500MB "file" directly into /dev/shm -- never touches disk
    print(f"\nGenerating {FILE_SIZE // 1024 // 1024}MB source file in /dev/shm...")
    source_path = os.path.join(SHM_DIR, "source.bin")
    with open(source_path, "wb") as f:
        f.write(os.urandom(FILE_SIZE))
    print(f"  Done in {time.time()-t0:.2f}s, memory now: {mem_mb():.1f} MB")

    with open(source_path, "rb") as f:
        original_bytes = f.read()
    original_hash = hashlib.sha256(original_bytes).hexdigest()
    original_array = np.frombuffer(original_bytes, dtype=np.uint8)
    print(f"  Loaded into memory as array, memory now: {mem_mb():.1f} MB")

    # XOR split
    t1 = time.time()
    shares = xor_split_numpy(original_array, NUM_SHARES)
    print(f"\nXOR split into {NUM_SHARES} shares in {time.time()-t1:.2f}s")
    print(f"  Memory after split: {mem_mb():.1f} MB")
    for i, s in enumerate(shares):
        sample = s[:20].tobytes()
        print(f"  Share {i} sample bytes: {sample}")

    # stream each share through a real OS pipe
    t2 = time.time()
    print(f"\nStreaming {NUM_SHARES} shares through OS pipes (threaded, chunked)...")
    streamed = []
    for i, s in enumerate(shares):
        received = stream_through_pipe_threaded(s.tobytes())
        ok = received == s.tobytes()
        streamed.append(np.frombuffer(received, dtype=np.uint8))
        print(f"  Share {i} streamed intact: {ok}")
    print(f"  Done in {time.time()-t2:.2f}s, memory now: {mem_mb():.1f} MB")

    # reconstruct, entirely in RAM
    t3 = time.time()
    reconstructed = xor_combine_numpy(streamed)
    reconstructed_bytes = reconstructed.tobytes()
    final_hash = hashlib.sha256(reconstructed_bytes).hexdigest()
    print(f"\nReconstructed in {time.time()-t3:.2f}s")
    print(f"  SHA-256 matches original: {final_hash == original_hash}")
    print(f"  Peak memory during process: {mem_mb():.1f} MB")

    # write reconstructed copy to /dev/shm to prove it, then clean up
    recon_path = os.path.join(SHM_DIR, "reconstructed.bin")
    with open(recon_path, "wb") as f:
        f.write(reconstructed_bytes)
    mount_check = os.popen("mount | grep /dev/shm").read().strip().splitlines()[0]
    print(f"  Mount confirms RAM-backed: {mount_check}")

    io_end = get_disk_io()
    print(f"\nTotal wall time: {time.time()-t0:.2f}s")
    print("Actual physical disk I/O for the whole 500MB run:")
    for k in io_start:
        print(f"  {k}: {io_end[k] - io_start[k]}")

    # cleanup
    os.remove(source_path)
    os.remove(recon_path)
    os.rmdir(SHM_DIR)
    print("\nCleaned up -- nothing left in /dev/shm or on disk.")
