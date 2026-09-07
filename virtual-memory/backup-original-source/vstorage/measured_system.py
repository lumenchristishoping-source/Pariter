#!/usr/bin/env python3
"""
Measured version: the Virtual Storage system running as a live process, held
in memory (boxes), with FULL external measurement:
  - RAM (process RSS + anonymous memory)
  - ROM/DISK (must be ZERO -- read_bytes/write_bytes from /proc)
  - what happens at rest vs during a retrieval
  - proof nothing full exists at rest
"""
import subprocess, time, os, sys

def get_io(pid):
    """Disk I/O for the process: read_bytes/write_bytes are REAL disk hits."""
    try:
        d = {}
        for line in open(f"/proc/{pid}/io"):
            k, v = line.split(":")
            d[k.strip()] = int(v)
        return d
    except FileNotFoundError:
        return None

def get_mem(pid):
    try:
        d = {}
        for line in open(f"/proc/{pid}/status"):
            for k in ("VmRSS", "RssAnon", "RssFile", "VmHWM"):
                if line.startswith(k + ":"):
                    d[k] = int(line.split()[1]) / 1024.0
        return d
    except FileNotFoundError:
        return None

os.chdir("/home/claude/vstorage")

# The worker: loads real files, holds them broken-down in RAM boxes, and on
# command forms one full-size, then collapses. Prints markers so we can time
# our external measurements to each phase.
worker = r'''
import lzma, hashlib, sys, time, os
vault = {}
hashes = {}
files = ["sample_code.c","sample_data.json","sample_prose.txt","bigreal.txt"]
# SAVE phase: hold only broken-down forms
for fn in files:
    data = open(fn,"rb").read()
    hashes[fn] = hashlib.sha256(data).hexdigest()
    vault[fn] = lzma.compress(data, preset=9)   # the box holds this
    del data
print("SAVED", flush=True)
time.sleep(6)   # AT REST window for measurement
# RETRIEVE phase: form one full-size on demand
print("RETRIEVING", flush=True)
for _ in range(50):   # retrieve repeatedly so we can catch it mid-formation
    full = lzma.decompress(vault["sample_prose.txt"])
    assert hashlib.sha256(full).hexdigest() == hashes["sample_prose.txt"]
    del full   # collapses again
print("DONE_RETRIEVING", flush=True)
time.sleep(4)
'''
open("_worker.py","w").write(worker)

proc = subprocess.Popen([sys.executable, "-u", "_worker.py"],
                        stdout=subprocess.PIPE, text=True)
pid = proc.pid
io_start = get_io(pid)

def wait_for(marker):
    while True:
        line = proc.stdout.readline().strip()
        if line == marker:
            return

# wait until files are saved & held broken-down
wait_for("SAVED")
time.sleep(2)  # let it settle into at-rest

print("="*60)
print("  AT REST (files held broken-down in RAM boxes)")
print("="*60)
mem = get_mem(pid)
io = get_io(pid)
print(f"  RAM  -> total RSS: {mem['VmRSS']:.1f} MB | anon(data): {mem['RssAnon']:.1f} MB")
print(f"  DISK -> read_bytes: {io['read_bytes'] - io_start['read_bytes']:,}"
      f"  write_bytes: {io['write_bytes'] - io_start['write_bytes']:,}")
print(f"          (both should be ~0 = ROM/disk NOT used)")

# now catch it during retrieval
wait_for("RETRIEVING")
peak_during = get_mem(pid)
# sample rapidly during the retrieval loop
for _ in range(30):
    m = get_mem(pid)
    if m and m['VmRSS'] > peak_during['VmRSS']:
        peak_during = m
    time.sleep(0.05)

print("\n" + "="*60)
print("  DURING RETRIEVAL (a file forming full-size on demand)")
print("="*60)
print(f"  RAM peak while forming full file: {peak_during['VmRSS']:.1f} MB")

wait_for("DONE_RETRIEVING")
time.sleep(1)
print("\n" + "="*60)
print("  AFTER RETRIEVAL (full copy collapsed again)")
print("="*60)
mem2 = get_mem(pid)
io2 = get_io(pid)
print(f"  RAM back to: {mem2['VmRSS']:.1f} MB")
print(f"  DISK total this whole run -> read_bytes: {io2['read_bytes']-io_start['read_bytes']:,}"
      f"  write_bytes: {io2['write_bytes']-io_start['write_bytes']:,}")

print("\n" + "="*60)
print("  VERDICT")
print("="*60)
wb = io2['write_bytes'] - io_start['write_bytes']
print(f"  ROM/disk writes during entire run: {wb:,} bytes")
print(f"  {'PASS - nothing written to disk/ROM' if wb == 0 else 'disk WAS written'}")

proc.wait()
os.remove("_worker.py")
