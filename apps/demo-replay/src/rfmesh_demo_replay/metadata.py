"""``ReplayMetadata`` -- extends ``IQMetadata`` with replay-specific provenance.

Per ``docs/design/ops-architecture.md`` §3.3, a recorded-IQ session
writes one ``.iqx`` payload + one ``.json`` sidecar per node. The
sidecar carries the same provenance as a standalone IQ capture
(``IQMetadata`` -- sample rate, centre freq, sample count, start time)
plus replay-specific fields that bind the recording to a scenario:

* ``ground_truth_emitter_enu_m`` -- the (east, north) ENU coords of
  the emitter at the moment of capture. Lets the Phase-C bench-test
  loop assert that the bearings produced from the replayed IQ point
  at the truth.
* ``ground_truth_bearing_deg`` -- the bearing the recording node
  should report, given its own ``heading_deg`` and the truth above.
  A second-stage cross-check for the L1 / L2 estimator tests.
* ``scenario_id`` / ``node_id`` / ``beat_id`` -- which scenario,
  which node within it, optionally which beat (a long capture covers
  several beats; per-beat captures stay per-beat).
* ``channel_model`` -- a free-form dict that records the channel
  configuration the simulator used when synthesising. Carrying it on
  the sidecar means the replay loop can reproduce the exact channel
  if a new simulator run wants to compare against the recording.
* ``n_channels`` -- 1 for L1 captures, 2+ for coherent L2 captures.
  ``IQMetadata`` itself is single-channel by construction
  (``IQRecorder.write`` rejects 2-D arrays); the replay format
  decouples the on-disk payload shape from the contracts-frozen
  sidecar by carrying ``n_channels`` here. A reader uses this to
  reshape the ``.iqx`` interleaved buffer.

DESIGN NOTE: WHY EXTEND IQMetadata RATHER THAN COMPOSE
------------------------------------------------------
``IQMetadata`` is frozen and has ``extra="forbid"`` -- adding fields
by composition (a wrapping object) would force every replay-aware
reader to know two types. Extending instead keeps a single Pydantic
model on the wire / on disk; an older ``IQMetadata`` reader that
sees a ``ReplayMetadata`` payload picks up the IQMetadata fields it
needs and *would* reject the extras -- but the readers in this app
explicitly construct ``ReplayMetadata`` from JSON, so the older
``IQReader`` path never sees a ``ReplayMetadata`` sidecar by
mistake (recordings live under ``recordings/<session>/`` which is
not where ``IQReader`` looks). The decoupling is by file layout,
not by type hierarchy.

DESIGN NOTE: SCHEMA VERSION DELIBERATELY MATCHES IQMetadata
-----------------------------------------------------------
``IQMetadata.schema_version`` is the on-disk file-format version
(see ``rfmesh_sdr.io.metadata`` module docstring). ``ReplayMetadata``
inherits it directly. A bump on either side coordinates on the
single literal; the replay metadata's evolution rides on the same
versioning discipline as the underlying capture format.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field
from rfmesh_sdr.io.metadata import IQMetadata  # type: ignore[import-untyped]


class ReplayMetadata(IQMetadata):  # type: ignore[misc]
    """``IQMetadata`` plus replay-session provenance.

    All replay-specific fields default to ``None`` so a ``ReplayMetadata``
    constructed without scenario context is structurally identical to an
    ``IQMetadata`` on the JSON wire. The Phase-C bench recorder
    populates them; live captures may leave them as ``None``.

    ``n_channels`` defaults to 1 to mirror the L1 / single-channel
    base case. The simulator coherent path emits multi-channel IQ;
    the recorder sets ``n_channels`` to ``ArraySpec.n_elements``.
    """

    # Re-declare model_config so ``extra="forbid"`` carries through.
    # Pydantic inheritance preserves it, but stating it locally makes
    # the discipline visible at this layer too.
    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- Replay session provenance -----------------------------------------
    scenario_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the scenario that produced this recording, "
            "matches the YAML ``scenario_id`` field. None for live "
            "captures outside the demo scenarios."
        ),
    )
    node_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the node the recording is for. Matches the "
            "corresponding ``NodeReplaySpec.node_id`` in the scenario."
        ),
    )
    beat_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the demo beat this recording covers. ``None`` "
            "for whole-scenario captures."
        ),
    )

    # -- Ground truth (for honesty / regression tests) ---------------------
    ground_truth_emitter_enu_m: tuple[float, float] | None = Field(
        default=None,
        description=(
            "Emitter (east, north) position in metres in the scenario's "
            "ENU frame at the moment of capture. Lets replay-time tests "
            "assert that bearings produced from the replayed IQ point "
            "at the truth."
        ),
    )
    ground_truth_bearing_deg: float | None = Field(
        default=None,
        ge=0.0,
        lt=360.0,
        description=(
            "Geographic bearing from this node to the emitter, degrees "
            "true, clockwise from north. ``None`` for captures whose "
            "geometry is not pinned to a known emitter."
        ),
    )

    # -- Channel + multi-channel shape -------------------------------------
    channel_model: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Free-form dict describing the simulator channel model "
            "used when synthesising the IQ. Captures key-value pairs "
            "such as multipath taps, shadowing std, etc. ``None`` for "
            "hardware-recorded IQ."
        ),
    )
    n_channels: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of coherent channels in the .iqx payload. 1 for "
            "L1 single-channel captures; ``ArraySpec.n_elements`` for "
            "coherent captures. Channels are interleaved within a "
            "sample on disk: ``s0c0, s0c1, ..., s1c0, s1c1, ...``."
        ),
    )


__all__ = ["ReplayMetadata"]
