"""Capability intersection + estimator dispatch -- the node's startup gate.

Two responsibilities, one module:

1. ``detect_active_capabilities`` -- intersect the declared
   ``NodeConfig.capabilities`` with what the live ``Receiver`` actually
   supports. If anything declared cannot be met, raise
   ``CapabilityMismatchError``. **No silent downgrade** (Invariant B3).

2. ``build_estimators`` -- build the concrete ``BearingEstimator``
   instances for the active capability set. The dispatch is a data
   table from ``Capability`` to a constructor, so adding a future
   capability is one row, not a runtime restructure (this is the
   shape Advantage #3 -- heterogeneous mesh -- depends on).

Per ``docs/design/ops-architecture.md`` §2.2 ``L2_MVDR_NULL`` and
``L3_CLASSIFY`` are *not* ``BearingEstimator`` s. ``L2_MVDR_NULL`` is
a utility worker (``rfmesh_dsp.l2_null_steering.compute_*``) wrapped
in an asyncio task when the operator clicks "engage null"; the
classifier is a separate task that decorates the *next* bearing.
Both are explicitly excluded from ``build_estimators``' output --
they are not estimators by nature.

References
----------
* ``INTERFACES.md`` §5 (``Receiver`` / ``CoherentReceiver`` /
  ``BearingEstimator`` Protocols).
* ``docs/design/ops-architecture.md`` §2.2 (the dispatch table and
  the L2_MVDR_NULL / L3_CLASSIFY exclusions).
* ``ADR-008`` §D6 (L2 Capon as ``Capability.L2_CAPON``, null
  steering as a utility not a BearingEstimator).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rfmesh_contracts import (
    ArrayConfig,
    BearingEstimator,
    Capability,
    CoherentReceiver,
    Receiver,
    SDRConfig,
)
from rfmesh_dsp.l1 import (  # type: ignore[import-untyped]
    L1AmplitudeSweepEstimator,
)
from rfmesh_dsp.l2_music import (  # type: ignore[import-untyped]
    L2MusicEstimator,
)
from rfmesh_dsp.l2_mvdr import (  # type: ignore[import-untyped]
    L2MvdrEstimator,
)

from .runtime import CapabilityMismatchError

if TYPE_CHECKING:
    from rfmesh_contracts.geospatial import GeodeticPosition


# Capabilities that require a phase-coherent receiver. Built once at
# import time so the lookup is O(1) on the hot path and the relationship
# is documented next to the literal set.
_L2_CAPABILITIES: frozenset[Capability] = frozenset(
    {Capability.L2_MUSIC, Capability.L2_CAPON, Capability.L2_MVDR_NULL}
)

# Capabilities that produce a ``BearingEstimator`` (i.e. emit
# ``BearingReport`` s). The two special members of ``Capability`` --
# ``L2_MVDR_NULL`` (utility) and ``L3_CLASSIFY`` (classifier) -- are
# absent from this set and therefore from ``build_estimators``'
# output, per docs/design/ops-architecture.md §2.2.
_ESTIMATOR_CAPABILITIES: frozenset[Capability] = frozenset(
    {Capability.L1_RSSI, Capability.L2_MUSIC, Capability.L2_CAPON}
)

# Minimum coherent-channel count for any L2 capability. A single
# channel cannot do phase DF (Bearing is recoverable only by
# inter-channel phase difference, which requires >= 2 channels).
_MIN_COHERENT_CHANNELS_FOR_L2: int = 2


def detect_active_capabilities(
    declared: tuple[Capability, ...],
    receiver: Receiver,
    array: ArrayConfig | None,
) -> tuple[Capability, ...]:
    """Intersect operator intent with hardware reality. Raise on mismatch.

    Args:
        declared: ``NodeConfig.capabilities`` -- what the operator
            wants this node to do.
        receiver: The live receiver, already ``open()``-ed and
            ``configure()``-ed. Its ``capabilities()`` snapshot is
            consulted for ``n_coherent_channels`` and (when it is a
            ``CoherentReceiver``) ``is_calibrated``.
        array: ``NodeConfig.array``, or ``None`` for L1-only nodes.

    Returns:
        The active capabilities, in the same order as ``declared``
        (the operator's declared order is the dashboard's display
        order; we do not re-sort).

    Raises:
        CapabilityMismatchError: if any declared capability cannot
            be met by the live receiver / array combination. The
            error message names *which* capability and *why*. No
            silent downgrade.
    """
    if not declared:
        msg = "detect_active_capabilities: at least one declared capability is required."
        raise CapabilityMismatchError(msg)

    caps = receiver.capabilities()
    n_coherent = caps.n_coherent_channels
    is_coherent = isinstance(receiver, CoherentReceiver)

    for capability in declared:
        if capability in _L2_CAPABILITIES:
            if not is_coherent:
                msg = (
                    f"Declared capability {capability.value} requires a "
                    f"CoherentReceiver; got driver={caps.driver} with "
                    f"n_coherent_channels={n_coherent}."
                )
                raise CapabilityMismatchError(msg)
            if n_coherent < _MIN_COHERENT_CHANNELS_FOR_L2:
                msg = (
                    f"Declared capability {capability.value} requires "
                    f">= {_MIN_COHERENT_CHANNELS_FOR_L2} coherent channels; "
                    f"receiver reports n_coherent_channels={n_coherent}."
                )
                raise CapabilityMismatchError(msg)
            if array is None:
                msg = (
                    f"Declared capability {capability.value} requires an "
                    "ArrayConfig but the NodeConfig has no array block."
                )
                raise CapabilityMismatchError(msg)
            # B3: L2 DSP refuses to emit from an uncalibrated coherent
            # stream -- INTERFACES.md §5 ``CoherentReceiver``. We refuse
            # to boot with L2 declared but no calibration done. Caller
            # is expected to ``calibrate()`` the receiver before
            # ``detect_active_capabilities``.
            coherent_recv = receiver
            assert isinstance(coherent_recv, CoherentReceiver)
            if not coherent_recv.is_calibrated:
                msg = (
                    f"Declared capability {capability.value} requires the "
                    "CoherentReceiver to be calibrated; is_calibrated is False. "
                    "Run receiver.calibrate() before detect_active_capabilities()."
                )
                raise CapabilityMismatchError(msg)

    return tuple(declared)


def build_estimators(
    active: tuple[Capability, ...],
    sdr_config: SDRConfig,
    array_config: ArrayConfig | None,
    heading_deg: float | None,
    *,
    node_id: str,
    node_position: GeodeticPosition,
    receiver: Receiver,
    sweep_dwell_samples: int = 1024,
) -> tuple[BearingEstimator, ...]:
    """Build one ``BearingEstimator`` per active estimator-capability.

    The dispatch is *data*, not branching code -- adding a future
    estimator (Capon was added with one row in the table; a future
    ESPRIT would be the same) is one map entry, not a refactor.

    Args:
        active: Result of ``detect_active_capabilities``.
        sdr_config: ``NodeConfig.sdr`` -- carries
            ``center_freq_hz`` consumed by L2 estimators.
        array_config: ``NodeConfig.array`` -- required for L2,
            must be ``None`` for L1-only.
        heading_deg: ``NodeConfig.heading_deg``. Passed for future
            use; the salvaged L1 estimator already records heading
            via ``observe`` per-sample, so this argument is reserved
            for any future estimator that needs it at construction.
        node_id: Stable node identifier; stamped onto every
            ``BearingReport`` this estimator produces.
        node_position: Surveyed node position; same.
        receiver: The live receiver. L2 estimators bind to a
            ``CoherentReceiver`` and consult its
            ``is_calibrated`` on every estimate; if the active set
            asks for L2 the receiver MUST satisfy
            ``CoherentReceiver`` (already checked by
            ``detect_active_capabilities``).
        sweep_dwell_samples: Per-heading sample count passed to the
            L1 estimator. Default 1024 -- a reasonable middle ground;
            the simulator-driven trench-demo tests override per
            scenario.

    Returns:
        A tuple of ``BearingEstimator`` instances, in
        capability-declared order. Capabilities that are not
        estimators (``L2_MVDR_NULL``, ``L3_CLASSIFY``) are simply
        absent from the output -- not an error, by design (those
        live on their own asyncio tasks).
    """
    # heading_deg is reserved for future estimators; the salvaged L1
    # estimator records heading per-observe, not at construction.
    del heading_deg

    estimators: list[BearingEstimator] = []
    for capability in active:
        if capability not in _ESTIMATOR_CAPABILITIES:
            # L2_MVDR_NULL / L3_CLASSIFY -- not BearingEstimators by
            # design. Skip silently; they are handled on their own
            # tasks elsewhere in the runtime.
            continue
        if capability is Capability.L1_RSSI:
            estimators.append(
                L1AmplitudeSweepEstimator(
                    node_id=node_id,
                    node_position=node_position,
                    sweep_dwell_samples=sweep_dwell_samples,
                )
            )
            continue
        # L2 estimators all need the array config and a coherent
        # receiver -- already validated by detect_active_capabilities.
        if array_config is None:
            msg = (
                f"build_estimators: capability {capability.value} requires an "
                "ArrayConfig but array_config is None (should be unreachable "
                "after detect_active_capabilities)."
            )
            raise CapabilityMismatchError(msg)
        if not isinstance(receiver, CoherentReceiver):
            msg = (
                f"build_estimators: capability {capability.value} requires a "
                "CoherentReceiver (should be unreachable after "
                "detect_active_capabilities)."
            )
            raise CapabilityMismatchError(msg)
        if capability is Capability.L2_MUSIC:
            estimators.append(
                L2MusicEstimator(
                    node_id=node_id,
                    node_position=node_position,
                    receiver=receiver,
                    array_config=array_config,
                    operating_frequency_hz=sdr_config.center_freq_hz,
                )
            )
        elif capability is Capability.L2_CAPON:
            estimators.append(
                L2MvdrEstimator(
                    node_id=node_id,
                    node_position=node_position,
                    receiver=receiver,
                    array_config=array_config,
                    operating_frequency_hz=sdr_config.center_freq_hz,
                )
            )

    return tuple(estimators)


__all__ = [
    "build_estimators",
    "detect_active_capabilities",
]
