# TICKET WS-CD-008: Honest-ellipse Monte Carlo — the sprint-1 acceptance gate

## Goal (one sentence)
Validate, end-to-end and quantitatively, that `StansfieldMLEFuser`'s
`FixEvent.confidence_ellipse_95` is the **honest** 95% confidence contour
— the true emitter position must fall inside it in 95 ± 3% of Monte Carlo
trials across four pinned scenarios, so the demo's central honesty claim
is a measured fact rather than an assertion.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.messages.FixEvent`,
  `rfmesh_contracts.geospatial.EllipseENU`,
  `rfmesh_contracts.geospatial.GeodeticPosition`,
  `rfmesh_contracts.enums.ConfidenceLevel`,
  `rfmesh_contracts.enums.Capability`,
  `rfmesh_contracts.config.FusionConfig`.
- Architecture references:
  `ARCHITECTURE.md` §7 (the honesty payload is the demo);
  `INTERFACES.md` §2 (`EllipseENU` — 95% chi-square contract, the load-
  bearing definition this test re-validates empirically);
  `INTERFACES.md` §3 (`FixEvent.confidence_ellipse_95` semantics).
- ADRs (binding):
  `ADR-005-fusion-confidence-policy.md` §"Validation gates" (this test
  IS the gate ADR-005 anticipates);
  `ADR-007-fusion-algorithm-choices.md` D1-D5 (the analytic Fisher-
  information covariance + chi-square scaling this test cross-validates
  against ground truth);
  `ADR-009-confidence-band-math-correction-and-demo-narrative.md`
  (the 1-σ vs 95% scale correction this test makes impossible to forget
  silently — if the ellipse is at the wrong scale, the inclusion rate
  falls outside the 92-98% band).
- HANDOFF references:
  HANDOFF §0 Advantage #6 (honesty payload — the ellipse is honest iff
  the Monte Carlo inclusion rate matches the declared 95% level);
  HANDOFF §1 sprint-1 acceptance gate; §2 B2 (σ honesty), B4 (honesty
  payload — empirically validated by THIS test); §2 B5 (pure fusion —
  this test is pytest-only, no hardware).
- Demo geometry:
  `docs/demo/trench-demo-geometry.md` §2.2 (Beat C / Beat D pin),
  `docs/demo/crlb_analysis.py` (the analytic reference the empirical
  covariance is cross-validated against).
- Prior tickets this depends on: WS-CD-001 through WS-CD-007 (all
  merged). This is the empirical acceptance gate on top of the wired
  fuser.
- Salvage: none.

## Acceptance criteria

1. `packages/rfmesh-fusion/tests/test_honest_ellipse_monte_carlo.py`
   exists with at least four `@pytest.mark.slow`-marked parametrized
   scenarios:

   - **trench-demo Beat C** — 3 L1 nodes (σ = 5°), emitter at
     `(0, 3000)` m ENU, geometry per
     `docs/demo/trench-demo-geometry.md` §1.
   - **trench-demo Beat D** — 3 L1 (σ = 5°) + 1 L2 (σ = 1.5°),
     same emitter, geometry per the demo doc.
   - **Isotropic 4-node ring** — 4 nodes on a 2 km circle around the
     emitter at origin, σ = 3° each. Near-circular ellipse, GDOP near 1.
   - **High-SNR 5-node mesh** — 5 nodes, σ = 1° each, emitter at 4 km
     from centroid. Sharp ellipse + HIGH band reachable.

2. For each scenario, `n_trials = 1000` with
   `numpy.random.default_rng(seed=42)`. Per-scenario assertions:

   - **Inclusion rate** in `[0.92, 0.98]` — true emitter falls inside
     the 95% ellipse (locally-rotated check) in 95 ± 3 % of trials.
   - **Mean position bias** ≤ `0.5 × semi_minor_m` (claimed first-trial
     value) — MLE-converged positions are unbiased to below the
     resolution of the ellipse.
   - **Empirical-vs-claimed covariance Frobenius ratio** in `[1/1.5,
     1.5]` — the empirical 2×2 covariance from trial positions agrees
     with the first trial's claimed `covariance_m2` to within ×1.5
     (verifies the *full covariance* is honest, not just its contour).
   - **Band distribution** — trench-demo Beat C/D produce MEDIUM-
     dominant (≥ 50 % MEDIUM, < 30 % HIGH per ADR-009); high-SNR 5-node
     produces HIGH-dominant (≥ 50 % HIGH); isotropic ring is informative
     (no strict band assertion — geometry is favourable but σ = 3°
     keeps the demo-band story plausible).

3. The slow-marker suite runs separately:
   `uv run pytest packages/rfmesh-fusion -v -m "slow"`. Default
   `just verify` (`-m "not hardware"`) **does include** slow tests by
   workspace convention; the marker is for explicit deselection (see
   `pyproject.toml` workspace pytest config). This ticket follows the
   precedent set by `test_mle_bias_correction_monte_carlo`.

4. `uv run pytest packages/rfmesh-fusion` — all 154 prior tests still
   pass.

5. `uv run mypy packages/rfmesh-fusion` clean (strict).

6. `uv run ruff check packages/rfmesh-fusion` + `ruff format --check
   packages/rfmesh-fusion` clean.

7. `uv run lint-imports` — "Fusion is pure" KEPT.

8. **Results section of this ticket is filled in** with the per-
   scenario inclusion rates and band distributions after the test
   passes. The ticket is the demo-honesty briefing-book page.

## Out of scope (explicit non-goals)

- Do NOT modify any source file in `packages/rfmesh-fusion/src/`. This
  is a TEST ticket — if a source bug surfaces, scratchpad and stop.
- Do NOT widen the `[0.92, 0.98]` inclusion band to make a failing
  scenario pass. The band IS the gate.
- Do NOT modify the `_HIGH_BAND_RANGE_FRACTION` constant or the
  `compute_confidence_level` policy. If band distribution does not
  match ADR-009 narrative, the policy or the test is wrong — diagnose
  via scratchpad.
- Do NOT introduce outlier rejection / IRLS — sprint-1 is honesty-over-
  robustness (ADR-007 D3, HANDOFF §3).
- Do NOT change the existing `tests/conftest.py` fixtures; reuse
  `seeded_rng`, `make_bearing`, `make_position`, `origin`,
  `azimuth_node_to_emitter_deg` as-is.

## Files you may touch

- `packages/rfmesh-fusion/tests/test_honest_ellipse_monte_carlo.py`
  (create — the MC test module).
- `docs/tickets/WS-CD-008-honest-ellipse-monte-carlo.md` (this file,
  including the Results section after the test passes).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- Any source file in `packages/rfmesh-fusion/src/` (this is a TEST
  ticket).
- Any sibling workstream package.
- `packages/rfmesh-fusion/tests/conftest.py` — existing fixtures stay
  as they are; the new MC test imports them.

## Stop conditions (council escalation)

- **If ANY scenario inclusion rate is outside `[0.92, 0.98]`, STOP.**
  The gate is the gate. Diagnosis tree per the ticket brief:
  - Inclusion < 0.92 (under-conservative): claimed ellipse too small —
    check the χ² scaling (5.99146), Jacobian sign, σ units (rad vs deg
    in `W = diag(1/σ²)`).
  - Inclusion > 0.98 (over-conservative): claimed ellipse too large —
    check for double-application of the χ² factor.
  Scratchpad to `.claude/scratchpad/ws-cd-008-<date>.md` with the
  failure mode, scenario, Frobenius ratio, and a hypothesis. Spawn
  rf-dsp-specialist for numerical disagreement.
- If trench-demo Beat C/D produces HIGH-dominant (per ADR-009 should
  be MEDIUM-dominant), then `compute_confidence_level` is wrong;
  scratchpad and stop.

## Implementation notes (non-binding)

### Inclusion test in ellipse-local frame

Given fix at `(fix_E, fix_N)` (ENU), ellipse with `semi_major_m`,
`semi_minor_m`, `orientation_deg` (angle of semi-major from East toward
North), true emitter at `(true_E, true_N)`:

1. Offset: `dE = true_E - fix_E`, `dN = true_N - fix_N`.
2. Rotate into ellipse-local frame:
   `u = dE cos(θ) + dN sin(θ)` (along semi-major)
   `v = -dE sin(θ) + dN cos(θ)` (along semi-minor)
   with `θ = math.radians(orientation_deg)`.
3. Inside iff `(u/semi_major)² + (v/semi_minor)² ≤ 1`.

The fix is in geodetic coords; project back to the trial's known ENU
origin via `to_enu(fix.position, origin=trial_enu_origin)` to compare
in the same frame as the true emitter ENU position.

### Per-scenario node coordinates

Pinned in module constants for reproducibility. The trench-demo
scenarios use the geodetic origin `(50.330 N, 5.000 E)` to match
`test_fuser.py` and `docs/demo/trench-demo-geometry.md`.

### Numerical convention agreement

The CRLB analytic prediction (`docs/demo/crlb_analysis.py`) uses the
*same* Jacobian convention as `covariance.py` and `mle.py`; agreement
to 5 % is the existing `test_trench_demo_geometry_reproduces_crlb`
gate. The MC test goes a step further: the *empirical* covariance from
trial positions agrees with the claimed analytic covariance to within
×1.5 (Frobenius ratio). The 1.5 band is wider than the analytic-vs-
analytic ×1.05 band — empirical covariance has its own MC noise floor
of `~sqrt(2/N)` ≈ 4.5 % at N = 1000, so the band absorbs both that
noise and any residual finite-sample bias that MLE has not fully
removed.

## Results (seed=42, n_trials=1000)

### Per-scenario inclusion rates

| Scenario | Inclusion rate | Pass band `[0.92, 0.98]` |
|---|---|---|
| trench-demo Beat C (3 L1, σ=5°)         | **93.70 %** | PASS |
| trench-demo Beat D (3 L1 + L2)           | **94.80 %** | PASS |
| Isotropic 4-node ring (σ=3°)             | **94.10 %** | PASS |
| High-SNR 5-node mesh (σ=1°)              | **95.10 %** | PASS |

### Per-scenario band distributions (HIGH / MEDIUM / LOW)

| Scenario | HIGH | MEDIUM | LOW | Dominant |
|---|---|---|---|---|
| trench-demo Beat C                       |    0 |   1000 |    0 | **MEDIUM** |
| trench-demo Beat D                       |    0 |   1000 |    0 | **MEDIUM** |
| Isotropic 4-node ring                    |    0 |   1000 |    0 | MEDIUM |
| High-SNR 5-node mesh                     | 1000 |      0 |    0 | **HIGH** |

Beat C/D produce MEDIUM in 100 % of trials — exactly the ADR-009
narrative: the trench-demo lives in MEDIUM throughout the four beats,
crossing into HIGH happens only when σ is L2-class on every node and
the geometry is favourable (the high-SNR 5-node mesh).

### Empirical-vs-claimed covariance Frobenius ratios

| Scenario | Ratio (empirical / claimed) | Within ×1.5 band |
|---|---|---|
| trench-demo Beat C                       | **1.146** | PASS |
| trench-demo Beat D                       | **1.020** | PASS |
| Isotropic 4-node ring                    | **1.002** | PASS |
| High-SNR 5-node mesh                     | **1.030** | PASS |

### Mean-bias check (`|mean position offset|`)

| Scenario | Bias (m) | 0.5 × semi_minor_m (m) | PASS |
|---|---|---|---|
| trench-demo Beat C                       | 7.23 | 173.10 | PASS |
| trench-demo Beat D                       | 5.76 |  62.34 | PASS |
| Isotropic 4-node ring                    | 2.91 |  60.37 | PASS |
| High-SNR 5-node mesh                     | 1.08 |  40.82 | PASS |

### Headline

The empirical 95 % inclusion rate matches the claimed 95 % confidence
contour across all four scenarios within the ±3 % statistical band.
**The honesty payload (`FixEvent.confidence_ellipse_95`) is honest** —
when the dashboard renders the ellipse and the jury asks *"how do you
know that contour really contains the truth 95 % of the time?"*, the
answer is this test. The chi-square scaling (factor ≈ 2.448) and the
Jacobian convention agree end-to-end; the deployment-density story
per ADR-009 is empirically supported (3 L1 nodes at σ=5° produces
MEDIUM in 100 % of trials, even with an L2 partner added; HIGH is
reachable when geometry and per-bearing precision both cooperate, as
in the high-SNR 5-node case).
