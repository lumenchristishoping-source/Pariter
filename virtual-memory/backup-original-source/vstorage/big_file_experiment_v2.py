"""
500MB test, v2: chunked processing.

The v1 crash proved the point empirically -- holding whole-file copies for
splitting/streaming/reconstructing at once blew past available RAM.

Fix: process the file in fixed-size chunks (8MB at a time). For each chunk:
  split into shares -> stream each through a pipe -> reconstruct -> write
  reconstructed chunk to output -> discard, move to next chunk.

Peak memory now depends on CHUNK size, not FILE size -- a 500MB file and a
5GB file cost the same peak RAM this way, just more chunks/time.
"""

import os
import time
import threading
import hashlib
import numpy as np
import psutil

SHM_DIR = "/dev/shm/vstorage_bigtest2"
FILE_SIZE = 500 * 1024 * 1024
NUM_SHARES = 3
CHUNK_SIZE = 8 * 1024 * 1024  # 8MB per chunk

proc = psutil.Process()

def mem_mb():
    return proc.memory_info().rss / 1024 / 1024

def get_disk_io():
    with open("/proc/self/io") as f:
        return {k: int(v) for k, v in (line.strip().split(": ") for line in f)}

def xor_split_chunk(chunk: np.ndarray, num_shares: int):
    shares = [np.random.randint(0, 256, size=chunk.shape, dtype=np.uint8)
              for _ in range(num_shares - 1)]
    last = chunk.copy()
    for s in shares:
        last ^= s
    shares.append(last)
    return shares

def xor_combine_chunk(shares):
    result = shares[0].copy()
    for s in shares[1:]:
        result ^= s
    return result

def stream_through_pipe(data: bytes) -> bytes:
    r_fd, w_fd = os.pipe()
    chunks = []
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
            chunks.append(piece)
    t.join()
    return b"".join(chunks)


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
          f"{NUM_SHARES} XOR shares each, streamed through real OS pipes...")

    t1 = time.time()
    peak_mem = mem_mb()
    chunks_done = 0
    recon_hasher = hashlib.sha256()

    with open(source_path, "rb") as fin, open(recon_path, "wb") as fout:
        while raw_chunk := fin.read(CHUNK_SIZE):
            arr = np.frombuffer(raw_chunk, dtype=np.uint8)
            shares = xor_split_chunk(arr, NUM_SHARES)

            streamed = []
            for s in shares:
                received = stream_through_pipe(s.tobytes())
                streamed.append(np.frombuffer(received, dtype=np.uint8))

            reconstructed_chunk = xor_combine_chunk(streamed)
            fout.write(reconstructed_chunk.tobytes())
            recon_hasher.update(reconstructed_chunk.tobytes())

            chunks_done += 1
            current_mem = mem_mb()
            peak_mem = max(peak_mem, current_mem)

    elapsed = time.time() - t1
    final_hash = recon_hasher.hexdigest()

    print(f"\nProcessed {chunks_done} chunks in {elapsed:.2f}s")
    print(f"Peak memory during chunked run: {peak_mem:.1f} MB")
    print(f"SHA-256 matches original: {final_hash == original_hash}")

    mount_check = os.popen("mount | grep /dev/shm").read().strip().splitlines()[0]
    print(f"Confirmed RAM-backed the whole time: {mount_check}")

    io_end = get_disk_io()
    print(f"\nTotal wall time: {time.time()-t0:.2f}s")
    print("Actual physical disk I/O for the whole 500MB run:")
    for k in io_start:
        print(f"  {k}: {io_end[k] - io_start[k]}")

    os.remove(source_path)
    os.remove(recon_path)
    os.rmdir(SHM_DIR)
    print("\nCleaned up -- nothing left behind.")
