# TICKET WS-CD-007: `StansfieldMLEFuser` — wire the `Fuser` Protocol

## Goal (one sentence)
Implement `fuser.py` exposing `StansfieldMLEFuser`, the orchestration class
that wires `projection` + `stansfield` + `mle` + `covariance` + `gdop` +
`residuals` + the new `confidence` policy into a single `Fuser` Protocol
implementation — turning a batch of `BearingReport`s into a fully-populated
`FixEvent` (or `None`) per the binding ADR-005 / ADR-007 / ADR-009 policies.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.protocols.Fuser` (the Protocol this implements),
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.messages.FixEvent`,
  `rfmesh_contracts.geospatial.GeodeticPosition`,
  `rfmesh_contracts.geospatial.EllipseENU`,
  `rfmesh_contracts.enums.ConfidenceLevel`,
  `rfmesh_contracts.enums.EmitterClass`,
  `rfmesh_contracts.config.FusionConfig`.
- Architecture references:
  `ARCHITECTURE.md` §6 (AoA cross-fix, NTP-sufficient timing — fusion
  batches by `t_unix_ns` regardless of arrival order);
  `ARCHITECTURE.md` §7 (honesty payload on every fix — every panel on the
  demo dashboard reads a `FixEvent` field this fuser produces);
  `INTERFACES.md` §2 (`EllipseENU` 95% contract — derived in `covariance.py`,
  consumed verbatim here);
  `INTERFACES.md` §3 (`FixEvent` — every field and the
  `contributing_nodes` ↔ `residuals_deg` ordering invariant);
  `INTERFACES.md` §5 (the `Fuser` Protocol — `fuse(bearings, config) ->
  FixEvent | None`).
- ADRs (binding):
  `ADR-005-fusion-confidence-policy.md` §D1-D6 (the operational
  HIGH-band threshold = 0.05 of range, residual gate via `is_outlier`,
  boundary cases including the `range_m < 10` degeneracy);
  `ADR-007-fusion-algorithm-choices.md` D1, D2, D3, D4, D5 (Stansfield
  seed + Gauss-Newton MLE + `fallback_centroid` + unweighted GDOP +
  closed-form ellipse — the algorithmic spine this fuser orchestrates);
  `ADR-009-confidence-band-math-correction-and-demo-narrative.md`
  (95%-scale correction → trench-demo Beat D produces `MEDIUM`, not
  `HIGH` — `test_fuser_trench_demo_beat_d_high_band_NOT_reached`
  pins this at the implementation level).
- docs/ADVANTAGES.md references:
  docs/ADVANTAGES.md §1 Advantage #6 (honesty payload — every field on `FixEvent`
  must be populated honestly; placeholders or magic constants in
  covariance/ellipse are exactly the silent-failure mode the demo's
  credibility depends on avoiding);
  AGENTS.md §1 B1 (contracts frozen — do not change `Fuser` Protocol),
  B3 (no silent fallbacks — `fallback_centroid` is loud and labelled,
  not silent), B4 (demo honesty payload), B5 (pure fusion).
- Demo geometry that this ticket's test pins:
  `docs/demo/trench-demo-geometry.md` §3 + `docs/demo/crlb_analysis.py`
  (the CRLB script the test cross-validates against).
