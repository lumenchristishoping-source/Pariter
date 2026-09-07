#!/usr/bin/env python3
"""
The real test: hold a 1GB file in the system, measure RAM excluding Python's
baseline. We measure the ACTUAL data cost (RssAnon) which excludes shared
library / interpreter overhead.

Key honesty check: the box holds the BROKEN-DOWN form. For 1GB of structured
data, how small is that form, and what's the true RAM cost at rest?
"""
import subprocess, time, os, sys

def get_mem(pid):
    d = {}
    try:
        for line in open(f"/proc/{pid}/status"):
            for k in ("VmRSS", "RssAnon", "VmHWM"):
                if line.startswith(k + ":"):
                    d[k] = int(line.split()[1]) / 1024.0
    except FileNotFoundError:
        return None
    return d

def get_io(pid):
    d = {}
    try:
        for line in open(f"/proc/{pid}/io"):
            k, v = line.split(":")
            d[k.strip()] = int(v)
    except FileNotFoundError:
        return None
    return d

os.chdir("/home/claude/vstorage")

worker = r'''
import lzma, hashlib, sys, time, gc
# SAVE the 1GB file into the system as a broken-down form
data = open("/dev/shm/onegig.dat","rb").read()
original_hash = hashlib.sha256(data).hexdigest()
original_size = len(data)
print(f"LOADED {original_size}", flush=True)

# break it down -- this is ALL the box holds
box = lzma.compress(data, preset=6)
del data                      # throw away the full 1GB
gc.collect()
print(f"BROKEN {len(box)}", flush=True)
time.sleep(6)                 # AT REST window -- only the broken form exists

# RETRIEVE: form the full 1GB on demand, verify, collapse
print("RETRIEVING", flush=True)
full = lzma.decompress(box)
ok = hashlib.sha256(full).hexdigest() == original_hash
print(f"RETRIEVED {len(full)} {ok}", flush=True)
del full
gc.collect()
print("COLLAPSED", flush=True)
time.sleep(4)
'''
open("_w1gb.py","w").write(worker)

proc = subprocess.Popen([sys.executable,"-u","_w1gb.py"],
                        stdout=subprocess.PIPE, text=True)
pid = proc.pid

def wait(marker):
    while True:
        l = proc.stdout.readline().split()
        if l and l[0] == marker:
            return l

wait("LOADED")
broken = wait("BROKEN")
broken_size = int(broken[1])
time.sleep(2)

print("="*60)
print("  1GB FILE SAVED — now held BROKEN-DOWN in the box")
print("="*60)
print(f"  Original file: 1,073,741,824 bytes (1 GB)")
print(f"  Box holds (broken-down): {broken_size:,} bytes ({broken_size/1024/1024:.2f} MB)")
m = get_mem(pid)
print(f"  Process RSS: {m['VmRSS']:.1f} MB")
print(f"  RssAnon (real data cost, excludes interpreter): {m['RssAnon']:.1f} MB")

wait("RETRIEVING")
peak = get_mem(pid)
for _ in range(100):
    x = get_mem(pid)
    if x and x['VmRSS'] > peak['VmRSS']: peak = x
    time.sleep(0.03)
r = wait("RETRIEVED")
print("\n" + "="*60)
print("  DURING RETRIEVAL — full 1GB forming on demand")
print("="*60)
print(f"  Retrieved {int(r[1]):,} bytes, exact match: {r[2]}")
print(f"  RAM peak during formation: {peak['VmRSS']:.1f} MB (the 1GB briefly exists)")

wait("COLLAPSED")
time.sleep(1)
m2 = get_mem(pid); io2 = get_io(pid)
print("\n" + "="*60)
print("  AFTER — full copy collapsed, back to holding only the box")
print("="*60)
print(f"  RAM back to: {m2['VmRSS']:.1f} MB (anon: {m2['RssAnon']:.1f} MB)")
print(f"  Disk write_bytes entire run: {io2['write_bytes']:,}  (ROM used? {'YES' if io2['write_bytes']>0 else 'NO'})")

proc.wait()
os.remove("_w1gb.py")
