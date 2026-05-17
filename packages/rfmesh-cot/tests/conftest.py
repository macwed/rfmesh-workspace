"""Shared pytest fixtures for the ``rfmesh-cot`` test suite.

* ``make_fix_event`` -- factory for ``FixEvent`` instances with sensible
  defaults so each test only overrides what it cares about.
* ``loopback_tcp_server`` -- asyncio TCP echo / capture server for the
  publisher integration test. Binds 127.0.0.1, an ephemeral port, and
  captures every byte received until shut down.

Per ``AGENTS.md`` §3.5, this is ``conftest.py`` (not ``__init__.py``)
so the tests directory is not an importable package, which would
collide with other packages' ``tests/`` trees at mypy / pytest
collection time.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from rfmesh_contracts import (
    ConfidenceLevel,
    EllipseENU,
    EmitterClass,
    FixEvent,
    GeodeticPosition,
)


@dataclass
class FixEventFactory:
    """Callable factory producing ``FixEvent`` with overridable defaults."""

    def __call__(
        self,
        *,
        fix_id: UUID | None = None,
        t_unix_ns: int = 1_700_000_000_000_000_000,  # 2023-11-14T22:13:20Z
        lat_deg: float = 50.123_456,
        lon_deg: float = 5.654_321,
        hae_m: float = 100.0,
        sigma_m: float = 12.0,
        semi_major_m: float = 80.0,
        semi_minor_m: float = 35.0,
        orientation_deg: float = 33.0,
        covariance_m2: tuple[float, float, float] = (1.0, 0.5, 1.0),
        confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH,
        contributing_nodes: tuple[str, ...] = (
            "node-rtl-01",
            "node-rtl-02",
            "node-bladerf-overwatch",
        ),
        residuals_deg: tuple[float, ...] = (0.4, -0.2, 0.1),
        gdop: float = 2.34,
        method: str = "stansfield+mle",
        emitter_class: EmitterClass | None = EmitterClass.ELRS,
    ) -> FixEvent:
        return FixEvent(
            fix_id=fix_id if fix_id is not None else uuid4(),
            t_unix_ns=t_unix_ns,
            position=GeodeticPosition(
                lat_deg=lat_deg,
                lon_deg=lon_deg,
                hae_m=hae_m,
                sigma_m=sigma_m,
            ),
            covariance_m2=covariance_m2,
            confidence_ellipse_95=EllipseENU(
                semi_major_m=semi_major_m,
                semi_minor_m=semi_minor_m,
                orientation_deg=orientation_deg,
            ),
            confidence_level=confidence_level,
            contributing_nodes=contributing_nodes,
            residuals_deg=residuals_deg,
            gdop=gdop,
            method=method,
            emitter_class=emitter_class,
        )


@pytest.fixture()
def make_fix_event() -> FixEventFactory:
    """Return a ``FixEventFactory`` callable; tests call it with overrides."""
    return FixEventFactory()


@dataclass
class LoopbackServer:
    """A tiny asyncio TCP server that captures every byte received.

    Bound to 127.0.0.1 on an ephemeral port. The ``url`` attribute is
    the PyTAK-style ``tcp://127.0.0.1:<port>`` string the publisher
    expects. ``received`` is the full byte stream captured so far
    (accumulated across all clients).
    """

    host: str
    port: int
    received: bytearray = field(default_factory=bytearray)
    _server: asyncio.base_events.Server | None = None
    _client_writers: list[asyncio.StreamWriter] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"tcp://{self.host}:{self.port}"

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._client_writers.append(writer)
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                self.received.extend(chunk)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            return
        finally:
            with contextlib.suppress(ValueError):
                self._client_writers.remove(writer)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]

    async def stop(self) -> None:
        # Close all client connections first so wait_closed below
        # does not hang on a still-active connection.
        for w in list(self._client_writers):
            with contextlib.suppress(Exception):
                w.close()
        self._client_writers.clear()
        if self._server is not None:
            self._server.close()
            # Bounded wait: a deadlocked socket must not hang the test
            # session forever.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            self._server = None

    async def close_all_clients(self) -> None:
        """Force-close every connected client. Used to simulate broken pipe."""
        for w in list(self._client_writers):
            try:
                w.close()
                # wait_closed may raise on already-closed; that is fine
                await asyncio.wait_for(w.wait_closed(), timeout=1.0)
            except Exception:
                pass
        self._client_writers.clear()


@pytest_asyncio.fixture()
async def loopback_tcp_server() -> AsyncIterator[LoopbackServer]:
    """Start a loopback TCP capture server; tear it down after the test."""
    server = LoopbackServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture()
def deterministic_fix_event() -> FixEvent:
    """A fully deterministic ``FixEvent`` for byte-exact fixture comparison.

    All fields fixed: pinned UUID, pinned timestamp, pinned floats. Used
    by ``test_xml_encoding.py`` to compare against ``canonical_fix.xml``.
    """
    return FixEvent(
        fix_id=UUID("12345678-1234-5678-1234-567812345678"),
        t_unix_ns=1_700_000_000_000_000_000,
        position=GeodeticPosition(
            lat_deg=50.1234567,
            lon_deg=5.6543210,
            hae_m=100.000,
            sigma_m=12.0,
        ),
        covariance_m2=(1.0, 0.5, 1.0),
        confidence_ellipse_95=EllipseENU(
            semi_major_m=80.0,
            semi_minor_m=35.0,
            orientation_deg=33.0,
        ),
        confidence_level=ConfidenceLevel.HIGH,
        contributing_nodes=("node-rtl-01", "node-rtl-02", "node-bladerf-overwatch"),
        residuals_deg=(0.4, -0.2, 0.1),
        gdop=2.34,
        method="stansfield+mle",
        emitter_class=EmitterClass.ELRS,
    )


# Helpful one-line factories that do not need a fixture wrapper
def _noop_iterator() -> Iterator[None]:  # pragma: no cover -- placeholder
    yield None


__all__: list[str] = [
    "FixEventFactory",
    "LoopbackServer",
]


# pytest_asyncio configuration ----------------------------------------
# Use strict mode so any forgotten `@pytest.mark.asyncio` is flagged
# rather than silently skipped.
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """No-op placeholder to keep the hook stable across test files."""
    _ = (config, items)


# Some IDEs require an unused-export hook for the factory typing alias.
_FactoryAlias = Callable[..., FixEvent]
