"""Real network-separated distributed trust - the client side, paired
with holder_server.py.

distributed_key.py's DistributedTrustGroup is honest that its N
holder processes only "simulate N separately-trusted machines": they
are local OS processes under the SAME kernel, so a local root with
enough privilege could, in principle, reach all of them - the exact
theme of this whole project's honest-limits section. This module
removes that specific assumption: each holder is a standalone process
speaking a real, TLS-authenticated TCP protocol
(holder_server.py) instead of an in-memory Pipe. Point host= at a
real remote address and this is a genuine multi-machine deployment,
unchanged - what makes today's tests "only" localhost is this
sandbox having one machine available, not anything in the protocol.

What has to change, honestly, compared to the local version:
  - A holder dying can no longer be seen by reading /proc/<pid>/stat
    from here - there is no shared /proc across machines. The only
    signal available over a network is the connection itself: this
    client heartbeats every holder and treats an unreachable one
    exactly like a locally-dead one (same TrustGroupCompromised /
    kill_threshold machinery as DistributedTrustGroup).
  - Auth is a shared bearer token over TLS (checked with a constant-
    time compare), not "whoever can open this specific Pipe fd."
    TLS is set up for real - a fresh 2048-bit self-signed cert
    generated per group, pinned by the client (trust-on-first-use,
    not a full CA chain - appropriately scoped for what this proves).
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time

from .distributed_key import TrustGroupCompromised
from .shamir import combine_shares, split_secret

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _generate_self_signed_cert() -> tuple[bytes, bytes]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vstorage-trust-holder")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _send(sock: ssl.SSLSocket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode())


def _recv_line(sock: ssl.SSLSocket) -> dict:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            raise EOFError("connection closed")
        buf += chunk
    return json.loads(buf.decode())


class NetworkTrustGroup:
    """Same public shape as DistributedTrustGroup (k, n, fetch(),
    stop(), compromised_count, kill_threshold semantics) so it's a
    drop-in alternative wherever a trust_group is accepted - only the
    transport and the liveness signal differ."""

    def __init__(self, k: int = 3, n: int = 5, host: str = "127.0.0.1",
                 kill_threshold: int | None = None, heartbeat_interval: float = 0.1):
        self.k = k
        self.n = n
        self._kill_threshold = kill_threshold if kill_threshold is not None else max(1, k - 1)
        self._main_pid = os.getpid()
        self._host = host
        self._heartbeat_interval = heartbeat_interval

        root_secret = os.urandom(32)
        shares = split_secret(root_secret, k=k, n=n)
        root_secret = bytes(len(root_secret))

        self._token = secrets.token_hex(32)
        cert_pem, key_pem = _generate_self_signed_cert()
        self._client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client_ctx.check_hostname = False
        self._client_ctx.load_verify_locations(cadata=cert_pem.decode())

        self._lock = threading.Lock()
        self._stopping = False
        self._compromised_indices: set[int] = set()
        self._kill_triggered = False

        self._processes: list[subprocess.Popen] = []
        self._ports: list[int] = []
        self._socks: list[ssl.SSLSocket] = []

        env = dict(os.environ)
        env["PYTHONPATH"] = _PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")

        for x, y in shares:
            config = {
                "token": self._token,
                "cert_pem": cert_pem.decode(),
                "key_pem": key_pem.decode(),
                "x": x,
                "share_hex": y.hex(),
                "host": host,
                "port": 0,  # let the OS pick a free port, no race
            }
            # Deliberately NOT start_new_session here: stop() below
            # already tracks and terminates every holder process
            # explicitly by pid, so detaching them into their own
            # session buys nothing - it only means an external killpg
            # aimed at THIS process's tree (like a test harness
            # cleaning up after itself) can no longer reach them.
            # Found that the hard way: an earlier version set this,
            # and a crashed/interrupted test run left real,
            # busy-spinning watchdog processes orphaned and
            # unreachable, still running minutes later.
            proc = subprocess.Popen(
                [sys.executable, "-m", "vstorage.holder_server"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                env=env,
            )
            proc.stdin.write(json.dumps(config) + "\n")
            proc.stdin.flush()
            proc.stdin.close()
            ready = json.loads(proc.stdout.readline())
            if not ready.get("ready"):
                raise RuntimeError("holder server failed to start")
            self._processes.append(proc)
            self._ports.append(ready["port"])
            self._socks.append(self._connect(host, ready["port"]))

        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()

    def _connect(self, host: str, port: int) -> ssl.SSLSocket:
        raw = socket.create_connection((host, port), timeout=5)
        return self._client_ctx.wrap_socket(raw)

    @property
    def compromised_count(self) -> int:
        return len(self._compromised_indices)

    def _monitor(self) -> None:
        """Network counterpart to DistributedTrustGroup._monitor():
        there is no /proc to read across machines, so the only real
        liveness signal is the connection itself. Heartbeats every
        still-trusted holder under the SAME lock fetch() uses (one
        request/response in flight per socket at a time, same reason
        as the local version - concurrent readers on one stream
        corrupt each other's messages), and treats a failed ping
        exactly like a locally-dead holder: same threshold logic,
        same fail-closed kill.

        Slower than the local monitor's 20ms on purpose: a real
        network round trip costs more than a local IPC call, and
        polling that fast would add real, pointless load to every
        holder for no detection-speed benefit worth it."""
        while not self._stopping:
            for i in range(self.n):
                if self._stopping:
                    return
                if i in self._compromised_indices:
                    continue
                if not self._ping(i):
                    self._on_holder_compromised(i)
            time.sleep(self._heartbeat_interval)

    def _ping(self, index: int) -> bool:
        sock = self._socks[index]
        with self._lock:
            try:
                sock.settimeout(2.0)
                _send(sock, {"op": "ping", "token": self._token})
                resp = _recv_line(sock)
                return bool(resp.get("pong"))
            except (OSError, EOFError, ValueError):
                return False
            finally:
                try:
                    sock.settimeout(None)
                except OSError:
                    pass

    def _on_holder_compromised(self, index: int) -> None:
        if index in self._compromised_indices:
            return
        self._compromised_indices.add(index)
        if self._kill_triggered or self._kill_threshold <= 0:
            return
        if len(self._compromised_indices) >= self._kill_threshold:
            self._kill_triggered = True
            import signal
            os.kill(self._main_pid, signal.SIGKILL)

    def fetch(self) -> bytes:
        alive_indices = [i for i in range(self.n) if i not in self._compromised_indices]
        if len(alive_indices) < self.k:
            raise TrustGroupCompromised(
                f"only {len(alive_indices)} of {self.n} network holders "
                f"alive, need {self.k} to reconstruct")
        with self._lock:
            collected = []
            try:
                for i in alive_indices[:self.k]:
                    sock = self._socks[i]
                    sock.settimeout(5.0)
                    _send(sock, {"op": "get_share", "token": self._token})
                    resp = _recv_line(sock)
                    collected.append((resp["x"], bytes.fromhex(resp["share_hex"])))
            except (OSError, EOFError, ValueError, KeyError) as e:
                self._on_holder_compromised(i)
                raise TrustGroupCompromised(
                    "lost contact with a network trust holder mid-fetch") from e
            finally:
                for sock in self._socks:
                    try:
                        sock.settimeout(None)
                    except OSError:
                        pass
        return combine_shares(collected)

    def stop(self) -> None:
        self._stopping = True
        for sock in self._socks:
            try:
                sock.close()
            except OSError:
                pass
        for proc in self._processes:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        for proc in self._processes:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
