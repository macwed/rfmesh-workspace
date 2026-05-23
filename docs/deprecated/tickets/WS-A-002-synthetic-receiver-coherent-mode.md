# TICKET WS-A-002: Extend SyntheticReceiver with coherent multi-channel mode against CoherentReceiver Protocol

## Goal (one sentence)

Extend the `SyntheticReceiver` from WS-A-001 with a coherent
multi-channel mode that structurally satisfies
`rfmesh_contracts.protocols.CoherentReceiver` — emitting sample-aligned,
phase-coherent IQ across N channels for a configured array geometry
(ULA / UCA / CUSTOM), with per-channel phase/gain offsets that a
`calibrate()` handshake recovers via a simulated wideband noise-source
reference — so Workstream B can begin building the L2 MUSIC/MVDR
estimator against ground-truth angles.

## Context (links only)

- Contracts touched (read-only):
  - `rfmesh_contracts.protocols.CoherentReceiver`
  - `rfmesh_contracts.protocols.CoherentIQBlock`
  - `rfmesh_contracts.protocols.Receiver` (parent Protocol — still implemented)
  - `rfmesh_contracts.config.ArrayConfig`
  - `rfmesh_contracts.config.NodeConfig`
  - `rfmesh_contracts.enums.ArrayGeometry`
- Architecture references: `ARCHITECTURE.md` §1 (L2 capability),
  §4 (simulator first-class); `INTERFACES.md` §5 (`CoherentReceiver`),
  §4 (`ArrayConfig`).
- Inherited context: `INHERITED_CONTEXT.md` §3.4 (Pluto+ delivery
  uncertain → coherent simulator is the L2 development path);
  §1.3 (no SDR is power-calibrated; calibration recovers
  *inter-channel* offsets, not absolute power).
- Prior tickets: **WS-A-001 must be merged** (this ticket extends
  `SyntheticReceiver`, `SimulationScenario`, and the test conftest).
- Blocks: WS-B-002 (L2 MUSIC estimator), WS-CD's L2-capability node
  runtime wiring tests.
- Coordination: ADR-004 (calibration file format) will be raised in
  parallel; this ticket implements `calibrate()` as
  **in-memory only**, no file persistence.

## Design decisions resolved for this ticket

Three architectural decisions were open after WS-A-001. They are
resolved here, prioritising robustness for forward-deployed nodes in
EW-contested environments (operator instruction).

### Decision 1 — Azimuth convention: array-local frame

The simulator's internal math operates in the array-local frame
(angles measured relative to the array boresight).
`set_antenna_heading(deg)` continues to accept geographic azimuth
(same as WS-A-001); the receiver converts internally:

    angle_array_local = wrap_pm180(emitter.azimuth_deg - heading_deg)

This relative angle drives both the antenna-pattern gain *and* the
steering-vector phase for each emitter. Consequence: WS-B's
`BearingEstimator` runs MUSIC in array-local frame, then converts to
geographic by adding `NodeConfig.heading_deg` exactly once at
`BearingReport` emission time.

**Why this and not the alternative.** The alternative is "simulator
produces geographic IQ; DSP works in geographic frame throughout".
That requires the simulator to bake `heading_deg` into every steering
vector, and forces the DSP to carry the heading through every layer
of MUSIC math. In a forward-deployed node where heading can update
faster than DSP latency (e.g. servo-controlled mast), a single
conversion point (BearingEstimator output) is one place to debug;
two distributed conversion points are an EW-debug nightmare. The L1
path in WS-A-001 already operates this way — staying consistent.

### Decision 2 — Calibration reference: simulated wideband noise injection

`calibrate()` simulates the operator triggering a built-in noise
source that injects wideband AWGN at all RX channels simultaneously,
with identity cross-channel coupling (i.e., a single noise
realisation broadcast to every channel through known internal
splitter geometry). The simulator measures the recovered cross-channel
phase/gain offsets after the scenario's injected per-channel
impairments, computes the inverse, and stores them in memory.
Subsequent `read_coherent` calls apply this inverse so the output is
phase-aligned to channel 0.

**Why not a pilot tone.** Three reasons specific to EW-contested
deployment:

