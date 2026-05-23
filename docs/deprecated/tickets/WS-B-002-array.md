# TICKET WS-B-002: Ship the array-manifold and sample-covariance module

## Goal (one sentence)
Implement the steering-vector / array-manifold primitives for ULA, UCA,
and CUSTOM array geometries plus a snapshot-averaging sample-covariance
estimator and forward-backward smoothing — the substrate L2 MUSIC and
MVDR will eventually consume.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.enums.ArrayGeometry`,
  `rfmesh_contracts.config.ArrayConfig`,
  `rfmesh_contracts.protocols.CoherentIQBlock`.
- Architecture references: ARCHITECTURE.md §1 (L2), §2 (Axis 1 absorption).
- Interface references: INTERFACES.md §1 `ArrayGeometry`,
  §4 `ArrayConfig`, §5 `CoherentReceiver` (the consumer of this module
  is the future L2 estimator, which reads coherent IQ).
- Bootstrap: `bootstrap-B.md` Deliverable 2 (this ticket is exactly
  that deliverable).
- WS-A reference for the convention this module must match:
  `rfmesh_sdr.array.ArraySpec.steering_phases()` (read it, do not import
  it — it documents the array-local coordinate convention the
  simulator uses: boresight = +x, ULA on y-axis, UCA centred at origin,
  channel 0 at (radius, 0) for UCA. Your module must adopt the same
  convention or every L2 test against the simulator will be off by a
  sign/axis.)

## Design — read before coding

### What this module is, and what it is *not*
This is the *manifold* — given an angle θ, a wavelength λ, and an
array geometry, produce a unit-norm complex steering vector
`a(θ, λ) ∈ ℂ^N`. It is *not* an angle estimator; MUSIC and MVDR are
future tickets (WS-B-003 / WS-B-004) and will consume this manifold
plus a sample-covariance R to produce bearings.

Two reasons to ship it now, alongside the L1 ticket:

- It is fully testable against closed-form expectations
  (`a(θ_broadside) ≡ ones / sqrt(N)` for a ULA at broadside; UCA
  cyclic-symmetry properties) — no simulator dependency required.
- It is the first prerequisite for L2 work and can land in parallel
  with WS-B-001 (different files in the same package — see the
  `__init__.py` discipline below).

### API shape

```python
def steering_vector(
    *,
    geometry: ArrayGeometry,
    element_positions_m: np.ndarray,  # shape (N, 2), float64
    azimuth_rad: float,                # array-local; broadside = 0
    wavelength_m: float,
) -> np.ndarray:                       # shape (N,), complex128, unit-norm
    """Far-field steering vector for one direction.

    Convention: boresight = +x; ULA on y-axis; UCA centred at origin;
    channel 0 at (radius, 0) for UCA. Matches rfmesh_sdr.array.ArraySpec
    (read its module docstring before changing anything here)."""
    ...

def steering_matrix(
    *,
    geometry: ArrayGeometry,
    element_positions_m: np.ndarray,
    azimuths_rad: np.ndarray,          # shape (K,), float64
    wavelength_m: float,
) -> np.ndarray:                       # shape (N, K), complex128
    """Stack of K unit-norm steering vectors."""
    ...

def sample_covariance(
    block: CoherentIQBlock,             # shape (N, n_samples), complex64
    *,
    n_snapshots: int | None = None,    # None = single snapshot over whole block
) -> np.ndarray:                       # shape (N, N), complex128, Hermitian
    """Snapshot-averaged sample covariance R = (1/L) sum_l x_l x_l^H.

    If n_snapshots is None, treats the whole block as one snapshot.
    Otherwise splits the block into n_snapshots equal segments and
    averages."""
    ...

def forward_backward_smooth(R: np.ndarray) -> np.ndarray:
    """Forward-backward averaging: R_fb = (R + J R* J) / 2 where J is
    the exchange matrix. De-correlates pairs of coherent sources;
    standard subspace-DF preprocessing. Returns a fresh (N, N) complex128
    Hermitian matrix."""
    ...
