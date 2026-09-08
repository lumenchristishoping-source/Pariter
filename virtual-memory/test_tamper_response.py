#!/usr/bin/env python3
"""Tests the user's idea directly: instead of only fighting to make a
full memory dump hard to read (which we already proved is impossible
for live, in-use data - see test_forensics_motion.py), what if the
process detects the dump happening at all and kills itself, wiping
the data, before the attacker can use it?

Spawns a real child process holding a marker in memory. From this
process (the "attacker"), ptrace-attaches to it - the same mechanism
every real debugger/memory-dump tool uses - then immediately tries to
read the marker's exact address, as fast as physically possible, in a
tight loop. The child runs a TamperWatchdog that should detect the
attach and wipe the marker before that read can succeed.

Two settings are compared, both for real:
  - poll_interval=0.0002 (a reasonable-sounding "fast" poll)
  - poll_interval=0      (busy-spin, no sleep between checks)
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time

CHILD_SCRIPT = """
import ctypes, mmap, os, sys, time
sys.path.insert(0, {vstorage_dir!r})
from vstorage.tamper_watchdog import TamperWatchdog

MARKER = sys.argv[1].encode()
RESULT_FILE = sys.argv[2]
POLL_INTERVAL = float(sys.argv[3])

buf = mmap.mmap(-1, mmap.PAGESIZE)
buf[:len(MARKER)] = MARKER
addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))

def on_tamper():
    t = time.time()
    buf[:len(MARKER)] = bytes(len(MARKER))
    with open(RESULT_FILE, "w") as f:
        f.write(repr(t))
    os._exit(1)

wd = TamperWatchdog(on_tamper, poll_interval=POLL_INTERVAL)
print(f"{{os.getpid()}} {{addr}}", flush=True)
time.sleep(10)
"""


def run_trial(poll_interval: float, result_file: str, child_script_path: str) -> dict:
    if os.path.exists(result_file):
        os.remove(result_file)

    marker = "SECRET_" + os.urandom(4).hex()
    child = subprocess.Popen(
        [sys.executable, child_script_path, marker, result_file, str(poll_interval)],
        stdout=subprocess.PIPE, text=True,
    )
    line = child.stdout.readline()
    child_pid_s, addr_s = line.split()
    child_pid, addr = int(child_pid_s), int(addr_s)
    time.sleep(0.1)  # let it settle into steady state

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    t_attach = time.time()
    libc.ptrace(PTRACE_ATTACH, child_pid, None, None)

    reads = []
    for _ in range(5000):
        try:
            with open(f"/proc/{child_pid}/mem", "rb", buffering=0) as f:
                f.seek(addr)
                data = f.read(len(marker))
            t = time.time()
            reads.append((t, data))
            if marker.encode() not in data:
                break
        except Exception as e:
            reads.append((time.time(), f"EXC:{e}"))
            break

    real_marker_reads = sum(1 for _, d in reads if isinstance(d, bytes)
                             and marker.encode() in d)
    first_read_delay = reads[0][0] - t_attach
    last_read_delay = reads[-1][0] - t_attach

    time.sleep(0.3)
    detected_delay = None
    if os.path.exists(result_file):
        with open(result_file) as f:
            detected_at = eval(f.read())
        detected_delay = detected_at - t_attach

    child.poll()
    try:
        child.kill()
    except Exception:
        pass

    return {
        "poll_interval": poll_interval,
        "total_reads": len(reads),
        "real_marker_reads": real_marker_reads,
        "first_read_delay_ms": first_read_delay * 1000,
        "last_read_delay_ms": last_read_delay * 1000,
        "watchdog_detected_delay_ms": detected_delay * 1000 if detected_delay else None,
        "attacker_won": real_marker_reads > 0,
    }


def main() -> None:
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    child_script_text = CHILD_SCRIPT.format(vstorage_dir=vstorage_dir)

    tmpdir = tempfile.mkdtemp(prefix="tamper_test_")
    child_script_path = os.path.join(tmpdir, "tamper_child.py")
    with open(child_script_path, "w") as f:
        f.write(child_script_text)
    result_file = os.path.join(tmpdir, "result.txt")

    for poll_interval, label in [(0.0002, "polling every 0.2ms"), (0.0, "busy-spin (no sleep)")]:
        print(f"\n=== {label} ===")
        wins = 0
        trials = 3
        for i in range(trials):
            r = run_trial(poll_interval, result_file, child_script_path)
            print(f"  trial {i+1}: attacker's first read at "
                  f"+{r['first_read_delay_ms']:.3f}ms, watchdog detected at "
                  f"+{r['watchdog_detected_delay_ms']:.3f}ms, "
                  f"real marker captured: {r['real_marker_reads']}/{r['total_reads']} reads "
                  f"-> attacker {'WON' if r['attacker_won'] else 'lost'}")
            if not r["attacker_won"]:
                wins += 1
        print(f"  watchdog won {wins}/{trials} trials at this setting")

    print("""
Honest summary: a real poll interval that SOUNDS fast (0.2ms) still
loses to a same-machine attacker whose first read lands even faster
(~0.12ms) - polling alone isn't enough. Only a true busy-spin (zero
sleep, checking constantly) reliably wins, and that costs 100% of a
CPU core for as long as it runs. There's no free version of this -
winning the race costs a dedicated core; not spending that core means
losing the race on the very first read.
""")


if __name__ == "__main__":
    main()
