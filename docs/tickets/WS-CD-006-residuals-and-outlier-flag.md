# TICKET WS-CD-006: Per-node residuals + `is_outlier` flag

## Goal (one sentence)
Implement `residuals.py` that, given a solved emitter ENU position, the
contributing `BearingReport`s, and the corresponding ENU node positions,
computes the post-fit signed angular residual for each bearing and a
per-bearing `is_outlier` flag (`|r_i| / σ_i > 3` per ADR-005 §D3) — the
self-diagnosis channel that lets the dashboard highlight a misbehaving
node and that downgrades `HIGH → MEDIUM` in the confidence policy.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.messages.FixEvent.residuals_deg`,
  `rfmesh_contracts.messages.FixEvent.contributing_nodes`.
- Architecture references:
  `ARCHITECTURE.md` §6 (AoA cross-fix; bearings are geographic; node
  runtime applies heading correction upstream — fusion never re-rotates);
  `INTERFACES.md` §0 (ENU axes; azimuth = true north, CW positive,
  `[0, 360)`);
  `INTERFACES.md` §3 (`FixEvent.residuals_deg` order matches
  `FixEvent.contributing_nodes` order; both have the same length —
  the ordering invariant is load-bearing for downstream
  dashboard/CoT consumers).
- Confidence policy: `docs/adr/ADR-005-fusion-confidence-policy.md`
  §D3 — `is_outlier_i ≡ (|r_i| / σ_i > 3)` with σ from *this bearing's
  own* `azimuth_sigma_deg` (not a global σ, not a re-derived empirical
  σ). Strictly `>`, not `>=`. ADR-009 confirms the outlier flag
  downgrades HIGH → MEDIUM.
- Algorithm reference: `packages/rfmesh-fusion/src/rfmesh_fusion/mle.py`
  — the predicted-azimuth convention is fixed there:
  `theta_pred = atan2(x_e − p_e, x_n − p_n)` (forward node→emitter
  direction; east-offset first, north-offset second), and the residual
  sign is `measured − predicted`. This module MUST use the same two
  conventions so that WS-CD-007's fuser can rely on MLE's internal
  residuals and these post-fit residuals agreeing on the converged
  iterate.
- Module: `packages/rfmesh-fusion/src/rfmesh_fusion/exceptions.py`
  (`FusionError` base class — used for the defensive non-positive sigma
  refusal, Invariant B3).
- HANDOFF §0 Advantage #6 (honesty payload, residuals + outlier flag =
  self-diagnosis); HANDOFF §2 B3 (no silent fallbacks), B4 (demo honesty
  payload), B5 (pure fusion).
- Prior tickets this depends on: WS-CD-001 (ENU projection helpers),
  WS-CD-002 (Stansfield + exceptions), WS-CD-003 (MLE refinement —
  consumed for the azimuth convention only; no runtime dependency).
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-fusion/tests/ -v` passes, including
   all prior WS-CD-00{1,1b,2,3} tests still green. New tests added to
   `tests/test_residuals.py`:

   - `test_perfect_fit_residuals_zero` — emitter and bearings
     analytically consistent (every measured azimuth = the exact
     node-to-emitter geographic azimuth) → every residual within `1e-9°`
     of zero, every `is_outlier == False`.
   - `test_one_bearing_offset_flagged_outlier` — clean 3-bearing
     scenario; inject a single bearing whose measured azimuth is offset
     by `+5°` from truth, with `σ = 1°`. Its residual ≈ ±5°
     (sign-checked: `measured − predicted` = `+5°`); `|r| / σ = 5 > 3`
     so `is_outlier == True`. The other two bearings have residuals
     ~0 and `is_outlier == False`.
   - `test_residual_wraps_180_360_seam` — geometry such that the
     predicted azimuth is `358°` and the measured azimuth is `2°`; the
     residual must be `+4°` (NOT `−356°`). Plus the symmetric case
     (predicted `2°`, measured `358°` → `−4°`). The wrap-around
     correctness is load-bearing for any emitter near the 0/360-deg
     seam; this is the single test most likely to catch a sign /
     modulo bug.
   - `test_residual_ordering_matches_contributing_nodes` — pass 3
     bearings with distinct `node_id`s in a specific order; the
     returned `residuals_deg[k]` and `is_outlier[k]` correspond to
     `contributing_node_ids[k]` and to `bearings[k].node_id`. The end-
     to-end ordering invariant per `INTERFACES.md` §3.
   - `test_two_bearing_minimum_residual_vacuous` — two-bearing solve
     (2 unknowns, 2 equations) at the analytic intersection → both
     residuals ≈ 0 within `1e-9°`. Documented as mathematically correct
     (over-determined system has zero residuals at the LS minimum when
     N_unknowns == N_equations), not a bug — matches ADR-005 §D4 first
     bullet.
   - `test_is_outlier_threshold_at_exactly_3` — residual exactly
     `3 · σ` → `is_outlier == False` (strict `>` per ADR-005 §D3).
     Residual at `3.001 · σ` → `True`. One assertion per side of the
     threshold, in both signed directions.
   - `test_negative_residual_outlier` — measured azimuth offset by
     `−5°` (σ = 1°) → residual ≈ `−5°`, `is_outlier == True` (absolute
     value comparison, not signed).
   - `test_zero_sigma_input_handled` — defensive: a bearing
     constructed with `σ ≤ 0` (bypassing the Pydantic validator via a
     `model_construct`-like path, OR a programmatically-mutated
     dataclass shim) → `compute_residuals` raises `FusionError`
     (Invariant B3, no silent division-by-zero). The contract
     validator already forbids this upstream; this test pins the
     in-module defence as a second line.
   - `test_residuals_result_dataclass_shape` — `ResidualsResult` is
     frozen / immutable; the three tuples (`residuals_deg`,
     `is_outlier`, `contributing_node_ids`) all have the same length;
     element types are `float`, `bool`, `str` respectively.
   - `test_length_mismatch_raises` — `len(bearings) !=
     len(node_positions_enu)` → `FusionError` with an informative
     message.