```

### Closed-form anchors (the tests verify these)
- ULA broadside: at θ = 0, every steering phase is 0;
  `a = (1/√N) · ones(N)`.
- ULA endfire: at θ = π/2, phase i = -2π (d/λ) i;
  asserted against `exp(-1j * 2*pi * d/lambda * arange(N))/√N`.
- UCA cyclic symmetry: `a_UCA(θ)[i] = a_UCA(0)[(i - k) mod N]` where
  `k = round(θ / (2π/N))` for θ at a ring-element direction.
- Hermitian and positive-semidefinite: every sample covariance from
  random complex IQ has `R == R.conj().T` (to 1e-12) and all
  eigenvalues ≥ -1e-12.
- Forward-backward: for a single-source rank-1 R, `R_fb` has the same
  rank-1 dominant eigenvector (up to phase) as R, within tolerance.
- Cross-check against WS-A: the steering phases produced for a ULA
  match `rfmesh_sdr.array.ArraySpec.ula(...).steering_phases(...)`
  modulo the wavelength factor, byte-for-byte. (This test uses
  `from rfmesh_sdr.array import ArraySpec` in the *test*, not in the
  module — Invariant 5.)

### Performance budget
Not a focus — L2 MUSIC's hot path is the eigendecomposition, not the
manifold. Plain NumPy with `@` is fine. Don't pre-optimise.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-dsp/tests/test_array_manifold.py` and
   `tests/test_array_covariance.py` pass:
   - `test_ula_broadside_is_ones_over_sqrt_n`
   - `test_ula_endfire_phase_progression`
   - `test_ula_matches_simulator_convention` — direct byte-equal
     comparison of `steering_vector` phases vs
     `rfmesh_sdr.array.ArraySpec.ula(n, d).steering_phases(δ, λ)`. If
     this fails, the convention is wrong somewhere — do not patch one
     side to match the other without consulting the lead.
   - `test_uca_cyclic_symmetry`
   - `test_custom_geometry_round_trip` — feed (N, 2) random positions,
     verify each column of `steering_matrix` is unit-norm and matches a
     hand-derived expression.
   - `test_sample_covariance_hermitian`
   - `test_sample_covariance_psd`
   - `test_sample_covariance_n_snapshots_averaging` — splitting a
     block into K snapshots vs one snapshot of the same length yields
     the *same* R for identical IQ (deterministic check), then
     non-identical for random noise.
   - `test_forward_backward_preserves_hermitian`
   - `test_forward_backward_rank1_dominant_eigenvector`

2. **Golden-file tests (Invariant 3).** `tests/golden/`:
   - `manifold_ula_8elem_half_lambda.npz` — the (8, 361) steering matrix
     for a half-wavelength ULA over [0, 360)° at 1° step. Stored as
     complex128. Recomputed test asserts max-abs diff ≤ 1e-12.
   - `manifold_uca_6elem_quarter_lambda.npz` — same shape for a 6-elem
     UCA. Same tolerance.
   - `covariance_known_seed.npz` — sample covariance for a fixed
     deterministic IQ block (constructed in `golden_generator.py` from
     a known seed). Asserted byte-equal modulo float precision.

3. `uv run mypy packages/rfmesh-dsp` clean.
4. `uv run ruff check packages/rfmesh-dsp` clean.
5. Pure DSP — no I/O, no network, no subprocess, no `rfmesh_sdr` import
   *in package code*. Tests may import `rfmesh_sdr.array.ArraySpec`
   strictly for the convention-cross-check test (4 above).
6. `__init__.py` re-exports `steering_vector`, `steering_matrix`,
   `sample_covariance`, `forward_backward_smooth`. **Important for
   parallel WS-B-001:** when editing `__init__.py`, insert your new
   exports in alphabetical position within `__all__`; do not touch
   lines you did not add. This minimises merge conflict.

