"""``BothBearer`` -- composite Wi-Fi + LoRa with de-duplication.

When the node config declares ``BearerKind.BOTH`` (Wi-Fi primary +
LoRa hot-standby), the runtime sends every message on *both* bearers
and the receiving end de-duplicates by ``(node_id, t_unix_ns, kind)``.
A message that lands on Wi-Fi only is delivered; one that arrives on
both is delivered once.

Honesty (Invariant B3, no silent fallbacks)
-------------------------------------------
If the LoRa half raises ``NotImplementedError`` (real-serial hardware
not wired in v1.0), the failure is **not** swallowed. The first
occurrence is logged at WARNING level, the ``_lora_available`` flag
flips to ``False`` (subsequent calls skip the LoRa send entirely to
avoid noisy retry), and ``health_summary()`` returns the canonical
string ``"LoRa bearer down, Wi-Fi only"``. ``Node._build_status`` reads
that summary into ``NodeStatus.status_detail`` so the operator
dashboard surfaces the degraded redundancy through the heartbeat
channel. INTERFACES.md §3 documents the example string verbatim.

Synchronous ``Bearer`` Protocol (matches contract).
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from rfmesh_contracts import BearingReport, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .lora import LoraBearer
    from .wifi import WifiBearer


_LOG = logging.getLogger(__name__)

_DEFAULT_DEDUP_WINDOW_S: float = 60.0
_LORA_DOWN_DETAIL: str = "LoRa bearer down, Wi-Fi only"


class BothBearer:
    """Composite bearer over Wi-Fi (primary) + LoRa (fallback)."""

    def __init__(
        self,
        wifi: WifiBearer,
        lora: LoraBearer,
        *,
        dedup_window_s: float = _DEFAULT_DEDUP_WINDOW_S,
    ) -> None:
        if dedup_window_s <= 0.0:
            msg = f"BothBearer: dedup_window_s must be > 0 (got {dedup_window_s})."
            raise ValueError(msg)
        self._wifi = wifi
        self._lora = lora
        self._dedup_window_s = dedup_window_s
        self._seen: OrderedDict[tuple[str, int, str], float] = OrderedDict()
        self._lora_available: bool = True

    def send_bearing(self, report: BearingReport) -> None:
        """Send on both bearers. Wi-Fi errors propagate; LoRa-unavailable is surfaced."""
        self._wifi.send_bearing(report)
        self._try_lora_send("bearing", lambda: self._lora.send_bearing(report))

    def send_status(self, status: NodeStatus) -> None:
        """Send heartbeats on both bearers."""
        self._wifi.send_status(status)
        self._try_lora_send("status", lambda: self._lora.send_status(status))

    def _try_lora_send(self, kind: str, send: Callable[[], None]) -> None:
        if not self._lora_available:
            return
        try:
            send()
        except NotImplementedError as exc:
            _LOG.warning(
                "BothBearer: LoRa send (%s) unavailable, marking bearer down (%s). "
                "Wi-Fi continues. Heartbeat status_detail will surface this.",
                kind,
                exc,
            )
            self._lora_available = False

    def is_lora_available(self) -> bool:
        """``True`` until the LoRa half raises ``NotImplementedError``."""
        return self._lora_available

    def health_summary(self) -> str:
        """One-line operator-facing health string for ``NodeStatus.status_detail``.

        Returns ``""`` when both bearers are operational; returns
        ``"LoRa bearer down, Wi-Fi only"`` after the LoRa half has
        raised ``NotImplementedError`` at least once.
        """
        if self._lora_available:
            return ""
        return _LORA_DOWN_DETAIL

    def receive(self) -> Sequence[BearingReport | NodeStatus]:
        """Drain both inboxes; de-duplicate by ``(node_id, t_unix_ns, kind)``."""
        now_s = time.time()
        self._evict_old(now_s)

        out: list[BearingReport | NodeStatus] = []
        for message in (*self._wifi.receive(), *self._lora.receive()):
            kind = "bearing" if isinstance(message, BearingReport) else "status"
            key = (message.node_id, message.t_unix_ns, kind)
            if key in self._seen:
                continue
            self._seen[key] = now_s
            out.append(message)
        return tuple(out)

    def _evict_old(self, now_s: float) -> None:
        cutoff = now_s - self._dedup_window_s
        while self._seen:
            oldest_key, oldest_ts = next(iter(self._seen.items()))
            if oldest_ts >= cutoff:
                break
            del self._seen[oldest_key]

    def close(self) -> None:
        """Close both underlying bearers."""
        self._wifi.close()
        self._lora.close()
        self._seen.clear()


__all__ = ["BothBearer"]
