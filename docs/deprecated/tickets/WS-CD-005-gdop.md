# TICKET WS-CD-005: GDOP (Geometric Dilution of Precision) — unweighted, range-normalised

## Goal (one sentence)
Implement a single-scalar GDOP computation (`gdop.py`) that, given an
emitter ENU position and the ENU positions of the contributing nodes,
returns the **unweighted** range-normalised Geometric Dilution of Precision
exactly as fixed by ADR-007 D4 — independent of any per-bearing σ, so the
ops dashboard can read "the *sensor placement* is geometrically weak" as a
diagnostic axis separable from the covariance/ellipse signal.

## Context (links only, not content)
- Contracts touched (read-only): none. GDOP feeds the `FixEvent.gdop`
  field (`INTERFACES.md` §3) but is wired in by WS-CD-007; this ticket
  owns the *computation*, not the wiring.
- Architecture references: `ARCHITECTURE.md` §6 (AoA cross-fix);
  `INTERFACES.md` §3 (`FixEvent.gdop` semantics — strictly positive,
  low ≈ favourable layout, high (> ~6) near-collinear).
- Algorithm choice: `docs/adr/ADR-007-fusion-algorithm-choices.md` D4
  — GDOP is `sqrt(trace((H^T H)^-1) / mean(r_i^2))`, **unweighted H**,
  range-normalised. Two separable diagnostic axes (layout vs sensor)
  vs the weighted Fisher-information path used for covariance.
- Reference implementation: `docs/demo/crlb_analysis.py` already uses
  this exact formula for the trench-demo CRLB analysis; the test gate
  here cross-checks the package implementation against it.
- Module: `packages/rfmesh-fusion/src/rfmesh_fusion/mle.py` already
  contains the analytic Jacobian (`_predicted_azimuths_and_jacobian`)
  with the project's azimuth convention encoded. This ticket does NOT
  import the private helper; it re-derives the Jacobian locally with
  the same convention (the helper is package-private and may be
  refactored by future tickets without breaking GDOP).
- AGENTS.md invariants: B3 (no silent fallbacks — degenerate geometry
  raises or returns `inf`, documented choice); B5 (fusion is pure —
  numpy-only).
