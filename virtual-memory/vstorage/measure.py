"""Measurement helpers, exactly per HANDBOOK.md section 5 ("Measuring RAM
correctly" / "Measuring disk usage") - read from /proc, not getrusage(),
since VmHWM/peak-RSS never decreases and would misrepresent the at-rest cost.
"""

from __future__ import annotations


def rss_anon_mb(pid: str = "self") -> float:
    """Current anonymous-memory RSS in MB. Excludes shared libraries and
    interpreter overhead - the most honest measure of what the process
    itself is holding as its own data.
    """
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("RssAnon:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def vm_rss_mb(pid: str = "self") -> float:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def disk_write_bytes(pid: str = "self") -> int:
    with open(f"/proc/{pid}/io") as f:
        for line in f:
            if line.startswith("write_bytes:"):
                return int(line.split()[1])
    return 0
