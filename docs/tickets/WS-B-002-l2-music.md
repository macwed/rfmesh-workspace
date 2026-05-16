# TICKET WS-B-003: L2 MUSIC bearing estimator with honest sigma

## Goal (one sentence)
Implement a MUSIC-based L2 `BearingEstimator` in `rfmesh-dsp` that consumes
a coherent IQ block from `CoherentReceiver.read_coherent()`, eigendecomposes
the sample covariance, scans the MUSIC pseudospectrum across azimuth, and
emits a `BearingReport` with `method=Capability.L2_MUSIC` and an honest
`azimuth_sigma_deg` validated against Monte-Carlo ground truth.

## Context (links only, not content)
- Contracts (read-only):
  `rfmesh_contracts.protocols.BearingEstimator`,
  `rfmesh_contracts.protocols.CoherentReceiver`,
  `rfmesh_contracts.protocols.CoherentIQBlock`,
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.enums.Capability` (L2_MUSIC),
  `rfmesh_contracts.enums.ArrayGeometry`,
  `rfmesh_contracts.config.ArrayConfig`.
- Architecture references: ARCHITECTURE.md §1 (L2 capability),
  §2 (Axis 1 absorbed via the sigma-weighted BearingReport).
- Interface references: INTERFACES.md §3 BearingReport (honesty of
  azimuth_sigma_deg is load-bearing — same constraint as L1),
  §5 CoherentReceiver (is_calibrated is the load-bearing gate).
- Inherited context: INHERITED_CONTEXT.md §1.3 (no SDR is power-calibrated;
  outputs are dBFS/SNR, never dBm).
- DSP modules already merged that this ticket consumes:
  `rfmesh_dsp.array_manifold.steering_vector`,
  `rfmesh_dsp.array_manifold.steering_matrix`,
  `rfmesh_dsp.array_covariance.sample_covariance`,
  `rfmesh_dsp.array_covariance.forward_backward_smooth`.
- WS-A simulator API (tests only — NOT imported by package code):
  `rfmesh_sdr.SyntheticReceiver` in coherent mode (returned automatically
  when `SimulationScenario.array is not None`),
  `rfmesh_sdr.ArraySpec.ula(...)` and `.uca(...)`,
  `rfmesh_sdr.SimulationScenario`,
  `rfmesh_sdr.EmitterSpec`.
- ADR-004 (calibration file format): out of scope for this ticket. L2 MUSIC
  consumes already-calibrated coherent IQ. The simulator's `calibrate()`
  handshake is in-memory and runs in test setup before any `read_coherent`.

## Design — read before coding

### The MUSIC algorithm in 6 lines
Given a coherent IQ block `X` of shape `(N, T)` (N channels, T samples) from
a `CoherentReceiver` whose `is_calibrated` is True:

1. `R = sample_covariance(X)` — Hermitian (N, N) complex128.
2. (optional) `R = forward_backward_smooth(R)` — if the constructor flag
   says so; default False.
3. Eigendecompose `R = U diag(lambda) U^H` with `np.linalg.eigh` (Hermitian
   eigensolver guarantees real eigenvalues and orthogonal eigenvectors).
4. Sort eigenvalues descending, eigenvectors with them. Take the smallest
   `N - n_sources` eigenvectors as the noise-subspace basis `E_n` of shape
   `(N, N - n_sources)`. v0.1 fixes `n_sources = 1`; defer source-count
   detection (AIC / MDL) to a future ticket.
5. For each scan azimuth `theta_k` on a coarse grid:
   `P(theta_k) = 1 / || E_n^H a(theta_k) ||^2`
   where `a` is `steering_vector(...)` from `array_manifold`.
6. argmax of `P` → coarse peak; parabolic fit on `1/P` (NOT log P) over 5-7
   points around the argmax → sub-grid fractional peak `theta_hat`.

### Why `1/P`, not `log P`
Near the true source angle `theta*`:
`||E_n^H a(theta)||^2 ≈ (theta - theta*)^2 · ||E_n^H d(theta*)||^2`
where `d = da/dtheta`. So `1/P(theta)` is *exactly* parabolic locally
(degree 2 in theta near the peak). `log P` has a logarithmic singularity at
`theta*` and is poorly modelled by a parabola. The parabolic fit on `1/P`
gives a sub-grid theta_hat AND a fit-driven uncertainty in one shot — same
recipe as L1's parabolic peak fit on RSSI(heading).

### The scan grid
Coarse: 0.5° step over [0, 360) → 720 points. UCA gives full 360°
coverage; ULA has a front-back ambiguity (theta and π - theta are
indistinguishable). For v0.1, the scan covers [0, 360) regardless; for
ULA the duplicate peak is documented as a known limitation. The bearing
returned is the one in the half-space the runtime narrows to via prior
information — that is the node-runtime's problem, not the estimator's.
For tests (UCA only), no ambiguity.

### Sigma recipe
Same pattern as WS-B-001's L1:

1. `polyfit(x, 1/P, 2, cov=True)` over the 5-7 points around the coarse
   argmax, where x is the scan-angle offset from argmax in degrees.
2. Vertex `x_v = -c1 / (2*c2)`. Sub-grid peak = argmax_heading + x_v.
3. `Var(x_v)` propagated via the Jacobian of the vertex w.r.t. the fit
   coefficients, using the polyfit-returned covariance — IDENTICAL formula
   to L1 (see `l1.py` docstring step 7).
4. **Empirical chi-square / variance-floor correction.** L1 found a 1/0.8392
   median-bias correction works for its 7-point fit. MUSIC pseudospectrum
   residuals have a DIFFERENT statistical distribution than L1's RSSI
   residuals (MUSIC's 1/P at off-peak grid points is dominated by the
   subspace-projection noise floor, which is non-Gaussian for small N).
   Calibrate the constant empirically against the honesty test: run the
   golden_generator with several candidate constants and pick the one that
   makes median(claimed sigma) hit empirical sigma within +/-5 % at SNR=20 dB.
   Document the chosen constant and the calibration procedure in the
   `l2_music.py` module docstring. If no constant brings honesty within
   +/-20 % across all three test SNRs, STOP and write a scratchpad note.

### Sweep-awareness vs MUSIC
Unlike L1, MUSIC does NOT sweep. One `read_coherent(N)` call yields one R
yields one bearing. The BearingEstimator Protocol's `estimate(samples)`
signature works directly: the runtime calls
`estimate(receiver.read_coherent(N))` and gets a BearingReport. But the
contract has no `t_unix_ns` channel either, just like L1. Resolution
without a contract change (Invariant 1):

```python
class L2MusicEstimator:
    method: Capability = Capability.L2_MUSIC

    def set_timestamp(self, t_unix_ns: int) -> None:
        """L2-runtime hook: stamp the bearing's instant of capture.
        Called by the node runtime immediately before estimate()."""

    def estimate(self, samples: IQBlock) -> BearingReport | None:
        """Protocol surface. samples is the (N, T) coherent block."""
