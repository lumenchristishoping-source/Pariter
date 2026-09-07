"""
Combined experiment: XOR-split + OS pipes (streaming, not stored variables)
+ tmpfs (/dev/shm, RAM-backed, never touches disk).

Goal: get as close as possible to "nothing sits still and nothing alone
means anything" and measure what's actually happening.

1. File is split into N shares via XOR, such that any single share alone
   is meaningless (looks like random noise) -- only combining ALL shares
   reconstructs the original.
2. Shares are moved between locations using os.pipe() -- real OS pipes,
   not variables sitting in Python objects. Once read from a pipe, that
   data is gone from the pipe.
3. The reconstruction workspace lives in /dev/shm (tmpfs) -- RAM-backed,
   never written to physical disk.
4. We measure: disk I/O during the whole process, whether individual
   shares are meaningful alone, and whether final reconstruction works.
"""

import os
import secrets
import hashlib

SHM_DIR = "/dev/shm/vstorage_test"

def xor_split(data: bytes, num_shares: int):
    """Split data into num_shares such that any single share is meaningless
    (pure random noise) and only XOR-ing ALL of them together recovers it."""
    shares = [secrets.token_bytes(len(data)) for _ in range(num_shares - 1)]
    last = bytearray(data)
    for s in shares:
        for i in range(len(last)):
            last[i] ^= s[i]
    shares.append(bytes(last))
    return shares

def xor_combine(shares):
    result = bytearray(len(shares[0]))
    for s in shares:
        for i in range(len(s)):
            result[i] ^= s[i]
    return bytes(result)

def looks_like_noise(data: bytes) -> bool:
    """Rough check: real text has low byte-value entropy variance vs random."""
    if len(data) == 0:
        return True
    printable = sum(1 for b in data if 32 <= b <= 126)
    return (printable / len(data)) < 0.5  # random bytes are mostly non-printable


def stream_share_through_pipe(share: bytes) -> bytes:
    """Move a share through a real OS pipe instead of just holding it in a
    Python variable. Once read, it's drained from the pipe -- the pipe
    itself never 'stores' it after the read."""
    r_fd, w_fd = os.pipe()
    os.write(w_fd, share)
    os.close(w_fd)
    received = os.read(r_fd, len(share))
    os.close(r_fd)
    return received


def get_disk_io_bytes():
    """Read this process's actual disk read/write bytes from /proc."""
    with open(f"/proc/self/io") as f:
        lines = f.readlines()
    stats = {}
    for line in lines:
        k, v = line.strip().split(": ")
        stats[k] = int(v)
    return stats


if __name__ == "__main__":
    with open("test_document.md", "rb") as f:
        real_file = f.read()
    original_hash = hashlib.sha256(real_file).hexdigest()

    print(f"Original file: {len(real_file)} bytes, SHA-256: {original_hash[:16]}...")
    print()

    io_before = get_disk_io_bytes()

    # 1. XOR split into 5 shares -- none meaningful alone
    NUM_SHARES = 5
    shares = xor_split(real_file, NUM_SHARES)

    print("Step 1: XOR split into shares")
    for i, s in enumerate(shares):
        preview = s[:30]
        print(f"  Share {i}: {'looks like noise' if looks_like_noise(s) else 'READABLE (bad!)'} "
              f"| preview: {preview}")
    print()

    # 2. Stream each share through a real OS pipe (not a stored variable)
    print("Step 2: streaming each share through an OS pipe")
    streamed_shares = []
    for i, s in enumerate(shares):
        received = stream_share_through_pipe(s)
        matches = (received == s)
        streamed_shares.append(received)
        print(f"  Share {i} streamed through pipe intact: {matches}")
    print()

    # 3. Reconstruct in /dev/shm (RAM-backed tmpfs, never touches disk)
    print("Step 3: reconstructing in /dev/shm (tmpfs)")
    os.makedirs(SHM_DIR, exist_ok=True)
    reconstructed = xor_combine(streamed_shares)
    shm_path = os.path.join(SHM_DIR, "reconstructed.md")
    with open(shm_path, "wb") as f:
        f.write(reconstructed)

    final_hash = hashlib.sha256(reconstructed).hexdigest()
    print(f"  Reconstructed SHA-256 matches original: {final_hash == original_hash}")
    print(f"  File written to: {shm_path}")

    # confirm /dev/shm is actually a RAM-backed tmpfs, not disk
    mount_info = os.popen("mount | grep /dev/shm").read().strip()
    print(f"  Mount type check: {mount_info}")
    print()

    io_after = get_disk_io_bytes()

    print("Step 4: actual disk I/O caused by this whole process")
    for k in io_before:
        delta = io_after[k] - io_before[k]
        print(f"  {k}: {delta}")
    print()

    print("Cleaning up /dev/shm test file...")
    os.remove(shm_path)
    os.rmdir(SHM_DIR)
    print("Done -- nothing left on disk or in /dev/shm.")
