#!/usr/bin/env python3
"""A genuinely separate, external attacker process for the 'ultimate
test'. Deliberately NOT part of _ultimate_multifile_child.py - if the
process holding the data also held the marker strings it's searching
for in its own Python variables, a "full self-scan" would trivially
"find" its own search terms and prove nothing. This process knows the
markers only because it reads the same manifest a real attacker who'd
seen the original files could also have read, and it reads the
TARGET's memory from OUTSIDE via /proc/<target_pid>/mem - a real
cross-process full memory dump, the same class of attack
test_tier1_security.py and test_tier2_encryption.py used, just against
the whole live process instead of one already-known buffer.

Usage: _ultimate_multifile_attacker.py <target_pid> <manifest_path> <trials>
Prints one JSON line per trial, then one JSON summary line, to stdout.
"""
from __future__ import annotations

import json
import sys
import time


def scan_once(target_pid: int, marker_bytes: dict) -> dict:
    found = {name: False for name in marker_bytes}
    regions_read = 0
    bytes_read = 0
    try:
        with open(f"/proc/{target_pid}/maps") as f:
            maps_lines = f.readlines()
    except (FileNotFoundError, ProcessLookupError):
        return {"found": found, "regions_scanned": 0, "bytes_scanned": 0,
                "target_alive": False}

    try:
        memf = open(f"/proc/{target_pid}/mem", "rb", buffering=0)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return {"found": found, "regions_scanned": 0, "bytes_scanned": 0,
                "target_alive": False}

    with memf:
        for line in maps_lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            addr_range, perms = parts[0], parts[1]
            if "r" not in perms:
                continue
            path = parts[-1] if len(parts) >= 6 else ""
            if "[vsyscall]" in path or "[vvar]" in path:
                continue
            try:
                start_s, end_s = addr_range.split("-")
                start, end = int(start_s, 16), int(end_s, 16)
            except ValueError:
                continue
            length = end - start
            if length <= 0 or length > 512 * 1024 * 1024:
                continue
            try:
                memf.seek(start)
                chunk = memf.read(length)
            except OSError:
                continue
            if not chunk:
                continue
            regions_read += 1
            bytes_read += len(chunk)
            for name, mb in marker_bytes.items():
                if not found[name] and mb in chunk:
                    found[name] = True

    return {"found": found, "regions_scanned": regions_read,
            "bytes_scanned": bytes_read, "target_alive": True}


def main() -> None:
    target_pid = int(sys.argv[1])
    manifest_path = sys.argv[2]
    trials = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    with open(manifest_path) as f:
        manifest = json.load(f)
    marker_bytes = {
        manifest["marker_1_file"]: manifest["marker_1"].encode("utf-8"),
        manifest["marker_2_file"]: manifest["marker_2"].encode("utf-8"),
        manifest["marker_3_file"]: manifest["marker_3"].encode("utf-8"),
    }

    results = []
    for i in range(trials):
        t0 = time.time()
        r = scan_once(target_pid, marker_bytes)
        dt = time.time() - t0
        r["trial"] = i
        r["seconds"] = round(dt, 3)
        print(json.dumps(r), flush=True)
        results.append(r)
        if not r["target_alive"]:
            break

    any_found = any(any(r["found"].values()) for r in results)
    total_hits = sum(sum(1 for v in r["found"].values() if v) for r in results)
    completed = [r for r in results if r["target_alive"]]
    summary = {
        "summary": True,
        "trials_completed": len(completed),
        "any_marker_found": any_found,
        "total_marker_hits": total_hits,
        "avg_seconds": round(sum(r["seconds"] for r in completed) / len(completed), 3) if completed else None,
        "regions_scanned_last": completed[-1]["regions_scanned"] if completed else 0,
        "bytes_scanned_last": completed[-1]["bytes_scanned"] if completed else 0,
    }
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
