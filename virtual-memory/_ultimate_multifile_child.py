#!/usr/bin/env python3
"""The actual work for the 'ultimate test' (test_ultimate_multifile.py):
runs inside its own OS process so the orchestrator (parent) can monitor
it from outside, abort it safely if something runs away, and - for the
finale - watch it get killed by its own tamper watchdog without taking
the whole session down too.

Every stage prints one JSON line to stdout (flushed immediately), same
convention as test_12gb_stream.py, so the parent can render/react to
each stage as it happens rather than waiting for the whole thing to
finish.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
from vstorage.measure_full import full_snapshot, system_snapshot  # noqa: E402
from vstorage.secure_system import SecureVirtualStorage  # noqa: E402

try:
    import psutil
except ImportError:
    psutil = None

OUT_DIR = os.environ.get("ULTIMATE_OUT_DIR", "/dev/shm/ultimate_test")
MANIFEST_PATH = os.path.join(OUT_DIR, "_manifest.json")
RESTORE_TARGET = os.environ.get("ULTIMATE_RESTORE_TARGET", "table_2.csv")
SETTLE_SECONDS = float(os.environ.get("ULTIMATE_SETTLE_SECONDS", "3.0"))
# The finale (a real ptrace_attach from the parent) is timed externally -
# this is just a generous ceiling so the child never dies of a plain
# timeout while the parent's separate attacker process is still working
# through a full memory scan against it (each trial can take minutes at
# this many protected regions - see _ultimate_multifile_attacker.py).
TAMPER_WAIT_CEILING = float(os.environ.get("ULTIMATE_TAMPER_WAIT", "600.0"))


def emit(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, "t": time.time(), **kw}), flush=True)


def _cpu_snapshot(pid: int) -> dict:
    if psutil is None:
        return {}
    try:
        p = psutil.Process(pid)
        ct = p.cpu_times()
        return {"user": ct.user, "system": ct.system, "num_threads": p.num_threads()}
    except Exception:
        return {}


def _hops_all(storage: SecureVirtualStorage) -> dict:
    """Every single box's hop counter, across every held file and every
    piece (content/structure/metadata) - the direct evidence for
    whether keys are moving CONCURRENTLY across the whole held set, not
    just one file at a time."""
    out = {}
    for file_id, held in storage._held.items():
        for piece, box in held.boxes.items():
            out[f"{file_id}:{piece}"] = box.hops
    return out


def main() -> None:
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    files_by_name = {e["name"]: e for e in manifest["files"]}

    emit("start", pid=os.getpid(), snapshot=full_snapshot(),
         system=system_snapshot(),
         cpu_count=(psutil.cpu_count() if psutil else None))

    storage = SecureVirtualStorage(watch_for_tampering=True, kill_on_tamper=True)
    watchdog_pid = storage._watchdog._process.pid if storage._watchdog else None
    emit("watchdog_started", watchdog_pid=watchdog_pid)

    # -- Save phase: every file, through the real save() path -----------
    save_times = {}
    file_ids = {}
    t_save_start = time.time()
    for name in sorted(files_by_name):
        path = os.path.join(OUT_DIR, name)
        t0 = time.time()
        fid = storage.save(path)
        dt = time.time() - t0
        save_times[name] = dt
        file_ids[name] = fid
        emit("saved_one", name=name, seconds=round(dt, 3),
             size=files_by_name[name]["size"])
    total_save_time = time.time() - t_save_start

    emit("all_saved", total_seconds=round(total_save_time, 3),
         file_count=len(file_ids), snapshot=full_snapshot(),
         system=system_snapshot(), thread_count=threading.active_count(),
         cpu_main=_cpu_snapshot(os.getpid()),
         cpu_watchdog=_cpu_snapshot(watchdog_pid) if watchdog_pid else {})

    hops_t0 = _hops_all(storage)
    time.sleep(SETTLE_SECONDS)
    emit("settled", snapshot=full_snapshot())

    # -- Mid-test retrieve, verify, and send it back in ------------------
    restore_entry = files_by_name[RESTORE_TARGET]
    t0 = time.time()
    retrieved = storage.retrieve(file_ids[RESTORE_TARGET])
    retrieve_dt = time.time() - t0
    got_hash = hashlib.sha256(retrieved).hexdigest()
    matched = (got_hash == restore_entry["sha256"])
    emit("mid_test_retrieve", name=RESTORE_TARGET, seconds=round(retrieve_dt, 4),
         byte_perfect=matched, expected_sha256=restore_entry["sha256"][:16],
         got_sha256=got_hash[:16])

    t0 = time.time()
    restored_id = storage.save_bytes(bytes(retrieved), name_hint=RESTORE_TARGET)
    restore_dt = time.time() - t0
    emit("sent_back_in", name=RESTORE_TARGET, seconds=round(restore_dt, 4),
         new_file_id=restored_id)

    time.sleep(SETTLE_SECONDS)

    hops_t1 = _hops_all(storage)
    deltas = {k: hops_t1[k] - hops_t0.get(k, 0) for k in hops_t1}
    stalled = [k for k, v in deltas.items() if v <= 0]
    emit("hop_concurrency_check", boxes_total=len(deltas),
         boxes_stalled=len(stalled), stalled_examples=stalled[:5],
         min_hops=min(deltas.values()) if deltas else 0,
         max_hops=max(deltas.values()) if deltas else 0,
         avg_hops=round(sum(deltas.values()) / max(len(deltas), 1), 2))

    # -- Final retrieval: everything, including the restored duplicate --
    all_ids = dict(file_ids)
    all_ids[f"{RESTORE_TARGET}::restored_copy"] = restored_id
    verify_results = []
    t_retrieve_all_start = time.time()
    for label, fid in all_ids.items():
        base_name = label.split("::")[0]
        expected = files_by_name[base_name]["sha256"]
        t0 = time.time()
        data = storage.retrieve(fid)
        dt = time.time() - t0
        got = hashlib.sha256(data).hexdigest()
        ok = (got == expected)
        verify_results.append({"label": label, "seconds": round(dt, 4),
                                "byte_perfect": ok, "size": len(data)})
        emit("final_retrieve_one", label=label, seconds=round(dt, 4),
             byte_perfect=ok, size=len(data))
    total_retrieve_time = time.time() - t_retrieve_all_start

    all_ok = all(r["byte_perfect"] for r in verify_results)
    total_bytes = sum(r["size"] for r in verify_results)
    emit("all_retrieved", total_seconds=round(total_retrieve_time, 3),
         file_count=len(verify_results), all_byte_perfect=all_ok,
         total_bytes=total_bytes, snapshot=full_snapshot(),
         system=system_snapshot(), thread_count=threading.active_count(),
         cpu_main=_cpu_snapshot(os.getpid()),
         cpu_watchdog=_cpu_snapshot(watchdog_pid) if watchdog_pid else {})

    emit("ready_for_tamper_test", pid=os.getpid())
    time.sleep(TAMPER_WAIT_CEILING)
    emit("tamper_test_timed_out_unexpectedly")  # should never print - the
    # parent's ptrace_attach + this process's own watchdog should end
    # this well before the ceiling elapses


if __name__ == "__main__":
    main()