2. `uv run mypy packages/rfmesh-fusion` is clean (strict mode).

3. `uv run ruff check packages/rfmesh-fusion` is clean.
4. `uv run ruff format --check packages/rfmesh-fusion` is clean.

5. Imports in the new source module are limited to:
   - standard library: `math`, `collections.abc`, `dataclasses`,
     `__future__`
   - `rfmesh_contracts.messages.BearingReport`
   - intra-package: `from .exceptions import FusionError`
   No `numpy`, no `scipy`, no sibling workstream package, no third-
   party dependency. The arithmetic is per-bearing trigonometry on a
   handful of floats; the `math` module is the right tool and faster
   than numpy at this size.

6. `residuals.py` is pure (Invariant B5): no network, no file I/O, no
   subprocess, no hardware access. No module-level side effects beyond
   constant definitions. The package-level `Fusion is pure`
   import-linter contract (`uv run lint-imports`) stays green.

7. The package-level `__init__.py` is NOT touched here (WS-CD-007 owns
   it). `ResidualsResult` and `compute_residuals` are importable via
   the fully-qualified `rfmesh_fusion.residuals` path; WS-CD-007 will
   re-export them when wiring the fuser.

## Out of scope (explicit non-goals)

- Do NOT compute or set `FixEvent.confidence_level` based on the
  outlier flag — that is `confidence.py`'s job (later ticket).
  `residuals.py` returns the per-bearing flag; the policy that maps
  "any-outlier ⇒ HIGH downgraded to MEDIUM" lives elsewhere.
- Do NOT modify or weight the MLE residual computation — `mle.py`
  computes its own residuals internally for the Gauss-Newton step,
  and this module computes the *post-fit* residuals on the converged
  solution for the `FixEvent` payload. Same arithmetic in two places,
  by design (one feeds the iteration, the other the operator).
