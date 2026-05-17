# ADR-005 — Fusion `ConfidenceLevel` policy and operational tolerance

> **AMENDED 2026-05-17 by ADR-009.** The "Rationale for the 5% choice"
> paragraph below contains a 1-σ vs 95%-chi-square scale error: the
> 150 m back-of-envelope is the 1-σ value, the contract puts
> `semi_major_m` at the 95% level (factor ≈ 2.45 larger). The
> threshold value (0.05) and the structural policy stay unchanged;
> the *demo narrative* is the part that needed correcting. See
> `ADR-009-confidence-band-math-correction-and-demo-narrative.md`.

- **Status:** ACCEPTED (2026-05-17, lead-Opus + Maciej). The 5%
  operational threshold (D1) is provisional pending `trench_demo.yaml`
  geometry verification (lead-side, separate task) and Phase C
  multipath calibration on Maciej's bench; structural policy (D2-D6)
  is final.
- **Author:** Opus-CD (Workstream C+D)
- **Date:** 2026-05-15
- **Decision scope:** internal to `rfmesh-fusion`, but the chosen value is
  the *operator-facing* threshold an RF/EW jury sees on ATAK. No contract
  change.
- **Supersedes / superseded by:** —
- **Depends on:** ADR-007 (algorithm choices fix the meaning of the
  underlying quantities).

---

## Context

`INTERFACES.md` §3 specifies the binding policy for
`FixEvent.confidence_level`:

> - `HIGH` iff `gdop ≤ FusionConfig.gdop_warn_threshold` (default 6.0)
>   *and* `confidence_ellipse_95.semi_major_m` is below an operational
>   tolerance (workstream-C-set, typically a fraction of fix range) *and*
>   no residual exceeds 3σ of its node's sigma.
> - `LOW` iff `gdop` is above threshold *or* `semi_major_m` exceeds
>   tolerance *or* the solver fell back to `fallback_centroid`.
> - `MEDIUM` otherwise.

Two things are intentionally left to the workstream: (a) the actual value
of "operational tolerance", and (b) the precise meaning of "fix range" the
fraction is taken of.

This ADR fixes both for v1.0.0.

---

## Decision

### D1. Operational tolerance: `semi_major_m_threshold = 0.05 · range_m`

where `range_m` is the Euclidean distance in ENU from the fused emitter
position to the centroid of the contributing nodes' positions.

A fix is considered to have a "tight enough" ellipse for the `HIGH` band
when its 95% semi-major axis is no more than 5% of its standoff range.

### D2. The `range_m` denominator: emitter ↔ node-centroid

Not "nearest node" (over-rewards lucky placement), not "farthest node"
(under-rewards). The centroid is the natural anchor that matches the ENU
origin choice (see `MODULE_PLAN.md` §3 `projection.py`), and produces a
single scalar that scales the tolerance consistently with the fusion
geometry.

### D3. Residual gate: consume `is_outlier` from `residuals.py`

For each contributing node $i$, the residuals module
(`rfmesh_fusion.residuals.compute_residuals`) computes the post-fit
angular residual $r_i$ and a per-bearing flag
$\text{is\_outlier}_i \equiv (|r_i| / \sigma_i > 3)$
where $\sigma_i$ is *that node's* reported `azimuth_sigma_deg`. See
`MODULE_PLAN.md` §3 `residuals.py` for the API.

The `confidence.py` policy *consumes* this flag — it does not recompute
the 3σ test locally. Single locus of truth: the same outlier definition
the ops dashboard uses to highlight a misbehaving bearing is the same
one that downgrades a fix from `HIGH` to `MEDIUM`.

A fix where *any* contributing bearing has `is_outlier == True`
downgrades from `HIGH` to `MEDIUM` (not to `LOW` — the fix is still
real, just imperfect). The downgrade is on *fix-level confidence
display*, not on inclusion in the solver: per ADR-007 D3 and
`MODULE_PLAN.md` §7.2, the sprint-1 solver is honesty-over-robustness
and does not reject the outlier. The dashboard shows the bearing as
included-but-flagged; the operator sees both the degraded fix *and*
which node caused the degradation.