## Out of scope (explicit non-goals)

- Do NOT implement MUSIC or any angle estimator. That is WS-B-003.
- Do NOT implement MVDR null weights. That is WS-B-004.
- Do NOT touch the `BearingEstimator` Protocol or any contract type.
  (Invariant 1.)
- Do NOT import from `rfmesh_sdr` in package source. (Invariant 2 /
  Invariant 5.) Test-only import of `ArraySpec` for the cross-check is
  the only exception, and it goes through `tests/`.
- Do NOT vendor PyArgus. v0.1 ships a hand-rolled module — the algorithms
  here are tens of lines and well-understood. PyArgus stays on the
  parking-lot list for the day a vendor primitive saves real work.
- Do NOT add a runtime dependency.

## Files you may touch (create | modify)

- `packages/rfmesh-dsp/src/rfmesh_dsp/__init__.py`              (modify)
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_manifold.py`        (create)
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_covariance.py`      (create)
- `packages/rfmesh-dsp/tests/__init__.py`                       (create OR no-op if
                                                                 WS-B-001 created it)
- `packages/rfmesh-dsp/tests/conftest.py`                       (create OR extend if
                                                                 WS-B-001 created it —
                                                                 add fixtures, do NOT
                                                                 remove)
- `packages/rfmesh-dsp/tests/golden_generator.py`               (create OR extend —
                                                                 add manifold/covariance
                                                                 generation, do NOT
                                                                 remove L1 entries)
- `packages/rfmesh-dsp/tests/golden/manifold_ula_8elem_half_lambda.npz`   (generate + commit)
- `packages/rfmesh-dsp/tests/golden/manifold_uca_6elem_quarter_lambda.npz` (generate + commit)
- `packages/rfmesh-dsp/tests/golden/covariance_known_seed.npz`            (generate + commit)
- `packages/rfmesh-dsp/tests/test_array_manifold.py`            (create)
- `packages/rfmesh-dsp/tests/test_array_covariance.py`          (create)
- `packages/rfmesh-dsp/pyproject.toml`                          (modify — add `numpy`
                                                                 if not present;
                                                                 add `rfmesh-sdr` as
                                                                 *test-only* dependency
                                                                 if not present)

## Files you may NOT touch

- `packages/rfmesh-contracts/**`                                (FROZEN — Invariant 1)
- `packages/rfmesh-sdr/**`                                      (other workstream — Invariant 2)
- `packages/rfmesh-dsp/src/rfmesh_dsp/l1.py`                    (WS-B-001's file)
- `packages/rfmesh-dsp/src/rfmesh_dsp/rssi.py`                  (WS-B-001's file —
                                                                 salvaged)
- `packages/rfmesh-dsp/src/rfmesh_dsp/spectrum.py`              (WS-B-001's file —
                                                                 salvaged)
- `packages/rfmesh-dsp/src/rfmesh_dsp/constants.py`             (WS-B-001's file —
                                                                 salvaged)
- `packages/rfmesh-dsp/tests/test_l1_estimator.py`              (WS-B-001's file)
- `packages/rfmesh-dsp/tests/test_sigma_honesty.py`             (WS-B-001's file)
- `packages/rfmesh-dsp/tests/test_snr_invariant_25db.py`        (WS-B-001's file)
- Anything outside `packages/rfmesh-dsp/`

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste the test output and `mypy` / `ruff` output into the
  conversation.
- If `test_ula_matches_simulator_convention` fails, **do not "fix"
  either side**. Stop and write a scratchpad note — the convention
  must match by design, and a mismatch is either a sign error here or
  a misread of WS-A's module docstring; both need lead review before
  proceeding.
- If a contract change appears necessary, stop and write
  `docs/adr/ADR-NNN-<short>.md` (status PROPOSED) instead — do NOT
  proceed.