- Do NOT add IRLS / outlier down-weighting / iterative reweighting
  on the solver path. Per HANDOFF §3 and ADR-007 D3, sprint-1 is
  honesty-over-robustness: the solver does not reject the outlier;
  the dashboard shows it.
- Do NOT introduce a new exception class — `FusionError` is the
  correct level. ADR-005 §D4 says σ ≤ 0 is rejected by the contract
  validator; this module's check is defensive (Invariant B3), not a
  new error semantic that downstream callers should switch on.
- Do NOT add `numpy` — this module is pure-Python trigonometry on at
  most a handful of bearings per fix. Allocating `numpy` arrays for
  a 3-element residual vector is slower and harder to read.
- Do NOT modify the `BearingReport` contract, add fields, rename
  fields, or relax validators. Invariant B1.

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/residuals.py` (create)
- `packages/rfmesh-fusion/tests/test_residuals.py` (create)
- `packages/rfmesh-fusion/tests/conftest.py` (extend ONLY if a new
  fixture is genuinely shared with future tickets; reuse the existing
  `make_bearing`, `make_position`, `origin`, `azimuth_node_to_emitter_deg`
  helpers without modification)
- `docs/tickets/WS-CD-006-residuals-and-outlier-flag.md` (this file)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1)
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (WS-CD-007
  owns; do not pre-export here)
- `packages/rfmesh-fusion/src/rfmesh_fusion/mle.py` (WS-CD-003 output;
  read-only — the azimuth and residual sign conventions are consumed
  verbatim)
- `packages/rfmesh-fusion/src/rfmesh_fusion/stansfield.py`,
  `geometry.py`, `projection.py` (read-only)
- `packages/rfmesh-fusion/src/rfmesh_fusion/{covariance,gdop,
  confidence,fuser}.py` (other tickets; do not pre-create empty
  modules)
- Anything outside `packages/rfmesh-fusion/`

## Implementation notes (non-binding, for guidance)

### Residual definition — aligned with `mle.py`

The predicted azimuth from a solved emitter `(x_e, x_n)` to node
`(p_e, p_n)` — both ENU metres — is the **forward** (node→emitter)
direction, east-offset first:

```
theta_pred_rad = atan2(x_e - p_e, x_n - p_n)
theta_pred_deg = degrees(theta_pred_rad) % 360.0
```

This matches `mle.py`'s `_predicted_azimuths_and_jacobian` (where
`delta_e = p_e - x_e` and `theta_pred = atan2(-delta_e, -delta_n)`
algebraically equals `atan2(x_e - p_e, x_n - p_n)`) and the
`conftest.azimuth_node_to_emitter_deg` helper. **Do not** use the
maths-textbook `atan2(north, east)` form.

The signed residual is **measured − predicted**, wrapped to the
shortest-arc representative in `(−180, +180]` degrees:

```
r_i_deg = wrap_to_pi_deg(b.azimuth_deg - theta_pred_deg)
```

The wrap formula `((diff + 180) % 360) - 180` works for any finite
input in degrees and maps `+180.0` to `-180.0` at the boundary; if the
test geometry ever lands on the exact `±180°` boundary, prefer
`+180.0` (we are clamping to a half-open interval). The robust
double-arctangent form is `degrees(atan2(sin(diff_rad), cos(diff_rad)))`
which avoids any modulo-at-boundary subtlety, at the cost of two
trig calls per bearing. For a 3-bearing fix this is cheap;
readability wins.

### Outlier flag — ADR-005 §D3, strictly `>`

```
is_outlier_i = abs(r_i_deg) / sigma_i_deg > 3.0
```

`sigma_i_deg` is `b.azimuth_sigma_deg` — that node's *own* reported
1-σ uncertainty (which `BearingEstimator`s in WS-B have already
calibrated to the ±20% honesty band, per HANDOFF §2 B2). Not a
global σ. Not a re-derived empirical σ across the fix.

The contract validator enforces `azimuth_sigma_deg > 0`; we trust
that and additionally raise `FusionError` if the field is ever
≤ 0 (defence in depth, Invariant B3 — never silently divide by zero).

### API

```python
from collections.abc import Sequence
from dataclasses import dataclass