```

The runtime calls `set_timestamp` then `estimate`. A Protocol-only consumer
(unaware of L2 specifics) calls just `estimate`, which raises if
`set_timestamp` was not called this cycle (don't silently fabricate "now").

### Calibration trust contract
The L2 estimator MUST refuse to emit a BearingReport on an uncalibrated
coherent stream (INTERFACES.md §5: "L2 DSP code refuses to emit bearings
from an uncalibrated coherent stream"). Two design options:

(a) The estimator takes a `CoherentReceiver` reference and reads
    `is_calibrated` itself.
(b) The estimator takes a boolean flag injected by the runtime and trusts it.

Pick (a). It puts the honesty guarantee at the source: the estimator's
constructor binds to the receiver, `estimate` checks `is_calibrated`
before fitting, returns None if False. The runtime does not need to be
trusted to plumb the flag correctly.

```python
class L2MusicEstimator:
    def __init__(
        self,
        *,
        node_id: str,
        node_position: GeodeticPosition,
        receiver: CoherentReceiver,        # constructor-injected
        array_config: ArrayConfig,         # geometry + element positions + wavelength
        operating_frequency_hz: float,     # for wavelength
        n_sources: int = 1,
        use_forward_backward: bool = False,
        scan_step_deg: float = 0.5,
    ) -> None: ...

    def estimate(self, samples: IQBlock) -> BearingReport | None:
        if not self._receiver.is_calibrated:
            return None  # Invariant 4: no silent bearings from uncalibrated IQ
        ...