1. **Self-contained calibration.** A noise-source handshake needs
   only the internal RF switch + terminator; a pilot-tone handshake
   needs an external signal generator and an RF distributor network.
   Each external piece is a failure point and an attack surface.

2. **No exploitable signature.** A pilot tone at a known frequency
   is a pattern an EW adversary can sense and selectively jam at
   exactly the calibration window. Wideband noise has no
   characteristic frequency to attack.

3. **Reference hardware precedent.** KrakenSDR — the production-grade
   5-channel coherent SDR most similar in spirit to our Pluto+
   target — uses a built-in noise source by design, for the same
   reasons. Following an established practice, not inventing one.

The simulator's API surface stays abstract over this — a future
`BladeRFCoherentReceiver` that uses a different physical mechanism
still implements `calibrate()` with the same Protocol-level
semantics. Whether bladeRF / Pluto+ in particular ships with a
built-in noise source is a hardware question outside this ticket's
scope.

### Decision 3 — UCA front/back un-ambiguity is tested explicitly

`INTERFACES.md` §1 documents that ULA has front/back ambiguity and UCA
does not. The simulator must produce both behaviours correctly. The
test enforces this *by simulation*, not by assertion: emit IQ at θ
and at (180° − θ), and verify that the recovered cross-channel
phase patterns are *identical* under ULA (ambiguous) and
*distinguishable* under UCA (unambiguous). This is the simulator's
honesty smoke test for array geometry — and a sanity check for
WS-B's later MUSIC code, which will rely on this distinction.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-sdr -v` passes; **all WS-A-001
   tests continue to pass** (regression-free); the following tests
   are added and pass:

   a. `tests/test_coherent_protocol_conformance.py::test_synthetic_receiver_is_coherent_receiver`
      — `isinstance(SyntheticReceiver(scenario_with_array), CoherentReceiver)`
      is True. The same instance is `isinstance(..., Receiver)` too
      (Receiver is a parent Protocol).

   b. `tests/test_coherent_protocol_conformance.py::test_l1_capability_preserved`
      — A scenario with `array=None` produces a `SyntheticReceiver`
      that is a `Receiver` but **not** a `CoherentReceiver`. Capabilities
      still report `n_coherent_channels == 1`.

   c. `tests/test_coherent_read.py::test_read_coherent_shape_and_dtype`
      — `read_coherent(n)` returns a NumPy array of shape `(n_channels, n)`
      and dtype `complex64` for n in {1024, 8192}. Same no-silent-short-read
      contract: returns exactly that shape or raises.

   d. `tests/test_coherent_read.py::test_read_single_channel_returns_channel_zero`
      — On a coherent instance, `read(n)` returns a 1-D array equal
      to row 0 of `read_coherent(n)` for the same RNG state. (Verify
      by calling `read(n)`, then constructing a fresh receiver with
      the same seed, opening, and reading `read_coherent(n)[0, :]`;
      arrays match byte-wise.)

   e. `tests/test_steering_ula.py::test_ula_phase_pattern_matches_closed_form`
      — A 4-element ULA at d = λ/2, emitter at θ_array_local = 35°,
      CW at the centre frequency, 30 dB SNR over n = 32768 samples.
      Cross-channel phase measurement (channel i+1 vs channel i,
      averaged over n samples). Expected:
      `Δφ = -2π · (d/λ) · sin(θ_array_local)` (sign per receive-array
      convention — incoming plane wave). Measured Δφ matches expected
      within ±2° (0.035 rad).

   f. `tests/test_steering_uca.py::test_uca_phase_pattern_matches_closed_form`
      — An 8-element UCA of radius 0.5·λ, emitter at θ_array_local = 137°,
      30 dB SNR, n = 32768. Element i at ring angle α_i; expected
      relative phase
      `φ_i = -2π · (r/λ) · cos(θ_array_local − α_i)`, measured
      against the ring centre. Measured per-element phase matches
      expected mod 2π within ±2°.

   g. `tests/test_steering_custom.py::test_custom_layout_matches_closed_form`
      — A CUSTOM 4-element non-symmetric layout (e.g. (0,0), (0.1,0),
      (0.0,0.15), (0.13,0.07) metres at 915 MHz), emitter at known
      array-local angle. Closed-form steering vector applied
      element-by-element; measured phase pattern matches within ±2°.

   h. `tests/test_geometry_honesty.py::test_ula_front_back_ambiguity_and_uca_unambiguity`
      — Two scenarios identical except for emitter array-local
      azimuth (θ vs 180° − θ for θ = 35°). For ULA, the measured
      cross-channel phase patterns are equal modulo numerical noise
      (this is the failure mode the algorithm must accept, not a
      bug). For UCA, the two scenarios produce measurably distinct
      phase patterns (proof of un-ambiguity). The test asserts both
      simultaneously.

   i. `tests/test_calibration.py::test_is_calibrated_lifecycle`
      — After construction: `receiver.is_calibrated is False`. After
      `calibrate()`: True. After `configure(node_config)` (simulates
      a retune): back to False.

   j. `tests/test_calibration.py::test_calibrate_recovers_injected_offsets`
      — A scenario carries explicit per-channel complex offsets
      (e.g. `[1.0+0j, 0.95·exp(j·0.4), 1.1·exp(j·-0.25), 1.02·exp(j·0.18)]`)
      injected during render. Run `calibrate()` (which performs the
      simulated noise-injection handshake). Then `read_coherent(n)`
      on a CW emitter at boresight. After calibration, the recovered
      inter-channel ratios match identity (1.0 + 0j on every channel
      pair) within 0.1 dB amplitude and ±1° phase at 30 dB SNR over
      n = 32768.

   k. `tests/test_calibration.py::test_uncalibrated_read_is_allowed_but_flagged`
      — On an uncalibrated coherent receiver, `read_coherent(n)`
      returns data without raising — but `is_calibrated` is False.
      The receiver does **not** silently calibrate or refuse; per
      the contract docstring the L2 estimator is the one to refuse
      to emit on uncalibrated input. The simulator stays honest about
      state. This is the Invariant 4 surface.

   l. `tests/test_calibration.py::test_calibrate_insufficient_snr_raises`
      — A scenario configured so the simulated noise-injection
      reference produces SNR below a configurable threshold (e.g.
      reference power lower than the noise floor by 3 dB).
      `calibrate()` raises `CalibrationFailedError`; `is_calibrated`
      remains False (no silent partial-success).

