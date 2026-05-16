"""Deterministic per-stream RNG manager for the simulator.

One integer seed in -> one fully reproducible sequence of IQ blocks out.
The manager wraps ``numpy.random.SeedSequence`` and exposes a dedicated
``numpy.random.Generator`` per stochastic stream (noise, per-emitter
jitter, per-channel phase error, ...). Streams are spawned from a single
root sequence at deterministic indices so adding a stream in a later
ticket never perturbs the seed of any pre-existing stream -- a property
that lets golden-file tests stay valid across simulator-feature growth.

``reseed(s)`` rebuilds the root from a fresh seed and re-spawns every
stream. This is the mechanism that the
``test_reseed_changes_noise_keeps_signal`` test relies on: only the
stochastic streams change, the deterministic emitter geometry (heading,
range, antenna pattern, sample-offset counter, frequencies) is untouched.

WS-A-001 ships only one active stream (single-channel complex AWGN).
Subsequent tickets that introduce phase jitter, multipath shadowing, or
coherent per-channel noise consume reserved indices below the noise one.
"""

from __future__ import annotations

import numpy as np


class RngManager:
    """Spawns and owns the simulator's stochastic-stream Generators.

    The class is intentionally tiny: it does no IQ math, only seed bookkeeping.
    """

    # Number of streams reserved up front so later tickets can add streams
    # without perturbing the seed of any earlier-numbered stream.
    # SeedSequence.spawn(n) returns children with spawn_keys [(0,), ..., (n-1,)]
    # so children[i] for any i < n is identical regardless of how large n is.
    # Growing this number is safe; reassigning indices below is not.
    _N_RESERVED_STREAMS = 16
    # Index assignments. Reserved indices may be added in future tickets;
    # NEVER renumber an assigned index without a SCHEMA_VERSION-equivalent
    # break in the determinism tests.
    _NOISE_INDEX = 0
    # 1: reserved for per-emitter phase jitter (unused at WS-A-003).
    # WS-A-003: stochastic channel impairments (LogNormalShadowing and any
    # future fading model) draw from this stream. FreeSpaceChannel /
    # TwoRayGroundChannel / MultipathFIRChannel do not draw.
    _SHADOWING_INDEX = 2
    # WS-A-002: the calibration handshake renders a noise reference on a
    # dedicated stream so a calibrate() call does not perturb the main
    # noise stream's state (preserving read/read_coherent reproducibility
    # across a calibration event).
    _CAL_NOISE_INDEX = 3
    # 4..7: reserved for coherent per-channel noise (unused at WS-A-003).
    # WS-A-003: stochastic receiver impairments (phase noise, future) draw
    # from this stream. IQImbalance / DCOffset / ADCQuantization do not.
    _RECEIVER_IMPAIRMENTS_INDEX = 8
    # 9..15: reserved.

    def __init__(self, seed: int = 0) -> None:
        """Initialise from a single integer seed.

        Two ``RngManager(seed=s)`` instances produce byte-identical Generator
        outputs in the same call order, on the same numpy build. That is the
        property the protocol-conformance and determinism tests rely on.
        """
        self._seed = seed
        self._spawn_streams()

    def _spawn_streams(self) -> None:
        """(Re)build every stream's Generator from the current root seed."""
        root = np.random.SeedSequence(self._seed)
        children = root.spawn(self._N_RESERVED_STREAMS)
        self._noise_gen = np.random.default_rng(children[self._NOISE_INDEX])
        self._cal_noise_gen = np.random.default_rng(children[self._CAL_NOISE_INDEX])
        self._shadowing_gen = np.random.default_rng(children[self._SHADOWING_INDEX])
        self._receiver_impairments_gen = np.random.default_rng(
            children[self._RECEIVER_IMPAIRMENTS_INDEX]
        )

    def reseed(self, seed: int) -> None:
        """Reset the root seed and re-spawn every stream.

        Deterministic geometry (heading, scenario, sample-offset counter) is
        untouched -- those live on the Receiver, not on the RngManager.
        """
        self._seed = seed
        self._spawn_streams()

    @property
    def noise(self) -> np.random.Generator:
        """The complex-AWGN Generator (the only active stream in WS-A-001)."""
        return self._noise_gen

    @property
    def cal_noise(self) -> np.random.Generator:
        """The calibration-handshake reference-noise Generator (WS-A-002).

        Held on its own stream so ``CoherentReceiver.calibrate()`` does not
        consume samples from the main ``noise`` stream -- meaning a calibrate
        call between two ``read_coherent`` calls does not change the IQ that
        those reads would have produced without it.
        """
        return self._cal_noise_gen

    @property
    def shadowing(self) -> np.random.Generator:
        """The channel-side stochastic-impairment Generator (WS-A-003).

        Held on its own stream so adding a stochastic channel (e.g.
        ``LogNormalShadowing``) to a scenario does not perturb the
        ``noise`` or ``cal_noise`` reproducibility of pre-existing tests.
        Deterministic channels (Free-space, Two-ray, Multipath-FIR) accept
        and ignore this Generator.
        """
        return self._shadowing_gen

    @property
    def receiver_impairments(self) -> np.random.Generator:
        """The receiver-side analog/ADC impairments Generator (WS-A-003).

        Held on its own stream for the same reason ``shadowing`` is --
        adding a stochastic receiver impairment in a future ticket must
        not perturb the existing noise streams. Deterministic impairments
        (IQImbalance, DCOffset, ADCQuantization) accept and ignore it.
        """
        return self._receiver_impairments_gen

    @property
    def seed(self) -> int:
        """The current root seed, exposed for diagnostics and test assertions."""
        return self._seed