```

`array_config` exposes `geometry`, `n_elements`, `element_spacing_m` (for
ULA/UCA) and the runtime can compute element_positions_m from those.
Better: have the estimator compute element_positions_m once at __init__
from the array_config. Element positions are FIXED in the array-local
frame; they don't change per estimate.

### Wavelength
`wavelength_m = constants.c / operating_frequency_hz` where
`constants.c = 299792458.0`. Use scipy.constants or a literal constant in
this module (NOT a new package dep).

### Failure modes (Invariant 4 surface)
`estimate` returns `None` (not a fabricated bearing) when:
- Receiver is not calibrated.
- The block has fewer than `n_sources + 1` channels (cannot define a
  non-empty noise subspace).
- The block has too few samples for a meaningful covariance estimate
  (require `T >= 4 * N` as a soft minimum; document this).
- The largest eigenvalue is not at least 3 dB above the mean of the
  smallest N - n_sources eigenvalues (no usable signal).
- The parabolic fit on 1/P fails (rank-deficient, non-finite cov, or
  curvature has the wrong sign).
- The fitted vertex lies outside the half-window around the argmax
  (extrapolation; the fit window did not cover the actual peak).
- The propagated Var(x_v) is non-positive or non-finite.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-dsp -v` passes, including:
   - `tests/test_l2_music.py::test_protocol_conformance`
     — `isinstance(estimator, BearingEstimator)` (runtime_checkable).
   - `tests/test_l2_music.py::test_method_is_l2_music`.
   - `tests/test_l2_music.py::test_peak_recovers_known_angle_uca` — UCA
     N=4, radius/lambda=0.25, single emitter at 137°, SNR=20 dB, 4096
     coherent samples; recovered azimuth within +/-1° of 137°. (Tighter
     than L1's +/-2° because MUSIC is supposed to outperform amplitude DF.)
   - `tests/test_l2_music.py::test_returns_none_on_uncalibrated_stream` —
     construct receiver, do NOT call `calibrate()`, call `estimate()`,
     expect None. This is the honesty gate.
   - `tests/test_l2_music.py::test_returns_none_on_low_snr` — emitter at
     -10 dB SNR; estimate returns None (eigenvalue ratio gate trips).
   - `tests/test_l2_music.py::test_set_timestamp_required` — calling
     estimate without prior set_timestamp raises RuntimeError. No "now"
     fallback.
   - `tests/test_l2_music.py::test_estimate_re_arms` — after one estimate,
     next set_timestamp/estimate cycle works on fresh state.

2. **Golden-file tests (Invariant 3).** `tests/golden/`:
   - `l2_music_pseudospectrum_137deg_uca4_20db.npz` — the (720,) MUSIC
     pseudospectrum P(theta) for the canonical UCA scenario at seed=42.
     Test recomputes and asserts max-abs-relative-diff <= 1e-9.
   - `l2_music_sigma_table.npz` — for each of {SNR=10, 20, 30} dB, the
     median claimed sigma over 200 trials. Test asserts <= 5 % relative
     difference. Marked slow.

3. **Sigma-honesty test (load-bearing).**
   `tests/test_l2_music_sigma_honesty.py::test_empirical_spread_matches_claimed_sigma`
   runs at SNR={10, 20, 30} dB, 200 trials each, using
   `receiver.reseed(i)` between trials. For each SNR:
     - empirical_std(recovered_azimuth) = sigma_emp
     - median(claimed_sigma) = sigma_claimed
     - assert `0.8 * sigma_claimed <= sigma_emp <= 1.2 * sigma_claimed`.
   If the band breaks, the sigma calibration constant (see Design "Sigma
   recipe") is wrong — re-calibrate, do NOT widen tolerance.

4. `uv run mypy packages/rfmesh-dsp` clean (strict).
5. `uv run ruff check packages/rfmesh-dsp` clean.
6. `uv run lint-imports` shows the "DSP is pure" contract still kept:
   `l2_music.py` imports ONLY numpy, scipy (for constants if used),
   `rfmesh_contracts`, and other `rfmesh_dsp` modules. NO import of
   `rfmesh_sdr` in `src/`. Tests in `tests/` may import `rfmesh_sdr`.

7. `__init__.py` re-exports `L2MusicEstimator`. List alphabetised.
   **Important for parallel WS-B-004:** insert in alphabetical position
   within `__all__`; do not touch lines you did not add. Identical
   discipline as WS-B-001/002.

## Out of scope

- AIC / MDL / Wax-Kailath source-count detection. v0.1 hardcodes
  `n_sources=1`. The constructor parameter exists so a future ticket can
  raise it without an API change.
- L2 MVDR (Capon). That is WS-B-004 (parallel ticket).
- Loading `ArrayCalibration` from `.npz/.json` files per ADR-004. That
  lives in `rfmesh_sdr` (the receiver implementations call it). MUSIC
  consumes already-calibrated IQ via the CoherentReceiver Protocol.
- Multi-source / multi-bearing emission. v0.1 emits at most one
  BearingReport per estimate call.
- ULA front-back ambiguity resolution. v0.1 returns the first peak;
  the runtime must narrow via prior info (out of scope here).
- Contract edits (Invariant 1). If you find a need, STOP and write
  `docs/adr/ADR-NNN-<short>.md` (status PROPOSED) instead.

## Files you may touch (create | modify)

- `packages/rfmesh-dsp/src/rfmesh_dsp/__init__.py`        (modify)
- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_music.py`        (create)
- `packages/rfmesh-dsp/tests/conftest.py`                 (modify — add
                                                           coherent scenario
                                                           fixtures; DO NOT
                                                           remove existing
                                                           L1 fixtures)
- `packages/rfmesh-dsp/tests/golden_generator.py`         (modify — add
                                                           music goldens; DO
                                                           NOT remove existing
                                                           L1 or manifold
                                                           generators)
- `packages/rfmesh-dsp/tests/golden/l2_music_pseudospectrum_137deg_uca4_20db.npz` (generate + commit)
- `packages/rfmesh-dsp/tests/golden/l2_music_sigma_table.npz`                    (generate + commit)
- `packages/rfmesh-dsp/tests/test_l2_music.py`            (create)
- `packages/rfmesh-dsp/tests/test_l2_music_sigma_honesty.py` (create — separated
                                                              from test_l2_music.py
                                                              so the ~75s slow
                                                              test is isolated)

## Files you may NOT touch

- `packages/rfmesh-contracts/**`                          (FROZEN — Invariant 1)
- `packages/rfmesh-sdr/**`                                (other workstream — Invariant 2)
- `packages/rfmesh-dsp/src/rfmesh_dsp/l1.py`              (merged; do not edit)
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_manifold.py`  (merged; do not edit)
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_covariance.py` (merged; do not edit)
- `packages/rfmesh-dsp/src/rfmesh_dsp/rssi.py`            (merged; do not edit)
- `packages/rfmesh-dsp/src/rfmesh_dsp/spectrum.py`        (merged; do not edit)
- `packages/rfmesh-dsp/src/rfmesh_dsp/constants.py`       (merged; do not edit)
- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_mvdr.py`         (WS-B-004's file)
- `packages/rfmesh-dsp/tests/test_l2_mvdr*.py`            (WS-B-004's files)
- Anything outside `packages/rfmesh-dsp/`

## Stop conditions
- Stop after producing the diff. Do not auto-commit or push.
- Paste `just verify` output into the conversation.
- If the sigma-honesty test cannot be made to pass within +/-20 % at all
  three SNRs without widening tolerance, STOP and write a scratchpad note
  in `.claude/scratchpad/ws-b-<date>.md` describing what calibration
  constants were tried, what empirical / claimed ratios resulted, and
  what the residual structure of the parabolic fit looked like (mean,
  variance, autocorrelation of residuals). Do NOT widen tolerance.
- If a contract change appears necessary, STOP and write
  `docs/adr/ADR-NNN-<short>.md` (status PROPOSED) instead — do NOT proceed.
- If the receiver's `calibrate()` succeeds but the calibrated IQ still
  produces wildly wrong bearings (>5° error at high SNR), STOP — the
  array_manifold convention or sample_covariance integration is wrong,
  not the MUSIC code.