- Prior tickets this depends on: WS-CD-001 (projection), WS-CD-002
  (geometry + Stansfield + exceptions), WS-CD-003 (MLE refinement),
  WS-CD-004 (covariance + ellipse), WS-CD-005 (GDOP), WS-CD-006
  (residuals + is_outlier). All merged; this is the wiring ticket.
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-fusion -v` passes, including all
   prior WS-CD-00{1,1b,2,3,4,5,6} tests still green. New tests:

   `tests/test_confidence.py`:
   - `test_confidence_policy_high_iff_all_three_pass` — table-driven over
     8 cases covering every combination of (gdop ≤ threshold, semi/range
     ≤ 0.05, `is_outlier_any` False, method != `fallback_centroid`).
     `HIGH` requires all four positive; any negative → `MEDIUM` or `LOW`.
   - `test_confidence_policy_fallback_always_low` — `method =
     "fallback_centroid"` → `LOW` regardless of GDOP / semi-major /
     outlier inputs.
   - `test_confidence_policy_outlier_downgrades_high_to_medium` — same
     inputs that would yield `HIGH` but with `is_outlier_any = True`
     → `MEDIUM` (per ADR-005 §D3: not LOW — the fix is still real).
   - `test_confidence_policy_range_below_10m_low` — `range_m = 5` →
     `LOW` regardless of every other input (per ADR-005 §"Negative"
     bullet: the operational tolerance collapses to ~0 at zero range).
   - `test_confidence_policy_high_band_constant_is_load_bearing` —
     `_HIGH_BAND_RANGE_FRACTION == 0.05` (binding per ADR-005 §D5(a)).
   - `test_confidence_policy_low_when_gdop_above_threshold_only` —
     GDOP exceeds threshold, semi/range good → MEDIUM (HIGH gate fails
     on geometry alone, fix is still useful — not LOW).
   - `test_confidence_policy_low_when_semi_over_range_above_threshold_only`
     — semi/range > 0.05, GDOP good → MEDIUM (HIGH gate fails on
     ellipse alone).
   - `test_confidence_policy_medium_when_both_high_gates_fail` — both
     GDOP and semi/range fail → LOW (both axes of geometry weak).

   `tests/test_fuser.py`:
   - `test_fuser_satisfies_protocol` — `isinstance(StansfieldMLEFuser(
     default_config), Fuser)` is `True`. The `Fuser` Protocol is
     `@runtime_checkable`; this is the cheapest end-to-end conformance
     gate.
   - `test_fuser_below_min_bearings_returns_none` — `fuse([single_bearing
     ], config)` returns `None`. Per the Protocol contract, "fewer than
     `config.min_bearings_for_fix`" means refuse to fabricate, not fall
     back.
   - `test_fuser_trench_demo_beat_d_high_band_NOT_reached` — at the
     trench-demo Beat D geometry (3 L1 nodes at σ=5° + 1 L2 node at
     σ=1.5°, emitter at (0, +3000) ENU per
     `docs/demo/trench-demo-geometry.md` §1), the produced
     `FixEvent.confidence_level` is `MEDIUM`, not `HIGH`. This pins
     ADR-009 at the implementation level: the demo lives in MEDIUM,
     this is the honest answer.
   - `test_fuser_synthetic_perfect_high_band` — a synthetic 6-node mesh
     (5 L1 + 1 L2) tight enough that semi/range ≤ 0.05 AND GDOP ≤ 6
     AND no outliers → `confidence_level == HIGH`. Demonstrates HIGH
     is reachable when geometry permits.
   - `test_fuser_outlier_downgrades_high_to_medium` — same synthetic
     mesh as above, inject one bearing 5σ off truth → HIGH conditions
     met geometrically but `is_outlier` flag True → `MEDIUM` (per
     ADR-005 §D3).
   - `test_fuser_fallback_centroid_marks_low_method` — degenerate
     geometry that triggers `DegenerateGeometryError` (all nodes
     coincident) → returned `FixEvent.method == "fallback_centroid"`
     AND `confidence_level == LOW`. Per ADR-007 D3 + ADR-005 §D4.
   - `test_fuser_mle_convergence_failure_falls_back_to_stansfield` —
     patched `solve_mle` raises `MLEConvergenceError` → returned
     `FixEvent.method == "stansfield"`, position is the Stansfield
     seed, covariance / ellipse still populated honestly from the
     seed position.
   - `test_fuser_consensus_emitter_class_majority` — 3 bearings,
     2 with `EmitterClass.ELRS`, 1 with `EmitterClass.CROSSFIRE` →
     consensus `ELRS`. All `None` → `None`. 1-1-1 split → `UNKNOWN`.
   - `test_fuser_contributing_nodes_order_matches_residuals` —
     `FixEvent.contributing_nodes[i]` corresponds to
     `FixEvent.residuals_deg[i]` for every i (INTERFACES.md §3
     invariant).
   - `test_fuser_fix_id_unique` — `fuse()` called twice with the same
     inputs returns `FixEvent`s with different `fix_id` (UUIDv4 per
     call).
   - `test_fuser_t_unix_ns_is_midpoint_integer` — 3 bearings at
     `t = 100, 200, 300` ns → `FixEvent.t_unix_ns == 200`. Integer
     arithmetic preserved end-to-end (never float64).

2. `uv run mypy packages/rfmesh-fusion` clean (strict).
3. `uv run ruff check packages/rfmesh-fusion` and
   `uv run ruff format --check packages/rfmesh-fusion` clean.
4. `uv run lint-imports` — "Fusion is pure" contract stays GREEN.
5. `__init__.py` rewritten with explicit `__all__` re-exporting the
   public surface (the implementer class, public helpers from
   `projection` / `geometry` / `stansfield` / `mle` / `covariance` /
   `gdop` / `residuals` / `confidence`, and the exception hierarchy).
6. The `_HIGH_BAND_RANGE_FRACTION = 0.05` module constant lives in
   `confidence.py` with the verbatim ADR-005 §D5(a) comment block.
   Per ADR-005 itself: "Claude Code is *not* free to remove or
   paraphrase it in subsequent ticket churn."

## Out of scope (explicit non-goals)

- Do NOT modify `Fuser` Protocol, `FixEvent` model, or any contract.
  Invariant B1.
- Do NOT modify `stansfield.py`, `mle.py`, `covariance.py`, `gdop.py`,
  `residuals.py`, `geometry.py`, `projection.py`, or `exceptions.py`.
  These are upstream tickets; their conventions are inherited.
- Do NOT introduce outlier rejection / IRLS inside the solver — per
  ADR-007 D3 / CLAUDE.md (council review protocol), sprint-1 is honesty-over-robustness: the
  outlier flag downgrades display confidence, the solver does not drop
  the bearing.
- Do NOT add `scipy` (closed-form 2x2 / per-bearing math is enough).
- Do NOT extract `_consensus_emitter_class`, `_choose_fallback_position`,
  or `_emit_fix_event` to a separate module; they are orchestration
  helpers private to `fuser.py` (single locus of read).

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py` (create — the
  orchestration class).
