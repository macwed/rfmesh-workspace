"""``BothBearer`` -- composite Wi-Fi + LoRa with de-duplication.

When the node config declares ``BearerKind.BOTH`` (Wi-Fi primary +
LoRa hot-standby), the runtime sends every message on *both* bearers
and the receiving end de-duplicates by ``(node_id, t_unix_ns, kind)``.
A message that lands on Wi-Fi only is delivered; one that arrives on
both is delivered once.

Synchronous ``Bearer`` Protocol (matches contract).
"""

from __future__ import annotations

import contextlib
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from rfmesh_contracts import BearingReport, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .lora import LoraBearer
    from .wifi import WifiBearer


_DEFAULT_DEDUP_WINDOW_S: float = 60.0


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

    def send_bearing(self, report: BearingReport) -> None:
        """Send on both bearers. Wi-Fi errors propagate; LoRa-unavailable does not."""
        self._wifi.send_bearing(report)
        # LoRa real-hardware path not yet wired (v1.0 limitation).
        # Wi-Fi already shipped; carry on if LoRa is unavailable.
        with contextlib.suppress(NotImplementedError):
            self._lora.send_bearing(report)

    def send_status(self, status: NodeStatus) -> None:
        """Send heartbeats on both bearers."""
        self._wifi.send_status(status)
        with contextlib.suppress(NotImplementedError):
            self._lora.send_status(status)

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
