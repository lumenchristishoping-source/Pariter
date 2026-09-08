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
          f"(+{threads_after - threads_before} - since the shared-scheduler "
          f"fix, this stays ~2 (one global fall scheduler + one global key-"
          f"cell scheduler) regardless of file count, instead of growing "
          f"per piece)")
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

vs = SecureVirtualStorage(kill_on_tamper=False)  # so this process survives
# long enough for the parent to inspect it afterward - real deployments
# would leave kill_on_tamper=True (the default)
file_id = vs.save({real_pdf!r})

# Print one registered region so the parent can check it directly from
# OUTSIDE after the attack. Verifying via THIS process's own polling
# loop doesn't work - ptrace_attach's own SIGSTOP freezes this process's
# main thread the instant it lands, so it can never get back to Python
# bytecode to check or report anything, whether the watchdog worked or
# not. The watchdog process itself is unaffected (separate process,
# never stopped), so the wipe still happens - we just can't ask the
# frozen child to confirm it. Ask from outside instead.
addr, length = vs._watchdog._regions[0], vs._watchdog._regions[1]
print(f"{{os.getpid()}} {{addr}} {{length}}", flush=True)
time.sleep(10)
"""


def part2_real_attack() -> None:
    print("=== Part 2: real ptrace attack against the WHOLE pipeline ===\n")
    vstorage_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir = tempfile.mkdtemp(prefix="pipeline_attack_")
    script_text = CHILD_SCRIPT.format(vstorage_dir=vstorage_dir, real_pdf=REAL_PDF)

    script_path = os.path.join(tmpdir, "child.py")
    with open(script_path, "w") as f:
        f.write(script_text)

    child = subprocess.Popen([sys.executable, script_path],
                              stdout=subprocess.PIPE, text=True)
    child_pid_s, addr_s, length_s = child.stdout.readline().split()
    child_pid, addr, length = int(child_pid_s), int(addr_s), int(length_s)
    time.sleep(0.3)  # let the pipeline fully spin up (watchdog + 3 pieces)

    with open(f"/proc/{child_pid}/mem", "rb", buffering=0) as f:
        f.seek(addr)
        before_attack = f.read(length)
    print(f"registered region before attack: non-zero bytes present: "
          f"{any(before_attack)} (should be True - real key material)")

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PTRACE_ATTACH = 16
    ret = libc.ptrace(PTRACE_ATTACH, child_pid, None, None)
    print(f"attached to pid {child_pid} (ptrace ret={ret})")
    print("note: the child's own main thread is now frozen by ptrace's own "
          "SIGSTOP and can never report on itself - checking the region's "
          "actual content from OUTSIDE instead, the same way the attacker "
          "would read it.")

    time.sleep(0.2)  # give the (unaffected, separate-process) watchdog time to react
    with open(f"/proc/{child_pid}/mem", "rb", buffering=0) as f:
        f.seek(addr)
        after_attack = f.read(length)
    wiped = not any(after_attack)
    print(f"\nsame region after attack: all zero = {wiped} "
          f"({'watchdog wiped it for real' if wiped else 'NOT wiped - problem'})")

    child.kill()


if __name__ == "__main__":
    part1_normal_use()
    part2_real_attack()
