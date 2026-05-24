"""Unit tests for :class:`TcpTransport`.

In-process TCP echo server (one thread, one client). Exercises:
  - connect / write / read round-trip
  - short read on timeout (B3: empty bytes, not exception)
  - reset_input_buffer drains pending bytes
  - close is idempotent
  - bad URL raises ValueError; connect-refused raises ConnectionError
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest
from rfmesh_servo.transport import TcpTransport


def _start_echo_server() -> tuple[socket.socket, int, threading.Thread]:
    """Bind 127.0.0.1:<ephemeral>, return (server_sock, port, accept_thread).

    Thread accepts ONE client + echoes everything until the client
    closes. Caller is responsible for closing the server socket.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def _serve() -> None:
        try:
            client, _ = server.accept()
        except OSError:
            return
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                data = client.recv(4096)
                if not data:
                    return
                client.sendall(data)
        except OSError:
            return
        finally:
            with contextlib.suppress(OSError):
                client.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return server, port, t


def test_tcp_transport_round_trip() -> None:
    server, port, _ = _start_echo_server()
    try:
        xport = TcpTransport(host="127.0.0.1", port=port, connect_timeout_s=2.0)
        try:
            xport.write(b"\x01\x02\x03\x04")
            # Give the echo loop a tick; localhost is fast but not instant.
            time.sleep(0.05)
            got = xport.read(n=64, timeout_s=1.0)
            assert got == b"\x01\x02\x03\x04"
        finally:
            xport.close()
    finally:
        server.close()


def test_tcp_transport_short_read_on_timeout() -> None:
    server, port, _ = _start_echo_server()
    try:
        xport = TcpTransport(host="127.0.0.1", port=port, connect_timeout_s=2.0)
        try:
            # Nothing in flight; read must return b"" on timeout (B3:
            # honest empty, not an exception).
            got = xport.read(n=16, timeout_s=0.05)
            assert got == b""
        finally:
            xport.close()
    finally:
        server.close()


def test_tcp_transport_reset_input_buffer() -> None:
    server, port, _ = _start_echo_server()
    try:
        xport = TcpTransport(host="127.0.0.1", port=port, connect_timeout_s=2.0)
        try:
            xport.write(b"stale-bytes-to-discard")
            time.sleep(0.1)  # let the echo arrive on our side
            xport.reset_input_buffer()
            # Any further read with a brief timeout should now see nothing.
            got = xport.read(n=64, timeout_s=0.05)
            assert got == b""
        finally:
            xport.close()
    finally:
        server.close()


def test_tcp_transport_close_is_idempotent() -> None:
    server, port, _ = _start_echo_server()
    try:
        xport = TcpTransport(host="127.0.0.1", port=port, connect_timeout_s=2.0)
        xport.close()
        # Second close must not raise.
        xport.close()
    finally:
        server.close()


def test_tcp_transport_rejects_empty_host() -> None:
    with pytest.raises(ValueError, match="host"):
        TcpTransport(host="", port=5555)


@pytest.mark.parametrize("bad_port", [0, -1, 65536, 999999])
def test_tcp_transport_rejects_bad_port(bad_port: int) -> None:
    with pytest.raises(ValueError, match="port"):
        TcpTransport(host="127.0.0.1", port=bad_port)


def test_tcp_transport_connect_refused() -> None:
    # Bind a server socket then close it: the OS frees the port and
    # the next connect attempt is refused immediately. Far more reliable
    # than picking a "probably-free" hard-coded port.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(ConnectionError, match="cannot connect"):
        TcpTransport(host="127.0.0.1", port=free_port, connect_timeout_s=0.5)
