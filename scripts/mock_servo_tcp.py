"""mock_servo_tcp.py - byte-echo TCP server for TcpTransport bench tests.

Stands in for an ESP32-C6 servo controller on the wire while the
firmware port is being written. Listens on the configured port,
accepts one client at a time, echoes every byte back. The byte echo
is enough to validate the Python transport layer (TcpTransport,
``_build_servo`` URL parsing, runtime_config plumbing) end-to-end on
a laptop with zero hardware.

Does NOT decode COBS frames or speak the servo protocol -- if a real
``ServoDriver`` connects, every request will be answered with the
request's own bytes, which the driver will reject as a CRC mismatch.
That is fine: the goal here is to prove the byte pipe, not to fake
the firmware.

For protocol-level smoke (CMD_GET_CALIBRATION etc.), wait for the
ESP32-C6 firmware port (Phase 2).

CLI::

    uv run python scripts/mock_servo_tcp.py --port 5555
    uv run python scripts/mock_servo_tcp.py --bind 0.0.0.0 --port 5556
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys

_LOG = logging.getLogger("mock_servo_tcp")


def _serve_forever(host: str, port: int) -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, port))
    except OSError as exc:
        _LOG.error("bind %s:%d failed: %s", host, port, exc)
        return 2
    server.listen(1)
    _LOG.info("mock_servo_tcp listening on %s:%d (one client at a time)", host, port)
    try:
        while True:
            try:
                client, addr = server.accept()
            except KeyboardInterrupt:
                _LOG.info("stop on Ctrl-C")
                return 0
            _LOG.info("client %s connected", addr)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                while True:
                    data = client.recv(4096)
                    if not data:
                        _LOG.info("client %s closed", addr)
                        break
                    _LOG.debug("rx %d bytes: %s", len(data), data.hex())
                    client.sendall(data)
            except (ConnectionResetError, BrokenPipeError) as exc:
                _LOG.info("client %s dropped: %s", addr, exc)
            finally:
                client.close()
    finally:
        server.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mock_servo_tcp", description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5555, help="TCP port (default 5555)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")
    return _serve_forever(args.bind, args.port)


if __name__ == "__main__":
    sys.exit(main())
