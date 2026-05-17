"""``ReplayRecorder`` -- Phase-C bench-side IQ capture tool.

Drives one or more ``Receiver`` instances (real or simulator) and writes
a ``.iqx`` + ``.json`` pair per node into a session directory. The
``.iqx`` format is described in ``docs/design/ops-architecture.md`` §3.3:

* Pure binary complex64 little-endian samples, headerless.
* For coherent multi-channel captures, channels are interleaved
  *within* a sample on disk:
  ``s0c0, s0c1, ..., s1c0, s1c1, ...``. The sidecar's ``n_channels``
  carries the channel count.

DESIGN NOTE: WHY ``.iqx`` AND NOT ``.iq``
-----------------------------------------
The existing ``IQRecorder`` writes uint8-interleaved ``.iq``
(rtl_sdr-native) for L1 RTL-SDR captures. The demo-replay format is
``complex64`` so:

* The simulator's ``Receiver`` (and the coherent path) produces
  ``complex64`` natively; converting to uint8 and back is lossy.
* Multi-channel coherent captures have no uint8 convention; defining
  a fresh format is cleaner than overloading ``.iq``.

The ``.iqx`` and ``.iq`` files therefore live in different format
families. The ``.iqx`` reader path is owned by the demo-replay's
``SyntheticReceiver`` replay mode (WS-A-005 follow-up); the
``IQReader`` continues to handle uint8 ``.iq`` files exclusively.

LIFECYCLE
---------
::

    rec = ReplayRecorder(output_dir, scenario_id="trench_demo_v1")
    await rec.record({
        "node-l1-west":  west_receiver,
        "node-l2-overwatch": overwatch_receiver,
    }, duration_s=60.0)

The async ``record`` reads from every receiver concurrently and writes
to per-node files. The recorder does not own the ``Scenario`` -- the
caller supplies the receivers already configured against their
scenarios -- it only owns the on-disk file format.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .metadata import ReplayMetadata

if TYPE_CHECKING:
    from rfmesh_contracts import Receiver

_LOG = logging.getLogger(__name__)


# Chunk size per ``read`` call -- 65k complex64 samples is ~512 kB,
# small enough to keep latency on the order of one block at 2 MS/s
# (~32 ms) and large enough to amortise per-block overhead.
_READ_CHUNK_SAMPLES: int = 65_536

# Shape ndim discriminator: a 2-D IQ buffer is coherent multi-channel;
# a 1-D buffer is single-channel L1. Named so the ``ndim == 2`` check
# is self-documenting.
_NDIM_MULTI_CHANNEL: int = 2


class ReplayRecorder:
    """Writes ``.iqx`` + ``.json`` pairs for a multi-node bench capture.

    Per-recording state is light (the output dir + scenario provenance);
    one instance can drive several ``record`` calls in series if
    desired, but concurrent ``record`` calls share output directory and
    would race -- the recorder is single-recording at a time.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        scenario_id: str | None = None,
        beat_id: str | None = None,
        ground_truth_emitter_enu_m: tuple[float, float] | None = None,
        channel_model: dict[str, Any] | None = None,
    ) -> None:
        """Configure the recorder.

        Args:
            output_dir: Destination directory for the session. Created
                if missing. Per-node files land at
                ``<output_dir>/<node_id>.iqx`` (+ ``.json``).
            scenario_id: Optional scenario identifier stamped onto
                every sidecar's ``ReplayMetadata.scenario_id``.
            beat_id: Optional beat identifier stamped onto every
                sidecar.
            ground_truth_emitter_enu_m: Optional emitter (east, north)
                in metres; stamped onto every sidecar.
            channel_model: Optional dict describing the channel; stamped
                onto every sidecar.
        """
        self._output_dir = output_dir
        self._scenario_id = scenario_id
        self._beat_id = beat_id
        self._ground_truth_emitter_enu_m = ground_truth_emitter_enu_m
        self._channel_model = channel_model

    async def record(
        self,
        receivers: dict[str, Receiver],
        duration_s: float,
        *,
        per_node_bearings_deg: dict[str, float] | None = None,
    ) -> dict[str, Path]:
        """Capture ``duration_s`` seconds from each receiver in parallel.

        Args:
            receivers: ``{node_id: Receiver}``. Each receiver must
                already be opened + configured; the recorder does not
                touch lifecycle (the caller may want different
                lifecycles per node).
            duration_s: Wall-clock target duration. Each receiver's
                capture stops at ``round(sample_rate * duration_s)``
                samples; sample rates are read from each receiver's
                ``capabilities()``.
            per_node_bearings_deg: Optional dict of
                ``{node_id: ground_truth_bearing_deg}`` for stamping the
                sidecars. Honoured only for node ids present in the
                receivers map.

        Returns:
            ``{node_id: payload_path}`` -- the ``.iqx`` paths actually
            written, one per node.
        """
        if duration_s <= 0.0:
            msg = f"ReplayRecorder.record: duration_s must be > 0 (got {duration_s})."
            raise ValueError(msg)

        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Each per-node capture runs in its own task; the recorder
        # gathers them. Errors propagate -- a single-node failure
        # surfaces as a raise rather than a silent skip.
        tasks = {
            node_id: asyncio.create_task(
                self._record_one(
                    node_id,
                    receiver,
                    duration_s,
                    ground_truth_bearing_deg=(
                        per_node_bearings_deg.get(node_id)
                        if per_node_bearings_deg is not None
                        else None
                    ),
                ),
                name=f"replay-record-{node_id}",
            )
            for node_id, receiver in receivers.items()
        }
        try:
            results = await asyncio.gather(*tasks.values())
        except BaseException:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            raise
        return dict(zip(tasks.keys(), results, strict=True))

    async def _record_one(
        self,
        node_id: str,
        receiver: Receiver,
        duration_s: float,
        *,
        ground_truth_bearing_deg: float | None,
    ) -> Path:
        """Capture from one receiver, write the .iqx + .json pair.

        Returns the payload path. The sidecar lives at
        ``payload.with_suffix(".json")``.
        """
        caps = receiver.capabilities()
        sample_rate_hz = caps.actual_sample_rate_hz
        n_channels = caps.n_coherent_channels
        # Total target sample count (per channel).
        total_samples = round(sample_rate_hz * duration_s)
        if total_samples <= 0:
            msg = (
                f"ReplayRecorder: computed total_samples={total_samples} "
                f"for node {node_id!r} (sample_rate_hz={sample_rate_hz}, "
                f"duration_s={duration_s}); refusing to write an empty file."
            )
            raise ValueError(msg)

        payload_path = self._output_dir / f"{node_id}.iqx"
        sidecar_path = self._output_dir / f"{node_id}.json"

        start_time = datetime.now(UTC)
        samples_written = 0
        with payload_path.open("wb") as fp:
            remaining = total_samples
            while remaining > 0:
                chunk_n = min(_READ_CHUNK_SAMPLES, remaining)
                # Coherent receivers expose ``read_coherent`` returning
                # (n_channels, n_samples); single-channel receivers
                # return (n_samples,) via ``read``.
                samples = await asyncio.to_thread(self._read_chunk, receiver, chunk_n)
                buf = _to_disk_layout(samples, expected_channels=n_channels)
                fp.write(buf.tobytes())
                samples_written += chunk_n
                remaining -= chunk_n

        metadata = ReplayMetadata(
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=_pluck_center_freq(receiver),
            n_samples=samples_written,
            start_time_utc=start_time,
            source=f"replay_recorder:{caps.driver}",
            n_channels=n_channels,
            scenario_id=self._scenario_id,
            node_id=node_id,
            beat_id=self._beat_id,
            ground_truth_emitter_enu_m=self._ground_truth_emitter_enu_m,
            ground_truth_bearing_deg=ground_truth_bearing_deg,
            channel_model=self._channel_model,
        )
        sidecar_path.write_text(metadata.model_dump_json(indent=2))
        _LOG.info(
            "ReplayRecorder: wrote %s (%d samples, %d channels)",
            payload_path,
            samples_written,
            n_channels,
        )
        return payload_path

    @staticmethod
    def _read_chunk(receiver: Receiver, n_samples: int) -> np.ndarray:
        """Read ``n_samples`` from ``receiver``, single- or multi-channel.

        Coherent receivers (with ``read_coherent``) return
        ``(n_channels, n_samples)``; single-channel receivers return
        ``(n_samples,)``. The chunk is normalised to ``complex64`` and
        passed to ``_to_disk_layout`` for serialisation.
        """
        coherent_read = getattr(receiver, "read_coherent", None)
        block = coherent_read(n_samples) if callable(coherent_read) else receiver.read(n_samples)
        if block.dtype != np.complex64:
            block = block.astype(np.complex64)
        out: np.ndarray = block
        return out


