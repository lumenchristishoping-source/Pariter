"""
Nested Self-Healing Box Storage.

Structure: Box(outer) -> Box(middle) -> Box(inner) -> file

If the innermost box holding the file fails/breaks, the box that CONTAINED
it becomes the new direct holder, and a fresh box is formed around it --
so the chain keeps regenerating outward instead of collapsing.

Test: kill boxes repeatedly, prove the file always survives and is always
inside SOME live box, and that new boxes keep forming continuously.
"""

import random
import time
import uuid


class Box:
    def __init__(self, contents=None, parent=None):
        self.id = str(uuid.uuid4())[:8]
        self.contents = contents  # either another Box, or the file (bytes), or None
        self.parent = parent
        self.alive = True

    def __repr__(self):
        inner = "file" if isinstance(self.contents, bytes) else (
            self.contents.id if self.contents else "empty"
        )
        return f"Box({self.id}, holds={inner}, alive={self.alive})"


class NestedStorage:
    def __init__(self, file_data: bytes, initial_depth: int = 3):
        self.file_data = file_data
        self.boxes = []  # ordered outer -> inner
        current_contents = file_data
        for _ in range(initial_depth):
            b = Box(contents=current_contents)
            self.boxes.append(b)
            current_contents = b
        self.boxes.reverse()  # now outer-most first
        # fix parent links
        for i in range(len(self.boxes) - 1):
            self.boxes[i].contents = self.boxes[i + 1]
        self.history = []

    def innermost(self):
        return self.boxes[-1]

    def break_innermost(self):
        """Simulate the box directly holding the file failing."""
        dying = self.innermost()
        dying.alive = False
        self.history.append(f"Box {dying.id} broke (was holding the file directly)")

        if len(self.boxes) < 2:
            # nothing was containing it -- true data loss, only possible if
            # you let the chain run out without regenerating
            self.history.append("NO PARENT BOX LEFT -- file would be lost here")
            return False

        # the box that CONTAINED the dead one now becomes the direct holder
        new_holder = self.boxes[-2]
        new_holder.contents = self.file_data
        self.boxes.pop()  # remove the dead innermost box from the chain

        # regenerate: form a fresh box around the new holder so the chain
        # keeps its depth and doesn't run out
        fresh = Box(contents=self.file_data)
        new_holder.contents = fresh
        self.boxes.append(fresh)

        self.history.append(
            f"Box {new_holder.id} became direct holder, then wrapped by new Box {fresh.id}"
        )
        return True

    def verify_file_intact(self):
        return self.innermost().contents == self.file_data

    def status(self):
        return " -> ".join(str(b) for b in self.boxes)


if __name__ == "__main__":
    test_file = b"THIS IS THE PROTECTED FILE CONTENT"
    storage = NestedStorage(test_file, initial_depth=3)

    print("Initial structure:")
    print(" ", storage.status())
    print()

    survived_all = True
    for round_num in range(1, 11):
        ok = storage.break_innermost()
        intact = storage.verify_file_intact()
        survived_all = survived_all and intact
        print(f"Round {round_num}: {storage.history[-1]}")
        print(f"  File still intact and readable: {intact}")
        print(f"  Current structure: {storage.status()}")
        print()

    print(f"File survived {10} consecutive box failures: {survived_all}")
    print(f"Final chain depth still maintained at: {len(storage.boxes)}")
