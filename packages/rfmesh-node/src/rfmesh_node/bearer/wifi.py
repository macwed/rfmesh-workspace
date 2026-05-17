"""``WifiBearer`` -- UDP transport for ``BearingReport`` / ``NodeStatus``.

Synchronous ``Bearer`` Protocol conformer (the contract is sync, see
INTERFACES.md §5 and ``rfmesh_contracts.protocols.Bearer``). Internally
uses a non-blocking ``socket.socket`` -- UDP ``sendto`` is non-blocking
from the kernel's perspective once the per-socket send buffer is sized
appropriately, and ``recvfrom`` is polled non-blockingly inside
``receive`` to drain whatever has arrived since the last call.

Salvaged transport pattern from the old repo (``SALVAGE_AUDIT.md``
Part 5): length-prefixed msgpack envelope, UDP datagrams. Best-effort
delivery is the right semantic for live mesh traffic -- fusion's batch
window already tolerates dropped packets; retransmits would introduce
head-of-line blocking that hurts the live demo more than a dropped
report would.

The fusion-side bearer binds its local port and reads; the node-side
bearer connects (in the kernel sense -- UDP connect just fixes the
peer) and writes.
"""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from rfmesh_contracts import BearingReport, NodeStatus

from .envelope import EnvelopeDecodeError, decode_envelope, encode_envelope

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG = logging.getLogger(__name__)

# Max UDP datagram size we will attempt to receive. The envelope cap is
# 64 KiB; UDP's theoretical max is 65507; we use 65536 to give the call
# enough room without ever truncating (we let MAX_MESSAGE_BYTES gate the
# logical size at encode time).
_RECV_BUFFER_BYTES: int = 65_536


class WifiBearer:
    """UDP + msgpack bearer (synchronous ``Bearer`` Protocol conformer)."""

    def __init__(self, endpoint_url: str, *, bind: bool = False) -> None:
        """Configure the bearer.

        Args:
            endpoint_url: ``udp://host:port``.
            bind: True if this is the receiving end (fusion server /
                dashboard side -- binds the configured host:port).
                False if this is the node-side bearer (uses
                ``socket.connect`` so subsequent ``sendto`` calls
                target the peer; binds an ephemeral local port).

        The socket is created eagerly in ``__init__`` so ``send_*`` can
        fire-and-forget without a separate ``start`` step.
        """
        parsed = urlparse(endpoint_url)
        if parsed.scheme != "udp":
            msg = f"WifiBearer: endpoint_url must be udp://host:port (got {endpoint_url!r})."
            raise ValueError(msg)
        if parsed.hostname is None or parsed.port is None:
            msg = f"WifiBearer: endpoint_url must specify host and port (got {endpoint_url!r})."
            raise ValueError(msg)
        self._host: str = parsed.hostname
        self._port: int = int(parsed.port)
        self._bind: bool = bind
        self._sock: socket.socket | None = None
        self._open()

    def _open(self) -> None:
        """Create the UDP socket + (optionally) bind / connect."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            if self._bind:
                sock.bind((self._host, self._port))
            else:
                # On the node side, connect() fixes the peer so we can
                # use send() without per-call destination, and the
                # kernel auto-picks an ephemeral local port.
                sock.connect((self._host, self._port))
        except OSError:
            sock.close()
            raise
        self._sock = sock

    @property
    def local_address(self) -> tuple[str, int]:
        """The actual bound local address (host, port).

        Useful for tests that ``bind`` to ``port=0`` (kernel-picked
        ephemeral port).
        """
        if self._sock is None:
            msg = "WifiBearer.local_address: socket is closed."
            raise RuntimeError(msg)
        host, port = self._sock.getsockname()
        return (str(host), int(port))

    def send_bearing(self, report: BearingReport) -> None:
        """Transmit one ``BearingReport``."""
        envelope = encode_envelope(report)
        self._send_bytes(envelope)

    def send_status(self, status: NodeStatus) -> None:
        """Transmit one ``NodeStatus`` heartbeat."""
        envelope = encode_envelope(status)
        self._send_bytes(envelope)

    def _send_bytes(self, data: bytes) -> None:
        if self._sock is None:
            msg = "WifiBearer.send: socket is closed."
            raise RuntimeError(msg)
        if self._bind:
            self._sock.sendto(data, (self._host, self._port))
        else:
            self._sock.send(data)

    def receive(self) -> Sequence[BearingReport | NodeStatus]:
        """Drain whatever has arrived since the last ``receive``."""
        if self._sock is None:
            return ()
        out: list[BearingReport | NodeStatus] = []
        while True:
            try:
                data, _addr = self._sock.recvfrom(_RECV_BUFFER_BYTES)
            except BlockingIOError:
                break
            try:
                message = decode_envelope(data)
            except EnvelopeDecodeError as exc:
                _LOG.warning("WifiBearer: dropped malformed datagram: %s", exc)
                continue
            if isinstance(message, BearingReport | NodeStatus):
                out.append(message)
            else:
                _LOG.warning(
                    "WifiBearer: dropped unexpected envelope type %s",
                    type(message).__name__,
                )
        return tuple(out)

    def close(self) -> None:
        """Release the UDP socket. Safe to call multiple times."""
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


__all__ = ["WifiBearer"]
