"""``PyTAKCotPublisher`` -- the only network-touching class in this package.

Implements ``rfmesh_contracts.CotPublisher``. Wraps PyTAK's
``protocol_factory`` to obtain a TCP/UDP/TLS writer for the configured
TAK endpoint, then hands encoded CoT blobs to it.

DESIGN
------
PyTAK >= 6 ships a full ``CLITool`` framework with worker queues and
configparser-based bootstrap. We use only the small piece of it we need
-- ``pytak.protocol_factory`` -- which returns an ``(reader, writer)``
pair (an asyncio StreamReader/StreamWriter for TCP, or an
asyncio-dgram-style writer for UDP). Our own publisher owns the writer,
the queue, and the TX task. Why this minimal slice instead of the full
``CLITool``:

* ``CLITool`` expects to be the long-running asyncio entry point; we
  are not -- the ``rfmesh-node`` service is. The publisher must
  cooperate with ``Node.run()`` as a child task.
* ``CLITool``'s queue worker swallows transport errors by design (it
  logs and reconnects). That violates Invariant B3 ("no silent
  fallbacks"); we want errors to surface as ``CotTransportError``.
* The full framework brings ``configparser`` and a TLS handshake
  surface we do not need for v1.0 (the trench-demo network is local;
  TLS is parked-lot in ops-architecture §6.4).

CONCURRENCY MODEL
-----------------
The publisher exposes ``publish(fix)`` as synchronous from the caller's
view (per the ``CotPublisher`` Protocol). Internally:

* On the first ``publish()``, the publisher starts a background asyncio
  task that drains a ``asyncio.Queue`` to the writer (TX loop).
* Each ``publish()`` enqueues the encoded blob; the TX loop awakes,
  pulls it, ``writer.write(blob)``, ``await writer.drain()``.
* A transport error in the TX loop is recorded; the next ``publish()``
  call raises ``CotTransportError`` with that record. The publisher
  does **not** auto-reconnect (that would be a silent fallback). The
  ops layer decides whether to construct a new publisher.

The ``async __aenter__`` / ``__aexit__`` are the recommended path for
test code and for the node runtime (both already have an event loop).
``publish()`` is also callable from sync code (it uses ``asyncio.run``
on a private loop if no running loop is found), but the async context
manager is preferred -- it is faster, and it surfaces errors without
the overhead of spinning up loops per call.

ENDPOINT URL
------------
The PyTAK-style ``scheme://host:port`` URL, e.g. ``tcp://10.0.0.2:8087``
for a FreeTAKServer, ``udp://224.10.10.1:6969`` for a multicast group,
``tls://...`` for an encrypted TLS link. PyTAK parses this; we just
pass it through.
"""

from __future__ import annotations

import asyncio
import configparser
import contextlib
import logging
from types import TracebackType
from typing import Any

# pytak ships no type stubs; suppress the import-untyped diagnostic.
import pytak  # type: ignore[import-untyped]
from rfmesh_contracts import FixEvent, NodeStatus

from .exceptions import CotEncodingError, CotTransportError
from .markers import fix_event_to_cot_xml, node_status_to_cot_xml

_LOG = logging.getLogger(__name__)


