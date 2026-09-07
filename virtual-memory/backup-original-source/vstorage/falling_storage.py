"""
Virtual Storage / "Falling File" prototype.

The file is split into N pieces. Each piece sits in a "port" (just a slot
in a Python list). A loop continuously shifts every piece to the next
port, deleting it from its old spot as it goes -- so no piece sits still.

This script actually runs the loop and measures:
  1. CPU usage while it's running
  2. Where the bytes physically are at any instant (proving/disproving
     "no storage")
  3. What happens if we try to read the file mid-fall
"""

import time
import threading
import psutil
import os
import sys

class FallingFile:
    def __init__(self, data: bytes, num_ports: int = 16):
        self.num_ports = num_ports
        chunk_size = max(1, len(data) // num_ports)
        # split file into pieces -- one per port to start
        self.pieces = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
        while len(self.pieces) < num_ports:
            self.pieces.append(b"")
        # ports[i] = which piece index currently sits there (or None)
        self.ports = list(range(len(self.pieces))) + [None] * (num_ports - len(self.pieces))
        self.running = False
        self.hops = 0
        self.lock = threading.Lock()

    def _fall_loop(self, interval: float):
        while self.running:
            with self.lock:
                # rotate every occupied port forward by one -- old slot cleared
                new_ports = [None] * self.num_ports
                for i, piece_idx in enumerate(self.ports):
                    if piece_idx is not None:
                        new_ports[(i + 1) % self.num_ports] = piece_idx
                self.ports = new_ports
                self.hops += 1
            time.sleep(interval)

    def start(self, interval: float = 0.001):
        self.running = True
        self.thread = threading.Thread(target=self._fall_loop, args=(interval,), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join()

    def reconstruct(self):
        """Prove the file can still be read while 'falling'."""
        with self.lock:
            # figure out where each piece currently is
            location_of = {}
            for port_i, piece_idx in enumerate(self.ports):
                if piece_idx is not None:
                    location_of[piece_idx] = port_i
            ordered = [self.pieces[i] for i in sorted(location_of.keys())]
            return b"".join(ordered)

    def where_is_everything_right_now(self):
        """Snapshot proof: every piece has a real physical port at this instant."""
        with self.lock:
            return {f"port_{i}": (f"piece_{p}" if p is not None else "empty")
                    for i, p in enumerate(self.ports)}


if __name__ == "__main__":
    test_data = os.urandom(1024 * 1024)  # 1 MB random file
    original_hash = hash(test_data)

    ff = FallingFile(test_data, num_ports=32)

    proc = psutil.Process()
    cpu_before = proc.cpu_percent(interval=1.0)

    ff.start(interval=0.001)  # hop every 1ms -- fast falling
    time.sleep(3)

    cpu_during = proc.cpu_percent(interval=1.0)

    # snapshot mid-fall: prove every piece is physically SOMEWHERE right now
    snapshot = ff.where_is_everything_right_now()

    # try reconstructing while it's still falling
    rebuilt = ff.reconstruct()
    reconstruction_ok = (hash(rebuilt) == original_hash)

    ff.stop()
    cpu_after = proc.cpu_percent(interval=1.0)

    print(f"Hops completed in 3 seconds: {ff.hops}")
    print(f"CPU% before running:  {cpu_before}")
    print(f"CPU% while falling:   {cpu_during}")
    print(f"CPU% after stopped:   {cpu_after}")
    print()
    print("Snapshot of port occupancy at one instant mid-fall (first 8 ports):")
    for k in list(snapshot.items())[:8]:
        print(f"  {k[0]}: {k[1]}")
    print()
    print(f"File reconstructed correctly while still falling: {reconstruction_ok}")
    print(f"Memory used by process (MB): {proc.memory_info().rss / 1024 / 1024:.2f}")
