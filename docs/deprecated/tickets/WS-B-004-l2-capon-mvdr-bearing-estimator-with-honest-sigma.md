# TICKET WS-B-004: L2 Capon (MVDR) bearing estimator with honest sigma

## Naming note
The original bootstrap-B Deliverable 4 calls this "L2 MVDR null-steering".
Capon / MVDR is technically a *spectrum estimator* (DoA via beampattern
peak), while *null-steering* is a beamforming operation (place nulls
toward known interferers). For a single-emitter bearing problem, the
Capon spectrum is what you want; null-steering is useful only when you
already know one source's DoA and want to suppress it. v0.1 implements
the Capon spectrum estimator as the L2_MVDR `BearingEstimator`. If pure
null-steering (synthesise weights given known interferer DoAs) is wanted
later, that's a separate utility, not a BearingEstimator. Capability
enum stays `Capability.L2_MVDR`.

## Goal (one sentence)
Implement a Capon-spectrum (a.k.a. MVDR) L2 `BearingEstimator` in
`rfmesh-dsp` that consumes a coherent IQ block from
`CoherentReceiver.read_coherent()`, inverts the sample covariance with
diagonal loading for numerical stability, scans the Capon pseudospectrum
across azimuth, and emits a `BearingReport` with
`method=Capability.L2_MVDR` and an honest `azimuth_sigma_deg` validated
against Monte-Carlo ground truth.

## Context (links only, not content)
- Same contracts and architecture references as WS-B-003.
- DSP modules consumed (merged): `rfmesh_dsp.array_manifold`,
  `rfmesh_dsp.array_covariance`.
- ADR-004: out of scope (same as WS-B-003 — DSP consumes calibrated IQ).
- WS-B-003 is a sibling ticket; reading it gives useful context on the
  shared sigma-honesty test pattern, the L2-runtime hook pattern
  (`set_timestamp`), and the calibration trust contract. Do NOT, however,
  import from `l2_music.py` — Capon is structurally independent. Code
  duplication of small numerics (eigendecomp, fit, sigma extraction) is
  fine for v0.1; a follow-up refactor ticket can extract shared helpers
  after both ship.

## Design — read before coding

### The Capon algorithm in 5 lines
Given a coherent IQ block `X` of shape `(N, T)` from a calibrated
`CoherentReceiver`:

1. `R = sample_covariance(X)` — Hermitian (N, N) complex128.
2. (optional) `R = forward_backward_smooth(R)` — default False.
3. `R_loaded = R + epsilon * I` where `epsilon = diagonal_loading_factor *
   trace(R) / N`. Default `diagonal_loading_factor = 1e-3`. This is the
   numerical-stability fix: a finite-sample R can be near-singular at
   low SNR or short T, and the inversion explodes. The loading regularises;
   `1e-3` adds 0.001× the average power per channel, swamping numerical
   noise without significantly perturbing the Capon peak location.
4. `R_inv = np.linalg.inv(R_loaded)` — could use `np.linalg.solve` for
   numerical reasons but for an N=2..8 array `inv` is fine.
5. For each scan azimuth `theta_k`:
   `P_capon(theta_k) = 1 / Re( a(theta_k)^H · R_inv · a(theta_k) )`
6. argmax → coarse peak; parabolic fit on `1/P_capon` over 5-7 points
   around argmax → sub-grid theta_hat. (Same fit machinery as MUSIC.)

### Why diagonal loading is REQUIRED, not optional
Sample covariance from a finite block has eigenvalues that include
"noise" eigenvalues close to but not exactly equal to the true noise
floor. The smallest eigenvalue can be orders of magnitude smaller than
the others, making R ill-conditioned. R_inv blows up that small eigenvalue
into the dominant contribution and the Capon spectrum becomes noise-driven
trash. Diagonal loading bounds the smallest eigenvalue of R_loaded by
`epsilon * trace(R) / N`, which is the *guaranteed* minimum eigenvalue
after loading. Document this loudly in `l2_mvdr.py`'s module docstring.

`diagonal_loading_factor` is constructor-exposed so a future calibration
ticket can tune it per array; v0.1 uses `1e-3` and verifies behaviour
empirically in the tests.

### Sigma recipe
SAME as WS-B-003: parabolic fit on `1/P_capon`, propagated variance via
polyfit cov, empirically-calibrated multiplicative constant to make
median(claimed) hit empirical_std at +/-5 % at SNR=20 dB. The constant
will likely be DIFFERENT from MUSIC's because the local shape of
`1/P_capon` near the peak depends on `R_inv`'s structure, not on noise-
subspace projection.

The Capon peak is theoretically *broader* than the MUSIC peak at the same
SNR (Capon has no super-resolution; MUSIC does). So expect a larger
sigma at the same SNR. This is honest: Capon's sigma IS bigger than
MUSIC's at the same scenario, and the honesty test will confirm both
estimators report what they actually do.

### Runtime surface
SAME pattern as MUSIC: constructor takes `node_id`, `node_position`,
`receiver`, `array_config`, `operating_frequency_hz`,
`diagonal_loading_factor`, `use_forward_backward`, `scan_step_deg`. The
estimator binds to the receiver and reads `is_calibrated` itself. The
runtime calls `set_timestamp(t)` then `estimate(coherent_block)`.