- Prior tickets this depends on: WS-CD-001 (ENU projection helpers
  landed), WS-CD-002 (geometry helpers, `FusionError` /
  `DegenerateGeometryError` landed), WS-CD-003 (MLE refiner landed and
  encodes the same azimuth convention this ticket re-uses).
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-fusion -v` passes, including all prior
   WS-CD-00{1,2,3} tests still green. New tests in
   `tests/test_gdop.py`, each name pointing at one property:

   - `test_two_node_symmetric_baseline_low_gdop` — two nodes equidistant
     from the emitter on a perpendicular line (emitter at `(0, 1000)`,
     nodes at `(±1000, 0)`) → GDOP in a bounded sensible range
     (~1.4, the closed-form expectation for this symmetric 2-node case
     after range normalisation).
   - `test_three_node_equilateral_low_gdop` — emitter at origin, three
     nodes at the vertices of an equilateral triangle around it
     (circumradius 1000/√3 m for a unit-side triangle, scaled) →
     GDOP ≈ 1.155 (closed-form 2/√3 — the "ideal" three-node geometry),
     tolerance `< 0.3` of 1.0 as the ticket specifies.
   - `test_near_collinear_high_gdop` — three collinear nodes on the
     east axis (`(-1000, 0)`, `(0, 0)`, `(+1000, 0)`) and an emitter
     beyond the baseline with small perpendicular offset
     (`(2000, 50)`) → GDOP ≥ 10. The "geometry stretches the ellipse"
     diagnostic case.
   - `test_unweighted_independent_of_sigma` — `compute_gdop` does NOT
     take σ; verified by signature inspection (no `sigma` / `weights`
     parameter on the public API) and by behavioural test: the same
     node positions and emitter position produce one and the same GDOP
     value regardless of any synthetic σ scenario the caller might
     consider. **This encodes ADR-007 D4's unweighted-by-design
     decision; renaming or extending the signature to accept σ requires
     a new ADR.**
   - `test_gdop_matches_crlb_script` — for the trench-demo Beat C
     (three L1 nodes — A at (-1800, 1800), B at (1800, 1800), C at
     (0, 900); emitter at (0, 3000)) and Beat D (Beat C + L2 node D at
     (2000, 3500); emitter at (0, 3000)) geometries from
     `docs/demo/trench-demo-geometry.md` §2.2, `compute_gdop` returns
     1.16 ± 0.05 and 1.02 ± 0.05 respectively. The tolerance is the
     *display rounding* of the CRLB script (`{:.2f}`), not a
     hand-tuned slack.
   - `test_emitter_on_node_raises` — emitter position equal to one of
     the node positions → `compute_gdop` raises
     `DegenerateGeometryError` (Invariant B3, no silent fallbacks; same
     class as the closed-form Stansfield's rank-deficiency signal).
     The docstring documents this behaviour explicitly.
   - `test_two_nodes_minimum` — calling with one node raises
     `DegenerateGeometryError` (under-determined; `H^T H` is rank-1 so
     not invertible — refuse honestly rather than return `inf`).
   - `test_rank_deficient_geometry_raises` — three nodes truly
     collinear *and* emitter on the same line (e.g. emitter at
     `(2000, 0)`, nodes at `(-1000, 0)`, `(0, 0)`, `(+1000, 0)`)
     → `H` has rank 1, `H^T H` is singular → raises
     `DegenerateGeometryError`. The "exactly on the line" companion
     to `test_near_collinear_high_gdop`.

2. `uv run mypy packages/rfmesh-fusion` clean (workspace-strict default).

3. `uv run ruff check packages/rfmesh-fusion` clean.
   `uv run ruff format --check packages/rfmesh-fusion` clean.

4. Imports in `gdop.py` limited to:
   - `numpy`
   - `math`, `collections.abc`
   - intra-package: `from .exceptions import DegenerateGeometryError`

   No imports from `scipy`, `pyproj`, any sibling workstream package,
   or `rfmesh_contracts` (this module operates on raw `(east, north)`
   tuples and is contract-free by design — wiring to `BearingReport`
   lives in WS-CD-007).

5. `gdop.py` is pure (Invariant B5): no network, no file I/O, no
   subprocess, no hardware access. No module-level side effects beyond
   constant definitions.

6. `lint-imports` "Fusion is pure" KEPT (no new forbidden imports).

## Out of scope (explicit non-goals)

- Do **NOT** accept per-bearing σ / weights / `BearingReport` instances
  in `compute_gdop`'s signature. ADR-007 D4 is binding: GDOP is
  unweighted by design. A "weighted GDOP" variant is a *new* function
  in a *new* ADR.
- Do NOT compute covariance, Fisher information, the 95% ellipse, or
  any ellipse-derived field. Those live in WS-CD-004 (`covariance.py`).
- Do NOT compute per-node residuals. WS-CD-006 (`residuals.py`).
- Do NOT wire GDOP into a `Fuser` Protocol implementation or set
  `FixEvent.gdop`. WS-CD-007 (`fuser.py`).
- Do NOT modify `mle.py`'s `_predicted_azimuths_and_jacobian`. The
  helper is package-private; this ticket re-derives the Jacobian
  locally to keep the public APIs cleanly decoupled.
- Do NOT add a "fallback" path that returns `0.0` or some other magic
  value on degenerate geometry. Invariant B3 — raise loudly.
- Do NOT add `scipy`, `pyproj`, or any new runtime dependency.
- Do NOT change the `BearingReport` or any other contract. Invariant
  B1 — frozen contracts.

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/gdop.py` (create)
- `packages/rfmesh-fusion/tests/test_gdop.py` (create)
- `packages/rfmesh-fusion/tests/conftest.py` (extend ONLY if a new
  shared fixture is genuinely needed; do NOT remove or rename any
  existing fixture)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1)
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (WS-CD-007
  owns the public re-exports)
- `packages/rfmesh-fusion/src/rfmesh_fusion/{mle,stansfield,geometry,
  projection,covariance,residuals}.py` (other tickets' scope; WS-CD-004
  may be writing `covariance.py` concurrently, WS-CD-006 may be
  writing `residuals.py` concurrently — read-only)
- Anything outside `packages/rfmesh-fusion/`

## Implementation notes (non-binding, for guidance)

### The formula, exactly

Let the candidate emitter be `x = (x_e, x_n)` in ENU metres and each
node be `p_i = (p_e_i, p_n_i)`. With `Δe_i = p_e_i - x_e`,
`Δn_i = p_n_i - x_n`, `r_i^2 = Δe_i^2 + Δn_i^2`:

```
H[i, :] = [ -Δn_i / r_i^2 ,  Δe_i / r_i^2 ]     (rad / m)
```

