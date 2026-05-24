"""Behavioural contracts -- the Protocol classes that bound the workstreams.

``messages.py`` and ``config.py`` freeze the *data* that crosses workstream
boundaries. This module freezes the *behaviour*: the abstract operations one
workstream may assume another provides. They are ``typing.Protocol`` classes,
which means structural typing -- an implementation conforms by having the right
methods, with no import-time inheritance coupling back to this package beyond
the data types. A workstream can be developed and unit-tested in total
isolation as long as it (a) consumes only contract data types and (b) satisfies
or depends-on only these Protocols.

WHY ``Protocol`` AND NOT ABC
----------------------------
An abstract base class forces every implementation to import and subclass it,
which is a hard dependency edge and a versioning headache. A ``Protocol`` is a
shape. The SDR workstream's ``SoapyReceiver`` does not import ``Receiver`` to
inherit it; it just *is* one, structurally, and ``@runtime_checkable`` lets a
test assert that cheaply. This keeps the dependency graph a clean star around
``rfmesh-contracts`` -- everyone depends on the contracts, no one depends on
anyone else.

WHAT IS AND IS NOT HERE
-----------------------
Here: the four boundary contracts that two different workstreams must agree
on -- signal input (``Receiver`` / ``CoherentReceiver``), bearing production
(``BearingEstimator``), fix production (``Fuser``), and the two output sinks
(``CotPublisher``, and the node->fusion ``Bearer`` transport).

Not here: anything internal to a single workstream. How the DSP workstream
structures MUSIC vs Capon, how fusion structures Stansfield vs MLE -- those are
that workstream's own business and must *not* be frozen here, or the contract
becomes a straitjacket. The rule of thumb: a Protocol belongs in this file iff
a *different* workstream calls it.

These Protocols intentionally describe *synchronous, hardware-free-testable*
shapes. Concurrency, async, threading, ret/buffering strategy -- all of that is
each workstream's implementation freedom. The node runtime is what stitches
the synchronous pieces into a running asyncio service.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

import numpy as np

from .config import FusionConfig, NodeConfig
from .enums import Capability
from .messages import BearingReport, FixEvent, NodeStatus

# ---------------------------------------------------------------------------
# Shared lightweight type aliases for the signal path.
#
# These are aliases, not classes, on purpose: the IQ path is hot, and forcing
# every sample buffer through a Pydantic model would be absurd. The contract
# is the *shape and dtype convention*, documented here and asserted in the
# workstreams' own tests.
# ---------------------------------------------------------------------------

#: A single-channel block of baseband IQ samples: 1-D complex64, length =
#: however many samples the caller requested. Real RTL-SDR/HackRF path.
IQBlock = np.ndarray

#: A coherent multi-channel block of baseband IQ: 2-D complex64 with shape
#: (n_channels, n_samples). Row i is channel i. The rows are *sample-aligned*
#: and *phase-coherent* -- that alignment is the whole point of a
#: CoherentReceiver and is its implementation's responsibility to guarantee
#: (shared LO + calibration). The L2 DSP code may assume it.
CoherentIQBlock = np.ndarray


class ReceiverCapabilities(Protocol):
    """Read-only description of what a concrete Receiver can actually do.

    Returned by ``Receiver.capabilities()``. Lets the node runtime decide,
    *from the live device*, which processing pipeline to build -- the
    hardware-side half of the capability intersection (the config-side half is
    ``NodeConfig.capabilities``). DSP code never inspects this; it is consumed
    by the node runtime only.
    """

    @property
    def driver(self) -> str:
        """The SDR driver/family backing this receiver, e.g. 'rtlsdr'."""
        ...

    @property
    def n_coherent_channels(self) -> int:
        """How many phase-coherent RX channels this device provides.

        1 for RTL-SDR / HackRF (no phase DF possible -- L1 only). 2+ for a
        bladeRF / Pluto in coherent mode (L2 possible). The node runtime
        compares this against any L2 capability the config declares.
        """
        ...

    @property
    def actual_sample_rate_hz(self) -> float:
        """The sample rate the device truly settled on, Hz.

        May differ from the requested ``SDRConfig.sample_rate_hz`` -- hardware
        quantises. Surfaced honestly so DSP can use the true rate and the
        operator can see any shortfall.
        """
        ...

    @property
    def is_power_calibrated(self) -> bool:
        """Whether the device's amplitude readings are calibrated to absolute power.

        False for every SDR in our pool (RTL-SDR, HackRF, bladeRF, Pluto).
        Consumers must therefore report *relative* RSSI / SNR, never dBm --
        claiming dBm without a calibration source is an RF-credibility
        own-goal (see ``docs/runbook-demo.md``). Carried explicitly so this is
        a checked fact, not a tribal assumption.
        """
        ...


@runtime_checkable
class Receiver(Protocol):
    """A source of single-channel baseband IQ. The L1 signal-input contract.

    Implemented by the SDR workstream: ``SoapyReceiver`` (covers RTL-SDR,
    HackRF, and single-channel bladeRF/Pluto) and ``SyntheticReceiver`` (the
    simulator -- same Protocol, IQ generated from a configured emitter
    geometry, so the *entire* pipeline runs and is tested with zero hardware).

    Consumed by the DSP workstream's L1 code and, via the node runtime, wired
    to whatever ``SDRConfig.driver`` names. The DSP code depends on this
    Protocol and nothing else from the SDR side -- it cannot tell, and must not
    care, whether it is reading a real RTL-SDR or generated samples.

    Lifecycle: ``open()`` -> ``configure()`` -> ``read(n)`` repeatedly ->
    ``close()``. Implementations should tolerate ``close()`` being called more
    than once and from a teardown path.
    """

    def open(self) -> None:
        """Acquire the device (or initialise the simulator). Idempotent-friendly."""
        ...

    def configure(self, config: NodeConfig) -> None:
        """Apply tuning from the node config (sample rate, freq, gain, bias-tee).

        Takes the whole ``NodeConfig`` rather than just ``SDRConfig`` so the
        receiver can also see, e.g., the array block if it needs to -- but a
        single-channel ``Receiver`` will use only ``config.sdr``.
        """
        ...

    def read(self, n_samples: int) -> IQBlock:
        """Return exactly ``n_samples`` of single-channel IQ as 1-D complex64.

        Blocking. If the device overflows/underflows, the implementation
        raises rather than silently returning a short or zero-padded buffer --
        a silent short read would corrupt every downstream estimate
        (Invariant 4: no silent fallbacks).
        """
        ...

    def capabilities(self) -> ReceiverCapabilities:
        """Describe what this live device can do (see ``ReceiverCapabilities``)."""
        ...

    def close(self) -> None:
        """Release the device. Safe to call multiple times; safe in teardown."""
        ...


@runtime_checkable
class CoherentReceiver(Receiver, Protocol):
    """A source of phase-coherent *multi-channel* IQ. The L2 signal-input contract.

    Extends ``Receiver``: a ``CoherentReceiver`` is also a valid single-channel
    ``Receiver`` (channel 0), so an L2-capable node can still do L1. Adds the
    coherent read and the calibration handshake that phase DF depends on.

    Implemented by the SDR workstream's ``BladeRFCoherentReceiver`` and
    ``PlutoCoherentReceiver`` -- and by a coherent mode of ``SyntheticReceiver``
    so L2 MUSIC is testable, against known ground-truth angles, with no
    hardware. Consumed by the DSP workstream's L2 (MUSIC / MVDR) code.

    The hard guarantee a ``CoherentReceiver`` makes, and the L2 DSP code may
    rely on: the rows of every ``read_coherent`` result are sample-aligned and
    phase-coherent *to the extent the last ``calibrate()`` established*, and
    ``is_calibrated`` truthfully reports whether that has happened.
    """

    def read_coherent(self, n_samples: int) -> CoherentIQBlock:
        """Return ``(n_channels, n_samples)`` complex64 of sample-aligned coherent IQ.

        Same blocking / no-silent-short-read contract as ``Receiver.read``.
        Rows are phase-coherent per the last ``calibrate()``; calling this
        before a successful ``calibrate()`` is allowed but the result must be
        treated as uncalibrated (``is_calibrated`` is False) and the L2 code
        must refuse to emit bearings from it.
        """
        ...

    def calibrate(self) -> None:
        """Run the phase/gain calibration handshake across the coherent channels.

        Typically: inject a common reference (noise source or pilot tone) into
        all channels, cross-correlate against channel 0, solve per-channel
        complex offsets, store them. Must be re-run after any retune (a retune
        randomises inter-channel phase). On success ``is_calibrated`` becomes
        True. On failure it raises -- it never returns leaving the device
        falsely 'calibrated'.
        """
        ...

    @property
    def is_calibrated(self) -> bool:
        """Whether a successful ``calibrate()`` is currently in effect.

        Goes False after construction and after any retune; True only after a
        successful ``calibrate()``. The L2 DSP code MUST check this and refuse
        to produce a ``BearingReport`` from an uncalibrated coherent stream.
        """
        ...


class TransmitterCapabilities(Protocol):
    """Read-only description of what a concrete Transmitter can actually do.

    Symmetric to ``ReceiverCapabilities``. Returned by
    ``Transmitter.capabilities()`` so the node runtime can validate
    declared comms capability against the live device -- a node
    declaring ``Capability.COMMS_DSSS`` whose backing SDR cannot
    transmit (e.g. RTL-SDR V4) is a fatal startup error (B3, no
    silent downgrade). Added in SCHEMA_VERSION 1.3.0 (ADR-025).

    DSP code never inspects this; it is consumed by the node runtime
    and the comms loop only.
    """

    @property
    def driver(self) -> str:
        """The SDR driver/family backing this transmitter, e.g. 'bladerf', 'hackrf', 'pluto'."""
        ...

    @property
    def n_tx_channels(self) -> int:
        """How many independent TX channels this device provides.

        1 for HackRF One / Pluto / single-chain BladeRF. 2+ for a
        coherent multi-channel device (BladeRF 2.0 micro in 2x2
        mode). DSSS v1.3.0 needs only 1; the field exists so a
        future coherent-TX feature (e.g. transmit beamforming) can
        validate the device at startup.
        """
        ...

    @property
    def actual_sample_rate_hz(self) -> float:
        """The TX sample rate the device truly settled on, Hz.

        Same honesty rule as ``ReceiverCapabilities.actual_sample_rate_hz``:
        may differ from the requested rate. Surfaced so the comms loop
        can validate ``CommsConfig.chip_rate_hz <= actual_sample_rate_hz``
        against the realised hardware capability, not the requested one.
        """
        ...

    @property
    def max_tx_power_normalized(self) -> float:
        """Maximum TX gain/power as a normalised float in ``[0.0, 1.0]``.

        Deliberately NOT in dBm. None of the SDRs in scope (HackRF,
        Pluto, BladeRF) is absolute-power-calibrated on transmit,
        same as the RX side (see ``ReceiverCapabilities``). The
        normalised float is the honest unit each driver's TX API
        actually exposes; the comms layer maps a desired link
        operating point to a normalised value through
        ``link_budget.py`` and the operator-tuned scenario, not
        through a dBm assumption.
        """
        ...


@runtime_checkable
class Transmitter(Protocol):
    """A sink for single-channel baseband IQ. The DSSS comms TX contract.

    Symmetric to ``Receiver``. Implemented by the SDR workstream:
    ``BladeRFTransmitter`` / ``HackRFTransmitter`` / ``PlutoTransmitter``
    (whichever hardware is on-site) and by ``SyntheticTransmitter``
    (the simulator -- same Protocol, IQ written to an in-process
    ``loopback_channel`` that one or more ``SyntheticReceiver`` s
    read, so the *entire* comms protocol runs and is tested with
    zero hardware -- ARCHITECTURE.md §4 simulator-first).

    Consumed by the ``rfmesh-dsss`` workstream's framing/modulation
    output and, via the node runtime, wired to whatever
    ``SDRConfig.driver`` names. The DSP/DSSS code depends on this
    Protocol and nothing else from the SDR side -- it cannot tell,
    and must not care, whether it is writing to a real BladeRF or
    a synthetic loopback.

    Lifecycle: ``open()`` -> ``configure()`` -> ``write(iq)``
    repeatedly -> ``close()``. Implementations should tolerate
    ``close()`` being called more than once and from a teardown path.

    Added in SCHEMA_VERSION 1.3.0 (ADR-025).
    """

    def open(self) -> None:
        """Acquire the TX device (or initialise the simulator). Idempotent-friendly."""
        ...

    def configure(self, config: NodeConfig) -> None:
        """Apply tuning from the node config (sample rate, freq, gain).

        Takes the whole ``NodeConfig`` for symmetry with
        ``Receiver.configure``; a single-channel ``Transmitter``
        will use only ``config.sdr`` and (when present)
        ``config.comms``.
        """
        ...

    def write(self, iq: IQBlock) -> int:
        """Send a block of 1-D complex64 IQ samples; return the number transmitted.

        Blocking. Returns ``len(iq)`` on success. If the device
        underflows / TX FIFO refuses the full block, the
        implementation raises rather than silently transmitting a
        truncated buffer (B3, mirror of ``Receiver.read``'s hard
        guarantee). A producer that requires all samples on-air
        MUST check the return value and treat any shortfall as a
        loud failure -- a partial DSSS frame is worse than no frame
        because the despreader will sync on garbage.
        """
        ...

    def capabilities(self) -> TransmitterCapabilities:
        """Describe what this live TX device can do (see ``TransmitterCapabilities``)."""
        ...

    def close(self) -> None:
        """Release the TX device. Safe to call multiple times; safe in teardown."""
        ...


@runtime_checkable
class CoherentTransmitter(Transmitter, Protocol):
    """A sink for phase-coherent *multi-channel* IQ. Reserved for future TX beamforming.

    Extends ``Transmitter``: a ``CoherentTransmitter`` is also a
    valid single-channel ``Transmitter`` (channel 0), symmetric to
    ``CoherentReceiver`` vs ``Receiver``. Adds the coherent write
    that future transmit-beamforming / null-steering-on-TX features
    will depend on.

    v1.3.0 DSSS does NOT need this -- one transmit chain is enough
    for BPSK. The Protocol is present in 1.3.0 so the contract
    surface is symmetric (every RX Protocol has a TX twin); the
    implementation is deferred. A ``CoherentTransmitter``
    implementation does not yet ship with v1.3.0; declaring this
    capability is a no-op until a future ADR adds the operational
    semantics.

    Added in SCHEMA_VERSION 1.3.0 (ADR-025).
    """

    def write_coherent(self, iq: CoherentIQBlock) -> int:
        """Send ``(n_channels, n_samples)`` complex64 coherent IQ; return samples per channel.

        Same blocking / no-silent-short-write contract as
        ``Transmitter.write``. Channels are phase-coherent to the
        extent the last RX-side ``calibrate()`` on the matching
        ``CoherentReceiver`` (paired device) established -- transmit
        beamforming reuses the receive calibration via reciprocity.
        Calling this with an uncalibrated coherent stream is allowed
        but the result is incoherent across channels and any
        beamforming intent is lost.
        """
        ...


@runtime_checkable
class BearingEstimator(Protocol):
    """Turns IQ into a ``BearingReport``. The contract DSP exposes to the node.

    This is the boundary between the DSP workstream and the node runtime. An
    estimator encapsulates one method -- an L1 amplitude-sweep estimator, an L2
    MUSIC estimator -- behind a uniform call. The node runtime owns one or more
    estimators (per the node's active capabilities), feeds them samples, and
    ships whatever ``BearingReport`` s they return.

    The node runtime depends on this Protocol; it does not depend on the DSP
    workstream's internal module layout. The DSP workstream is free to
    restructure MUSIC, Capon, peak-finding, smoothing -- anything -- as long as
    its estimators still satisfy this shape.

    ``method`` advertises which ``Capability`` this estimator implements, so
    the node runtime can match estimators to the node's active capabilities
    without hard-coding classes.
    """

    @property
    def method(self) -> Capability:
        """Which capability this estimator implements (L1_RSSI or L2_MUSIC)."""
        ...

    def estimate(self, samples: IQBlock | CoherentIQBlock) -> BearingReport | None:
        """Produce a ``BearingReport`` from a block of IQ, or ``None``.

        ``None`` is the honest 'no usable bearing this block' answer -- emitter
        below detection threshold, pseudospectrum too flat to peak-pick, an
        uncalibrated coherent input. ``None`` is *not* an error; it is a valid,
        expected outcome that the node runtime simply does not forward. What an
        estimator must never do is fabricate a confident bearing from nothing
        (Invariant 4).

        An L1 estimator expects a 1-D ``IQBlock``; an L2 estimator expects a
        2-D ``CoherentIQBlock``. Passing the wrong shape is a programming
        error and the estimator raises.
        """
        ...


@runtime_checkable
class Fuser(Protocol):
    """Cross-fixes ``BearingReport`` s into a ``FixEvent``. The fusion contract.

    The boundary between the fusion workstream and the node runtime / ops
    layer. A ``Fuser`` takes a batch of bearings (already time-windowed by the
    caller) plus the ``FusionConfig`` and returns a ``FixEvent`` -- or ``None``
    if it cannot responsibly produce one.

    The fusion workstream's internals -- Stansfield seed, MLE refinement, GDOP
    gating, outlier rejection -- are entirely its own; only this shape is
    frozen. Note the ``Fuser`` is indifferent to *how many* bearings it gets
    (Axis 3) and to *how* each was produced (Axis 1/2): a batch is just an
    iterable of the frozen ``BearingReport`` type, each carrying its own
    ``azimuth_sigma_deg`` weight and ``method`` tag.
    """

    def fuse(
        self,
        bearings: Iterable[BearingReport],
        config: FusionConfig,
    ) -> FixEvent | None:
        """Cross-fix a batch of time-windowed bearings into a ``FixEvent``.

        Returns ``None`` -- not a fabricated fix -- when it cannot responsibly
        solve: fewer than ``config.min_bearings_for_fix`` bearings, or a
        geometry so degenerate that even the ``fallback_centroid`` method is
        not defensible. A returned ``FixEvent`` always carries its own honesty
        payload (covariance, ellipse, residuals, GDOP, ``confidence_level``)
        so a weak-but-real fix is *labelled* weak, not withheld -- withholding
        is reserved for 'cannot solve at all'.
        """
        ...


@runtime_checkable
class CotPublisher(Protocol):
    """Publishes a ``FixEvent`` as Cursor-on-Target. The CoT output contract.

    The boundary between the CoT workstream and the fusion/ops layer.
    Implemented by the CoT workstream: serialise a ``FixEvent`` to CoT XML (a
    hostile-emitter marker plus an ellipse polygon for
    ``confidence_ellipse_95``) and ship it to a TAK server / ATAK clients via
    PyTAK.

    Kept deliberately tiny -- one method -- because everything interesting (XML
    schema, marker type strings, transport, FreeTAKServer quirks) is the CoT
    workstream's internal concern and must stay changeable without a contract
    bump.
    """

    def publish(self, fix: FixEvent) -> None:
        """Serialise ``fix`` to CoT and transmit it to the configured TAK endpoint.

        Synchronous from the caller's view. Transport ret/queueing is the
        implementation's business. A transmit failure raises -- the ops layer
        decides whether to log-and-continue or surface it; the publisher does
        not silently swallow it.
        """
        ...


@runtime_checkable
class Bearer(Protocol):
    """Carries messages between a node and the fusion server. The transport contract.

    The boundary inside the node-runtime workstream between 'produce messages'
    and 'move bytes'. One Protocol, several implementations: a Wi-Fi
    (UDP+msgpack) bearer, a LoRa (compressed) bearer, and a composite 'both'
    bearer. DSP and fusion never see this -- it exists in the contract only so
    the node-runtime's producer side and transport side can be built and
    tested against each other independently.

    The send side is split by message type rather than taking a union,
    because the LoRa bearer treats them differently: a ``BearingReport`` may
    be heavily compressed (debug payload dropped) while a ``NodeStatus`` is
    already tiny. The receive side is the fusion server's inbound half.
    """

    def send_bearing(self, report: BearingReport) -> None:
        """Transmit one ``BearingReport`` toward the fusion server.

        May compress / drop optional fields (notably ``raw_pseudospectrum``)
        according to the bearer kind -- a LoRa bearer must, a Wi-Fi bearer need
        not. What it must not do is alter a *semantic* field; the bearing that
        arrives is the bearing that was measured.
        """
        ...

    def send_status(self, status: NodeStatus) -> None:
        """Transmit one ``NodeStatus`` heartbeat toward the fusion server."""
        ...

    def receive(self) -> Sequence[BearingReport | NodeStatus]:
        """Return messages received since the last call (fusion-server side).

        Possibly empty. The composite 'both' bearer de-duplicates by
        ``(node_id, t_unix_ns)`` so a message arriving on both Wi-Fi and LoRa
        is delivered once. Ordering is best-effort, not guaranteed -- the
        fusion server time-windows by ``t_unix_ns`` regardless of arrival
        order.
        """
        ...

    def close(self) -> None:
        """Release transport resources (sockets, serial ports). Idempotent-friendly."""
        ...
