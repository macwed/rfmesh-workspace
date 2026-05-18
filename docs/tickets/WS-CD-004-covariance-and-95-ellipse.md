# TICKET WS-CD-004: Position covariance from Fisher information + 95% confidence ellipse

## Goal (one sentence)
Implement `covariance.py` — the module that turns a converged MLE emitter
position plus the same batch of `BearingReport`s + ENU node positions into
(a) the 2×2 position covariance Σ = (JᵀWJ)⁻¹ in metres² and (b) the 95%
confidence `EllipseENU` via the chi-square scaling that ADR-007 D5 and
ADR-009 fix, closing the honesty-payload half of the `FixEvent` (covariance
+ ellipse) that the dashboard and CoT publisher render.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.geospatial.EllipseENU`,
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.messages.FixEvent` (the `covariance_m2` /
  `confidence_ellipse_95` fields this module fills).
- Architecture references: `ARCHITECTURE.md` §7 ("honesty payload" on
  every fix); `INTERFACES.md` §0 (ENU convention; sigma in **radians**
  for weights); `INTERFACES.md` §2 (`EllipseENU` — 95% chi-square
  contour, `semi_major_m >= semi_minor_m`, `orientation_deg` from East
  toward North in [-180, +180]°); `INTERFACES.md` §3
  (`FixEvent.covariance_m2` as `(σ_xx, σ_xy, σ_yy)` unique entries of
  symmetric 2×2 in m²).
- Algorithm choices: `docs/adr/ADR-007-fusion-algorithm-choices.md` D2
  (analytic Jacobian, same as MLE step), D5 (closed-form 2×2
  eigendecomposition + `χ²_{0.95,2} = 5.991464547107979`).
- Math correction: `docs/adr/ADR-009-confidence-band-math-correction-and-demo-narrative.md`
  (1-σ vs 95%-chi-square scale — the load-bearing factor ≈ 2.45 this
  module must respect).