— the same analytic Jacobian rows `mle.py` uses. The sign convention
follows `geometry.bearing_to_unit_vector`'s `(east, north) = (sin az,
cos az)` mapping; see `mle.py`'s docstring "WHY THE JACOBIAN ARG ORDER
IS atan2(east, north)" for the full derivation. Crucially for GDOP, the
sign of each row drops out of `H^T H` — both `mle.py`'s convention and
the CRLB script's opposite-sign convention produce the identical
2×2 `H^T H` and therefore the identical GDOP.

GDOP:

```
GDOP = sqrt( trace( (H^T H)^-1 ) / mean(r_i^2) )
```

Unitless. The `1/mean(r_i^2)` factor cancels the implicit `1/r^2`
in each Jacobian row, leaving a dimensionless layout-quality scalar.

### Degenerate-geometry behaviour — raise `DegenerateGeometryError`

The docstring documents this choice explicitly. Three cases:

1. `n_nodes < 2` — under-determined; raise.
2. Emitter exactly on a node (`r_i^2 == 0` for some `i`) — Jacobian
   row is singular at that node; raise.
3. `H^T H` is singular (rank-deficient: all nodes collinear with
   emitter on the same line) — `numpy.linalg.inv` raises
   `LinAlgError`; catch and re-raise as `DegenerateGeometryError`.

The alternative — returning `float("inf")` on rank-deficient geometry —
was considered and rejected: it forces every caller to test for `inf`,
which is exactly the kind of silent-degradation path Invariant B3
forbids. Raising puts the burden on the orchestrator
(`fuser.py`, WS-CD-007) to route to `fallback_centroid` per ADR-007 D3,
mirroring how `stansfield_seed`'s `DegenerateGeometryError` is already
handled.

### API

```python
from collections.abc import Sequence

import numpy as np

from .exceptions import DegenerateGeometryError


def compute_gdop(
    emitter_xy: tuple[float, float],
    node_positions_enu: Sequence[tuple[float, float]],
) -> float:
    """Compute GDOP per ADR-007 D4 — unweighted, range-normalised.

    Parameters
    ----------
    emitter_xy
        Emitter position ``(east_m, north_m)`` in the local ENU plane.
    node_positions_enu
        Sequence of ``(east_m, north_m)`` node positions in the same
        ENU frame. Length must be >= 2.

    Returns
    -------
    float
        Dimensionless GDOP. Low (~1) means a well-conditioned sensor
        layout; high (> ~6 per ``FusionConfig.gdop_warn_threshold``)
        means near-collinear geometry that stretches the position
        covariance regardless of per-bearing σ.

    Raises
    ------
    DegenerateGeometryError
        If fewer than 2 nodes are supplied, if the emitter coincides
        with any node (Jacobian singular at that bearing), or if all
        nodes are collinear with the emitter on the same line
        (``H^T H`` singular).
    """
```

### Numerical hygiene

- All accumulation in float64.
- Use `numpy.linalg.inv` on the 2x2 `H^T H` and catch
  `numpy.linalg.LinAlgError` — re-raise as `DegenerateGeometryError`.
  Do not use `numpy.linalg.pinv` (the pseudo-inverse would silently
  return a finite answer on rank-deficient input, exactly the
  silent-fallback Invariant B3 forbids).
- Compute `mean(r_i^2)` with float64 sum — these geometries have
  small `n`, no need for `math.fsum`-level Kahan summation.

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation the full output of:
    * `uv run pytest packages/rfmesh-fusion -v`
    * `uv run mypy packages/rfmesh-fusion`
    * `uv run ruff check packages/rfmesh-fusion`
    * `uv run lint-imports`
- If `test_gdop_matches_crlb_script` produces a value differing from
  1.16 or 1.02 by more than 0.05, **STOP**. The Jacobian or the formula
  has a bug — diff against `docs/demo/crlb_analysis.py` and reconcile.
  Do NOT hand-tune the test threshold.
- If you find yourself wanting to accept σ in the API "because it would
  be more useful" — **STOP**. ADR-007 D4 is binding. σ-weighting is the
  *covariance* concern (Fisher information, WS-CD-004), not the GDOP
  concern. The two-axis diagnostic story is the whole point of the
  unweighted choice; collapsing it requires a new ADR, not an
  implementation choice.
- If `H^T H` for a "should be invertible" geometry is reported as
  singular by numpy, double-check the Jacobian sign convention — both
  `mle.py`'s and the CRLB script's give the *identical* `H^T H` despite
  opposite row signs, so if they disagree numerically the bug is
  arithmetic, not conceptual.
