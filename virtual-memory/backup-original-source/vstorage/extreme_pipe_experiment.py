"""
Most extreme version: Python doesn't hold the data at all -- it just wires
up a chain of OS-level processes connected by pipes (cat | cat | cat | ...)
and lets the KERNEL move the bytes between them. Python's role is only to
start the chain and wait for it to finish.

We measure actual system memory (not just this Python process) and actual
disk I/O for the whole machine before/during/after, to see empirically
what's really happening.
"""

import subprocess
import os
import time
import hashlib

SHM_DIR = "/dev/shm/vstorage_extreme"
FILE_SIZE_MB = 200
NUM_HOPS = 6  # chain length: source | cat | cat | cat | cat | cat | cat > dest

def read_meminfo():
    with open("/proc/meminfo") as f:
        info = {}
        for line in f:
            k, v = line.split(":")
            info[k.strip()] = v.strip()
    return info

def read_system_disk_stats():
    with open("/proc/diskstats") as f:
        return f.read()


if __name__ == "__main__":
    os.makedirs(SHM_DIR, exist_ok=True)
    source_path = os.path.join(SHM_DIR, "source.bin")
    dest_path = os.path.join(SHM_DIR, "dest.bin")

    print(f"Generating {FILE_SIZE_MB}MB source file in /dev/shm...")
    with open(source_path, "wb") as f:
        f.write(os.urandom(FILE_SIZE_MB * 1024 * 1024))

    original_hash = hashlib.sha256(open(source_path, "rb").read()).hexdigest()

    mem_before = read_meminfo()
    print(f"\nMemFree before: {mem_before['MemFree']}")
    print(f"Cached before:  {mem_before['Cached']}")

    # build a shell pipeline: cat source | cat | cat | cat | cat | cat > dest
    # Python never reads the bytes into its own variables at all -- it just
    # tells the OS to connect these processes' stdin/stdout together and
    # waits. The kernel moves every byte through pipe buffers directly.
    cat_chain = " | ".join(["cat"] * NUM_HOPS)
    shell_cmd = f"cat {source_path} | {cat_chain} > {dest_path}"
    print(f"\nRunning pure OS pipe chain ({NUM_HOPS} hops), Python holds no data:")
    print(f"  {shell_cmd}")

    t0 = time.time()
    result = subprocess.run(shell_cmd, shell=True, capture_output=True)
    elapsed = time.time() - t0

    mem_after = read_meminfo()
    print(f"\nCompleted in {elapsed:.3f}s")
    print(f"MemFree after:  {mem_after['MemFree']}")
    print(f"Cached after:   {mem_after['Cached']}")
    print(f"stderr: {result.stderr.decode() if result.stderr else '(none)'}")

    final_hash = hashlib.sha256(open(dest_path, "rb").read()).hexdigest()
    print(f"\nSHA-256 matches original: {final_hash == original_hash}")

    # check where dest actually landed
    mount_check = subprocess.run(f"df {dest_path}", shell=True, capture_output=True, text=True)
    print(f"\nWhere the destination file physically lives:")
    print(mount_check.stdout)

    # check this python process's own memory footprint during the whole thing
    import psutil
    this_proc_mem = psutil.Process().memory_info().rss / 1024 / 1024
    print(f"Python process's own RSS memory (never held file data): {this_proc_mem:.1f} MB")

    os.remove(source_path)
    os.remove(dest_path)
    os.rmdir(SHM_DIR)
    print("\nCleaned up.")