def _to_disk_layout(samples: np.ndarray, *, expected_channels: int) -> np.ndarray:
    """Reshape an in-memory IQ block to the on-disk interleaved layout.

    Single-channel: input is ``(n,)``, output is the same view.
    Multi-channel: input is ``(n_channels, n)``, output is interleaved
    along samples so on disk ``s0c0, s0c1, s1c0, s1c1, ...``. We do
    this by ``.T.copy()`` -- the transpose makes axis 1 (channels)
    the fastest-varying, then ``.copy()`` collapses to a contiguous
    buffer.
    """
    if samples.ndim == 1:
        if expected_channels != 1:
            msg = (
                f"ReplayRecorder: receiver returned 1-D IQ but "
                f"n_coherent_channels={expected_channels}."
            )
            raise ValueError(msg)
        return np.ascontiguousarray(samples, dtype=np.complex64)
    if samples.ndim == _NDIM_MULTI_CHANNEL:
        n_channels, _n_samples = samples.shape
        if n_channels != expected_channels:
            msg = (
                f"ReplayRecorder: receiver returned {n_channels}-channel IQ "
                f"but n_coherent_channels={expected_channels}."
            )
            raise ValueError(msg)
        # Transpose then make contiguous -- channels become fastest-
        # varying axis, matching the on-disk interleave-by-sample
        # convention.
        return np.ascontiguousarray(samples.T, dtype=np.complex64)
    msg = (
        f"ReplayRecorder: unexpected IQ array shape {samples.shape}; "
        "expected 1-D (single-channel) or 2-D (multi-channel)."
    )
    raise ValueError(msg)


