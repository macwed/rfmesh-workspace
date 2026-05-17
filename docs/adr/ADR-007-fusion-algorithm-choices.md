# ADR-007 — Fusion algorithm choices: Stansfield variant, MLE optimiser, GDOP definition

*(Renumbered from ADR-004 on 2026-05-17 to resolve a duplicate-number collision with `ADR-004-array-calibration-file-format.md`. Content unchanged.)*

- **Status:** ACCEPTED (2026-05-17, lead-Opus + Maciej)
- **Author:** Opus-CD (Workstream C+D)
- **Date:** 2026-05-15
- **Decision scope:** internal to `rfmesh-fusion`. No contract change; no
  `SCHEMA_VERSION` bump.
- **Supersedes / superseded by:** —

---

## Context

The bootstrap (`bootstrap-CD.md`) gives Workstream C+D the freedom — and
the responsibility — to pick within scope: "Stansfield variant, MLE
optimiser (Gauss-Newton vs Levenberg-Marquardt), ellipse-from-covariance
convention (chi-square 95% is the contract), GDOP definition (the H matrix
conditioning)". `INTERFACES.md` §3 (`FixEvent`) names
`"stansfield+mle"` as the expected default `method` value but leaves the
specific variant and optimiser unspecified, exactly so this ADR can pick
them once for the project.

This ADR fixes those choices for v1.0.0 so the first sprint's tickets, the
sprint-1 property tests, and the honest-ellipse acceptance criteria all
agree on what they mean.

---

## Decision

### D1. Stansfield variant: classical weighted-LS in ENU, with $w_i = 1/\sigma_i^2$

Minimise $\sum_i w_i\,(\hat{\mathbf{n}}_i \cdot (\mathbf{x} - \mathbf{p}_i))^2$
in the local ENU plane, $\hat{\mathbf{n}}_i$ being the unit normal to the
bearing-i ray and $w_i = 1/\sigma_i^2$ the inverse-variance weight from
`BearingReport.azimuth_sigma_deg`. Closed-form 2×2 normal-equation solve.

**Not chosen:** Kaplan's improved Stansfield (range-weighted), Don Ho's
algebraic estimator. They reduce small-error bias slightly but the MLE
refinement (D2) removes bias fully anyway, and the classical form is the
textbook reference that an EW-expert juror recognises immediately.

### D2. MLE optimiser: in-house Gauss-Newton with analytic Jacobian

Iterate
$\Delta\mathbf{x} = (J^T W J)^{-1} J^T W\,\mathbf{r}$
to convergence ($\|\Delta\mathbf{x}\| < 10^{-3}$ m, max 50 iterations),
seeded from D1. $J$ is the analytic Jacobian of the predicted azimuths
w.r.t. emitter position (closed form, see `MODULE_PLAN.md` §3
`mle.py`). $W = \mathrm{diag}(1/\sigma_i^2)$.

**Not chosen:** Levenberg-Marquardt, `scipy.optimize.least_squares`. LM is
strictly more robust on ill-conditioned problems, but the Stansfield seed
already places us in the convex basin for non-degenerate geometries; LM's
robustness pays off on bad initialisations we will not have. The cost of
the choice is one new runtime dependency (`scipy`), which we are
intentionally not taking on in sprint 1. If sprint-1 testing reveals
geometries where Gauss-Newton fails to converge, we re-open this ADR.

### D3. Failure path: degenerate → `fallback_centroid`

If $J^T W J$ is singular (condition number > $10^{10}$), or
Gauss-Newton fails to converge in 50 iterations, or the solution
diverges (>50 km from node centroid):

1. Compute pairwise ray-ray crossings (each pair of bearing lines).
2. Take the $w_i + w_j$–weighted mean of those crossings.
3. Set `method = "fallback_centroid"`, `confidence_level = LOW`.

This produces an *honest, labelled* weak fix rather than withholding
information. Withholding is reserved for "fewer than `min_bearings_for_fix`
bearings" or "every pairwise crossing is at infinity" (truly collinear
node-emitter geometry).

### D4. GDOP definition: `sqrt(trace((H^T H)^{-1}))` with unweighted H

$H$ is the geometric Jacobian (azimuth-vs-position partial derivatives) at
the converged solution, evaluated **without** the per-bearing $\sigma$
weights. GDOP is dimensionless after normalising by the mean square
node-emitter range.

**Why unweighted.** GDOP in this project carries a specific diagnostic
role: it says "the *sensor placement* is geometrically weak" — independent
of any individual sensor's quality. Per-sensor σ feeds the *covariance*
separately (Fisher information uses the weighted Jacobian). Two separate
signals, two separate panels on the demo dashboard:

- **High GDOP** → reposition nodes (the layout is bad).
- **Tight ellipse but high per-node residual on node N** → check node N
  (multipath / calibration on that node).

Mixing the two by weighting GDOP would collapse this diagnostic. We pay a
small interpretive complexity cost to keep the diagnostic axes separable.

**This is the standard convention.** The unweighted definition matches
the long-established surveying and satellite-navigation usage of DOP
metrics: GDOP, PDOP, HDOP, VDOP and TDOP in GNSS are all defined from
the geometry matrix $H$ that contains only unit line-of-sight vectors —
satellite signal quality and pseudorange variance live elsewhere in the
estimator. Adopting the same convention here means an RF/EW reviewer
reads our GDOP number with the same meaning they read it on any GNSS
receiver. The rationale above is *why* the convention is the right one
for our diagnostic story; this paragraph is the answer to "is this how
the field does it?" — yes.

### D5. Ellipse construction: closed-form 2×2 eigendecomposition

Eigenvalues of the 2×2 covariance from D2:
$\lambda_{1,2} = \tfrac{a+c}{2} \pm \sqrt{\big(\tfrac{a-c}{2}\big)^2 + b^2}$
with $a = \sigma_{xx}$, $b = \sigma_{xy}$, $c = \sigma_{yy}$.

Semi-axes of the 95% ellipse:
$s_i = \sqrt{\chi^2_{0.95,\,df=2} \cdot \lambda_i}$,
constant $\chi^2_{0.95,\,df=2} = 5.991464547107979$ hard-coded inline with
a comment cross-referencing `scipy.stats.chi2.ppf(0.95, df=2)` for
reviewer verification.

Orientation: $\phi = \tfrac{1}{2}\,\mathrm{atan2}(2b, a-c)$, ENU-plane
mathematical positive (East→North).

**Not chosen:** importing `scipy.stats.chi2.ppf` at runtime. The 2-DOF
95% value is a fixed constant of the universe; one inline literal with a
clear comment is cheaper, more auditable, and zero deps.

---

## Consequences

### Positive

- **No new runtime deps on sprint 1.** `rfmesh-fusion` ships against
  numpy alone. No `/uvadd-request`, no transitive bloat on CI runners,
  no licence audit.
- **Every step has a closed-form sanity check.** Stansfield is one
  matrix solve; Gauss-Newton update is one matrix solve per iteration;
  eigendecomposition is closed-form 2×2; chi² is a constant. There is
  nothing in this package that a reviewer cannot derive on paper in
  five minutes — which is exactly what we want for an RF/EW-expert
  jury's credibility check.
- **GDOP stays interpretable as a layout-quality signal.** Demo
  storyline "the geometry is bad here, look at the GDOP heatmap" stays
  honest, separable from "this *node* is bad".

### Negative

- **Gauss-Newton can diverge on bad initialisations.** The Stansfield
  seed prevents that for non-degenerate geometries, but a discovery of
  a real geometry where it fails sends us back to scipy + LM, costing
  one ADR + `/uvadd-request` + a re-test cycle.
- **No automatic outlier rejection on sprint 1.** A node with bad
  multipath contributes to the fix and the residual flags it
  post-hoc; the *fix* is degraded. Sprint 2 may add IRLS or trimmed
  least-squares (separate ADR). This is the right trade for sprint 1:
  honesty (the residual is visible) over robustness (silently dropping
  the outlier).

### Neutral

- **`method = "stansfield"` (seed-only) is offered as a side-by-side
  diagnostic mode.** The dashboard can show "Stansfield: X. Stansfield
  +MLE: Y." live, making the bias removal visible. This is a
  *demo-honesty* gain and costs near-zero implementation effort because
  the seed is computed regardless.

---

## Validation gates

- Closed-form ground-truth tests for D1, D2, D3, D4, D5 land in sprint 1
  (per `MODULE_PLAN.md` §5).
- The honest-ellipse Monte Carlo test (95% ± 3% inclusion rate over
  1000 trials) is the gate that says "this fusion is not lying".
- Convergence-failure rate of Gauss-Newton over a parameter sweep of
  geometries (N=2..6 nodes, GDOP=1..10, σ=1°..15°) is measured at
  sprint-1 end. If >1% — re-open this ADR.

---

## Status disposition

PROPOSED until lead review. Workstream C+D begins implementation against
these choices; if the lead overrides any of D1–D5, the affected sprint-1
tickets are amended before the corresponding diff lands.
