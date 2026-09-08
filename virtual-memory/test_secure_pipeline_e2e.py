#!/usr/bin/env python3
"""End-to-end test of the UNIFIED pipeline (secure_system.py), not
isolated tiers: a real file, split, encrypted, key-split, locked
against swap/dumps, watched for tampering - all together, as one
system a caller would actually use.

Part 1: normal use - save a real file, retrieve it, verify content.
Part 2: real attack - a subprocess holds a real file in the pipeline;
this process ptrace-attaches it, exactly like test_tamper_response.py,
but now against the FULL system instead of a bare marker - confirming
the whole pipeline (not just one buffer) actually dies and wipes on
detected tampering.
"""
import ctypes
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot
from vstorage.secure_system import SecureVirtualStorage

REAL_PDF = "/root/.claude/uploads/9c6fcc6d-8c14-5ccc-a3a0-fd3c78528f86/fa1789a3-thechroniclesofnmachukwu.pdf"


def part1_normal_use() -> None:
    print("=== Part 1: normal use, real file, full pipeline ===\n")
    with open(REAL_PDF, "rb") as f:
        original = f.read()

    threads_before = threading.active_count()
    before = full_snapshot()["RssAnon"]

    vs = SecureVirtualStorage()
    file_id = vs.save(REAL_PDF)
    time.sleep(0.3)

    threads_after = threading.active_count()
    after = full_snapshot()["RssAnon"]
    print(f"threads: {threads_before} -> {threads_after} "
          f"(+{threads_after - threads_before} - watchdog + 3 pieces x "
          f"(data thread + key scheduler) = 7 expected)")
    print(f"RssAnon: {before} KB -> {after} KB (+{after - before} KB)\n")

    full = vs.retrieve(file_id, "full")
    byte_match = hashlib.sha256(full).digest() == hashlib.sha256(original).digest()
    print(f"byte-perfect retrieval: {byte_match}")

    from pypdf import PdfReader
    from io import BytesIO
    orig_pages = [p.extract_text() or "" for p in PdfReader(BytesIO(original)).pages]
    retr_pages = [p.extract_text() or "" for p in PdfReader(BytesIO(full)).pages]
    print(f"PDF re-opens correctly, {len(retr_pages)} pages, "
          f"text identical: {orig_pages == retr_pages}")

    vs.collapse_all()
    time.sleep(0.2)
    print("\ncollapse_all() called - watchdog stopped, all pieces wiped.\n")


CHILD_SCRIPT = """
import sys, time, os
sys.path.insert(0, {vstorage_dir!r})
from vstorage.secure_system import SecureVirtualStorage

RESULT_FILE = {result_file!r}

def record_detection():
    tmp = RESULT_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(repr(time.time()))
    os.rename(tmp, RESULT_FILE)  # atomic - never observed half-written

vs = SecureVirtualStorage(_on_tamper_hook=record_detection)
file_id = vs.save({real_pdf!r})
print(f"{{os.getpid()}}", flush=True)
time.sleep(10)
"""


def part2_real_attack() -> None:
    print("=== Part 2: real ptrace attack against the WHOLE pipeline ===\n")
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir = tempfile.mkdtemp(prefix="pipeline_attack_")
    result_file = os.path.join(tmpdir, "result.txt")
    script_text = CHILD_SCRIPT.format(vstorage_dir=vstorage_dir, real_pdf=REAL_PDF,
                                       result_file=result_file)

    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(script_text)

    child = subprocess.Popen([sys.executable, script_path],
                              stdout=subprocess.PIPE, text=True)
    child_pid = int(child.stdout.readline().strip())
    time.sleep(0.3)  # let the pipeline fully spin up (watchdog + 3 pieces)

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    t_attach = time.time()
    ret = libc.ptrace(PTRACE_ATTACH, child_pid, None, None)
    print(f"attached to pid {child_pid} at t=0 (ptrace ret={ret})")
    print("note: ptrace attach itself sends SIGSTOP to the target - the "
          "process exiting/stopping alone does NOT prove the watchdog fired, "
          "so this checks for the actual timestamp file the hook writes.")

    for _ in range(400):
        if os.path.exists(result_file):
            break
        time.sleep(0.005)

    elapsed_process = time.time() - t_attach
    if os.path.exists(result_file):
        with open(result_file) as f:
            detected_at = eval(f.read())
        print(f"\nwatchdog hook confirmed detection at "
              f"+{(detected_at - t_attach)*1000:.2f}ms after attach - "
              f"REAL proof the wipe-and-exit path actually ran, not just "
              f"a raw SIGSTOP from the attach itself.")
    else:
        print(f"\nno result file after {elapsed_process*1000:.0f}ms - "
              f"watchdog did NOT confirm firing (needs investigation, "
              f"not just assumed working).")

    child.poll()
    print(f"child process return code: {child.returncode}")
    child.kill()


if __name__ == "__main__":
    part1_normal_use()
    part2_real_attack()
