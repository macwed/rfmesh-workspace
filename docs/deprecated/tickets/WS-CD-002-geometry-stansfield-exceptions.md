# TICKET WS-CD-002: Bearing geometry helpers + Stansfield weighted-LS seed + fusion exceptions

## Goal (one sentence)
Implement the bearing-geometry primitives (`geometry.py`), the
closed-form Stansfield weighted-LS seed (`stansfield.py`), and the
fusion-internal exception hierarchy (`exceptions.py`) — the three
modules that together produce a deterministic emitter position estimate
from a sequence of `BearingReport`s in ENU coordinates, which the
sprint-1 MLE step (WS-CD-003) will then refine.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.geospatial.GeodeticPosition`.
- Architecture references: `ARCHITECTURE.md` §6 (AoA, not TDOA;
  bearings cross at the fix); `INTERFACES.md` §0 (ENU convention,
  azimuth = true north, CW positive); `INTERFACES.md` §2
  (`BearingReport.azimuth_deg`, `azimuth_sigma_deg`).
- Algorithm choices: `ADR-004-fusion-algorithm-choices.md` — D1
  (Stansfield classical weighted-LS), D3 (degenerate-geometry path —
  raises in this layer, caught by `fuser.py` in WS-CD-007).
- Module plan: `packages/rfmesh-fusion/docs/MODULE_PLAN.md` §3
  (`geometry.py`, `stansfield.py`), §4 (dependency graph — `geometry`
  feeds `stansfield`; `exceptions` feeds `stansfield`).
- Prior tickets this depends on: WS-CD-001 (projection helpers — used
  internally by tests that construct ENU node positions; not by the
  sources of this ticket directly), WS-CD-001b (mypy config + test
  scaffolding — assumed landed before this one).
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-fusion/tests/` (full package) passes,
   including all WS-CD-001 tests still green. New tests added:

   For `geometry.py`:
   - `test_bearing_to_unit_vector_cardinals` — azimuth 0° → (0, 1),
     90° → (1, 0), 180° → (0, -1), 270° → (-1, 0), each within 1e-12.
     The 0° = north, CW-positive convention is the load-bearing
     invariant; one assertion per cardinal, no parametrize-over-all,
     so a failure surfaces *which* cardinal broke.
   - `test_bearing_to_unit_vector_intermediate` — parametrized over
     {45°, 135°, 225°, 315°} confirming the diagonal directions
     within 1e-12. Catches sin/cos swaps and sign errors.
   - `test_bearing_to_unit_vector_wraps_360` — azimuth 360°, 720°,
     -360° all match azimuth 0° exactly. The function does not silently
     normalise its input; if the contract validator rejects out-of-range
     azimuths before they reach us, this test still passes (the function
     handles wrapping by using `math.radians` and trig periodicity).
   - `test_ray_ray_crossing_perpendicular` — ray from (-1, 0) heading
     east, ray from (0, -1) heading north → crosses at (0, 0)
     within 1e-9.
   - `test_ray_ray_crossing_parallel_returns_none` — two rays with
     identical unit direction return `None`, regardless of starting
     points. Anti-parallel rays (same line, opposite direction) also
     return `None` — the function cannot disambiguate a forward
     crossing from a backward one and refuses to guess.
   - `test_ray_ray_crossing_general_geometry` — a non-trivial
     configuration (e.g. node A at (0, 0) bearing 60°, node B at
     (1000, 500) bearing 210°), result matches a hand-computed
     expected position within 1e-6 m.
   - `test_weighted_centroid_equal_weights` — three crossings, equal
     weights → arithmetic mean within 1e-12.
   - `test_weighted_centroid_one_dominant_weight` — one weight 1000×
     larger than the others → result within 0.1% of the dominant
     crossing position.
   - `test_weighted_centroid_rejects_empty` — empty input raises
     `ValueError` (Invariant 4).
   - `test_weighted_centroid_rejects_mismatched_lengths` — crossings
     and weights of different lengths raise `ValueError`.

   For `stansfield.py`:
   - `test_stansfield_recovers_truth_equilateral_3_nodes` — emitter at
     (0, 0) in ENU; three nodes at the vertices of an equilateral
     triangle of side 4000 m centred on the emitter; perfect bearings
     (no noise); σ=1° for all → Stansfield recovers (0, 0) within
     1e-6 m.
   - `test_stansfield_recovers_truth_square_4_nodes` — emitter at
     (1500, 800); four nodes at the corners of a 5000 m square
     centred on origin; perfect bearings; σ=1° → recovery within
     1e-6 m.
   - `test_stansfield_two_bearings_exact` — minimum case: 2 perfect
     bearings → exact triangulation within 1e-9 m (over-determined
     becomes determined; result is the unique ray-ray crossing).
   - `test_stansfield_weights_favour_precise_bearing` — 3 nodes; one
     has σ=0.01° pointing at the *true* emitter; two have σ=10°
     pointing at deliberately-perturbed positions (still inside the
     basin, but a few hundred metres off). Result must be closer to
     the precise bearing's implied position than to the unweighted
     centroid of the three rays. Pins that `w_i = 1/σ_i²` actually
     enters the LS.
   - `test_stansfield_nearly_collinear_geometry_still_solves` — three
     nodes in a row at y=0, spaced 2000 m apart; emitter at (1000, 3000);
     perfect bearings → recovery within 1e-3 m. The matrix is poorly
     conditioned but not yet singular; tolerance is looser than the
     equilateral case but still tight.
   - `test_stansfield_raises_on_parallel_rays` — all bearings have
     azimuth 90°, so all rays are parallel → raises
     `DegenerateGeometryError`. The error message must mention
     "singular" or "degenerate" or "rank".
   - `test_stansfield_raises_on_coincident_nodes` — all node positions
     are the same point — every ray emanates from one origin and
     `(N^T W N)` is rank-1 → raises `DegenerateGeometryError`.
   - `test_stansfield_raises_on_below_minimum` — only 1 bearing → raises
     `DegenerateGeometryError` (or `ValueError` with a message naming the
     minimum count; agent's choice, document in the function docstring).

   For `exceptions.py`:
   - `test_fusion_error_hierarchy` — `DegenerateGeometryError` is a
     subclass of `FusionError`; both subclass `Exception` (not
     `BaseException`). One assertion per inheritance edge.

2. `uv run mypy packages/rfmesh-fusion` is clean.
3. `uv run ruff check packages/rfmesh-fusion` is clean.
4. Imports across the three new modules are limited to:
   - `numpy` (the first place numpy enters `rfmesh-fusion` — the LS
     solve in `stansfield.py` uses `numpy.linalg.solve`)
   - `math`, `typing`, `collections.abc`
   - `rfmesh_contracts.messages.BearingReport` (in `stansfield.py`,
     for the input type)
   - intra-package: `from .geometry import ...`, `from .exceptions
     import ...`
   No imports from `scipy`, `pyproj`, or any sibling workstream
   package.
5. The three new modules are pure (Invariant 5).
6. The package-level `__init__.py` is extended to re-export the public
   API surface this ticket introduces: `bearing_to_unit_vector`,
   `ray_ray_crossing`, `weighted_centroid_of_crossings`,
   `stansfield_seed`, `FusionError`, `DegenerateGeometryError`. (The
   internal helpers do not need re-exporting.)

## Out of scope (explicit non-goals)

- Do NOT implement MLE refinement. That is WS-CD-003.
- Do NOT compute covariance, ellipse, GDOP, or residuals. Those are
  WS-CD-004, -005, -006.
- Do NOT build the `Fuser` Protocol implementation or any
  `StansfieldMLEFuser` class. That is WS-CD-007.
- Do NOT add `scipy`, `pyproj`, or any new runtime dependency. Numpy is
  the only new dep introduced here, and it is already in the
  workspace's transitive dependency tree (used by `rfmesh-sdr`); the
  agent must add `numpy` to `packages/rfmesh-fusion/pyproject.toml`
  `[project] dependencies` explicitly (no relying on transitive
  resolution).
- Do NOT add per-bearing range-iterated reweighting (Kaplan's
  modification). Classical Stansfield only — see ADR-004 D1.
- Do NOT bake in a "default σ if BearingReport doesn't have one"
  fallback. `BearingReport.azimuth_sigma_deg` is a required field with
  a validator (`> 0`), so a `0` sigma cannot arrive at this layer; if
  one does, raise (do not invent a value — Invariant 4).
- Do NOT introduce randomness or noise injection in tests. All
  WS-CD-002 tests use deterministic perfect-bearing inputs; noise is
  exercised in WS-CD-008's honest-ellipse Monte Carlo.
- Do NOT compute or return any `EllipseENU`. That requires covariance
  (WS-CD-004).

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/geometry.py` (create)
- `packages/rfmesh-fusion/src/rfmesh_fusion/stansfield.py` (create)
- `packages/rfmesh-fusion/src/rfmesh_fusion/exceptions.py` (create)
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (modify —
  extend `__all__` and the re-exports per acceptance criterion 6)
- `packages/rfmesh-fusion/pyproject.toml` (modify — add `numpy` to
  `[project] dependencies`; pin a version range consistent with the
  rest of the workspace, e.g. `numpy>=2.0,<3.0` matching `rfmesh-sdr`)
- `packages/rfmesh-fusion/tests/test_geometry.py` (create)
- `packages/rfmesh-fusion/tests/test_stansfield.py` (create)
- `packages/rfmesh-fusion/tests/test_exceptions.py` (create)
- `packages/rfmesh-fusion/tests/conftest.py` (modify — add a
  `make_bearing` factory fixture for synthesising `BearingReport`
  values with sensible defaults; future tickets will reuse it. Do not
  touch the existing `make_position` and `origin` fixtures.)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant 1)
- `packages/rfmesh-fusion/src/rfmesh_fusion/projection.py` (WS-CD-001
  output; no regression to its API)
- `packages/rfmesh-fusion/src/rfmesh_fusion/{mle,covariance,gdop,
  residuals,confidence,fuser}.py` (other tickets' scope)
- Anything outside `packages/rfmesh-fusion/`

## Implementation notes (non-binding, for guidance)

### `exceptions.py`

```python
class FusionError(Exception):
    """Base class for all rfmesh-fusion-internal exceptions.

    Not part of the contracts (ARCHITECTURE.md "no exception hierarchy
    in contracts"); lives with the workstream that raises.
    """


class DegenerateGeometryError(FusionError):
    """Raised by the closed-form solver when the geometry is
    rank-deficient (parallel rays, coincident nodes, fewer-than-
    minimum bearings, or numerically singular normal matrix).

    Callers in the Fuser pipeline catch this and route to the
    fallback_centroid path per ADR-004 D3.
    """
```

No fields beyond the inherited `args`. If a richer payload is
genuinely needed by `fuser.py`'s log line, add a `reason: str`
keyword arg in a follow-up ticket; not in this scope.

### `geometry.py`

**`bearing_to_unit_vector(azimuth_deg: float) -> tuple[float, float]`**:
`return (math.sin(math.radians(azimuth_deg)),
math.cos(math.radians(azimuth_deg)))`. Two-line function. The 360°
wrap is handled by trig periodicity for free.

**`ray_ray_crossing(p1, u1, p2, u2) -> tuple[float, float] | None`**:
solve `p1 + t1·u1 = p2 + t2·u2` for `(t1, t2)`. This is a 2×2 linear
system in (t1, -t2). If the determinant of the 2×2 direction matrix is
below 1e-12 (rays parallel or anti-parallel), return `None`. Otherwise
return `p1 + t1·u1`.

**`weighted_centroid_of_crossings(crossings, weights)`**: assert
non-empty + lengths match; otherwise compute
`(Σ w_i · x_i / Σ w_i, Σ w_i · y_i / Σ w_i)`. Reject zero `Σ w_i`
defensively.

### `stansfield.py`

**API:**

```python
def stansfield_seed(
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """Closed-form weighted-LS Stansfield seed.

    Returns the emitter (east_m, north_m) position estimate in the same
    ENU frame as ``node_positions_enu``. Per-bearing weights are
    ``1 / azimuth_sigma_deg**2``; the constant deg²→rad² factor cancels
    in the LS and is omitted for numerical hygiene.

    Raises
    ------
    DegenerateGeometryError
        If ``len(bearings) < 2``, if the bearings and positions have
        different lengths, if all rays are parallel, or if the normal
        matrix is singular (determinant magnitude below 1e-12 times
        the trace squared).
    """
```

**Algorithm.** For each bearing $i$ with azimuth $\theta_i$ and node
position $\mathbf{p}_i$ in ENU:

1. Compute the unit direction $\hat{\mathbf{u}}_i = (\sin\theta_i,
   \cos\theta_i)$ via `bearing_to_unit_vector`.
2. Compute the unit normal $\hat{\mathbf{n}}_i = (\cos\theta_i,
   -\sin\theta_i)$ (rotate $\hat{\mathbf{u}}_i$ by 90° clockwise).
3. The signed perpendicular distance from a candidate emitter
   $\mathbf{x}$ to bearing-$i$'s line is $d_i = \hat{\mathbf{n}}_i
   \cdot (\mathbf{x} - \mathbf{p}_i)$.
4. Weight $w_i = 1 / \sigma_i^2$ where $\sigma_i$ is
   `bearings[i].azimuth_sigma_deg`. (Constant `deg→rad` scaling
   cancels.)
5. The 2×2 normal-equation system is:
   ```
   [Σ w_i n_x²       Σ w_i n_x n_y ] [x_e]   [Σ w_i n_x (n_x p_x + n_y p_y)]
   [Σ w_i n_x n_y    Σ w_i n_y²    ] [x_n] = [Σ w_i n_y (n_x p_x + n_y p_y)]
   ```
6. Solve with `numpy.linalg.solve`. If `numpy.linalg.LinAlgError`
   fires, re-raise as `DegenerateGeometryError` with the original
   message attached. Also pre-check `det(A) / (trace(A)² + ε)` against
   1e-12 to catch near-singular cases before they slip past
   `linalg.solve`'s tolerance.

**Numerical hygiene.** Build the normal matrix and RHS in float64.
Compute everything in ENU metres (do not mix with the global geodetic
frame). Do not normalise the unit vectors after computing them
(`math.sin`/`cos` already give exact unit length for any finite input).

### `conftest.py` — `make_bearing` fixture

Add (alongside `make_position` and `origin`):

```python
MakeBearing = Callable[..., BearingReport]


@pytest.fixture
def make_bearing() -> MakeBearing:
    """Factory for BearingReport with sensible defaults.

    Defaults cover the boring fields (timestamp, node_id, snr, etc.) so
    tests can write make_bearing(azimuth_deg=90.0, sigma_deg=1.0,
    node_position=...) and read like the test's intent. The full
    contract surface stays available via keyword overrides.
    """
    ...
```

Implement defaults that satisfy *every* validator in
`rfmesh_contracts.messages.BearingReport`: a non-empty `node_id`, a
plausible UTC timestamp, a positive `azimuth_sigma_deg`, etc. Look at
`messages.py` for the validator list — if a new required field has
been added since the module plan was written, surface it in the
ticket result, do not improvise.

### General

- Type hints throughout: `Sequence[float]`, `Sequence[BearingReport]`,
  `tuple[float, float] | None` (PEP 604), no `Optional`.
- Each function gets a docstring with a short summary, a more detailed
  rationale paragraph if any algorithmic choice deserves it, an
  `Algorithm` block for `stansfield_seed`, and explicit `Raises`
  documentation.
- Module-level docstrings reference ADR-004 and `MODULE_PLAN.md §3`
  the same way `projection.py` does.

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste the test/mypy/ruff output into the conversation.
- If any algorithm choice you make differs from D1/D3 of
  `ADR-004-fusion-algorithm-choices.md` — STOP and write a SCRATCHPAD
  entry under `.claude/scratchpad/ws-cd-2026-05-XX.md`. Algorithm
  divergence from the ADR is not an implementation choice; it is a new
  ADR.
- If you find that `BearingReport` is missing a field this module
  needs (e.g. you reach for `bearing.range_estimate_m` and it is not
  there), STOP and write `docs/adr/ADR-NNN-...md` PROPOSED. Do not
  modify the contract.
- If `numpy.linalg.solve` fails on a geometry the test suite expects
  to *succeed* (i.e. one of the recovery tests, not one of the
  degenerate ones), STOP — do not loosen the test tolerance. The
  algorithm is wrong, not the test.
- If you find that `weighted_centroid_of_crossings` is dead code (no
  caller in the resulting WS-CD-002 sources), surface this in the
  result. It is intended for `fuser.py`'s fallback path (WS-CD-007),
  but if WS-CD-007 ends up using `ray_ray_crossing` directly and
  pruning the centroid helper, knowing now means we can defer it.
