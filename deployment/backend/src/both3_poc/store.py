"""In-memory store for fixes and bearings. No DB — this is a PoC.

- Fixes keyed by `fix_id` (UUID). Latest write wins per id.
- Bearings keyed by `(node_id, t_unix_ns)` so multiple bearings per node over
  time are retained (keying by node_id alone would collapse the history and
  break LOBs for a selected historical fix).

`seeded_fix_ids` records which fixes came from the demo seed so the staleness
layer can treat them as fresh when SEED_AS_FRESH is set.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from rfmesh_contracts import BearingReport, FixEvent


class Store:
    def __init__(self) -> None:
        self._fixes: dict[UUID, FixEvent] = {}
        self._bearings: dict[tuple[str, int], BearingReport] = {}
        self._fix_freq: dict[UUID, float | None] = {}
        self.seeded_fix_ids: set[UUID] = set()
        # Operator-placed red nodes (sensors/jammers) for the exposure lenses.
        # Plain dicts (lat, lon, h_m, role, erp_class, node_id, bands_hz). Kept
        # here so they survive across requests; mesh-derived jammers are computed
        # on demand from the fixes, not stored.
        self._reds: dict[str, dict[str, Any]] = {}

    # ----- fixes -----
    def upsert_fix(
        self, fix: FixEvent, *, seeded: bool = False, center_freq_hz: float | None = None
    ) -> None:
        self._fixes[fix.fix_id] = fix
        self._fix_freq[fix.fix_id] = center_freq_hz
        if seeded:
            self.seeded_fix_ids.add(fix.fix_id)

    def freq_for(self, fix_id: UUID) -> float | None:
        """Observed center frequency annotation for a fix (None if unknown).

        FixEvent (frozen contract) has no frequency field, so the deployment
        carries it as a side annotation, populated from the seed file's
        ``center_freq_hz`` or the ingest payload.
        """
        return self._fix_freq.get(fix_id)

    def get_fix(self, fix_id: UUID) -> FixEvent | None:
        return self._fixes.get(fix_id)

    def list_fixes(self) -> list[FixEvent]:
        # newest first by timestamp
        return sorted(self._fixes.values(), key=lambda f: f.t_unix_ns, reverse=True)

    def is_seeded(self, fix_id: UUID) -> bool:
        return fix_id in self.seeded_fix_ids

    # ----- bearings -----
    def upsert_bearing(self, bearing: BearingReport) -> None:
        self._bearings[(bearing.node_id, bearing.t_unix_ns)] = bearing

    def list_bearings(self) -> list[BearingReport]:
        return list(self._bearings.values())

    def latest_bearings_per_node(self) -> list[BearingReport]:
        """One bearing per node — the most recent — for the live overlay."""
        latest: dict[str, BearingReport] = {}
        for b in self._bearings.values():
            cur = latest.get(b.node_id)
            if cur is None or b.t_unix_ns > cur.t_unix_ns:
                latest[b.node_id] = b
        return list(latest.values())

    def latest_peer_bearing(self, node_id: str) -> BearingReport | None:
        """Most recent PEER_LINK-prior bearing for ``node_id`` (ADR-026 §I).

        Used by ``GET /node/{id}/peer_bearing`` to combine likelihood +
        prior into a posterior for the soldier UI. Returns ``None`` if
        the node has not yet emitted any peer-acquired bearing.
        """
        from rfmesh_contracts import BearingPriorKind  # noqa: PLC0415

        latest: BearingReport | None = None
        for b in self._bearings.values():
            if b.node_id != node_id:
                continue
            if b.prior_kind is not BearingPriorKind.PEER_LINK:
                continue
            if latest is None or b.t_unix_ns > latest.t_unix_ns:
                latest = b
        return latest

    def counts(self) -> tuple[int, int]:
        return len(self._fixes), len(self._bearings)

    # ----- operator-placed red nodes (exposure lenses) -----
    def add_red(self, red: dict[str, Any]) -> str:
        """Store a red marker; returns its id (generated if not supplied)."""
        red_id = str(red.get("id") or uuid4().hex[:12])
        red["id"] = red_id
        self._reds[red_id] = red
        return red_id

    def list_reds(self) -> list[dict[str, Any]]:
        return list(self._reds.values())

    def delete_red(self, red_id: str) -> bool:
        return self._reds.pop(red_id, None) is not None

    def clear_reds(self) -> None:
        self._reds.clear()
