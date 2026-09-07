#!/usr/bin/env python3
"""
Reliable, external memory measurement for both Virtual Storage designs.

We do NOT trust the program's own internal RSS printouts (those got us into
trouble). Instead we read the OS's own truth from /proc/<pid>/status,
repeatedly, from outside the process:

  VmRSS   = total current physical RAM the process holds RIGHT NOW
  RssAnon = the anonymous-memory portion (our actual file data lives here)
  VmHWM   = peak RSS ever reached (shows the transfer spike)

We sample many times over several seconds and report min/max/avg, so timing
flukes can't produce a single misleading number.
"""

import subprocess
import time
import os
import statistics

def read_proc_mem(pid):
    """Return (VmRSS, RssAnon, VmHWM) in MB, or None if process gone."""
    try:
        with open(f"/proc/{pid}/status") as f:
            content = f.read()
    except (FileNotFoundError, ProcessLookupError):
        return None
    vals = {}
    for line in content.splitlines():
        for key in ("VmRSS", "RssAnon", "VmHWM"):
            if line.startswith(key + ":"):
                vals[key] = int(line.split()[1]) / 1024.0  # KB -> MB
    if len(vals) < 3:
        return None
    return vals["VmRSS"], vals["RssAnon"], vals["VmHWM"]

def benchmark(binary, args, label, data_size_mb, sample_seconds=8):
    print(f"\n{'='*64}")
    print(f"  {label}")
    print(f"  Holding {data_size_mb}MB of data | binary: {binary}")
    print(f"{'='*64}")

    proc = subprocess.Popen([binary] + args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    pid = proc.pid
    time.sleep(2)  # let it get past setup into steady-state bouncing

    rss_samples, anon_samples, hwm_samples = [], [], []
    start = time.time()
    while time.time() - start < sample_seconds:
        m = read_proc_mem(pid)
        if m:
            rss, anon, hwm = m
            rss_samples.append(rss)
            anon_samples.append(anon)
            hwm_samples.append(hwm)
        time.sleep(0.2)

    proc.kill()
    proc.wait()

    if not rss_samples:
        print("  (process exited before sampling — likely errored)")
        return

    def summarize(name, samples, ratio_base=None):
        lo, hi, avg = min(samples), max(samples), statistics.mean(samples)
        line = f"  {name:10s} min={lo:7.1f}MB  avg={avg:7.1f}MB  max={hi:7.1f}MB"
        if ratio_base:
            line += f"   (avg is {avg/ratio_base:.2f}x the {ratio_base}MB data)"
        print(line)

    print(f"  Samples taken: {len(rss_samples)} over {sample_seconds}s\n")
    summarize("VmRSS", rss_samples, data_size_mb)
    summarize("RssAnon", anon_samples, data_size_mb)
    summarize("VmHWM", hwm_samples)
    print()
    print(f"  --> RssAnon avg = {statistics.mean(anon_samples):.1f}MB is the honest "
          f"cost of the {data_size_mb}MB of actual data.")


if __name__ == "__main__":
    os.chdir("/home/claude/vstorage")

    # Design 1: separate regions A<->B, holding a single 100MB file.
    # (two 100MB regions allocated; we measure what's actually resident)
    benchmark(
        "./anonymous_mmap_storage",
        [str(100 * 1024 * 1024)],
        "DESIGN 1: Separate regions A <-> B (single 100MB file)",
        data_size_mb=100,
    )

    print("\n\n")

    # Design 2: single self-destroying box holding one 100MB file.
    benchmark(
        "./one_box_100mb",
        [],
        "DESIGN 2: Single self-destroying box (single 100MB file)",
        data_size_mb=100,
    )

    print("\n\n" + "="*64)
    print("  Both measured identically, from outside, via /proc. Compare RssAnon.")
    print("="*64)

