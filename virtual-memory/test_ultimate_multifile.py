#!/usr/bin/env python3
"""The 'ultimate test': 24 real files (md, txt, json, geojson, csv, log,
py, pdf, docx, zip - every branch splitter.py has), ~2GB total, all
through the real SecureVirtualStorage in one run. Tracks:

  - save + retrieve speed, per file and in total
  - every RAM figure /proc exposes, at every stage
  - CPU cost, separately for the main process and the watchdog process
    (the watchdog busy-spins by design - see process_watchdog.py - so
    its own CPU cost is worth reporting honestly, not folded into "the
    system's" cost as if it were free)
  - whether every single held box's key is actually still hopping
    concurrently, not just one file's
  - real attack trials: a full memory dump of the WHOLE live process
    from a genuinely separate attacker process
    (_ultimate_multifile_attacker.py), hunting for 3 markers planted
    in plaintext source files, while everything is encrypted and
    falling. Deliberately a SEPARATE process, not the storage process
    scanning itself - a first version did that and "found" its own
    search strings trivially, because holding a marker in a variable
    to search for it necessarily puts that marker in this process's
    own memory. Caught by smoke-testing on 2 files before committing
    to the full ~2GB run - worth recording as a real methodology bug
    found and fixed here, not glossed over.
  - a real mid-run retrieve-verify-restore of one file while every
    other file keeps falling untouched
  - final retrieval and verification of every file (including the
    restored duplicate)
  - the finale: a real ptrace_attach from OUTSIDE this test, proving
    the watchdog still reacts and kills under this much concurrent
    load, timed the same way test_tier1_security.py verified it in
    isolation

The actual work runs in a child process (_ultimate_multifile_child.py)
so this orchestrator can monitor it from outside, abort safely if
something runs away, and watch the finale kill it without taking the
whole session down too - same pattern as test_12gb_stream.py.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time

PTRACE_ATTACH = 16
SAFETY_LIMIT_KB = 3_000_000  # generous given ~15GB free, still a real guard
HERE = os.path.dirname(os.path.abspath(__file__))
CHILD_PATH = os.path.join(HERE, "_ultimate_multifile_child.py")
ATTACKER_PATH = os.path.join(HERE, "_ultimate_multifile_attacker.py")
OUT_DIR_FOR_ATTACKER = os.environ.get("ULTIMATE_OUT_DIR", "/dev/shm/ultimate_test")
# The attacker reads ONLY this file - never the plain _manifest.json the
# child/target reads, so the markers never touch the target's memory as
# a side effect of test bookkeeping (see the module docstring above).
MARKERS_PATH = OUT_DIR_FOR_ATTACKER + "/_markers.json"
ATTACKER_TRIALS = int(os.environ.get("ULTIMATE_ATTACKER_TRIALS", "3"))
EVENTS_LOG = "/tmp/ultimate_test_events.jsonl"


def kb(x):
    return f"{x:>10,} KB" if isinstance(x, int) else str(x)


def run_attacker(target_pid: int, out: dict) -> None:
    """Runs in a background thread of the PARENT (not the child) so it
    doesn't stall reading the child's own event stream while a scan is
    in flight - each trial can take real time against a heavily
    protected process (see the attacker script's own docstring)."""
    proc = subprocess.run(
        [sys.executable, ATTACKER_PATH, str(target_pid), MARKERS_PATH,
         str(ATTACKER_TRIALS)],
        capture_output=True, text=True,
    )
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    trials = []
    summary = None
    for line in lines:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("summary"):
            summary = evt
        else:
            trials.append(evt)
    out["trials"] = trials
    out["summary"] = summary
    out["stderr"] = proc.stderr
    out["done"] = True


def main() -> None:
    print(f"child: {CHILD_PATH}")
    print(f"events log: {EVENTS_LOG}\n")

    child = subprocess.Popen(
        [sys.executable, CHILD_PATH],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    events = []
    aborted = False
    tamper_result = None
    attacker_out: dict = {"done": False}
    attacker_thread: threading.Thread | None = None

    with open(EVENTS_LOG, "w") as logf:
        for line in child.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                print("  [non-json]", line)
                continue
            events.append(evt)
            logf.write(json.dumps(evt) + "\n")
            logf.flush()

            stage = evt.get("stage", "?")

            if stage == "start":
                s = evt["snapshot"]
                print(f"[start] pid={evt['pid']} cpu_count={evt.get('cpu_count')} "
                      f"RssAnon={kb(s.get('RssAnon'))}")

            elif stage == "watchdog_started":
                print(f"[watchdog] separate OS process pid={evt['watchdog_pid']}")

            elif stage == "saved_one":
                print(f"  saved {evt['name']:16s} {evt['size']/1024/1024:8.2f} MB "
                      f"in {evt['seconds']:6.2f}s")

            elif stage == "all_saved":
                s = evt["snapshot"]
                print(f"\n[all_saved] {evt['file_count']} files in "
                      f"{evt['total_seconds']:.2f}s  threads={evt['thread_count']}")
                print(f"  RssAnon={kb(s.get('RssAnon'))}  VmRSS={kb(s.get('VmRSS'))}")
                print(f"  cpu_main={evt.get('cpu_main')}  cpu_watchdog={evt.get('cpu_watchdog')}\n")

                print(f"[attacker] launching external attacker against pid={child.pid} "
                      f"({ATTACKER_TRIALS} full memory-dump trials, runs concurrently "
                      f"in the background)...\n")
                attacker_thread = threading.Thread(
                    target=run_attacker, args=(child.pid, attacker_out), daemon=True)
                attacker_thread.start()

            elif stage == "settled":
                s = evt["snapshot"]
                print(f"[settled +window] RssAnon={kb(s.get('RssAnon'))}")

            elif stage == "mid_test_retrieve":
                print(f"\n[mid-test retrieve] {evt['name']} in {evt['seconds']}s  "
                      f"byte_perfect={evt['byte_perfect']}")

            elif stage == "sent_back_in":
                print(f"[sent back in] {evt['name']} re-stored in {evt['seconds']}s "
                      f"as new id {evt['new_file_id'][:8]}...\n")

            elif stage == "hop_concurrency_check":
                print(f"[key motion check] {evt['boxes_total']} boxes total, "
                      f"{evt['boxes_stalled']} stalled")
                print(f"  hops/box over the window: min={evt['min_hops']} "
                      f"avg={evt['avg_hops']} max={evt['max_hops']}\n")

            elif stage == "final_retrieve_one":
                mark = "OK" if evt["byte_perfect"] else "MISMATCH"
                print(f"  retrieved {evt['label']:32s} {evt['seconds']:6.3f}s  {mark}")

            elif stage == "all_retrieved":
                s = evt["snapshot"]
                print(f"\n[all_retrieved] {evt['file_count']} retrievals in "
                      f"{evt['total_seconds']:.2f}s  all_byte_perfect={evt['all_byte_perfect']}")
                print(f"  total bytes: {evt['total_bytes']/1024/1024:.1f} MB")
                print(f"  RssAnon={kb(s.get('RssAnon'))}  VmRSS={kb(s.get('VmRSS'))}")
                print(f"  cpu_main={evt.get('cpu_main')}  cpu_watchdog={evt.get('cpu_watchdog')}\n")

            elif stage == "ready_for_tamper_test":
                print("[finale] child ready. Waiting for the attacker to finish "
                      "its trials before triggering the kill test...")
                if attacker_thread is not None:
                    attacker_thread.join()
                if attacker_out.get("summary"):
                    a = attacker_out["summary"]
                    print(f"[attacker done] {a['trials_completed']} trials completed, "
                          f"any_marker_found={a['any_marker_found']} "
                          f"(total hits: {a['total_marker_hits']})")
                    print(f"  avg scan time: {a['avg_seconds']}s over "
                          f"{a['regions_scanned_last']} regions / "
                          f"{a['bytes_scanned_last']/1024/1024:.1f} MB\n")

                print("[finale] Attaching via real ptrace_attach from OUTSIDE "
                      "the process now...")
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                t0 = time.perf_counter()
                ret = libc.ptrace(PTRACE_ATTACH, child.pid, None, None)
                attach_errno = ctypes.get_errno() if ret == -1 else None
                # Busy-poll for the REAL death, not just the attach's own
                # SIGSTOP: because we are both the real parent AND the
                # ptrace tracer here, a plain child.poll() reports that
                # attach-induced stop almost instantly (WIFSTOPPED, Python
                # surfaces it as returncode -19) - that is NOT the
                # watchdog's kill, just a side effect of attaching. Found
                # by smoke-testing first: the finale looked "done" in
                # under 100us every time, which was suspiciously faster
                # than the watchdog's own busy-spin poll interval could
                # explain. Skip past WIFSTOPPED and keep waiting for a
                # real WIFEXITED/WIFSIGNALED status.
                real_status = None
                while True:
                    try:
                        wpid, status = os.waitpid(child.pid, os.WNOHANG | os.WUNTRACED)
                    except ChildProcessError:
                        break
                    if wpid == 0 or os.WIFSTOPPED(status):
                        continue
                    real_status = status
                    break
                t1 = time.perf_counter()
                if real_status is not None and os.WIFSIGNALED(real_status):
                    child.returncode = -os.WTERMSIG(real_status)
                elif real_status is not None and os.WIFEXITED(real_status):
                    child.returncode = os.WEXITSTATUS(real_status)
                tamper_result = {
                    "ptrace_attach_return": ret,
                    "ptrace_attach_errno": attach_errno,
                    "elapsed_seconds": t1 - t0,
                    "elapsed_us": (t1 - t0) * 1_000_000,
                    "child_returncode": child.returncode,
                    "killed_by_sigkill": child.returncode == -9,
                }
                print(f"  ptrace_attach returned {ret} (errno={attach_errno})")
                print(f"  child died {tamper_result['elapsed_us']:.0f}us after attach, "
                      f"returncode={child.returncode} "
                      f"(SIGKILL: {tamper_result['killed_by_sigkill']})\n")

            if "snapshot" in evt:
                rss_anon = evt["snapshot"].get("RssAnon", 0)
                if rss_anon > SAFETY_LIMIT_KB:
                    print(f"\n!!! SAFETY ABORT: RssAnon {rss_anon:,} KB exceeded "
                          f"{SAFETY_LIMIT_KB:,} KB - killing child.")
                    child.kill()
                    aborted = True
                    break

    if child.poll() is None:
        child.wait(timeout=10)
    if attacker_thread is not None and attacker_thread.is_alive():
        attacker_thread.join(timeout=30)

    print("=" * 60)
    print(f"child final exit code: {child.returncode}  aborted_by_safety: {aborted}")
    if tamper_result:
        print(f"tamper test: {json.dumps(tamper_result, indent=2)}")

    with open("/tmp/ultimate_test_summary.json", "w") as f:
        json.dump({"events": events, "tamper_result": tamper_result,
                   "attacker": attacker_out, "aborted": aborted,
                   "child_returncode": child.returncode}, f)
    print(f"\nfull event log: {EVENTS_LOG}")
    print("summary json: /tmp/ultimate_test_summary.json")


if __name__ == "__main__":
    main()
