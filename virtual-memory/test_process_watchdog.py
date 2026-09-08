#!/usr/bin/env python3
"""Confirms the fix: the SAME setup that made the thread-based watchdog
take ~63-90ms to react (a busy target with 7 GIL-competing threads),
now protected by a watchdog running as its own OS process instead.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time

CHILD_SCRIPT = """
import sys, time, os, threading, mmap, ctypes
sys.path.insert(0, {vstorage_dir!r})

MARKER = {marker!r}.encode()
buf = mmap.mmap(-1, mmap.PAGESIZE)
buf[:len(MARKER)] = MARKER
addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))

# the SAME busy-GIL setup that caused the 90ms regression before
stop = threading.Event()
def busy():
    x = 0
    while not stop.is_set():
        x += 1
for _ in range(7):
    threading.Thread(target=busy, daemon=True).start()

print(f"{{os.getpid()}} {{addr}}", flush=True)
time.sleep(10)
"""


def main() -> None:
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    marker = "SECRET_" + os.urandom(4).hex()
    tmpdir = tempfile.mkdtemp(prefix="process_watchdog_test_")
    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(CHILD_SCRIPT.format(vstorage_dir=vstorage_dir, marker=marker))

    child = subprocess.Popen([sys.executable, script_path],
                              stdout=subprocess.PIPE, text=True)
    line = child.stdout.readline()
    child_pid_s, addr_s = line.split()
    child_pid, addr = int(child_pid_s), int(addr_s)
    print(f"target pid={child_pid} (with 7 busy GIL-competing threads), "
          f"marker address={hex(addr)}")

    sys.path.insert(0, vstorage_dir)
    from vstorage.process_watchdog import ProcessWatchdog

    wd = ProcessWatchdog(target_pid=child_pid, kill_target=False)
    wd.add_region(addr, len(marker))
    time.sleep(0.2)

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    t_attach = time.time()
    libc.ptrace(PTRACE_ATTACH, child_pid, None, None)

    reads = []
    for _ in range(2000):
        try:
            with open(f"/proc/{child_pid}/mem", "rb", buffering=0) as f:
                f.seek(addr)
                data = f.read(len(marker))
            reads.append((time.time(), data))
            if marker.encode() not in data:
                break
        except Exception as e:
            reads.append((time.time(), f"EXC:{e}"))
            break

    real_marker_reads = sum(1 for _, d in reads if isinstance(d, bytes)
                             and marker.encode() in d)
    print(f"\nattacker: {len(reads)} reads attempted, "
          f"{real_marker_reads} captured the real marker")
    print(f"attacker's first read: +{(reads[0][0]-t_attach)*1000:.3f}ms after attach")
    print(f"attacker's last read:  +{(reads[-1][0]-t_attach)*1000:.3f}ms after attach")

    for _ in range(200):
        if wd.triggered:
            break
        time.sleep(0.005)
    if wd.triggered:
        print(f"\nProcessWatchdog detected + wiped at "
              f"+{(wd.detected_at - t_attach)*1000:.3f}ms after attach "
              f"(compare to ~63-90ms for the thread-based version under "
              f"the same busy-GIL conditions)")
        print(f"attacker won: {real_marker_reads > 0}")
    else:
        print("\nwatchdog never triggered")

    wd.stop()
    child.kill()


if __name__ == "__main__":
    main()