- Confidence policy: `docs/adr/ADR-005-fusion-confidence-policy.md`
  (the `semi_major_m / range_m` ratio downstream consumers compute
  from this module's output).
- CRLB ground truth: `docs/demo/crlb_analysis.py` +
  `docs/demo/trench-demo-geometry.md` §2.2 (the per-beat semi-major /
  semi-minor / GDOP numbers `compute_covariance` reproduces).
- Reusable helpers: `mle.py`'s `_predicted_azimuths_and_jacobian`
  documents the project's azimuth convention and the exact Jacobian
  shape; `covariance.py` mirrors that convention (a sign flip between
  the two would silently bias every ellipse).
- AGENTS.md / docs/ADVANTAGES.md.md invariants: B1 (contracts frozen — read-only
  imports), B3 (no silent fallbacks — singular `JᵀWJ` raises), B4
  (demo honesty payload — covariance + ellipse are what the dashboard
  shows), B5 (`rfmesh-fusion` stays pure: numpy only).
- Prior tickets this depends on: WS-CD-001 (ENU projection helpers),
  WS-CD-001b (mypy namespace config), WS-CD-002 (Stansfield seed,
  exceptions, geometry helpers), WS-CD-003 (MLE refinement + the
  analytic Jacobian whose sign and convention this module reuses).
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-fusion -v` passes, including every
   existing WS-CD-001/002/003 test still green. New tests added to
   `tests/test_covariance.py`, named so a failure points at exactly one
   property of the covariance / ellipse pipeline:

   - `test_chi_square_constant` — assert the hard-coded
     `CHI2_95_DF2 = 5.991464547107979` agrees with
     `scipy.stats.chi2.ppf(0.95, df=2)` to within 1e-12 (oracle:
     `pytest.importorskip("scipy.stats")`; if scipy is not installed
     the test is skipped, not failed). Documents the magic number and
     fails loudly if a future copy-paste typo'd it.
   - `test_isotropic_geometry_yields_circular_ellipse` — emitter at
     (0, 0); three nodes on a symmetric equilateral arc 3000 m away
     (every 120°); σ = 3° for all → covariance is near-scalar
     (`|σ_xy| / sqrt(σ_xx · σ_yy) < 1e-6`, `σ_xx ≈ σ_yy` to 1e-6
     relative), ellipse is near-circular
     (`semi_minor / semi_major > 0.999`).
   - `test_asymmetric_geometry_elongates_ellipse` — two nodes on the
     East baseline (`(-2000, 0)` and `(+2000, 0)`) both pointing at
     emitter at `(0, 3000)`, σ = 5° identical → ellipse semi-major is
     along the North direction (orientation ≈ ±90° from East), and
     `semi_major / semi_minor > 3` (much more uncertainty along the
     baseline-normal direction).
   - `test_ellipse_orientation_convention_east_to_north` — synthetic
     covariance matrix with known principal eigenvector along the
     direction 30° from East toward North → `orientation_deg` matches
     `30.0` to 1e-6°. Also tested at orientations of 0° (along East),
     90° (along North), −45° (South-East), to pin down range
     normalisation to `[-180, +180]°`.
   - `test_semi_major_ge_semi_minor_always` — generated 50 random PSD
     covariance matrices (seeded RNG, `cov = A @ A.T` for random A
     plus jitter) → `compute_covariance_to_ellipse` returns an
     `EllipseENU` whose Pydantic validator accepts (i.e. semi-major
     was sorted to ≥ semi-minor) every time.
   - `test_chi_square_inclusion_rate_matches_95_percent` — sample
     `n = 10_000` 2-D Gaussian draws with a known covariance,
     construct the 95% `EllipseENU` from that covariance, count how
     many samples fall inside the ellipse (point-in-rotated-ellipse
     test computed in the test, not in the module under test) →
     inclusion fraction is in [0.93, 0.97]. **The load-bearing
     honesty test**: the chi-square scaling is right iff the
     inclusion rate matches 0.95.
   - `test_trench_demo_geometry_reproduces_crlb` — feed the Beat-D
     geometry of `docs/demo/crlb_analysis.py` (4 nodes A/B/C/D, σ =
     5°/5°/5°/1.5°, emitter at `(0, 3000)`) into `compute_covariance`
     and `covariance_to_ellipse`. Assert `semi_major_m` is within
     ±10 % of 359 m (the published Beat-D CRLB number from
     `docs/demo/trench-demo-geometry.md` §2.2) and `semi_minor_m`
     within ±10 % of 125 m. **Catches azimuth-convention sign bugs
     end-to-end**: the same geometry the CRLB analysis script
     computes, the same chi-square scaling, the same Jacobian — any
     divergence between this module and `crlb_analysis.py` is a real
     bug, not a tolerance issue.
   - `test_trench_demo_beat_c_reproduces_crlb` — same as above but for
     Beat C (3 L1 nodes, σ = 5° each, same emitter): assert
     semi-major ≈ 393 m ± 10 %, semi-minor ≈ 357 m ± 10 %. Two beats,
     two independent gates on the Jacobian sign and the chi-square
     constant.
   - `test_singular_fisher_information_raises` — construct two nodes
     at the *same* position with parallel bearings (the rank-1
     geometry the closed-form Stansfield path raises
     `DegenerateGeometryError` on, but supplied here directly to
     `compute_covariance` after a hypothetical converged solve) →
     raises `SingularFisherInformationError`. The exception subclasses
     `FusionError` (Invariant B3: covariance refuses loudly rather
     than returning `inf` or numpy's silent `linalg.LinAlgError`).
   - `test_negative_eigenvalue_raises` — synthetic input that pushes
     the eigendecomposition into a non-PSD region (e.g. directly
     pass a non-PSD 2×2 matrix to `covariance_to_ellipse`; this is
     not a runtime path on real inputs but defends the function in
     isolation) → raises `SingularFisherInformationError` with a
     message naming "eigenvalue" or "PSD". Pinned so a future
     refactor that adds caller-side filtering does not silently
     accept a non-PSD matrix.
   - `test_covariance_to_ellipse_returns_ellipse_enu_instance` —
     identity-shaped covariance through `covariance_to_ellipse` →
     returns an `EllipseENU` instance (not a tuple or a dict). One
     `isinstance` assertion; pins the return type for downstream
     consumers (WS-CD-007 `Fuser.fuse`).
   - `test_fisher_information_error_hierarchy` — extends
     `test_exceptions.py`-style assertions: `SingularFisherInformationError`
     is a subclass of `FusionError`; *not* a subclass of
     `DegenerateGeometryError` or `MLEConvergenceError` (the three
     are siblings; each represents a structurally distinct failure
     mode in a different stage of the pipeline).

2. `uv run mypy packages/rfmesh-fusion` clean (strict mode, the
   workspace default per workspace-root `pyproject.toml`).

3. `uv run ruff check packages/rfmesh-fusion` clean.
   `uv run ruff format --check packages/rfmesh-fusion` clean.

4. Imports across the new module are limited to:
   - `numpy`
   - `math`, `typing`, `collections.abc`
   - `rfmesh_contracts.geospatial.EllipseENU`,
     `rfmesh_contracts.messages.BearingReport`
   - intra-package: `from .exceptions import SingularFisherInformationError`
   No `scipy`, no `pyproj`, no sibling-workstream package in the
   **source**. `scipy.stats` appears only inside
   `tests/test_covariance.py`, behind `pytest.importorskip`.

5. `covariance.py` is pure (Invariant B5): no network, no file I/O,
   no subprocess, no hardware. No module-level side effects beyond
   constant definitions.

6. The hard-coded `CHI2_95_DF2 = 5.991464547107979` sits next to a
   comment that cross-references `scipy.stats.chi2.ppf(0.95, df=2)`
   for reviewer verification *and* names ADR-009 as the rationale
   for why the chi-square scale (vs 1-σ) is the load-bearing choice.
   This comment is binding — Claude Code is not free to remove or
   paraphrase it in subsequent ticket churn (same discipline as
   ADR-005 D5(a)'s rosetta-stone comment in `confidence.py`).

7. The package-level `__init__.py` is **not** modified by this
   ticket. WS-CD-007 (`StansfieldMLEFuser` wiring) re-exports the
   public surface in one batch when it lands; pre-empting that here
   would force a merge conflict.

## Out of scope (explicit non-goals)

- Do NOT compute or return GDOP. That is WS-CD-005 (separate
  unweighted-H definition per ADR-007 D4).
- Do NOT compute per-node residuals as a public output. That is
  WS-CD-006 (`residuals.py`); the module here uses residuals only
  internally if at all (the closed-form covariance does not need
  them — it evaluates `JᵀWJ` at the converged position directly).
- Do NOT build the `Fuser` Protocol implementation or wire
  covariance into a `FixEvent`. That is WS-CD-007.
- Do NOT compute or apply the `ConfidenceLevel` policy from
  ADR-005 / ADR-009. That is `confidence.py`, a separate ticket;
  this module only produces the inputs (`covariance_m2`,
  `EllipseENU`) that policy consumes.
- Do NOT modify the package-level `__init__.py` to re-export the
  new surface. WS-CD-007 handles that batch (see Acceptance #7).
- Do NOT add `scipy`, `pyproj`, or any new runtime dependency. Scipy
  is used as an oracle inside one test, behind `pytest.importorskip`,
  and never imported from `covariance.py` itself.
- Do NOT change the `EllipseENU`, `FixEvent`, or `BearingReport`
  contracts. Invariant B1 — frozen.
- Do NOT introduce an "if eigenvalue is slightly negative, clamp to
  0" silent fallback. A non-PSD covariance is a structural input
  error — raise (Invariant B3).
- Do NOT compute the `covariance` from the *Stansfield* seed; per
  ADR-007 D2 the covariance is evaluated at the **MLE-converged**
  position (the seed is biased; the converged position is the MLE
  argument). The module's API takes the converged `(east, north)`
  as input and is agnostic to how the caller obtained it.

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/covariance.py` (create)
- `packages/rfmesh-fusion/src/rfmesh_fusion/exceptions.py` (modify —
  add `SingularFisherInformationError(FusionError)` only; do not
  touch the existing `FusionError`, `DegenerateGeometryError`, or
  `MLEConvergenceError` classes or their docstrings)
- `packages/rfmesh-fusion/tests/test_covariance.py` (create)
- `packages/rfmesh-fusion/tests/conftest.py` (extend only if a new
  fixture is genuinely needed; the existing `make_position`,
  `make_bearing`, `seeded_rng`, and `azimuth_node_to_emitter_deg`
  helpers cover the common cases — re-use rather than duplicate)
- `docs/tickets/WS-CD-004-covariance-and-95-ellipse.md` (this file)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1; read-only
  imports only)
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (WS-CD-007's
  batch; see Acceptance #7)
- `packages/rfmesh-fusion/src/rfmesh_fusion/{mle,stansfield,geometry,projection}.py`
  (other tickets' scope; read-only imports of public helpers only)
- `packages/rfmesh-fusion/tests/test_{exceptions,geometry,mle,projection,stansfield}.py`
  (other tickets' test scope; the new `SingularFisherInformationError`
  hierarchy assertion lives in `test_covariance.py`, not in
  `test_exceptions.py`, to keep this ticket self-contained)
- Anything outside `packages/rfmesh-fusion/` (except this ticket
  file)

## Implementation notes (non-binding, for guidance)

### API

```python
import numpy as np
from rfmesh_contracts.geospatial import EllipseENU
from rfmesh_contracts.messages import BearingReport
from collections.abc import Sequence


CHI2_95_DF2: float = 5.991464547107979
# scipy.stats.chi2.ppf(0.95, df=2). The 2-DOF 95% chi-square scale is
# what turns 1-σ principal variances into the 95%-confidence ellipse
# semi-axes the EllipseENU contract requires (INTERFACES.md §2). See
# ADR-009 for the historical 1-σ vs 95% scale mix-up that this
# constant exists to make impossible going forward.


def compute_covariance(
    emitter_xy: tuple[float, float],
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> np.ndarray:
    """Return the 2x2 position covariance at the MLE-converged emitter.

    Σ = (J^T W J)^-1 in m^2, with J the analytic Jacobian of predicted
    azimuths w.r.t. (east, north) emitter coordinates (radians per
    metre), W = diag(1 / σ_i_rad^2). Sigma is converted from degrees
    to radians once before forming W (same convention as solve_mle).
    """
    ...


def covariance_to_ellipse(cov_2x2: np.ndarray) -> EllipseENU:
    """Convert a 2x2 position covariance to a 95% confidence EllipseENU.

    Eigendecomposes Σ, sorts eigenvalues descending, scales each by
    sqrt(CHI2_95_DF2) to get semi-axes, derives orientation_deg from
    the major-axis eigenvector as atan2(north, east) in degrees
    range-normalised to [-180, +180].
    """
    ...
```

### Jacobian — reuse `mle.py`'s convention exactly

The project's azimuth convention is fixed by
`geometry.bearing_to_unit_vector`: azimuth = `atan2(east, north)` (east
first, north second). The predicted azimuth from node `(p_e, p_n)` to
emitter `(x_e, x_n)` is therefore `atan2(x_e - p_e, x_n - p_n)`. With
`Δe = p_e - x_e`, `Δn = p_n - x_n`, `r² = Δe² + Δn²`, the Jacobian row
for bearing `i` (radians per metre) is:

```
J[i, :] = [ -Δn / r² ,  Δe / r² ]
        = [ (x_n - p_n) / r² , -(x_e - p_e) / r² ]
```

This is **byte-identical** to `mle.py`'s `_predicted_azimuths_and_jacobian`
helper and to `docs/demo/crlb_analysis.py:jac_row`. Reuse the convention;
**do not re-derive** — a sign error here propagates into every confidence
ellipse the system ever publishes.

### Chi-square scaling

```
eigvals, eigvecs = np.linalg.eigh(cov_2x2)   # ascending
# Sort descending so the major axis is first.
order = np.argsort(eigvals)[::-1]
lambdas = eigvals[order]
vecs = eigvecs[:, order]

if lambdas[1] <= 0.0:
    raise SingularFisherInformationError(
        f"covariance_to_ellipse: non-PSD covariance, eigenvalues={lambdas}"
    )

semi_major = math.sqrt(CHI2_95_DF2 * lambdas[0])
semi_minor = math.sqrt(CHI2_95_DF2 * lambdas[1])

# Orientation: the major-axis eigenvector, projected as (east, north),
# gives the direction. atan2(north_component, east_component) in
# degrees is the angle from East toward North, which is exactly the
# EllipseENU convention (INTERFACES.md §2).
major_vec = vecs[:, 0]
orient_deg = math.degrees(math.atan2(major_vec[1], major_vec[0]))
# Wrap to (-180, +180] -- atan2 already does this for finite inputs.

return EllipseENU(
    semi_major_m=semi_major,
    semi_minor_m=semi_minor,
    orientation_deg=orient_deg,
)
```

### Numerical hygiene

- Float64 everywhere.
- Convert σ from degrees to radians **once**, before forming W.
- Use `np.linalg.eigh` (PSD-aware, returns real eigenvalues) rather
  than `np.linalg.eig` (general; returns complex).
- Use `np.linalg.solve(F, np.eye(2))` rather than `np.linalg.inv(F)`
  for the inversion step — same numerical hygiene as Stansfield and
  MLE. Or, if the matrix is tiny (2×2), use the closed-form
  inverse: `(1/det) · [[c, -b], [-b, a]]` — equally honest and
  avoids one numpy call. The implementation's choice; both are fine.
- Wrap the `np.linalg.LinAlgError` from a singular `JᵀWJ` solve into
  `SingularFisherInformationError` with a message naming the
  geometry (node positions, eigenvalues if available).

### Sigma in radians (load-bearing)

`BearingReport.azimuth_sigma_deg` is **degrees**; the Jacobian is in
**radians per metre**. The weights `W = diag(1/σ²)` must be in
`rad⁻²`, otherwise the covariance is off by `(π/180)² ≈ 3.05e-4`
across the board — i.e. every ellipse comes out ~57× too small,
which would silently demolish the honesty payload. Convert once,
outside any loop. Use `mle.py`'s `_inverse_variance_weights_rad`
pattern as reference (it lives in another module — re-derive
locally rather than importing a private helper).

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation the full output of:
  * `uv run pytest packages/rfmesh-fusion -v`
  * `uv run mypy packages/rfmesh-fusion`
  * `uv run ruff check packages/rfmesh-fusion`
  * `uv run ruff format --check packages/rfmesh-fusion`
- If the analytic Jacobian disagrees with `crlb_analysis.py:jac_row`
  on any one node — STOP. Do not hand-tune signs. Re-read
  `mle.py`'s module docstring and the azimuth-convention paragraph;
  the convention is settled.
- If `test_chi_square_inclusion_rate_matches_95_percent` produces an
  inclusion fraction outside [0.93, 0.97] — STOP. The χ²-scaling is
  wrong somewhere (most likely: missing the σ-deg-to-σ-rad
  conversion, or sorting eigenvalues ascending instead of
  descending). Diagnose; do **not** widen the tolerance.
- If `test_trench_demo_geometry_reproduces_crlb` produces a
  semi-major outside ±10 % of 359 m — STOP. Compare against
  `docs/demo/crlb_analysis.py` line-by-line; the disagreement is a
  bug in this module, not a tolerance issue.
- If any algorithm choice differs from D5 of ADR-007 — STOP and
  write a SCRATCHPAD entry. The ADR is settled; divergence from it
  is a new ADR proposal, not an implementation choice.
- If you find the `EllipseENU` contract is missing a field this
  module needs — STOP and write `docs/adr/ADR-NNN-...md` PROPOSED.
  Do not modify the contract.