### D4. Boundary cases (deterministic, never surprise)

- 2 bearings exactly (the minimum): residual is mathematically zero
  (over-determined system has 2 unknowns, 2 equations). Residual gate
  is vacuously satisfied; D1 + GDOP still apply.
- All residuals zero (perfect simulation case): D3 trivially satisfied;
  D1 + GDOP are what gates HIGH.
- `method == "fallback_centroid"`: `LOW`, full stop. No further checks.
- A bearing with $\sigma_i = 0$: rejected at `BearingReport`
  construction by `azimuth_sigma_deg > 0` validator. Will not reach
  fusion.

### D5. Operational visibility — the 5% threshold is what the operator sees

The 5% value in D1 is meaningful only if the operator can *see* the
quantity it gates on. Three concrete obligations on the wider system
follow from D1 and are recorded here so the relevant tickets carry them:

**(a) Code-level rosetta-stone comment in `confidence.py`.** The module
constant carrying the value is named `_HIGH_BAND_RANGE_FRACTION` and
sits next to a comment that explicitly distinguishes:

> ```
> # Operational threshold for the HIGH confidence band — NOT the
> # BoTH3 Counter-Jamming Challenge 2 spec tolerance.
> #
> # BoTH3 spec:                     ≤ 20 m at 2-5 km   ⇒  0.4-1.0 %.
> # Our HIGH-band operational gate: ≤ 5 % of range.
> #
> # A "HIGH" label means "well above the noise floor of usable fixes",
> # not "competition-compliant". The dashboard surfaces the actual
> # percentage (see ADR-005 D5(b)) so the operator and a jury can see
> # the spec-compliant regime (< 1 %) cross independently of the band.
> ```

This comment is binding — Claude Code is *not* free to remove or
paraphrase it in subsequent ticket churn.

**(b) Ops dashboard renders the actual percentage.** Next to every
fix, the dashboard displays `100 · semi_major_m / range_m` to one
decimal place, with the BoTH3 spec band (≤ 1%) shown as a reference
shaded region. The `HIGH` colour does *not* replace this number; both
are shown. This is a separate ticket (sprint 2/3, in `rfmesh-ops`),
referenced here so it is not forgotten.