2. `uv run mypy packages/rfmesh-sdr` clean (strict).
3. `uv run ruff check packages/rfmesh-sdr` clean.
4. `uv run ruff format --check packages/rfmesh-sdr` clean.
5. No imports from `rfmesh_dsp`, `rfmesh_node`, `rfmesh_fusion`,
   `rfmesh_cot`, `rfmesh_ml`, `rfmesh_servo`, `rfmesh_ops`. (Invariant 2.)
6. No hardware-library imports.
7. No new runtime dependencies. NumPy only.

## Out of scope (explicit non-goals)

- Persisting the calibration to disk (i.e. realising
  `ArrayConfig.calibration_file`). Depends on ADR-004; expose
  `calibrate()` as in-memory only.
- Channel impairments beyond what WS-A-001 already supports
  (free-space + AWGN). Multipath, two-ray, log-normal shadowing,
  IQ imbalance, ADC quantisation stay in WS-A-003.
- Real `BladeRFCoherentReceiver` or `PlutoCoherentReceiver` — later
  tickets.
- The MUSIC algorithm itself — WS-B's scope.
- Modulations beyond CW. (LoRa-chirp emitters: later, driven by
  L3 classifier needs.)
- Mutating `packages/rfmesh-contracts/**`. (Invariant 1.)

## Files you may touch

