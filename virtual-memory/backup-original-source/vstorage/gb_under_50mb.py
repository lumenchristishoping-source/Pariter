"""
1GB file, pure motion (no numpy, no XOR -- lean version), designed to run
under an actual OS-enforced 50MB virtual memory ceiling (ulimit -v), not
just a hoped-for target.

Small chunk size + explicit deletion between hops, since every hop and every
open file object costs some memory and we have very little headroom.
"""

import os
import time
import threading
import hashlib
import gc

SHM_DIR = "/dev/shm/vstorage_50mb_test"
FILE_SIZE = 1024 * 1024 * 1024  # 1GB
CHUNK_SIZE = 512 * 1024  # 512KB chunks -- small, to leave headroom under 50MB
NUM_HOPS = 3

def get_disk_io():
    with open("/proc/self/io") as f:
        return {k: int(v) for k, v in (line.strip().split(": ") for line in f)}

def get_rss_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1])
    return 0

def stream_through_pipe(data: bytes) -> bytes:
    r_fd, w_fd = os.pipe()
    parts = []
    def writer():
        with os.fdopen(w_fd, "wb") as w:
            w.write(data)
    t = threading.Thread(target=writer)
    t.start()
    with os.fdopen(r_fd, "rb") as r:
        while True:
            piece = r.read(32768)
            if not piece:
                break
            parts.append(piece)
    t.join()
    return b"".join(parts)

def hop_n_times(chunk: bytes, hops: int) -> bytes:
    current = chunk
    for _ in range(hops):
        moved = stream_through_pipe(current)
        del current
        current = moved
    return current


if __name__ == "__main__":
    os.makedirs(SHM_DIR, exist_ok=True)
    source_path = os.path.join(SHM_DIR, "source.bin")
    recon_path = os.path.join(SHM_DIR, "reconstructed.bin")

    print(f"Baseline RSS at start: {get_rss_kb() / 1024:.2f} MB")
    io_start = get_disk_io()
    t0 = time.time()

    print(f"Generating {FILE_SIZE // 1024 // 1024}MB source file directly in /dev/shm "
          f"(written in small pieces so generation itself stays under the ceiling too)...")
    with open(source_path, "wb") as f:
        remaining = FILE_SIZE
        while remaining > 0:
            n = min(CHUNK_SIZE, remaining)
            f.write(os.urandom(n))
            remaining -= n
    print(f"  Done in {time.time()-t0:.2f}s")
    print(f"  RSS after generation: {get_rss_kb() / 1024:.2f} MB")

    original_hasher = hashlib.sha256()
    with open(source_path, "rb") as f:
        while c := f.read(CHUNK_SIZE):
            original_hasher.update(c)
    original_hash = original_hasher.hexdigest()

    print(f"\nProcessing 1GB in {CHUNK_SIZE // 1024}KB chunks, {NUM_HOPS} hops each, "
          f"under a 50MB ulimit ceiling...")

    t1 = time.time()
    peak_rss = get_rss_kb()
    chunks_done = 0
    recon_hasher = hashlib.sha256()

    with open(source_path, "rb") as fin, open(recon_path, "wb") as fout:
        while raw_chunk := fin.read(CHUNK_SIZE):
            moved = hop_n_times(raw_chunk, NUM_HOPS)
            del raw_chunk
            fout.write(moved)
            recon_hasher.update(moved)
            del moved

            chunks_done += 1
            if chunks_done % 200 == 0:
                gc.collect()
                current_rss = get_rss_kb()
                peak_rss = max(peak_rss, current_rss)

    peak_rss = max(peak_rss, get_rss_kb())
    elapsed = time.time() - t1
    final_hash = recon_hasher.hexdigest()

    print(f"\nProcessed {chunks_done} chunks in {elapsed:.2f}s")
    print(f"PEAK RSS during entire run: {peak_rss / 1024:.2f} MB")
    print(f"SHA-256 matches original: {final_hash == original_hash}")

    io_end = get_disk_io()
    print(f"\nActual physical disk I/O for the whole 1GB run:")
    for k in io_start:
        print(f"  {k}: {io_end[k] - io_start[k]}")

    mount_check = os.popen(f"df {SHM_DIR}").read().strip()
    print(f"\nWhere the files actually live:\n{mount_check}")

    os.remove(source_path)
    os.remove(recon_path)
    os.rmdir(SHM_DIR)
    print("\nCleaned up.")
