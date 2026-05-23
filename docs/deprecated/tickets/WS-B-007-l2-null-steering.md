# TICKET WS-B-007: L2 null-steering — MVDR weight synthesis, apply, receive-pattern

## Goal (one sentence)

Implement `packages/rfmesh-dsp/src/rfmesh_dsp/l2_null_steering.py` —
the genuine null-steering deliverable backing pitch Advantage #4
("one matrix, two products"): a pure-numpy utility that synthesises an
MVDR receive-weight vector toward a configured look direction, applies
it to a coherent IQ block to produce a "nulled" scalar stream, and
computes the resulting receive pattern for the demo A/B polar plot.

## Context (links only, not content)

- Contracts touched (read-only):
  `rfmesh_contracts.enums.Capability.L2_MVDR_NULL` (the capability this
  module *implements* — note: this module advertises the capability via
  the node-runtime; it does NOT itself emit `BearingReport`s, so the
  `BearingReport.method` field never carries `L2_MVDR_NULL` in v1.1.0).
  `rfmesh_contracts.enums.ArrayGeometry` (for the receive-pattern scan).
- ADR: `docs/adr/ADR-008-l2-capon-enum-and-null-steering-reservation.md`
  — D6 (this module's binding API shape), D8 (the ≤20 dB UI-claim
  cap), §"Rationale" (anti-desense framing).
- Architecture: `docs/ADVANTAGES.md` §1 Advantage #4.
- Council review (folded in): the three subagent verdicts from the
  2026-05-17 L2-null-steering council session (Architect approve;
  RF-DSP four corrections; Demo-Integrity cap-the-claimed-depth).
  Summaries in `ADR-008` §D6 council corrections.
- Reuses (read-only): WS-B-002's `array_manifold.py` (steering vectors
  for ULA / UCA / CUSTOM); `array_covariance.py` (sample R + FB
  smoothing).
