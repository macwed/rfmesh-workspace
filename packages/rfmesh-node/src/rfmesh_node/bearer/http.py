"""``HttpBearer`` -- HTTP/JSON transport straight to the both3 backend.

A ``Bearer`` Protocol conformer (``rfmesh_contracts.protocols.Bearer``)
that POSTs a ``BearingReport`` as JSON to the backend's ``POST /bearings``
route (``deployment/backend/src/both3_poc/app.py``). It exists so a single
field node can light up the live map *without* standing up the UDP fusion
ingest path: the backend already validates ``BearingReport`` JSON and
renders the line-of-bearing on ``GET /bearings`` GeoJSON.

Transport choice -- stdlib ``urllib`` (sync), not ``aiohttp``: the
``Bearer`` contract is synchronous (``send_bearing`` returns ``None``),
and a short blocking POST keeps this dependency-free. Delivery is
best-effort, matching ``WifiBearer``'s UDP semantics -- a momentarily
unreachable backend logs a warning and drops the report rather than
crashing the node. Fusion's batch window already tolerates loss.

Selected by ``rfmesh_node.cli.run_node`` when ``NodeConfig.fusion_endpoint``
has an ``http://`` or ``https://`` scheme; the ``udp://`` scheme keeps the
salvaged ``WifiBearer``.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from rfmesh_contracts import BearingReport, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG = logging.getLogger(__name__)

# Default per-request timeout. A node sweep produces a bearing every few
# seconds; a 2 s ceiling keeps a stalled backend from blocking the sweep
# loop's worker thread for long.
_DEFAULT_TIMEOUT_S: float = 2.0


class HttpBearer:
    """HTTP + JSON bearer (synchronous ``Bearer`` Protocol conformer).

    Node-side only: it sends ``BearingReport`` s (and, best-effort,
    ``NodeStatus`` heartbeats) and never receives -- ``receive`` returns
    an empty tuple. The fusion-server inbound half is the backend itself.
    """

    def __init__(self, base_url: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        """Configure the bearer against a backend base URL.

        Args:
            base_url: Backend base, e.g. ``http://10.0.0.1:8000``. The
                ``/bearings`` and ``/status`` paths are appended.
            timeout_s: Per-request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._bearings_url = f"{self._base_url}/bearings"
        self._status_url = f"{self._base_url}/status"
        self._timeout_s = timeout_s
        # The backend has no /status route today; probe-and-disable on the
        # first failure so we stop hammering a 404 every heartbeat.
        self._status_enabled = True

    def send_bearing(self, report: BearingReport) -> None:
        """POST one ``BearingReport`` as JSON to ``/bearings`` (best-effort)."""
        self._post(self._bearings_url, report.model_dump(mode="json"))

    def send_status(self, status: NodeStatus) -> None:
        """POST one ``NodeStatus`` to ``/status`` if the backend accepts it.

        The both3 backend does not expose ``/status`` yet; the first
        failure disables further attempts so the heartbeat loop does not
        spend a request budget on a route that is not there.
        """
        if not self._status_enabled:
            return
        ok = self._post(self._status_url, status.model_dump(mode="json"))
        if not ok:
            self._status_enabled = False
            _LOG.info("HttpBearer: /status not available; disabling status POSTs.")

    def receive(self) -> Sequence[BearingReport | NodeStatus]:
        """Node-side bearer never receives; always empty."""
        return ()

    def close(self) -> None:
        """No persistent resources to release (urllib opens per-request)."""

    def _post(self, url: str, payload: dict[str, object]) -> bool:
        """POST ``payload`` as JSON. Return True on 2xx, False on failure.

        Never raises -- live-mesh delivery is best-effort (see module
        docstring). Failures are logged at WARNING and the report dropped.
        """
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 -- url is operator-supplied config
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as resp:  # noqa: S310
                return bool(200 <= resp.status < 300)  # noqa: PLR2004 -- HTTP 2xx range
        except urllib.error.HTTPError as exc:
            _LOG.warning("HttpBearer: %s -> HTTP %s", url, exc.code)
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _LOG.warning("HttpBearer: %s unreachable: %s", url, exc)
            return False


__all__ = ["HttpBearer"]
