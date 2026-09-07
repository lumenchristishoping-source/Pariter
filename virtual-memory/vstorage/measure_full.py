"""Comprehensive memory measurement - every RAM figure Linux exposes for a
process, plus the system-wide picture, not just RssAnon.

Why more than RssAnon: RssAnon (what HANDBOOK.md's own methodology uses)
is the right number for "how much of MY OWN heap data is this process
holding" - it's honest and it's what we've reported everywhere so far.
But it's one slice of a bigger picture. Full picture, all from /proc,
nothing estimated:

  Per-process (/proc/pid/status):
    VmRSS      - total resident memory (anon + file + shared)
    RssAnon    - resident anonymous memory (heap/stack - "my own data")
    RssFile    - resident memory-mapped files (code, libraries)
    RssShmem   - resident shared memory (tmpfs, /dev/shm, shared mappings)
    VmHWM      - peak RSS ever reached (high water mark - never decreases)
    VmSize     - total virtual address space reserved (not necessarily
                 resident - includes memory the OS hasn't backed with
                 real pages yet)
    VmSwap     - how much of this process's memory has been swapped to disk
    VmData     - size of the data segment (heap)
    VmStk      - stack size

  Per-process, finer-grained (/proc/pid/smaps_rollup):
    Pss             - proportional share (shared pages divided fairly
                       among the processes sharing them - the "fair"
                       total across all processes on the machine)
    Private_Dirty   - memory only this process has, modified (the real
                       cost if this process alone is considered)
    Private_Clean   - memory only this process has, unmodified
    Shared_Dirty/Shared_Clean - memory other processes could also see

  System-wide (/proc/meminfo):
    MemAvailable, SwapFree, Shmem, AnonPages, Cached - the whole
    machine's state, not just this one process's view of it.
"""

from __future__ import annotations

STATUS_FIELDS = (
    "VmPeak", "VmSize", "VmHWM", "VmRSS", "RssAnon", "RssFile", "RssShmem",
    "VmData", "VmStk", "VmExe", "VmLib", "VmPTE", "VmSwap", "VmLck", "VmPin",
)

SMAPS_FIELDS = (
    "Rss", "Pss", "Shared_Clean", "Shared_Dirty", "Private_Clean",
    "Private_Dirty", "Referenced", "Anonymous", "LazyFree",
    "AnonHugePages", "Swap", "SwapPss",
)

MEMINFO_FIELDS = (
    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached",
    "SwapCached", "SwapTotal", "SwapFree", "Shmem", "AnonPages",
    "Mapped", "Slab", "SReclaimable", "SUnreclaim", "Dirty", "Writeback",
)


def full_snapshot(pid: str = "self") -> dict:
    """Every per-process memory figure available, in KB (as /proc reports
    them). Missing fields (e.g. no smaps_rollup on some kernels) are
    simply absent from the result rather than faked.
    """
    result = {}

    with open(f"/proc/{pid}/status") as f:
        for line in f:
            for key in STATUS_FIELDS:
                if line.startswith(key + ":"):
                    result[key] = int(line.split()[1])

    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                for key in SMAPS_FIELDS:
                    if line.startswith(key + ":"):
                        result[f"smaps_{key}"] = int(line.split()[1])
    except FileNotFoundError:
        pass

    return result


def system_snapshot() -> dict:
    """Every relevant system-wide figure from /proc/meminfo, in KB."""
    result = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            key = parts[0].rstrip(":")
            if key in MEMINFO_FIELDS:
                result[key] = int(parts[1])
    return result