def _pluck_center_freq(receiver: Receiver) -> float:
    """Best-effort centre-frequency lookup for the sidecar.

    The ``Receiver`` Protocol does not expose centre frequency
    directly -- it goes in via ``configure(NodeConfig)`` and stays on
    the receiver's private state. For sidecar provenance, we try the
    simulator's ``_scenario.center_freq_hz`` (the only in-tree
    receiver with a stable accessor); if unavailable, raise -- the
    sidecar's ``center_freq_hz`` is validator-enforced ``gt=0.0`` so a
    fabricated zero would itself be a silent fallback (Invariant 4).
    """
    scenario = getattr(receiver, "_scenario", None)
    if scenario is not None:
        freq = getattr(scenario, "center_freq_hz", None)
        if isinstance(freq, (int, float)) and freq > 0.0:
            return float(freq)
    # If a future receiver does not carry a scenario, surface a
    # configure-time accessor instead -- explicit, not silent.
    node_cfg = getattr(receiver, "_node_config", None)
    if node_cfg is not None:
        freq = getattr(getattr(node_cfg, "sdr", None), "center_freq_hz", None)
        if isinstance(freq, (int, float)) and freq > 0.0:
            return float(freq)
    msg = (
        "ReplayRecorder: cannot determine centre frequency for the receiver "
        f"({type(receiver).__name__}). The sidecar's center_freq_hz is "
        "validator-enforced > 0; the recorder refuses to fabricate it. "
        "Pass a receiver that exposes its scenario or its configured "
        "NodeConfig.sdr.center_freq_hz."
    )
    raise RuntimeError(msg)


__all__ = ["ReplayRecorder"]
