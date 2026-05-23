"""CoT/TAK send — lifecycle wrapper around the reused PyTAKCotPublisher.

`PyTAKCotPublisher.publish()` is sync and, inside a running event loop, will try
`loop.run_until_complete(_ensure_started())` if it was never entered — which
breaks under FastAPI. So we always `await pub.__aenter__()` (in async context)
before any `.publish()` call. The publisher does NOT auto-reconnect: a transport
failure is parked and surfaces as `CotTransportError`. On any CoT error we tear
the publisher down and rebuild it before the next send.

We open lazily on the first send (not at app startup) so the backend boots even
when the bundled FreeTAKServer is down or absent.
"""

from __future__ import annotations

import asyncio
import time

from rfmesh_contracts import FixEvent
from rfmesh_cot import CotError, PyTAKCotPublisher


class CotSender:
    def __init__(self, endpoint_url: str, callsign: str, send_fresh: bool = True) -> None:
        self._endpoint_url = endpoint_url
        self._callsign = callsign
        # rfmesh_cot sets the CoT stale time to fix.t_unix_ns + STALE_AFTER_S. Our
        # demo fixes carry old timestamps, so the marker would arrive already stale
        # and never render in a TAK client. When send_fresh, restamp t_unix_ns to
        # now on dissemination so the marker is live for the stale window.
        self._send_fresh = send_fresh
        self._pub: PyTAKCotPublisher | None = None
        self._lock = asyncio.Lock()

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    async def _open(self) -> None:
        pub = PyTAKCotPublisher(self._endpoint_url, node_callsign=self._callsign)
        await pub.__aenter__()  # opens transport + starts TX task in this loop
        self._pub = pub

    async def _reopen(self) -> None:
        await self.aclose()
        await self._open()

    async def send(self, fix: FixEvent) -> None:
        """Encode + enqueue a fix to the TAK endpoint.

        Raises CotError (CotTransportError / CotEncodingError) on failure; the
        caller maps that to HTTP 502. A connect failure (e.g. FTS down) surfaces
        here synchronously via __aenter__; a mid-stream TX failure surfaces on
        the next send. Either way we rebuild for the subsequent attempt.
        """
        if self._send_fresh:
            fix = fix.model_copy(update={"t_unix_ns": time.time_ns()})
        async with self._lock:
            if self._pub is None:
                await self._open()
            assert self._pub is not None
            try:
                self._pub.publish(fix)
            except CotError:
                # Park is consumed; rebuild so the next send starts clean.
                try:
                    await self._reopen()
                except CotError:
                    self._pub = None
                raise

    async def aclose(self) -> None:
        if self._pub is not None:
            try:
                await self._pub.__aexit__(None, None, None)
            finally:
                self._pub = None