- `packages/rfmesh-sdr/src/rfmesh_sdr/exceptions.py`                (modify — add `CalibrationFailedError`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/__init__.py`                  (modify — export new public symbols)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/__init__.py`        (modify — export `ArraySpec`, `ChannelImpairments`, `Calibration`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/scenario.py`        (modify — add optional `array: ArraySpec | None = None` field + optional `impairments: ChannelImpairments | None = None`, validators in `__post_init__`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/synthetic_receiver.py`  (modify — add coherent path: `read_coherent`, `calibrate`, `is_calibrated`, coherent `_render_block`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/array.py`           (create — `ArraySpec` frozen dataclass with `.ula(...)`, `.uca(...)`, `.custom(...)` constructors and the steering-vector function)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/impairments.py`     (create — `ChannelImpairments` frozen dataclass: per-channel complex offsets)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/calibration.py`     (create — `Calibration` immutable record holding the recovered inverse offsets, plus `apply_to_block` helper)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/capabilities.py`    (modify — `n_coherent_channels` reflects `array.n_elements`)
- `packages/rfmesh-sdr/tests/conftest.py`                           (modify — add `ula_scenario`, `uca_scenario`, `custom_scenario`, `impaired_scenario` fixtures alongside the existing `default_scenario`)
- `packages/rfmesh-sdr/tests/test_coherent_protocol_conformance.py` (create)
- `packages/rfmesh-sdr/tests/test_coherent_read.py`                 (create)
- `packages/rfmesh-sdr/tests/test_steering_ula.py`                  (create)
- `packages/rfmesh-sdr/tests/test_steering_uca.py`                  (create)
- `packages/rfmesh-sdr/tests/test_steering_custom.py`               (create)
- `packages/rfmesh-sdr/tests/test_geometry_honesty.py`              (create)
- `packages/rfmesh-sdr/tests/test_calibration.py`                   (create)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant 1)
- Any other workstream's package (Invariant 2)
- WS-A-001's existing test files. Regression-free: add new test
  modules. If a WS-A-001 test starts failing because of a change you
  made, STOP and surface — do not paper over.

## Design hints (non-binding)

### `ArraySpec` shape

A frozen dataclass internal to `rfmesh_sdr.simulator` (not in
contracts). Holds: `geometry` (a member of
`rfmesh_contracts.enums.ArrayGeometry`), `n_elements: int`,
`element_positions_m: np.ndarray` of shape `(n_elements, 2)` (x, y in
the array's local frame, channel 0 at origin by convention).
`__post_init__` validates n_elements >= 2, positions array shape,
geometry-vs-positions consistency (a ULA has y == 0 for all elements,
a UCA's elements lie on a circle of constant radius). Convenience
factories:

```python
ArraySpec.ula(n_elements: int, spacing_m: float) -> ArraySpec
ArraySpec.uca(n_elements: int, radius_m: float) -> ArraySpec
ArraySpec.custom(positions_xy_m: np.ndarray) -> ArraySpec
```

### `ChannelImpairments` shape

A frozen dataclass holding a single field for this ticket:
`channel_offsets: np.ndarray` of shape `(n_elements,)` and dtype
`complex128` — the per-channel complex offsets injected on render
(simulating cable phase mismatches, gain stage variation, etc.).
Validator: shape matches `array.n_elements`, channel 0 is exactly
`1+0j` (the reference channel by convention).

### Render path for coherent mode

For each emitter e:

    delta_local = wrap_pm180(e.azimuth_deg - heading_deg)
    g_lin = antenna.gain_linear(delta_local)
    a_tx  = 10**(e.tx_power_db / 20)
    base = channel.apply(  # FreeSpace for now
        a_tx * sqrt(g_lin) * exp(j*(2*pi*f_offset*t + radians(e.phase_deg))),
        e.range_m, e.frequency_hz, rng,
    )

    wavelength = c / e.frequency_hz
    k_hat = [cos(delta_local), sin(delta_local)]   # incoming-wave direction
    for i in range(n_elements):
        path_delta_i = dot(element_positions[i, :], k_hat)
        phase_i = -2*pi * (path_delta_i / wavelength)
        offset_i = (impairments.channel_offsets[i]
                    if impairments else 1+0j)
        applied_i = (calibration.complex_offsets[i] if is_calibrated
                     else 1+0j)
        per_channel[i, :] += base * exp(j * phase_i) * offset_i * applied_i

    noise_per_channel = complex_awgn_2d(rng, n_elements, n,
                                        scenario.noise_floor_dbfs)
    out = (per_channel + noise_per_channel).astype(complex64)

The wavelength is computed from `e.frequency_hz` (per-emitter), not
`scenario.center_freq_hz`. The L1 path glossed over this because
heading-sweep peaks don't depend on the f_emitter vs f_center
distinction; coherent mode is the first place this matters.

### `calibrate()` semantics in the simulator

`calibrate()` is the simulated noise-source handshake:

1. Refuse if the scenario does not declare a calibration-reference
   SNR (default: 30 dB above noise floor); otherwise compute the
   reference amplitude from that SNR.
2. Internally render a "noise injection" period: a fictitious
   wideband noise source broadcast identically to all channels
   through the array's *internal* RF path — crucially, the
   per-channel impairments **are** applied (impairments live
   between the noise source and the ADC, by design — the offsets
   are the very thing calibration is meant to measure).
3. Measure the cross-channel offsets present in the rendered noise
   reference, using channel 0 as the phase/gain reference. The
   measurement is the per-channel complex correlation
   `R_i = <x_i · conj(x_0)>`, normalised: `offset_i_measured = R_i /
   abs(R_i)` for phase, `abs(R_i) / R_00` for gain.
4. Compute the inverse: `calibration.complex_offsets[i] =
   1 / measured_offset_i`. Channel 0 is exactly `1+0j`.
5. Validate the recovered reference SNR is above threshold; raise
   `CalibrationFailedError` otherwise (with a message that names the
   measured-vs-required SNR).
6. Store `Calibration(...)` on the receiver; flip `is_calibrated`
   to True.

A retune (`configure()`) in this minimal model flips `is_calibrated`
back to False *without* changing the offsets (because impairments
are scenario-static). This is the honest Protocol behaviour: the
receiver doesn't claim to remember a calibration valid for a
different tuning.

### Capability surface change

After `ArraySpec` is added to `SimulationScenario`:

- If `array is None`: `n_coherent_channels = 1`,
  `isinstance(..., CoherentReceiver) is False`. Single-channel
  receiver — same as WS-A-001's behaviour.
- If `array is not None`: `n_coherent_channels = array.n_elements`,
  `read_coherent`, `calibrate`, and `is_calibrated` exist,
  `isinstance(..., CoherentReceiver) is True`.

`CoherentReceiver` extends `Receiver`, so the coherent instance
must continue to expose `read()` returning a single channel.
Implementation: when `read()` is called on a coherent instance, it
renders the full coherent block internally and returns row 0. This
is the behaviour test 1.d checks.

### Conftest fixtures (a hint for the agent — adjust if simpler shapes work)

```python
@pytest.fixture
def ula_scenario() -> SimulationScenario:
    """4-element ULA at d = lambda/2 for 915 MHz; one CW emitter at 35 deg."""
    ...

@pytest.fixture
def uca_scenario() -> SimulationScenario:
    """8-element UCA at r = 0.5 · lambda for 915 MHz; one CW emitter at 137 deg."""
    ...

@pytest.fixture
def custom_scenario() -> SimulationScenario:
    """4-element non-symmetric custom layout for 915 MHz."""
    ...

@pytest.fixture
def impaired_ula_scenario(ula_scenario: SimulationScenario) -> SimulationScenario:
    """ULA scenario with injected per-channel complex offsets for calibration tests."""
    ...
```

## Stop conditions

- Stop after producing the diff. Do not auto-commit.
- Paste pytest + mypy + ruff output when reporting back.
- If you find that satisfying `CoherentReceiver` cleanly requires a
  contract change (e.g. unclear method signature, missing capability
  flag, ambiguous semantics), STOP and write
  `docs/adr/ADR-NNN-<short>.md` with status `PROPOSED`. Do not
  proceed.
- If a steering-vector test fails for a physics reason (recovered
  Δφ ≠ closed-form Δφ beyond tolerance at 30 dB SNR), STOP and
  surface in `.claude/scratchpad/ws-a-<date>.md`. This is the
  honesty failure mode the architecture exists to detect.
- If you find yourself needing a new runtime dependency, STOP and
  surface. Same for scipy — if a numerically thorny step needs
  scipy, surface; do not just add it. NumPy's `np.linalg`,
  `np.fft`, `np.random` should cover this ticket.

## Provenance note

The three architectural decisions documented above (azimuth in
array-local frame, simulated noise-source calibration, explicit
front/back honesty test) were resolved by the WS-A architect
(Opus-A), optimised for forward-deployed reliability in EW-contested
environments per operator instruction. WS-B inherits these as fait
accompli; if WS-B's L2 MUSIC work finds a reason to revisit any of
them, surface via ADR rather than negotiating in tickets.