**(c) Demo script has a scene showing spec-compliant transition.**
The `scenarios/trench_demo.yaml` jury sequence includes a beat where,
after adding a fourth node (or swapping in the L2 node), the
percentage crosses below 1% live on screen — the moment the system
demonstrably enters the competition-compliant regime. The dashboard
labels this transition explicitly (e.g. a brief flash badge "≤ BoTH3
spec"). This is the visual proof of the central pitch claim — see
below.

### D6. Pitch positioning — "deployment density, not per-sensor magic"

The architecture's whole point is that absolute precision is bought by
geometry (node count, baseline length, placement diversity), not by
expensive radios. D5(b) and D5(c) make this point *visible*: the
operator sees the percentage shrink as nodes are added or repositioned,
and crosses the BoTH3 spec line not because any single sensor improved
but because the *layout* improved. The 5% operational threshold is the
*hysteresis band* between "working" and "competition-grade" — and our
demo deliberately lives in both, showing the transition. The pitch line
is "you don't buy a better radio, you deploy one more node", and ADR-005
is what makes that line a measured fact rather than an assertion.

---

## Rationale for the 5% choice

**Connecting to the brief.** BoTH3 Counter-Jamming Challenge 2 specifies
≤ 20 m at 2–5 km range — that is a ratio of **0.4% to 1%**. A `HIGH`
threshold at 5% is comfortably looser than the *competition* requirement,
meaning a `HIGH`-labelled fix in our system is already well inside the
competition target.

**Demo behaviour.** With three L1 nodes at 5° σ and a 3 km standoff
emitter in good geometry, the analytic CRLB on `semi_major_m` is roughly
$\sigma_\theta \cdot R / \sqrt{N}$ converted to metres
$\approx (5\degree \cdot \pi/180) \cdot 3000 / \sqrt{3} \approx 150\,\mathrm{m}$.
That is 5% of 3 km — exactly at the boundary, which is the *right*
calibration: L1-only fixes hover around `MEDIUM`, and adding an L2 node
with 1.5° σ pushes the fix unambiguously to `HIGH`. The "ellipse shrinks
visibly as nodes are added" demo story (see `ARCHITECTURE.md` §7) is
designed around this transition.

**Why 5% and not 2% or 10%.** Below 5%, an L1-only mesh almost never gets
`HIGH` even in good conditions — the band becomes a "we have L2" marker
rather than a fix-quality marker, which is the wrong story. Above 5%, the
`HIGH` band loses connection to the competition target and starts
flattering noisy fixes. 5% is the sweet spot for the demo geometries we
have in mind.

**Reversibility.** This is a single constant in `confidence.py`. Tuning
it post-`trench_demo.yaml` dry-run is a one-line diff. ADR is amended (not
re-written) if the value changes.

---

## Consequences

### Positive

- One scalar — `0.05` — captures the entire HIGH/MEDIUM transition.
  Reviewer can audit it in three seconds.
- Couples the policy to **range**, which is the operationally
  meaningful quantity (the operator cares about absolute target
  location, and absolute precision scales with standoff).
- Aligns naturally with the BoTH3 competition success criterion — a
  `HIGH` label in our system already implies "well inside spec".
- Residual gate gives the dashboard a concrete reason to surface
  `MEDIUM` ("node X is 4σ off — likely multipath") rather than just a
  band name.

### Negative

- The policy assumes `range_m` is meaningful. For a node-on-emitter
  pathology (range → 0, which is non-physical but could happen in test
  data), the tolerance collapses to zero and every fix downgrades. The
  implementation treats `range_m < 10 m` as a degenerate condition and
  forces `confidence_level = LOW` with the existing `fallback_centroid`
  path. Edge case is closed.
- The chosen value is *uncalibrated against real Phase C data*. Phase
  C has never been run (`INHERITED_CONTEXT.md` §3.1), so the multipath
  factor that inflates real-world σ vs simulated σ is unknown. If
  Phase C reveals that real-world σ is, e.g., 2× the σ the L1 estimator
  reports, the operative `range_m` ratio at the boundary doubles too,
  and the 5% threshold may be wrong. Amend post-Phase-C.

### Neutral

- The policy is per-fix, not per-node. A heterogeneous mesh (one L2 +
  several L1 nodes) is rewarded by the geometry-derived tightness of
  the ellipse, automatically — no per-method bonus or penalty.

---

## Open issue (the one this ADR cannot close on its own)

**Q1.** Is `0.05 · range_m` the right initial value for the demo
scenario, or should it be tuned against the *specific* node positions in
`scenarios/trench_demo.yaml`? Lead/Maciej decision; ADR is amended
post-decision.

The implementation lands with `0.05` as a module constant in
`confidence.py`, named `_HIGH_BAND_RANGE_FRACTION` with the
ADR reference inline. Changing it is a one-token diff.

---

## Validation gates

- The boundary-case unit tests in `tests/test_confidence.py` cover D4
  exhaustively.
- The honest-ellipse Monte Carlo (from ADR-007) at the sprint-1 demo
  geometry produces a `HIGH/MEDIUM/LOW` distribution that the workstream
  reports to the lead at sprint-1 review. If that distribution is
  obviously wrong (e.g. every fix is `LOW`, or every fix is `HIGH`), the
  threshold is re-tuned before the demo.

---

## Status disposition

PROPOSED until lead/Maciej ratification of the 0.05 value. The structural
policy (D2, D3, D4) does not need re-ratification; only the single scalar
in D1.