### Failure modes (Invariant 4 surface)
`estimate` returns None when:
- Receiver is not calibrated.
- Channels < 2 (cannot do a meaningful 2x2 inverse).
- T < 4 * N (too few samples for a stable covariance).
- After loading, the condition number of R_loaded exceeds 1e8 (the
  loading is failing — emit None rather than a garbage bearing).
- The Capon spectrum has no clear peak (max(P_capon) < 3 * median(P_capon)).
- The parabolic fit fails (rank-deficient cov, wrong curvature sign,
  out-of-window vertex, non-positive Var(x_v)).

## Acceptance criteria

1. `uv run pytest packages/rfmesh-dsp -v` passes, including:
   - `tests/test_l2_mvdr.py::test_protocol_conformance`.
   - `tests/test_l2_mvdr.py::test_method_is_l2_mvdr`.
   - `tests/test_l2_mvdr.py::test_peak_recovers_known_angle_uca` — UCA
     N=4, single emitter at 137°, SNR=20 dB, 4096 coherent samples;
     recovered azimuth within +/-2° of 137°. (Looser than MUSIC's +/-1°
     because Capon's peak is broader.)
   - `tests/test_l2_mvdr.py::test_returns_none_on_uncalibrated_stream`.
   - `tests/test_l2_mvdr.py::test_returns_none_on_low_snr` — emitter at
     -10 dB SNR; estimate returns None (peak-prominence gate or
     ill-conditioning gate trips).
   - `tests/test_l2_mvdr.py::test_set_timestamp_required`.
   - `tests/test_l2_mvdr.py::test_estimate_re_arms`.
   - `tests/test_l2_mvdr.py::test_diagonal_loading_stabilises_low_snr` —
     at SNR=0 dB with default loading, the condition number of R_loaded
     stays below 1e6 and the estimator emits a bearing (or honest None
     via peak-prominence gate) — never NaN, never inf.

2. **Golden-file tests (Invariant 3).** `tests/golden/`:
   - `l2_mvdr_pseudospectrum_137deg_uca4_20db.npz` — (720,) Capon
     pseudospectrum at seed=42. Max-abs-relative-diff <= 1e-9.
   - `l2_mvdr_sigma_table.npz` — median claimed sigma over 200 trials at
     SNR={10, 20, 30} dB. <= 5 % relative diff. Marked slow.

3. **Sigma-honesty test.**
   `tests/test_l2_mvdr_sigma_honesty.py::test_empirical_spread_matches_claimed_sigma`
   — same +/-20 % band over the same three SNRs as MUSIC. Same stop
   condition: do NOT widen tolerance; re-calibrate the constant.

4. `uv run mypy packages/rfmesh-dsp` clean.
5. `uv run ruff check packages/rfmesh-dsp` clean.
6. `uv run lint-imports` shows "DSP is pure" still kept; no import of
   `rfmesh_sdr` in `src/`.
7. `__init__.py` re-exports `L2MvdrEstimator` (or `L2CaponEstimator` —
   pick `L2MvdrEstimator` for symmetry with the Capability.L2_MVDR
   enum name). Alphabetical insert; do not touch lines you did not add
   (parallelism discipline with WS-B-003).

## Out of scope

- Eigenvalue-based source-count detection. Capon doesn't need it for v0.1.
- Robust Capon variants (RAB, worst-case Capon).
- Adaptive diagonal loading. Constant `1e-3` factor is fine for v0.1.
- Pure null-steering as a separate utility — see "Naming note" above.
- Multi-source emission.
- `ArrayCalibration` loading from disk.
- Contract edits (Invariant 1).

## Files you may touch (create | modify)

- `packages/rfmesh-dsp/src/rfmesh_dsp/__init__.py`         (modify)
- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_mvdr.py`          (create)
- `packages/rfmesh-dsp/tests/conftest.py`                  (modify — add
                                                            mvdr fixtures
                                                            if not already
                                                            present from
                                                            WS-B-003; reuse
                                                            shared coherent
                                                            scenario factories
                                                            if WS-B-003 added
                                                            them; otherwise
                                                            add your own under
                                                            new names)
- `packages/rfmesh-dsp/tests/golden_generator.py`          (modify — add
                                                            mvdr generators;
                                                            do NOT remove
                                                            anything)
- `packages/rfmesh-dsp/tests/golden/l2_mvdr_pseudospectrum_137deg_uca4_20db.npz` (generate + commit)
- `packages/rfmesh-dsp/tests/golden/l2_mvdr_sigma_table.npz`                    (generate + commit)
- `packages/rfmesh-dsp/tests/test_l2_mvdr.py`              (create)
- `packages/rfmesh-dsp/tests/test_l2_mvdr_sigma_honesty.py` (create)

## Files you may NOT touch

- Everything in WS-B-003's "may NOT touch" list.
- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_music.py`         (WS-B-003's file)
- `packages/rfmesh-dsp/tests/test_l2_music*.py`            (WS-B-003's files)

## Stop conditions
- Same as WS-B-003 (paste verify output; scratchpad on honesty failure;
  ADR on contract pressure; STOP on suspect manifold/covariance integration).
- Additional MVDR-specific stop: if diagonal loading at `1e-3` is
  insufficient to stabilise R_inv for the test scenarios (i.e. condition
  number keeps exceeding 1e8 even on clean test data), STOP — there is
  a numerical issue upstream (perhaps the `sample_covariance` upcast to
  complex128 from the merged module is being lost somewhere). Do NOT
  silently bump the loading factor to mask the symptom.
