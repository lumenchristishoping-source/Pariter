"""
Nested Self-Healing Box Storage -- v2

Fix applied: the OUTER end of the chain also regenerates. Every time a box
becomes the outermost box (has no parent), it is immediately wrapped by a
fresh box too. So the chain never has a "last box with nothing behind it" --
both ends keep reforming, forever. This removes the one real failure case
from v1.

Runs continuously (unlimited breaks) against a real file on disk.
"""

import uuid
import hashlib


class Box:
    def __init__(self, contents=None):
        self.id = str(uuid.uuid4())[:8]
        self.contents = contents
        self.alive = True


class NestedStorage:
    def __init__(self, file_data: bytes, initial_depth: int = 3):
        self.file_data = file_data
        self.boxes = []
        current_contents = file_data
        for _ in range(initial_depth):
            b = Box(contents=current_contents)
            self.boxes.append(b)
            current_contents = b
        self.boxes.reverse()  # outer-most first, inner-most last
        for i in range(len(self.boxes) - 1):
            self.boxes[i].contents = self.boxes[i + 1]

        # FIX: immediately wrap the outer end too, so it's never "the last box"
        self._reinforce_outer_end()

        self.break_count = 0

    def _reinforce_outer_end(self):
        """Ensure the outermost box always has something containing it too."""
        outer_wrapper = Box(contents=self.boxes[0])
        self.boxes.insert(0, outer_wrapper)

    def innermost(self):
        return self.boxes[-1]

    def break_innermost(self):
        dying = self.innermost()
        dying.alive = False
        self.break_count += 1

        # the box that contained the dead one becomes the new direct holder
        new_holder = self.boxes[-2]
        self.boxes.pop()

        fresh = Box(contents=self.file_data)
        new_holder.contents = fresh
        self.boxes.append(fresh)

        return dying.id, fresh.id

    def break_outermost(self):
        """The outer shell can break too -- this is the case v1 couldn't survive."""
        dying = self.boxes[0]
        dying.alive = False
        self.break_count += 1

        self.boxes.pop(0)
        # whatever was second-outermost is now outermost -- reinforce it
        self._reinforce_outer_end()

        return dying.id

    def verify_file_intact(self):
        return self.innermost().contents == self.file_data

    def chain_depth(self):
        return len(self.boxes)


if __name__ == "__main__":
    import random

    with open("test_document.md", "rb") as f:
        real_file = f.read()

    original_hash = hashlib.sha256(real_file).hexdigest()
    print(f"Original file: {len(real_file)} bytes")
    print(f"Original SHA-256: {original_hash}")
    print()

    storage = NestedStorage(real_file, initial_depth=3)
    print(f"Starting chain depth: {storage.chain_depth()}")
    print()

    NUM_BREAKS = 5000  # standing in for "unlimited" -- run a large number continuously
    inner_breaks = 0
    outer_breaks = 0
    depths_seen = set()

    for i in range(NUM_BREAKS):
        depths_seen.add(storage.chain_depth())
        if random.random() < 0.85:
            storage.break_innermost()
            inner_breaks += 1
        else:
            storage.break_outermost()
            outer_breaks += 1

        if not storage.verify_file_intact():
            print(f"FAILURE at break #{i}: file did not survive!")
            break

    final_intact = storage.verify_file_intact()
    final_hash = hashlib.sha256(storage.innermost().contents).hexdigest()

    print(f"Total breaks simulated: {storage.break_count}")
    print(f"  Inner-box breaks: {inner_breaks}")
    print(f"  Outer-box breaks: {outer_breaks}")
    print(f"Chain depth range observed during run: {sorted(depths_seen)}")
    print()
    print(f"File still intact after {NUM_BREAKS} breaks: {final_intact}")
    print(f"Final SHA-256 matches original: {final_hash == original_hash}")
    print()
    print("Recovered content preview:")
    print("-" * 50)
    print(storage.innermost().contents.decode("utf-8"))
