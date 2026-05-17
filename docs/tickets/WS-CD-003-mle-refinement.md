# TICKET WS-CD-003: MLE refinement of the Stansfield seed (Gauss-Newton, analytic Jacobian)

## Goal (one sentence)
Implement an in-house Gauss-Newton refiner (`mle.py`) that takes the
closed-form Stansfield seed (WS-CD-002) and the same batch of
`BearingReport`s + ENU node positions, iterates to a maximum-likelihood
emitter position with an analytic Jacobian and inverse-variance
weighting, and returns a refined `(east_m, north_m)` together with an
iteration count and a convergence flag — closing the finite-sample bias
gap that the Stansfield estimator carries by construction.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.messages.BearingReport`.
- Architecture references: `ARCHITECTURE.md` §6 (AoA cross-fix, not
  TDOA); `INTERFACES.md` §0 (ENU convention, azimuth = true north, CW
  positive); `INTERFACES.md` §3 (`FixEvent.method = "stansfield+mle"`).
- Algorithm choices: `docs/adr/ADR-007-fusion-algorithm-choices.md` D2
  (in-house Gauss-Newton with analytic Jacobian, `||Δx|| < 1e-3 m`,
  max 50 iterations, no scipy runtime dep), D3 (degeneracy on the
  closed-form path is caught in `stansfield.py`; MLE divergence is the
  parallel honesty-loud failure on this path).
- Module: `packages/rfmesh-fusion/src/rfmesh_fusion/stansfield.py` (the
  seed), `packages/rfmesh-fusion/src/rfmesh_fusion/geometry.py`
  (`bearing_to_unit_vector` — fixes the azimuth convention that the
  Jacobian must respect), `packages/rfmesh-fusion/src/rfmesh_fusion/
  exceptions.py` (`FusionError` base class).
- AGENTS.md invariants: B3 ("no silent fallbacks") — divergence raises;
  B5 ("pure DSP and fusion") — `rfmesh-fusion` stays numpy-only.
- Prior tickets this depends on: WS-CD-001 (ENU projection helpers
  landed), WS-CD-001b (mypy namespace-packages config landed), WS-CD-002
  (Stansfield seed, geometry helpers, `FusionError` /
  `DegenerateGeometryError` landed).
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-fusion/tests/ -v` passes, including
   all WS-CD-001 + WS-CD-002 tests still green. New tests added to
   `tests/test_mle.py` and named so a failure points at exactly one
   property of the refiner:

   - `test_mle_result_dataclass_shape` — `MLEResult` exposes
     `position: tuple[float, float]`, `n_iter: int`, `converged: bool`
     in that order; constructed as a frozen dataclass (or equivalent
     immutable container).
   - `test_mle_recovers_truth_equilateral_3_nodes_noise_free` — emitter
     at `(0, 0)`, three nodes at the vertices of an equilateral triangle
     of side 4000 m centred on the emitter, perfect bearings, σ = 1° for
     all → MLE converges in ≤ 5 iterations from the Stansfield seed,
     final position within 1e-6 m, `converged = True`.
   - `test_mle_recovers_truth_square_4_nodes_noise_free` — emitter at
     `(1500, 800)`, four nodes at the corners of a 5000 m square centred
     on origin, perfect bearings, σ = 1° → MLE within 1e-6 m of truth in
     ≤ 5 iterations.
   - `test_mle_converges_from_perturbed_seed` — same equilateral-3
     scenario, but seed manually perturbed 200 m from the Stansfield
     output → MLE still converges in ≤ 10 iterations to within 1e-6 m
     of truth. Demonstrates the basin extends beyond the
     Stansfield-derived starting point and is not a coincidence of the
     Stansfield+MLE step being a no-op on noise-free input.
   - `test_mle_residual_norm_strictly_less_than_stansfield` —
     parametrised over three synthetic scenarios (3 nodes / 4 nodes /
     6 nodes) with Gaussian noise on the bearings (σ = 3°, seeded RNG
     for determinism), MLE's final weighted residual sum-of-squares is
     strictly less than the Stansfield-seed RSS on every scenario. This
     is the bias-closing property in numerical form.
   - `test_mle_matches_scipy_oracle_within_1e-6` — single reference
     scenario (3 nodes, 5° σ, fixed seed, fixed noise realisation).
     `scipy.optimize.least_squares` is invoked **inside the test, with
     a guarded import** (`pytest.importorskip("scipy.optimize")`)
     against the same residual function with `method="lm"` and the same
     Stansfield seed. Refined positions must agree within 1e-6 m. Scipy
     is the **oracle** here only; it does **not** become a runtime
     dependency of `rfmesh-fusion`. If scipy is not installed, the test
     is skipped, not failed.
   - `test_mle_bias_correction_monte_carlo` — 3 nodes, 5° σ, 3 km
     stand-off, 1000 Monte-Carlo trials with seeded numpy RNG:
       * the mean Euclidean distance of the MLE position to ground
         truth is within 5 m;
       * the mean Euclidean distance of the Stansfield seed to ground
         truth is in the 15–30 m band (this is the *bias* the MLE
         closes; the test pins it both ways so a future regression that
         silently turns MLE into a no-op is caught).
     The test is marked `@pytest.mark.slow` (a custom marker registered
     in the package `pyproject.toml`'s `tool.pytest.ini_options`)
     because 1000 trials cost real seconds; default `pytest` collects
     and runs it, `pytest -m "not slow"` skips it for the inner-loop
     `just verify` of WS-CD-003 follow-ups. The mark exists so
     downstream tickets can do the same; it is fine to land the
     registration here.
   - `test_mle_diverges_raises_mle_convergence_error` — construct a
     synthetic input that pushes Gauss-Newton outside the basin
     (e.g. seed manually overridden to 50 km from the node centroid
     with mutually-cancelling bearings, or `max_iter=2` with a
     hand-perturbed seed) → `MLEConvergenceError` is raised. The
     exception's message names "diverged" or "did not converge" and
     reports the iteration count. The exception subclasses `FusionError`
     (Invariant B3: refuse loudly, never silently keep iterating or
     silently return the last iterate).
   - `test_mle_damping_parameter_smoke` — calling
     `solve_mle(..., damping=1e-3)` on the equilateral scenario
     still converges within tolerance and to within 1e-3 m of the
     undamped result. Smoke-only — locks the call signature; the
     numerical equivalence of LM-damped and undamped solutions on
     well-conditioned problems is not the point, the *parameter
     plumbing* is. Damping defaults to `0.0` and a damping of `0.0`
     produces pure Gauss-Newton (i.e. is byte-for-byte equal to
     calling `solve_mle` without the kwarg).
   - `test_mle_analytic_jacobian_matches_finite_differences` — at a
     handful of representative geometries (the equilateral seed
     position, a 4-node square seed, one perturbed point), the
     analytic Jacobian (built by the helper the implementation uses,
     however it is named internally) agrees with a central-difference
     Jacobian (step 1e-3 m) to within 1e-6 rad/m elementwise. Pure
     numpy — no scipy. This is the gate that catches a sign error in
     the derivation *before* it propagates into bias.
   - `test_fusion_error_hierarchy_includes_mle` — extends
     `test_exceptions.py`'s existing assertions:
     `MLEConvergenceError` is a subclass of `FusionError`;
     `MLEConvergenceError` is *not* a subclass of
     `DegenerateGeometryError` (it is the divergence path, distinct
     from the rank-deficiency path). One assertion per inheritance
     edge.

2. `uv run mypy packages/rfmesh-fusion` is clean (strict mode, the
   workspace default per `pyproject.toml`).

3. `uv run ruff check packages/rfmesh-fusion` is clean.

4. Imports across the new module are limited to:
   - `numpy`
   - `math`, `typing`, `collections.abc`, `dataclasses`
   - `rfmesh_contracts.messages.BearingReport`
   - intra-package: `from .exceptions import ...`, `from .geometry
     import bearing_to_unit_vector` (used only if the implementation
     decides to share the azimuth-convention point of truth — see
     implementation notes; the predicted azimuth comes from `atan2`
     of node-minus-emitter offsets, which is the *inverse* of
     `bearing_to_unit_vector`, so the geometry helper is not
     strictly required, but reusing it for any forward-direction
     work is fine)
   No imports from `scipy`, `pyproj`, or any sibling workstream
   package in the **source**. `scipy.optimize` appears only inside
   `tests/test_mle.py`, behind `pytest.importorskip`.

5. `mle.py` is pure (Invariant B5): no network, no file I/O, no
   subprocess, no hardware access. No module-level side effects beyond
   constant definitions.

6. `pyproject.toml` of `rfmesh-fusion` registers the `slow` pytest
   marker (one line in `[tool.pytest.ini_options]`'s `markers`). No
   change to `[project] dependencies` — numpy is already present from
   WS-CD-002.

7. The package-level `__init__.py` re-exports the public API surface
   this ticket introduces: `solve_mle`, `MLEResult`,
   `MLEConvergenceError`. (Internal helpers — residual function,
   Jacobian builder — are not re-exported; they are package-private.)

## Out of scope (explicit non-goals)

- Do NOT compute covariance, the Fisher information matrix, the 95%
  ellipse, or any ellipse-derived field. That is WS-CD-004. The MLE
  here returns only the refined position + iteration count +
  convergence flag.
- Do NOT compute or return GDOP. That is WS-CD-005.
- Do NOT compute or return per-node residuals on the converged solution
  as a public field of `MLEResult`. Residuals as an end-user diagnostic
  live in WS-CD-006 (`residuals.py`); the MLE algorithm uses residuals
  internally only, and the test that checks "MLE RSS < Stansfield RSS"
  re-derives them at the test edge, which is fine.
- Do NOT build the `Fuser` Protocol implementation, the
  `StansfieldMLEFuser` class, or wire MLE into any orchestrator. That
  is WS-CD-007.
- Do NOT add `scipy`, `pyproj`, or any new runtime dependency. Scipy is
  used as an *oracle inside one test*, behind `pytest.importorskip`,
  and never imported from `mle.py` itself.
- Do NOT introduce IRLS or any outlier-down-weighting iteration on top
  of the inverse-variance weights. Sprint-1 honesty-over-robustness is
  explicit (ADR-007 §"Negative consequences", `HANDOFF.md` §4 item 8).
- Do NOT add a "default σ if `BearingReport.azimuth_sigma_deg` is zero"
  fallback. The contract validator enforces strict positivity; trusting
  that is Invariant B3.
- Do NOT modify the Stansfield seed to round-trip through MLE
  internally. Stansfield stays a separable seed; the dashboard's
  "Stansfield vs Stansfield+MLE" side-by-side (ADR-007 §Neutral) needs
  both outputs visible.
- Do NOT change the `BearingReport` contract, add fields, rename
  fields, or relax validators. Invariant B1 — frozen contracts.

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/mle.py` (create)
- `packages/rfmesh-fusion/src/rfmesh_fusion/exceptions.py` (modify —
  add `MLEConvergenceError(FusionError)`; do not touch `FusionError`
  or `DegenerateGeometryError`)
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (modify —
  extend `__all__` and the re-exports with `solve_mle`, `MLEResult`,
  `MLEConvergenceError`; do not touch existing entries)
- `packages/rfmesh-fusion/pyproject.toml` (modify — add the `slow`
  marker to `[tool.pytest.ini_options]`'s `markers`; if that section
  does not exist yet, create it alongside the existing pytest config)
- `packages/rfmesh-fusion/tests/test_mle.py` (create)
- `packages/rfmesh-fusion/tests/test_exceptions.py` (modify — extend
  with the `MLEConvergenceError` hierarchy assertion only; do not
  touch the existing `FusionError` / `DegenerateGeometryError`
  assertions)
- `packages/rfmesh-fusion/tests/conftest.py` (extend — add a small
  helper for the Monte-Carlo scenarios and/or a `seeded_rng` fixture
  if useful. Do not touch the existing `make_position`, `origin`, or
  `make_bearing` fixtures' signatures or defaults; extension only)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1)
- `packages/rfmesh-fusion/src/rfmesh_fusion/stansfield.py` (WS-CD-002
  output; the seed's API is consumed verbatim)
- `packages/rfmesh-fusion/src/rfmesh_fusion/geometry.py` (WS-CD-002
  output; `bearing_to_unit_vector` is read-only)
- `packages/rfmesh-fusion/src/rfmesh_fusion/projection.py` (WS-CD-001
  output; no regression to its API)
- `packages/rfmesh-fusion/src/rfmesh_fusion/{covariance,gdop,residuals,
  confidence,fuser}.py` (other tickets' scope; do not pre-create
  empty modules either)
- Anything outside `packages/rfmesh-fusion/`

## Implementation notes (non-binding, for guidance)

### Azimuth convention and the analytic Jacobian — *do not re-derive, use this*

The project's azimuth convention is fixed by
`geometry.bearing_to_unit_vector`:

> Azimuth in degrees, true north = 0, clockwise positive. So azimuth
> `0` points along +north, azimuth `90` along +east. The mapping is
> `east = sin(az_rad)`, `north = cos(az_rad)`.

The **predicted** azimuth from a candidate emitter `(x_e, x_n)` to a
node at `(p_x, p_y)` (both in ENU metres) is therefore the *inverse*:
the unit vector from emitter to node is
`((p_x - x_e) / r, (p_y - x_n) / r)` with `r = sqrt((p_x - x_e)^2 +
(p_y - x_n)^2)`, and the azimuth that produces this unit vector under
the `(east, north) = (sin, cos)` convention is:

```
θ_pred(x_e, x_n) = atan2(p_x - x_e, p_y - x_n)
```

Note the argument order: `atan2(east_offset, north_offset)`, **not**
the maths-textbook `atan2(y, x)` with `y = north`. This trips agents
up; checking against `bearing_to_unit_vector(θ_pred)` =
`((p_x - x_e)/r, (p_y - x_n)/r)` on one example before continuing is
cheap insurance.

**Derivation of `∂θ_pred/∂x_e` and `∂θ_pred/∂x_n`.** Let
`Δe = p_x - x_e`, `Δn = p_y - x_n`, `r² = Δe² + Δn²`. The standard
derivative identities are:

```
∂ atan2(a, b) / ∂a =  b / (a² + b²)
∂ atan2(a, b) / ∂b = -a / (a² + b²)
```

Applied to `θ_pred = atan2(Δe, Δn)` with `∂Δe/∂x_e = -1`,
`∂Δn/∂x_n = -1`:

```
∂θ_pred / ∂x_e = (Δn / r²) * (-1) = -Δn / r²  =  -(p_y - x_n) / r²
∂θ_pred / ∂x_n = (-Δe / r²) * (-1) = Δe / r²  =   (p_x - x_e) / r²
```

The Jacobian row for bearing `i` is therefore:

```
J[i, :] = [ -(p_y_i - x_n) / r_i² ,  (p_x_i - x_e) / r_i² ]
```

with `r_i² = (p_x_i - x_e)² + (p_y_i - x_n)²`. **Units are radians
per metre** — convert measured σ to radians before forming `W`, or
keep both in degrees consistently; do **not** mix.

### Gauss-Newton step

Residual vector (radians, wrapped to `[-π, π]` to handle the 0°/360°
seam):

```
r_i = wrap_to_pi( θ_measured_i_rad - θ_pred_i_rad )
```

Weight matrix:

```
W = diag( 1 / σ_i_rad² )
```

Normal equations:

```
( Jᵀ W J )  Δx  =  Jᵀ W r
```

Solve with `numpy.linalg.solve` on the 2×2 system; on
`numpy.linalg.LinAlgError`, raise `MLEConvergenceError` (this is a
*divergence* / *singular update* event, structurally different from
the Stansfield rank-deficiency path).

Update:

```
x_new = x + Δx     (no line search; the basin is convex on
                    non-degenerate geometries seeded by Stansfield)
```

Convergence: `||Δx|| < 1e-3 m` and `n_iter <= 50`.
Divergence: `||x_new - x_seed|| > 50_000 m` (50 km from the seed; same
threshold as ADR-007 D3 uses against the node centroid — keeping the
two thresholds aligned makes the operator-facing failure stories
read the same way).

### Damping stub (Levenberg-Marquardt opt-in)

Per the lead's brief: add `damping: float = 0.0` as a keyword
argument. When non-zero, replace `(Jᵀ W J)` by
`(Jᵀ W J + damping * I_2)` before solving — one extra add to the
diagonal, two lines of code. Default `0.0` is pure Gauss-Newton (the
ADR-007 D2 choice). The parameter exists so a follow-up ticket can
turn on damping without re-shaping the API if real-data geometries
surface a convergence issue (ADR-007 §"Negative consequences", and
the WS-CD-to-Lead risk register §"Gauss-Newton convergence on real
geometries").

### API

```python
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from rfmesh_contracts.messages import BearingReport


@dataclass(frozen=True, slots=True)
class MLEResult:
    """Refined emitter position from Gauss-Newton MLE.

    Attributes
    ----------
    position
        ``(east_m, north_m)`` in the same ENU frame as the seed and
        node positions.
    n_iter
        Number of Gauss-Newton iterations actually run (>= 1 on
        success; equal to ``max_iter`` only on the divergence-by-
        budget path, which raises before returning).
    converged
        ``True`` iff the final ``||Δx|| < tol`` and the trajectory
        stayed inside the divergence radius. On any other terminal
        condition the function raises ``MLEConvergenceError`` rather
        than returning ``converged = False`` — the flag is on the
        result type for symmetry with future variants, not to label
        silent failures.
    """

    position: tuple[float, float]
    n_iter: int
    converged: bool


def solve_mle(
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
    seed_xy: tuple[float, float],
    *,
    tol_m: float = 1e-3,
    max_iter: int = 50,
    divergence_radius_m: float = 50_000.0,
    damping: float = 0.0,
) -> MLEResult:
    """Refine the Stansfield seed via Gauss-Newton with analytic Jacobian.

    See module docstring for the algorithm and the ∂θ/∂x derivation.

    Raises
    ------
    MLEConvergenceError
        If ``max_iter`` iterations elapse without ``||Δx|| < tol_m``,
        the position drifts more than ``divergence_radius_m`` from the
        seed, or ``numpy.linalg.solve`` reports a singular update
        system. The exception message names the cause and the
        iteration count.
    """
    ...
```

`solve_mle` does **not** call `stansfield_seed` itself — callers
(eventually `StansfieldMLEFuser` in WS-CD-007, the tests here in the
meantime) compute the seed and pass it in. Separation of concerns:
this module knows nothing about Stansfield; it knows how to refine
*any* seed by Gauss-Newton.

### `exceptions.py` addition

```python
class MLEConvergenceError(FusionError):
    """Raised by ``solve_mle`` when Gauss-Newton fails to converge.

    Concretely: ``max_iter`` iterations elapsed without ``||Δx|| <
    tol_m``, the iterate drifted outside ``divergence_radius_m`` of
    the seed, or ``numpy.linalg.solve`` reported a singular update
    system (indicating the Jacobian collapsed mid-iteration — the
    bearings effectively went rank-1 at the current iterate, e.g.
    because the iterate is sitting on top of a node).

    Distinct from ``DegenerateGeometryError``: that is raised by the
    *closed-form* Stansfield seed when the input geometry is
    rank-deficient ab initio; ``MLEConvergenceError`` is the
    parallel-but-separate honesty-loud signal on the iterative
    refinement path. Both are caught by ``fuser.py`` (WS-CD-007),
    which routes ``DegenerateGeometryError`` to ``fallback_centroid``
    and (per ADR-007 D3) decides separately how to handle MLE
    divergence (likely: return the Stansfield seed with
    ``confidence_level = LOW`` and ``method = "stansfield"``).
    """
```

### Numerical hygiene

- All accumulation in float64.
- Convert σ from degrees to radians **once**, outside the iteration
  loop. Store the radian σ vector and the squared-inverse weight
  vector as 1-D numpy arrays.
- Wrap the residual `θ_measured - θ_pred` to `[-π, π]` *every*
  iteration before forming `Jᵀ W r`. A 1° emitter near the 0°/360°
  seam otherwise produces a ~2π residual that destroys the step.
  Use `numpy.arctan2(numpy.sin(δ), numpy.cos(δ))` or equivalent —
  not `(δ + π) % (2π) - π` (that fails on inputs slightly outside
  `[-π, π]` due to round-off at the boundary).
- Build `J` as a `(n, 2)` numpy array per iteration. The geometry is
  small (handful of nodes); the allocation cost is negligible
  compared to the readability win over hand-rolled element sums.
- `Jᵀ W J` is a 2×2 numpy matrix; solve with `numpy.linalg.solve`,
  not `numpy.linalg.inv`-then-multiply. Same hygiene rule as
  `stansfield.py`.

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation the full output of:
    * `uv run pytest packages/rfmesh-fusion -v`
    * `uv run mypy packages/rfmesh-fusion`
    * `uv run ruff check packages/rfmesh-fusion`
- If the analytic Jacobian (`test_mle_analytic_jacobian_matches_
  finite_differences`) does **not** match central differences to
  within 1e-6 rad/m, **STOP**. Do **not** hand-tune the Jacobian
  signs to make scipy or finite differences happy — the derivation
  in the implementation notes is the contract; a disagreement means
  either the convention assumption is wrong (in which case the
  problem is in `geometry.bearing_to_unit_vector`'s usage and needs
  a SCRATCHPAD note before any code change) or the test geometry
  triggers a `r → 0` singularity (emitter at a node). Write a
  scratchpad entry under `.claude/scratchpad/ws-cd-2026-05-XX.md`
  naming what disagrees with what, before changing anything.
- If `solve_mle` and `scipy.optimize.least_squares` disagree on the
  oracle scenario by more than 1e-6 m, **STOP**. Same reasoning —
  scipy on an analytically-derivable problem with the same residual
  function is the trusted oracle, and a disagreement is a bug in
  the analytic path (sign error, wrap-to-π forgotten, units mixed),
  *not* a tolerance to relax. Write a scratchpad entry.
- If you find that `MLEResult.converged = False` is unreachable in
  the implementation (because every non-converged terminal condition
  raises), surface this in the result text. The field is on the type
  deliberately for future-symmetry (a future LM variant may want to
  return a non-converged-but-not-divergent result with a confidence
  downgrade rather than raising); confirming this is the case
  documents the contract.
- If any algorithm choice you make differs from D2 of `ADR-007-fusion-
  algorithm-choices.md` — STOP and write a SCRATCHPAD entry. The ADR
  is settled; algorithm divergence from it is a new ADR proposal, not
  an implementation choice.
- If you find that `BearingReport` is missing a field this module
  needs, STOP and write `docs/adr/ADR-NNN-...md` PROPOSED. Do not
  modify the contract.
