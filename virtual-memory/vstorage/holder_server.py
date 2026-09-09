"""The real-network counterpart to distributed_key.py's _holder_process.

That version "simulates N separately-trusted machines" with local OS
processes talking over multiprocessing Pipes - honest about the fact
that a single root on the local kernel could, in principle, reach all
of them at once, because they all live under the same kernel. This
version is a genuine standalone network service: TLS-authenticated,
reachable only over a real TCP socket, no shared kernel assumption
required. Point it at a different host and it works unchanged - what
makes today's test "only" localhost is the sandbox, not the protocol.

Run as: python3 -m vstorage.holder_server
Configuration arrives over stdin (never argv, which is visible to any
local user via `ps`) as one line of JSON: token, cert_pem, key_pem,
x, share_hex, host, port (0 = let the OS pick a free port). Prints
{"ready": true, "pid": ..., "port": ...} once listening, then serves
"get_share" and "ping" requests to whoever presents the right token
over the TLS connection, forever, until stop or killed.

Self-protects exactly like the local-mode holder wants to but can't:
a plain `python3 -m ...` process is NOT a multiprocessing daemon
child, so (unlike distributed_key.py's holder) it's free to spawn its
own ProcessWatchdog directly, no parent-side workaround needed.
"""

from __future__ import annotations

import ctypes
import hmac
import json
import mmap
import os
import signal
import socket
import ssl
import sys
import tempfile

from .distributed_key import _lock_and_hide
from .process_watchdog import ProcessWatchdog


def _write_cert_files(cert_pem: str, key_pem: str) -> tuple[str, str, str]:
    """ssl.SSLContext.load_cert_chain needs real file paths - there is
    no in-memory-PEM API for the server side in the stdlib. Written to
    a private (0700) temp dir, read back immediately, then deleted -
    they exist on disk only for the instant loading requires, same
    discipline as everything else in this project."""
    tmpdir = tempfile.mkdtemp(prefix="vstorage_holder_tls_")
    os.chmod(tmpdir, 0o700)
    certfile = os.path.join(tmpdir, "cert.pem")
    keyfile = os.path.join(tmpdir, "key.pem")
    fd = os.open(certfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(cert_pem)
    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key_pem)
    return tmpdir, certfile, keyfile


def _handle_connection(conn: ssl.SSLSocket, token: str, x: int, share_hex: str) -> None:
    f = conn.makefile("rwb")
    try:
        while True:
            line = f.readline()
            if not line:
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                return
            if not hmac.compare_digest(str(msg.get("token", "")), token):
                return  # wrong token - close, no error detail leaked
            op = msg.get("op")
            if op == "get_share":
                resp = {"x": x, "share_hex": share_hex}
            elif op == "ping":
                resp = {"pong": True}
            else:
                return
            f.write((json.dumps(resp) + "\n").encode())
            f.flush()
    except (OSError, ValueError):
        return
    finally:
        f.close()


def main() -> None:
    config = json.loads(sys.stdin.readline())
    token = config["token"]
    cert_pem = config["cert_pem"]
    key_pem = config["key_pem"]
    x = int(config["x"])
    share_bytes = bytes.fromhex(config["share_hex"])
    host = config["host"]
    port = int(config.get("port", 0))

    # same protection as the local-mode holder: lock the share into a
    # non-swappable, core-dump-excluded page. share_hex below is a
    # separate plain-Python copy kept for the life of this process to
    # answer requests cheaply - the SAME honest gap the local-mode
    # holder already has (see distributed_key.py), not a regression.
    buf = mmap.mmap(-1, mmap.PAGESIZE, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    buf[:len(share_bytes)] = share_bytes
    _lock_and_hide(buf)
    share_hex = bytes(buf[:len(share_bytes)]).hex()

    watchdog = ProcessWatchdog(target_pid=os.getpid(), kill_target=True)
    addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    watchdog.add_region(addr, len(share_bytes))

    # A clean shutdown (NetworkTrustGroup.stop()) sends this process
    # SIGTERM. Watchdog's own child (the actual watcher process) is
    # OUR child, spawned via multiprocessing with daemon=True - but
    # daemon=True only cleans up on a NORMAL interpreter exit
    # (atexit), which a raw SIGTERM does NOT trigger. Without this
    # handler the watchdog process is orphaned every time a group is
    # stopped cleanly - hit this directly, watched it happen. Not an
    # issue on the ATTACK path: there, the watchdog kills US, and its
    # own function then returns and it exits normally on its own.
    def _on_term(_signum, _frame) -> None:
        watchdog.stop()
        os._exit(0)

    signal.signal(signal.SIGTERM, _on_term)

    tmpdir, certfile, keyfile = _write_cert_files(cert_pem, key_pem)
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(certfile, keyfile)
    os.unlink(certfile)
    os.unlink(keyfile)
    os.rmdir(tmpdir)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    bound_port = listener.getsockname()[1]
    ssl_listener = ssl_ctx.wrap_socket(listener, server_side=True)

    print(json.dumps({"ready": True, "pid": os.getpid(), "port": bound_port}), flush=True)

    while True:
        try:
            conn, _addr = ssl_listener.accept()
        except (OSError, ssl.SSLError):
            return
        try:
            _handle_connection(conn, token, x, share_hex)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