class PyTAKCotPublisher:
    """Implements ``rfmesh_contracts.CotPublisher``.

    See module docstring for the design. Public surface mirrors the
    architect spec (``docs/design/ops-architecture.md`` §2.1):

    .. code-block:: python

        async with PyTAKCotPublisher("tcp://10.0.0.2:8087") as pub:
            pub.publish(fix_event)
            pub.publish_node_status(node_status)

    Parameters
    ----------
    endpoint_url:
        PyTAK-style URL of the TAK endpoint, e.g.
        ``tcp://freetakserver.lan:8087`` or
        ``udp://224.10.10.1:6969``. Passed straight to PyTAK.
    node_callsign:
        Prefix used in CoT marker UIDs and the ``<contact callsign=>``
        attribute. Default ``"rfmesh"``.
    pytak_config:
        Optional extra key/value pairs forwarded into PyTAK's
        ``configparser`` section. Used for TLS material in real
        deployments; ``None`` (the default) is fine for plain TCP/UDP.
    """

    def __init__(
        self,
        endpoint_url: str,
        node_callsign: str = "rfmesh",
        pytak_config: dict[str, str] | None = None,
    ) -> None:
        if "://" not in endpoint_url:
            msg = (
                f"endpoint_url must be a PyTAK-style URL with scheme, "
                f"e.g. tcp://host:port; got {endpoint_url!r}"
            )
            raise ValueError(msg)
        self._endpoint_url = endpoint_url
        self._node_callsign = node_callsign
        self._pytak_config_extras: dict[str, str] = dict(pytak_config or {})

        # Lazily initialised on the first publish() call.
        self._writer: Any | None = None
        self._tx_queue: asyncio.Queue[bytes] | None = None
        self._tx_task: asyncio.Task[None] | None = None
        self._closing_task: asyncio.Task[None] | None = None
        self._closed: bool = False
        # If the TX loop dies, the exception is parked here. The next
        # public call raises ``CotTransportError`` with this as cause.
        self._tx_error: BaseException | None = None

    # ----- async context manager --------------------------------------

    async def __aenter__(self) -> PyTAKCotPublisher:
        """Open the transport and start the TX loop."""
        await self._ensure_started()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Drain and close the transport. Idempotent."""
        await self._shutdown()

    # ----- public API per CotPublisher Protocol -----------------------

    def publish(self, fix: FixEvent) -> None:
        """Serialise ``fix`` to CoT and enqueue it for transmission.

        Synchronous from the caller's view; the actual ``writer.write``
        runs in the TX task. The encode happens on the calling thread
        (CPU-bound, sub-millisecond), so encode failures surface
        immediately as ``CotEncodingError``.
        """
        self._check_open()
        try:
            blob = fix_event_to_cot_xml(fix, callsign_prefix=self._node_callsign)
        except CotEncodingError:
            raise
        except Exception as exc:
            msg = f"Failed to encode FixEvent.fix_id={fix.fix_id}: {exc}"
            raise CotEncodingError(msg) from exc
        self._enqueue(blob)

    def publish_node_status(self, status: NodeStatus) -> None:
        """Serialise a ``NodeStatus`` heartbeat to CoT and enqueue it."""
        self._check_open()
        try:
            blob = node_status_to_cot_xml(status, callsign_prefix=self._node_callsign)
        except CotEncodingError:
            raise
        except Exception as exc:
            msg = f"Failed to encode NodeStatus.node_id={status.node_id}: {exc}"
            raise CotEncodingError(msg) from exc
        self._enqueue(blob)

    def close(self) -> None:
        """Tear down the transport. Idempotent; safe in cleanup paths.

        From sync code: this spins a private event loop if needed. From
        async code, prefer ``await __aexit__``.
        """
        if self._closed:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop -- create one to run the shutdown coroutine.
            asyncio.run(self._shutdown())
            return
        # A loop is running; schedule the shutdown as a task. Caller is
        # responsible for awaiting it via __aexit__ if they want a
        # definitive close. We do best-effort here. The task reference is
        # parked on the instance to satisfy RUF006 (so it is not GC'd
        # before completion) and to allow re-entrant ``close()`` paths
        # to observe an in-flight shutdown.
        loop = asyncio.get_running_loop()
        self._closing_task = loop.create_task(self._shutdown())

    # ----- internal helpers -------------------------------------------

    def _check_open(self) -> None:
        """Raise ``CotTransportError`` if the publisher is closed or broken."""
        if self._closed:
            raise CotTransportError("PyTAKCotPublisher is closed")
        if self._tx_error is not None:
            raise CotTransportError(
                f"PyTAKCotPublisher transport failed: {self._tx_error}"
            ) from self._tx_error

    def _enqueue(self, blob: bytes) -> None:
        """Place ``blob`` on the TX queue, starting the TX loop if needed."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -- use asyncio.run to perform a one-shot
            # send. Slower than the async path; meant for occasional
            # sync callers (test scaffolds, one-off scripts).
            asyncio.run(self._one_shot_send(blob))
            return
        # A loop is running. Make sure the writer is up, then push.
        if self._tx_queue is None or self._tx_task is None or self._writer is None:
            # First call in this loop -- bootstrap.
            loop.run_until_complete(self._ensure_started())  # pragma: no cover
        assert self._tx_queue is not None
        self._tx_queue.put_nowait(blob)

    async def _ensure_started(self) -> None:
        """Open the writer and start the TX task. Idempotent."""
        if self._closed:
            raise CotTransportError("PyTAKCotPublisher is closed")
        if self._writer is not None:
            return
        config = self._build_pytak_config()
        try:
            result = await pytak.protocol_factory(config)
        except Exception as exc:
            msg = f"PyTAK could not open {self._endpoint_url}: {type(exc).__name__}: {exc}"
            raise CotTransportError(msg) from exc
        # protocol_factory returns (reader, writer) for TCP/TLS, or a
        # writer-only object for UDP. We do not need the reader.
        if isinstance(result, tuple):
            _, writer = result
        else:
            writer = result
        self._writer = writer
        self._tx_queue = asyncio.Queue()
        self._tx_task = asyncio.create_task(self._tx_loop())

    def _build_pytak_config(self) -> configparser.SectionProxy:
        """Build the configparser section PyTAK's protocol_factory wants."""
        parser = configparser.ConfigParser()
        parser["rfmesh-cot"] = {
            "COT_URL": self._endpoint_url,
            **self._pytak_config_extras,
        }
        return parser["rfmesh-cot"]

    async def _tx_loop(self) -> None:
        """Drain the TX queue to the writer until cancelled.

        A transport error is recorded in ``self._tx_error``; the loop
        then exits. The next public call surfaces the error as
        ``CotTransportError`` (no silent retry).
        """
        assert self._tx_queue is not None
        assert self._writer is not None
        writer = self._writer
        try:
            while True:
                blob = await self._tx_queue.get()
                try:
                    writer.write(blob)
                    if hasattr(writer, "drain"):
                        await writer.drain()
                except Exception as exc:
                    self._tx_error = exc
                    _LOG.exception(
                        "PyTAKCotPublisher TX failed; publisher will refuse "
                        "further publishes (no silent retry)."
                    )
                    return
        except asyncio.CancelledError:
            # Clean shutdown path; not an error.
            raise

    async def _one_shot_send(self, blob: bytes) -> None:
        """Send a single blob in a fresh loop (sync-caller fallback)."""
        await self._ensure_started()
        assert self._tx_queue is not None
        await self._tx_queue.put(blob)
        # Give the TX loop one round trip to drain the queue.
        assert self._tx_queue is not None
        while not self._tx_queue.empty():
            await asyncio.sleep(0)
        if self._tx_error is not None:
            err = self._tx_error
            await self._shutdown()
            raise CotTransportError(f"PyTAKCotPublisher send failed: {err}") from err
        await self._shutdown()

    async def _shutdown(self) -> None:
        """Cancel the TX task and close the writer. Idempotent.

        Bounded by a short timeout so a wedged remote side (server gone,
        TCP FIN never ACK'd) cannot hang a service shutdown.
        """
        if self._closed:
            return
        self._closed = True
        if self._tx_task is not None:
            self._tx_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._tx_task, timeout=2.0)
            self._tx_task = None
        if self._writer is not None:
            with contextlib.suppress(Exception):
                # Some writers expose close(); StreamWriter does, and
                # so does asyncio_dgram's writer. We do not surface
                # close errors here; we are tearing down.
                close_fn = getattr(self._writer, "close", None)
                if callable(close_fn):
                    close_fn()
                wait_closed = getattr(self._writer, "wait_closed", None)
                if callable(wait_closed):
                    # Bounded wait: a half-closed peer must not hang us.
                    with contextlib.suppress(asyncio.TimeoutError, Exception):
                        await asyncio.wait_for(wait_closed(), timeout=2.0)
            self._writer = None
        self._tx_queue = None