- Pre-condition: ADR-008 contract bump (`SCHEMA_VERSION = "1.1.0"`,
  `Capability.L2_CAPON` added, WS-B-004 `method` emission swapped) has
  landed before this ticket starts. The builder verifies this with
  `python -c "from rfmesh_contracts import Capability; assert
  Capability.L2_CAPON in Capability"` as the first step.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-dsp/tests/test_l2_null_steering.py -v`
   passes. New tests:

   - `test_weight_formula_distortionless` — for an ideal R with one
     dominant source at `θ_look`, `compute_null_steering_weights(R,
     a(θ_look))` returns `w` with `|w^H a(θ_look)| ≈ 1` to within 1e-6
     (the distortionless-response constraint). The unit-response
     direction is `θ_look`, NOT `θ_null` — RF-DSP review's mis-wording
     correction.
   - `test_null_depth_2_emitter_ideal` — canonical scenario: signal at
     θ_s = 30°, jammer at θ_j = 100°, N=4 UCA at λ/4 for 915 MHz, SNR
     signal/jammer/noise = 10/20/0 dB, T = 4096 snapshots, seed = 42.
     `null_depth_db ≥ 25` (ideal calibration); `look_gain_db ≥ -3`
     (look direction within 3 dB of unsteered).
   - `test_null_depth_robust_under_mismatch` — same scenario + ±2°
     steering-vector mismatch + ±10° per-channel phase calibration
     error. `null_depth_db ≥ 15` (the budget Maciej rehearses on stage).
     1000-trial Monte Carlo; assert p5 ≥ 15 dB, median ≥ 20 dB.
   - `test_loading_default_does_not_fill_null` — `compute_null_steering_weights`
     with default `diagonal_loading_factor = 1e-6` produces null
     depth ≥ 30 dB on the noise-free 2-emitter scenario;
     `diagonal_loading_factor = 1e-3` (Capon's default) produces
     depth ≤ 22 dB on the same scenario. **The test exists to enforce
     RF-DSP review correction #2** (loading defaults are not Capon's).
   - `test_fb_smoothing_default_on` — when the simulator is configured
     with a coherent ground reflection (two-ray channel from
     WS-A-003), `use_forward_backward = True` (default) produces null
     depth ≥ 15 dB while `use_forward_backward = False` produces
     ≤ 8 dB. Folded-in RF-DSP correction #3.
   - `test_n2_ula_broadside_jammer_rejected` — N=2 ULA at λ/2, signal
     and jammer within HPBW/√SNR of each other; the function returns a
     result with `condition_number > 1e6` and `null_depth_db < 6`.
     **The function does NOT silently return high-norm weights when
     DoF is insufficient** — Invariant B3 / no-silent-fallback. The
     consumer reads `condition_number` and refuses to claim a null.
   - `test_singular_R_raises` — when `cond(R + load·I) > 1e8` (e.g.
     T=2 with N=4, R rank-deficient), `compute_null_steering_weights`
     raises `NullSteeringError` (new exception class). Mirrors
     WS-B-004's `cond > 1e8` gate.
   - `test_compute_receive_pattern_shape` — pattern returned for a UCA
     N=4 has length `int(360 / scan_step_deg)`; gain_db at θ_look is
     within 1 dB of `0 dB` reference; gain_db at θ_jammer is
     `null_depth_db` below θ_look (consistency between
     `compute_null_steering_weights` and `compute_receive_pattern`).
   - `test_apply_null_shape_and_dtype` — `apply_null((N=4, T=1024)
     complex64 block, (N=4,) complex64 w)` returns shape `(1024,)`,
     dtype `complex64`. Performance check (informational only): the
     operation is a single matmul, ≤ 1 ms for T=4096 on a 2019-era
     laptop — flag if regress.
   - `test_result_struct_attribute_completeness` — every field on
     `NullSteeringResult` (`weights`, `null_depth_db`,
     `look_gain_db`, `condition_number`, `jammer_dominance_db`) is
     populated and consistent. The struct is the **demo-honesty
     surface**: every number Maciej quotes on stage must be a returned
     attribute (Demo-Integrity review folded into ADR-008 §D6).

2. `uv run mypy packages/rfmesh-dsp` clean (strict mode).
3. `uv run ruff check packages/rfmesh-dsp` clean.
4. `lint-imports` clean — `rfmesh-dsp` stays pure (no I/O, no network,
   no subprocess). The module is pure numpy + reads `Capability` /
   `ArrayGeometry` from contracts.
5. Module satisfies its own invariant: **it does NOT satisfy the
   `BearingEstimator` Protocol.** A test asserts this explicitly:

   ```python
   def test_module_does_not_grow_a_bearing_estimator() -> None:
       """ADR-008 §D6: null-steering is a utility, not a bearing producer."""
       import rfmesh_dsp.l2_null_steering as ns
       assert not hasattr(ns, "method")
       assert not any(
           callable(getattr(ns, name, None)) and
           getattr(getattr(ns, name), "__name__", "") == "estimate"
           for name in dir(ns)
       )
   ```

   This catches the temptation in review to grow `l2_null_steering.py`
   into a third L2 BearingEstimator.

6. **Sigma honesty does NOT apply** (no `BearingReport` emitted). But
   the demo-honesty analog is the `NullSteeringResult.null_depth_db`
   value matching the receive-pattern minimum within 0.5 dB on the
   canonical scenario. Tested.

## Out of scope (explicit non-goals)

- **Do NOT add a new `BearingEstimator` for null-steering.** It is
  not a bearing producer. Adding `class L2NullSteeringEstimator(...)`
  is wrong (ADR-008 §D6 binding).
- **Do NOT modify** `rfmesh-contracts/**` (ADR-008 already did).
- **Do NOT modify** WS-B-004's `l2_mvdr.py` (its `method` emission
  swap to `L2_CAPON` lands in the ADR-008 contract-bump commit, not
  here).
- **Do NOT modify** `array_manifold.py` or `array_covariance.py` (read-
  only consumers; if they prove insufficient, write a SCRATCHPAD note
  and stop).
- **Do NOT** render anything (matplotlib, plotly, ASCII art). The demo
  panel rendering lives in `rfmesh-ops`. This module produces numbers;
  ops produces pixels (ADR-008 §D7).
- **Do NOT** introduce new runtime dependencies. Pure numpy.

## Files you may touch

- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_null_steering.py` (create).
- `packages/rfmesh-dsp/src/rfmesh_dsp/exceptions.py` (create if absent,
  else extend) — add `NullSteeringError` class for the cond / DoF /
  singular-R failures. Follows the pattern of `rfmesh-fusion`'s
  `FusionError` hierarchy: lives with the workstream that raises it,
  NOT in `rfmesh-contracts`.
- `packages/rfmesh-dsp/tests/test_l2_null_steering.py` (create).
- `packages/rfmesh-dsp/tests/test_l2_null_steering_sigma_honesty.py`
  (create — the receive-pattern-vs-null_depth_db consistency test
  belongs here; named after the WS-B-001/003/004 sigma-honesty test
  convention so the discipline is visible).
- `packages/rfmesh-dsp/tests/conftest.py` (extend with a 2-emitter
  scenario fixture; do NOT modify existing fixtures).
- `packages/rfmesh-dsp/src/rfmesh_dsp/__init__.py` (optional public
  re-export if convenient).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_mvdr.py` (WS-B-004 territory;
  its `method` change is part of the contract-bump commit).
- `packages/rfmesh-dsp/src/rfmesh_dsp/l2_music.py` (WS-B-003).
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_manifold.py` (WS-B-002,
  read-only).
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_covariance.py` (WS-B-002,
  read-only).
- Any sibling package.

## Stop conditions

- Stop after producing the diff. Paste:

  ```
  uv run pytest packages/rfmesh-dsp -v
  uv run mypy packages/rfmesh-dsp
  uv run ruff check packages/rfmesh-dsp
  uv run lint-imports
  ```

  in the conversation.

- **If the Monte Carlo robustness test (`test_null_depth_robust_under_mismatch`)
  fails p5 ≥ 15 dB**, STOP. Write a SCRATCHPAD note
  (`.claude/scratchpad/ws-b-007-<date>.md`) describing what depth was
  achievable and under what mismatch budget. **DO NOT** tune the test
  threshold to match the implementation; the threshold is the slide
  number Maciej is going to quote. If the implementation cannot meet
  the slide budget honestly, the slide budget needs revision —
  lead-Opus mediates (likely with Demo-Integrity subagent).

- **If the analytic null-depth (from `NullSteeringResult.null_depth_db`)
  disagrees with the receive-pattern minimum (from
  `compute_receive_pattern`) by more than 0.5 dB**, STOP. One of the
  two formulae is wrong. Write SCRATCHPAD; do NOT silently apply a
  fudge factor.

- **If the `test_module_does_not_grow_a_bearing_estimator` shape test
  fails** because something has imported `BearingReport` /
  `BearingEstimator` into `l2_null_steering.py`, that is the
  Reviewer's red flag. STOP and remove the import. The module is a
  utility, not an estimator.

## Implementation hints (non-binding, just helpful)

- **The formula:** `R_loaded = R + (eps * trace(R) / N) * I` with
  `eps = diagonal_loading_factor` (default 1e-6). `R_inv = inv(R_loaded)`.
  `numerator = R_inv @ look_steering_vector`.
  `denominator = look_steering_vector.conj().T @ numerator`.
  `w = numerator / denominator`. Dtype throughout: `complex64`.

- **Null depth derivation:** for weights `w`, the output power at
  steering direction `θ` against an isotropic-noise R-equivalent
  reference is `|w^H a(θ)|^2`. Total output power against the actual
  R is `w^H R w`. The "null depth at the dominant interferer" is
  conventionally reported as the ratio of total output power to
  trace(R)/N (the average per-channel input power), in dB. The
  module's `NullSteeringResult.null_depth_db` field uses:
  `10 * log10( trace(R) / N / |w^H R w| )`. Document this in the
  module's docstring so reviewers can verify the formula on inspection.

- **Forward-backward smoothing (default ON):** `R_fb = 0.5 * (R + J R*
  J)` where `J` is the exchange matrix (anti-diagonal ones). The
  `array_covariance.forward_backward_smooth` helper from WS-B-002 does
  this — call it before the loading step when `use_forward_backward =
  True`. Returns Hermitian-symmetric R; loading + inversion proceed
  unchanged.

- **The receive pattern function:** scan azimuth on a grid (default
  0.5° step), compute steering vector `a(θ)` from the array manifold
  module, compute `|w^H a(θ)|^2`, convert to dB. Plot domain is
  `[0, 360)` to match the project-wide convention; the ULA front/back
  ambiguity is a real artefact and should be visible in the plot
  (do NOT suppress it).

- **The `apply_null` function:** literally `w.conj() @ coherent_iq`
  (shape `(N,)` conjugated, dot with `(N, T)` block, result `(T,)`).
  One-liner. Test exists to catch dtype regressions.

- **Naming:** the function argument is `look_steering_vector` (the
  *signal* direction whose response is preserved). RF-DSP council
  correction #1: NOT `null_steering_vector`. Get this right in the
  docstring or the jury asks awkward questions.

## Why this ticket exists

The contracts label this capability `L2_MVDR_NULL`. The pitch slide
for Advantage #4 promises it. WS-B-004's docstring confesses it has
not been built. ADR-008 split the enum to fix the producer-side
honesty problem; WS-B-007 builds the capability the enum now correctly
labels. Half a day's work + a Monte Carlo run + a demo panel in
`rfmesh-ops` (separate ticket). Closes the largest "the slide is real,
not aspirational" gap in the project.

Demo claim budget (binding on the UI authoring): null depth quoted as
"15-20 dB typical, up to 25 dB with fresh calibration" — caps at
20 dB in dashboard labels, never claims ≥ 30 dB. Per ADR-008 §D8.
