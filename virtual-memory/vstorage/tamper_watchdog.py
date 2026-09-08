"""The user's idea: instead of only fighting a full memory dump to
make data hard to read (which we proved is fundamentally impossible
for live, in-use data), detect the dump/attach happening AT ALL and
kill the process immediately - the same "Death" step already in the
handbook's design (section 4, step 5), just triggered by an attacker
instead of only by the owner or a crash.

How detection works: when another process ptrace-attaches to this
one (the same mechanism every debugger and memory-dumping tool uses
to read another process's memory), the kernel records that in this
process's own /proc/self/status, under TracerPid - normally 0, and
set to the attacker's pid the instant an attach happens. A background
thread polls that field as fast as practical; the moment it goes
non-zero, it wipes whatever sensitive material it's been told about
and calls os._exit() immediately - no cleanup, no chance for anything
else to run first.

Honest limits, stated up front:
  - This only catches ptrace-based access (debuggers, most memory-dump
    tools). A dump taken by suspending the whole VM/container from
    OUTSIDE (a hypervisor snapshot, a cold-boot RAM image) never goes
    through ptrace at all, so TracerPid never changes - this defense
    is blind to that class of attack entirely.
  - Root can generally still bypass this window (as with PR_SET_DUMPABLE
    in Tier 1) if it moves fast enough or uses a method that doesn't
    trip TracerPid.
  - It's a race, not a guarantee - and it's genuinely close. Tested for
    real in test_tamper_response.py: polling every 0.2ms LOSES the race
    (the attacker's first read lands ~0.12ms after attach - faster than
    a 0.2ms poll interval, so they get the real data before the
    watchdog even checks again). A true busy-spin (poll_interval=0, no
    sleep at all between checks) WINS reliably - detected in ~0.05ms,
    beating the attacker's ~0.12ms every trial (0/3 successful reads
    across repeated tests). The cost is real and worth knowing: a
    busy-spin burns 100% of one CPU core, continuously, for as long as
    the watchdog runs - there is no free version of winning this race.
"""

from __future__ import annotations

import os
import threading
from typing import Callable


def _tracer_pid() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("TracerPid:"):
                return int(line.split()[1])
    return 0


class TamperWatchdog:
    def __init__(self, on_tamper: Callable[[], None], poll_interval: float = 0.0):
        """poll_interval=0 (default) busy-spins - confirmed the only
        setting that actually wins the race against a fast attacker in
        testing. A positive interval is cheaper on CPU but was measured
        to lose the race outright (see module docstring)."""
        self._on_tamper = on_tamper
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._triggered = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        while not self._stop.is_set():
            if _tracer_pid() != 0:
                self._triggered.set()
                self._on_tamper()
                return
            self._stop.wait(self._poll_interval)

    @property
    def triggered(self) -> bool:
        return self._triggered.is_set()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
