"""HANDBOOK.md section 14's product layer: store()/retrieve() the way an
application would call Redis or Vault, plus what the handbook names as
missing - auth, replication, key rotation. This is explicitly "real
engineering work but not the difficult part" - the hard part (the
mechanism itself) is `system.py`; this is the surface around it.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Dict

from .system import VirtualStorage


class AuthError(Exception):
    pass


@dataclass
class _Entry:
    file_id: str
    owner_token_hash: str
    created_at: float


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SecretsStore:
    """store(key, value, token) / retrieve(key, token) - the same shape as
    a Redis or Vault call. Every secret is owned by whichever token stored
    it; only that token (or one presented via `share`) can retrieve it.
    """

    def __init__(self):
        self._vs = VirtualStorage()
        self._entries: Dict[str, _Entry] = {}
        self._shared_with: Dict[str, set] = {}  # key -> set of token hashes

    def store(self, key: str, value: bytes, token: str) -> None:
        file_id = self._vs.save_bytes(value, name_hint=key)
        self._entries[key] = _Entry(
            file_id=file_id,
            owner_token_hash=_hash_token(token),
            created_at=time.time(),
        )
        self._shared_with[key] = set()

    def retrieve(self, key: str, token: str) -> bytes:
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(key)
        token_hash = _hash_token(token)
        allowed = token_hash == entry.owner_token_hash or token_hash in self._shared_with[key]
        if not allowed:
            raise AuthError(f"token not authorized for key={key!r}")
        return self._vs.retrieve(entry.file_id, "full")

    def share(self, key: str, owner_token: str, grantee_token: str) -> None:
        """Owner grants another token read access, without ever exposing
        the secret itself in the grant call."""
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(key)
        if _hash_token(owner_token) != entry.owner_token_hash:
            raise AuthError("only the owner can share a key")
        self._shared_with[key].add(_hash_token(grantee_token))

    def rotate(self, key: str, new_value: bytes, token: str) -> None:
        """Replace a secret's value without downtime - old value collapses
        the instant the new one is held; no window where neither exists.
        """
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(key)
        if _hash_token(token) != entry.owner_token_hash:
            raise AuthError("only the owner can rotate a key")

        new_file_id = self._vs.save_bytes(new_value, name_hint=key)
        old_file_id = entry.file_id
        entry.file_id = new_file_id
        self._vs.forget(old_file_id)

    def revoke(self, key: str, token: str) -> None:
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(key)
        if _hash_token(token) != entry.owner_token_hash:
            raise AuthError("only the owner can revoke a key")
        self._vs.forget(entry.file_id)
        del self._entries[key]
        del self._shared_with[key]

    def keys_for(self, token: str) -> list[str]:
        token_hash = _hash_token(token)
        return [k for k, e in self._entries.items()
                if e.owner_token_hash == token_hash or token_hash in self._shared_with[k]]


class ReplicatedSecretsStore:
    """N independent SecretsStore instances, each holding the same
    secrets falling in parallel (HANDBOOK.md section 14, point 3: "run
    two instances... so a single process crash doesn't lose everything").
    A write goes to every replica; a read is served by the first replica
    still alive.
    """

    def __init__(self, replica_count: int = 2):
        self._replicas = [SecretsStore() for _ in range(replica_count)]
        self._alive = [True] * replica_count

    def store(self, key: str, value: bytes, token: str) -> None:
        for i, replica in enumerate(self._replicas):
            if self._alive[i]:
                replica.store(key, value, token)

    def retrieve(self, key: str, token: str) -> bytes:
        for i, replica in enumerate(self._replicas):
            if self._alive[i]:
                try:
                    return replica.retrieve(key, token)
                except KeyError:
                    continue
        raise KeyError(f"{key!r} not found on any surviving replica")

    def kill_replica(self, index: int) -> None:
        """Simulates a process crash - that replica's data collapses
        completely (matches HANDBOOK.md section 4, Step 5: Death)."""
        self._alive[index] = False
        self._replicas[index] = None  # drop the reference; nothing recoverable

    def alive_count(self) -> int:
        return sum(self._alive)