from rfmesh_contracts.messages import BearingReport


@dataclass(frozen=True, slots=True)
class ResidualsResult:
    """Per-bearing post-fit residuals + outlier flags.

    Attributes
    ----------
    residuals_deg
        Signed angular residual per bearing, in degrees, in
        ``(-180, +180]``. Indexed parallel to ``contributing_node_ids``
        (and to the input ``bearings`` sequence). Sign convention:
        ``measured azimuth − predicted azimuth`` at the solved
        emitter position, shortest-arc.
    is_outlier
        Per-bearing flag: ``True`` iff ``|r_i| / sigma_i > 3``
        (strictly greater, per ADR-005 §D3). Consumed by
        ``confidence.py`` to downgrade HIGH → MEDIUM (ADR-009).
    contributing_node_ids
        ``node_id`` of each contributing bearing, in input order.
        The ordering anchor that ``FixEvent.contributing_nodes``
        and ``FixEvent.residuals_deg`` must match — per
        ``INTERFACES.md`` §3.
    """

    residuals_deg: tuple[float, ...]
    is_outlier: tuple[bool, ...]
    contributing_node_ids: tuple[str, ...]


def compute_residuals(
    solved_emitter_xy_enu: tuple[float, float],
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> ResidualsResult:
    """Post-fit residuals + outlier flags for one fix.

    Raises
    ------
    FusionError
        If ``len(bearings) != len(node_positions_enu)`` (structural
        input error); if any bearing has ``azimuth_sigma_deg <= 0``
        (defence-in-depth, Invariant B3 — the contract validator
        already rejects this upstream).
    """
    ...
```

### Numerical hygiene

- All arithmetic in `float` (Python's `math` module). Float64 is the
  Python default; explicit dtype not needed.
- Convert each azimuth to radians inside the `atan2` step; convert
  the wrapped residual back to degrees once.
- `math.fmod` is not the same as `%` for negative inputs; use Python's
  `%` which always returns a non-negative result for a positive
  divisor — that is what the wrap math relies on.

## Stop conditions

- Stop after producing the diff. Paste into the conversation the
  output of:
    * `uv run pytest packages/rfmesh-fusion -v`
    * `uv run mypy packages/rfmesh-fusion`
    * `uv run ruff check packages/rfmesh-fusion`
    * `uv run ruff format --check packages/rfmesh-fusion`
    * `uv run lint-imports`
- If `test_residual_wraps_180_360_seam` does not pass cleanly, the
  wrap math is wrong. The robust pattern is `((diff + 180) % 360) -
  180` in degrees, or equivalently `degrees(atan2(sin, cos))`. Do
  not hand-pick test inputs to make a buggy wrap pass — verify
  against explicit unit cases at `359°/1°`, `1°/359°`, `180°/0°`,
  `−180.0` boundary.
- If the residual sign convention does not match what `mle.py`'s
  internal residual computes (read `mle.py`'s `_wrap_to_pi` call site
  carefully — `residual = _wrap_to_pi(theta_meas - theta_pred)`),
  align on `mle.py`'s convention. The two MUST agree because
  WS-CD-007's fuser uses both MLE's residuals (for Gauss-Newton
  convergence) and this module's post-fit residuals (for the
  `FixEvent.residuals_deg` payload); if they disagreed, the dashboard
  and the solver would tell different stories about the same fix.
- If a math disagreement surfaces that cannot be resolved from the
  in-package docstrings, escalate to `rf-dsp-specialist`.
- Do NOT modify `mle.py`, `stansfield.py`, `geometry.py`, or any
  contract — those are the binding conventions; a disagreement is
  fixed in *this* module.