- `packages/rfmesh-fusion/src/rfmesh_fusion/confidence.py` (create —
  `_HIGH_BAND_RANGE_FRACTION` constant + `compute_confidence_level`).
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (rewrite for
  public re-exports + explicit `__all__`).
- `packages/rfmesh-fusion/tests/test_fuser.py` (create).
- `packages/rfmesh-fusion/tests/test_confidence.py` (create).
- `packages/rfmesh-fusion/tests/conftest.py` (extend only if a
  fixture is genuinely shared; reuse existing `make_bearing`,
  `make_position`, `origin`, `azimuth_node_to_emitter_deg`).
- `docs/tickets/WS-CD-007-stansfield-mle-fuser.md` (this file).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- `packages/rfmesh-fusion/src/rfmesh_fusion/{stansfield,mle,
  covariance,gdop,residuals,geometry,projection,exceptions}.py`
  (read-only — these are this ticket's inputs).
- Any sibling workstream package.

## Stop conditions

- Stop after producing the diff. Paste verify output into the
  conversation:
    * `uv run pytest packages/rfmesh-fusion -v`
    * `uv run mypy packages/rfmesh-fusion`
    * `uv run ruff check packages/rfmesh-fusion`
    * `uv run ruff format --check packages/rfmesh-fusion`
    * `uv run lint-imports`
- If `test_fuser_trench_demo_beat_d_high_band_NOT_reached` does NOT
  produce `MEDIUM`, the confidence policy is wrong OR the trench-demo
  CRLB numbers were wrong. Re-run `uv run python
  docs/demo/crlb_analysis.py`; if numbers diverge, write a scratchpad
  and stop — ADR-009 is the binding narrative.
- If `compute_confidence_level` produces `HIGH` for a fix with
  `is_outlier == True`, the ADR-005 §D3 downgrade is broken. Fix in
  `confidence.py`, do not weaken the test.
- If the `isinstance(fuser, Fuser)` check fails, this class is missing
  a required Protocol method. Diff against
  `rfmesh_contracts.protocols.Fuser` and add. Do NOT alter the
  Protocol's `@runtime_checkable` decoration — that would defeat the
  test's purpose and counts as a contract change.

## Implementation notes (non-binding)

### Confidence policy signature

```python
def compute_confidence_level(
    *,
    gdop: float,
    semi_major_m: float,
    range_m: float,
    is_outlier_any: bool,
    method: str,
    gdop_warn_threshold: float,
) -> ConfidenceLevel:
    """ADR-005 §D1-D5 + ADR-009 policy. See module docstring."""
```

`method == "fallback_centroid"` short-circuits to `LOW` first (D4).
`range_m < 10.0` short-circuits to `LOW` second (ADR-005 §"Negative"
bullet). Then HIGH gate is `gdop ≤ threshold` AND `semi/range ≤ 0.05`
AND `not is_outlier_any`. Failing any of the three: MEDIUM unless
*both* of (GDOP, semi/range) fail → LOW. Outlier alone never produces
LOW — only the HIGH→MEDIUM downgrade.

### Fuser orchestration sequence

1. Materialise `bearings` iterable to a tuple. Filter to ones inside
   the median ± `batch_window_ms / 2` window (so a stale bearing in the
   batch does not corrupt the fix). If `len(filtered) <
   config.min_bearings_for_fix` → return `None`.
2. ENU origin = `projection.choose_enu_origin` over contributing
   nodes' geodetic positions.
3. Project each `node_position` through `projection.to_enu(origin=
   centroid)`. The mapping is per-bearing (one bearing per node, in
   input order).
4. Try `stansfield.stansfield_seed`. On `DegenerateGeometryError`:
   take the `fallback_centroid` path — pairwise ray-ray crossings via
   `geometry.ray_ray_crossing` (skipping `None` results), weighted by
   `w_i + w_j = 1/σ_i² + 1/σ_j²`, then `geometry.
   weighted_centroid_of_crossings`. If no usable crossings at all
   (every pair parallel) → return `None`. Set `method =
   "fallback_centroid"`, placeholder ellipse with `semi_major =
   semi_minor = max(range_m * 0.5, 1.0)` orientation 0 (honest
   "we have no covariance" — the LOW confidence label is the
   load-bearing signal).
5. Otherwise: try `mle.solve_mle` seeded by Stansfield. On
   `MLEConvergenceError`: fall back to Stansfield seed unchanged,
   `method = "stansfield"`. Otherwise: `method = "stansfield+mle"`,
   use MLE result's position.
6. Compute covariance + ellipse via `covariance.compute_covariance` +
   `covariance.covariance_to_ellipse`. On
   `SingularFisherInformationError` (defensive — should not happen
   post-MLE): downgrade to Stansfield-only `method` with placeholder
   ellipse.
7. Compute GDOP via `gdop.compute_gdop`.
8. Compute residuals via `residuals.compute_residuals`.
9. Compute `range_m` = Euclidean ENU distance from emitter to node
   centroid (origin, in ENU = (0, 0); range_m = ||emitter_enu||).
10. Confidence level via `compute_confidence_level(...)`.
11. Consensus `emitter_class` across `bearings`:
    - If all `b.emitter_class is None` → `None`.
    - Else gather non-`None` classes; majority (> 50%) wins, with
      classification_confidence = mean of contributing nodes'
      `classification_confidence`. Split → `UNKNOWN`. Note: `FixEvent`
      does not carry `classification_confidence`; this is for future
      extension. v1.0 sets only `FixEvent.emitter_class`.
12. Reproject emitter ENU → geodetic via `projection.from_enu(origin=
    centroid, sigma_m=semi_major_m)`.
13. Compute `t_unix_ns` = midpoint of contributing bearings'
    `t_unix_ns`. Integer arithmetic only: `(min + max) // 2` or
    `sum // n` — pick one, document.
14. Construct `FixEvent` with `fix_id = uuid.uuid4()`, ordering of
    `contributing_nodes` = `residuals.contributing_node_ids` =
    input bearing order.

### Why `__init__(config)` AND `fuse(bearings, config)`

The `Fuser` Protocol takes `config` per call (so the same fuser
instance can serve multiple deployments). The class also takes
`config` in `__init__` to satisfy "construct from a single deployment
config" ergonomics. The per-call `config` overrides the constructor's
default. This is documented in the class docstring.
