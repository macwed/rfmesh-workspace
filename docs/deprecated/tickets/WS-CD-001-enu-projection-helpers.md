# TICKET WS-CD-001: Implement WGS-84 ↔ local ENU flat-Earth projection

## Goal (one sentence)
Create the `projection.py` module in `rfmesh-fusion`, providing
WGS-84 ↔ local ENU conversion primitives that every downstream fusion
module (Stansfield, MLE, GDOP, residuals) will depend on, plus the
node-centroid origin-choice helper.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.geospatial.GeodeticPosition`
- Architecture references: `ARCHITECTURE.md` §6 (no GNSS dependency,
  surveyed positions), `INTERFACES.md` §0 (geodetic conventions: WGS-84,
  HAE, ENU East-North-Up right-handed)
- Design references: `packages/rfmesh-fusion/docs/MODULE_PLAN.md` §3
  `projection.py`
- Inherited context: none applicable (this is new code)
- Salvage: none — `rfmesh-fusion` is NEW per `SALVAGE_AUDIT.md` (no row
  for it; fusion is greenfield)
- Prior tickets this depends on: none. First ticket of the package.

## Acceptance criteria

Each line is something `just verify` or a specific command will check.

1. `uv run pytest packages/rfmesh-fusion/tests/test_projection.py -v`
   passes. Tests added:
   - `test_origin_roundtrips_to_zero` — `to_enu(origin, origin)` returns
     `(0.0, 0.0)` exactly.
   - `test_roundtrip_within_1m_at_10km` — for a grid of points within
     10 km of the origin (lat ± 0.09°, lon ± 0.09°), `from_enu(to_enu(p,
     o), o)` reproduces `p` to within 1 m, 1e-6° in lat/lon.
   - `test_north_displacement_matches_great_circle` — a point 1000 m due
     north of the origin (computed by formula `lat0 + 1000/R_earth ·
     180/π`) projects to ENU `(0.0, 1000.0)` within 0.5 m.
   - `test_east_displacement_scales_with_cos_lat` — a point 1000 m due
     east at latitude 60° projects to ENU `(1000.0, 0.0)` within 0.5 m
     (validates the `cos(lat0)` factor is applied).
   - `test_choose_enu_origin_returns_centroid` — for three known node
     positions in a triangle, `choose_enu_origin(...)` returns the
     mean-lat / mean-lon position (height set to 0.0, sigma to 0.0).
   - `test_choose_enu_origin_single_node` — degenerate case: one
     position in, that position out (height/sigma reset to 0/0).
   - `test_from_enu_sigma_passthrough` — `from_enu(..., origin,
     sigma_m=5.0)` returns a position with `sigma_m=5.0` (the caller
     sets it; projection does not invent it).
2. `uv run mypy packages/rfmesh-fusion` is clean.
3. `uv run ruff check packages/rfmesh-fusion` is clean.
4. The new module imports only from `numpy` and from
   `rfmesh_contracts.geospatial`. No imports from `scipy`, `pyproj`, or
   any sibling workstream package.
5. The new code is pure (no I/O, no network, no subprocess, no logging
   handlers that write to disk) — Invariant 5.

## Out of scope (explicit non-goals)

- Do NOT add fields to `GeodeticPosition` or any other contract type.
- Do NOT introduce `scipy`, `pyproj`, or any other new runtime
  dependency — see ADR-004 D1 and Section §1 of `MODULE_PLAN.md`.
- Do NOT implement bearing geometry, Stansfield, MLE, or any other
  fusion logic — those are WS-CD-002 through WS-CD-007.
- Do NOT use a full geodetic solver (Vincenty, ellipsoidal). Flat-Earth
  is the explicit choice; document its valid-domain limits in the
  module docstring.
- Do NOT silently degrade for far-from-origin queries — if a point is
  more than 50 km from the origin, log nothing but ensure the test
  matrix exposes the worst-case error; if it exceeds 10 m, raise
  `ValueError` (the operational range is well below this).

## Files you may touch

- `packages/rfmesh-fusion/src/rfmesh_fusion/projection.py` (create)
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` (modify —
  re-export `choose_enu_origin`, `to_enu`, `from_enu` so internal
  imports across the package stay clean)
- `packages/rfmesh-fusion/tests/test_projection.py` (create)
- `packages/rfmesh-fusion/tests/conftest.py` (create — add minimal
  `GeodeticPosition` factory helpers used here and by later tickets)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant 1)
- Anything outside `packages/rfmesh-fusion/`
- `packages/rfmesh-fusion/src/rfmesh_fusion/{geometry,stansfield,mle,
  covariance,gdop,residuals,confidence,fuser}.py` (other tickets'
  scope — WS-CD-002 through WS-CD-007)

## Implementation notes (non-binding, for guidance)

- Earth radius constant: `R_EARTH_M = 6_378_137.0` (WGS-84 equatorial).
  Define as a module-level `Final[float]` constant with the WGS-84
  reference in a comment.
- The flat-Earth formulae are:
  ```
  dx_east  = (lon_deg - lon0_deg) * cos(lat0_rad) * R_EARTH_M * π/180
  dy_north = (lat_deg - lat0_deg)                * R_EARTH_M * π/180
  ```
  Inverse:
  ```
  lat_deg  = lat0_deg + dy_north / R_EARTH_M * 180/π
  lon_deg  = lon0_deg + dx_east  / (cos(lat0_rad) * R_EARTH_M) * 180/π
  ```
- `choose_enu_origin` takes an `Iterable[GeodeticPosition]` and returns
  a `GeodeticPosition` with `lat_deg = mean(lat)`, `lon_deg =
  mean(lon)`, `hae_m = 0.0`, `sigma_m = 0.0`. The mean handles wrap-
  around naively (lon mean of -179° and +179° is wrong); document this
  limit explicitly. For our deployment scales (single-region operations
  inside a country), wrap is not a real risk; mark it as a known
  limitation in the docstring.
- `to_enu` returns a `tuple[float, float]` of `(east_m, north_m)`. Not
  a numpy array — the values are scalars and we want them to be cheap
  to compose in Python-level code without numpy overhead.
- `from_enu` constructs and returns a `GeodeticPosition`. The caller
  decides `sigma_m`; default `0.0` is fine for sketches but the
  *production* call site (the `fuser.py` final step) will pass the
  scalar derived from `EllipseENU.semi_major_m`.
- Module docstring states the valid domain (<50 km from origin), the
  approximation source (flat-Earth, `cos(lat0)` scaling for east), and
  references ADR-004 D1 / `MODULE_PLAN.md` §3.

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste the test output and `mypy`/`ruff` output into the conversation.
- If you find that `GeodeticPosition` needs a new field to support
  this work — STOP and write `docs/adr/ADR-NNN-...md` with status
  PROPOSED instead. Do not begin the contract change.
- If you find a need to import from a sibling workstream package —
  STOP and write a SCRATCHPAD entry under
  `.claude/scratchpad/ws-cd-2026-05-15.md` (Invariant 2).
- If the test for `test_roundtrip_within_1m_at_10km` fails by a wide
  margin (>10 m), STOP and check the formula — do not "fix" it by
  loosening the tolerance.
