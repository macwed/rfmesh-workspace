# TICKET WS-B-001: Ship the L1 amplitude-sweep BearingEstimator with honest σ

## Goal (one sentence)
Implement a sweep-aware L1 `BearingEstimator` in `rfmesh-dsp` that consumes
per-heading IQ blocks from a servo-swept antenna, finds the RSSI peak,
emits a `BearingReport` with `method=Capability.L1_RSSI` and an honest
`azimuth_sigma_deg` validated against Monte-Carlo ground truth.

## Context (links only, not content)
- Contracts touched (read-only):
  `rfmesh_contracts.protocols.BearingEstimator`,
  `rfmesh_contracts.protocols.IQBlock`,
  `rfmesh_contracts.messages.BearingReport`,
  `rfmesh_contracts.enums.Capability`,
  `rfmesh_contracts.geospatial.GeodeticPosition`.
- Architecture references: ARCHITECTURE.md §1 (L1 capability),
  §2 (axes — Axis 1/2 absorbed via the σ-weighted BearingReport).
- Interface references: INTERFACES.md §3 `BearingReport` (the honesty of
  `azimuth_sigma_deg` is load-bearing), §5 `BearingEstimator`.
- Inherited context: INHERITED_CONTEXT.md §1.3 (no SDR is power-calibrated;
  outputs are dBFS/SNR, never dBm), §5.1 (the 25 dB SNR-invariant
  regression anchor — folded into Acceptance 5 below).
- Salvage: SALVAGE_AUDIT.md Part 3 — TAKE
  `rfmesh/dsp/rssi.py`, `rfmesh/dsp/spectrum.py`, `rfmesh/dsp/constants.py`
  from `github.com/macwed/rf-mesh` as the foundation. Re-home into
  `packages/rfmesh-dsp/src/rfmesh_dsp/`. Salvage notes: 99–100 % covered;
  near-mechanical lift; only import paths shift.
- WS-A simulator API (consumed by tests only, never imported by the
  package code): `rfmesh_sdr.SyntheticReceiver`,
  `rfmesh_sdr.SimulationScenario`, `rfmesh_sdr.EmitterSpec`,
  `rfmesh_sdr.AntennaPattern`. `SyntheticReceiver.set_antenna_heading()`
  rotates the simulated antenna; `reseed()` re-randomises noise while
  keeping the deterministic geometry (this is what the σ-honesty
  Monte-Carlo uses).
- Bootstrap: `bootstrap-B.md` "What makes your output trustworthy"
  (the empirical-spread vs claimed-σ test).

## Design — read before coding

### The sweep-awareness gap in the `BearingEstimator` Protocol
The frozen Protocol `estimate(samples: IQBlock) -> BearingReport | None`
carries no heading channel. L1 needs heading per block. The resolution
*without a contract change*: the L1 estimator class satisfies the
Protocol *and* exposes two L1-specific methods that the node runtime
calls. The Protocol typing stays minimal; the runtime is L1-aware
enough to call the extra methods.

Concrete shape:

```python
class L1AmplitudeSweepEstimator:
    # Protocol surface (the only attributes a runtime that knows
    # only `BearingEstimator` sees):
    method: Capability  # = Capability.L1_RSSI
    def estimate(self, samples: IQBlock) -> BearingReport | None: ...

    # L1-specific state-injection surface (runtime calls these when
    # active capability is L1_RSSI):
    def begin_sweep(self, t_unix_ns: int) -> None: ...
    def observe(self, heading_deg: float, samples: IQBlock) -> None: ...
    # Then runtime calls estimate(last_block) which finalises and emits
    # a BearingReport, or None if the sweep was unusable (too few
    # samples, peak too flat, SNR below threshold).
```

Rationale: keeps Invariant 1 intact (no contract edit), Invariant 5
intact (pure compute, no SDR/servo reach), and isolates the L1-vs-L2
asymmetry where it belongs — in the runtime's wiring, not in the
contract.

Document this design choice in the module docstring of `l1.py`. WS-C+D
will read it when wiring the runtime.

### Constructor injection (the node-identity fields)
A `BearingReport` requires `node_id`, `node_position`, `t_unix_ns`,
`method`, `azimuth_deg`, `azimuth_sigma_deg`. The estimator gets the
first two at construction (they do not change per sweep) and the
fourth from its own constant `method = Capability.L1_RSSI`. `t_unix_ns`
arrives via `begin_sweep(t_unix_ns)` — the sweep-start timestamp,
which is the bearing's "instant" per `INTERFACES.md` §3 (a sweep takes
some hundreds of ms; the instant is the *start*).

```python
class L1AmplitudeSweepEstimator:
    def __init__(
        self,
        *,
        node_id: str,
        node_position: GeodeticPosition,
        sweep_step_deg: float = 1.0,
        sweep_dwell_samples: int,  # caller-supplied; matches what the
                                   # runtime reads per heading
        peak_prominence_db_min: float = 6.0,
    ) -> None: ...
```

### The σ estimator — the load-bearing piece
The empirical rule the bootstrap demands: if the estimator reports
`azimuth_sigma_deg = 1.5°`, the *empirical* recovered-angle std over
many noise realisations at the same scenario must be 1.5° ± 20 %.

Reference σ derivation for L1 (document it in `l1.py`; this is the
formula tests verify against):

For a CW emitter against a smooth main-lobe antenna pattern, the
SNR-floored Cramér-Rao-style bound on the peak-position estimate from
a sampled lobe of N RSSI points spaced `Δθ` apart is well-approximated by
    σ_θ ≈ k · HPBW / sqrt(2 · SNR_linear · N_eff)
where `N_eff` is the effective number of in-lobe samples that
contribute to the fit (samples with antenna gain ≥ -3 dB) and `k` is a
shape constant ≈ 0.5–1.0 depending on the fit method. The constant `k`
is calibrated empirically from the simulator against the honesty test
(Acceptance 4 below); the literal formula is *documented* in the
module, but the estimator's runtime path uses a *fitted parabola width*
(see below) and reports its uncertainty from that fit, not from a
priori formulae. The formula is for sanity-check only — the run-time σ
is the fit-driven σ.

Run-time σ path (binding):

1. Compute per-heading RSSI dB via the salvaged `compute_rssi_dbfs`.
2. Locate the maximum (`argmax`).
3. Fit a quadratic to the 5–7 RSSI points around the peak.
4. Solve the parabola vertex → fractional peak heading.
5. Estimate noise on the RSSI samples (median absolute deviation off-peak
   gives a robust std σ_rssi).
6. Propagate `σ_rssi` through the parabola-fit Jacobian to
   `azimuth_sigma_deg`. This is the *fit-driven* σ that the test
   harness verifies for honesty.

If the peak is not prominent enough (peak height minus median floor
< `peak_prominence_db_min`) or the quadratic fit fails (curvature has
the wrong sign — peak is a saddle, e.g. multipath fluke), `estimate`
returns `None`. Never fabricate a confident bearing from a flat
pseudospectrum (Invariant 4).

### Heading normalisation
`BearingReport.azimuth_deg` is in [0, 360). The fractional fit may
return any real; wrap to [0, 360) before constructing the message.

### What goes into `snr_db`
`snr_db` is diagnostic only (fusion ignores it). Compute as
    peak_rssi_db − median_floor_db
using the salvaged `compute_noise_floor_dbfs`'s median-with-strongest-
bin-exclusion. This is dB above the *node's own noise-floor estimate*,
honest per INTERFACES.md §0.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-dsp -v` passes, including:
   - `tests/test_l1_estimator.py::test_protocol_conformance`
     — `isinstance(estimator, BearingEstimator)` (the `@runtime_checkable`
     check from `rfmesh_contracts.protocols`).
   - `tests/test_l1_estimator.py::test_peak_recovers_known_angle`
     — driven by `peak_test_scenario` from WS-A's `conftest.py`
     pattern (recreated locally); recovered azimuth is within ±2° of
     137° at 20 dB SNR over 8192-sample blocks per heading.
   - `tests/test_l1_estimator.py::test_returns_none_on_flat_sweep`
     — a scenario with the emitter dropped (or SNR far below
     threshold) returns `None` from `estimate`; no fabricated bearing.
   - `tests/test_l1_estimator.py::test_method_is_l1_rssi`
     — `estimator.method == Capability.L1_RSSI`.

2. **Golden-file tests (Invariant 3).** A `tests/golden/` directory
   holds:
   - `l1_sweep_137deg_20db.npz` — RSSI vs heading for the canonical
     scenario, seed 42. Generated by
     `tests/golden_generator.py` (committed). The test
     `test_l1_estimator.py::test_golden_sweep_rssi` recomputes the sweep
     and asserts max-abs diff in dB ≤ 1e-6 against the stored array.
   - `l1_sigma_table.npz` — for each of {(SNR=5 dB), (SNR=10 dB),
     (SNR=20 dB), (SNR=30 dB)}, the median claimed σ over 200 trials.
     The test recomputes and asserts ≤ 5 % relative difference.

3. **σ-honesty test (the load-bearing one).**
   `tests/test_sigma_honesty.py::test_empirical_spread_matches_claimed_sigma`
   runs at three SNRs (10, 20, 30 dB), 200 trials each, using
   `SyntheticReceiver.reseed(i)` to perturb noise without touching
   geometry. For each SNR:
     - empirical std of recovered azimuth over trials = σ_emp
     - median claimed σ over trials = σ_claimed
     - assert `0.8 * σ_claimed ≤ σ_emp ≤ 1.2 * σ_claimed`
   No tolerance widening to make a test pass — if the band breaks,
   the σ estimator is wrong, not the test. If you cannot meet ±20 %,
   stop and write a scratchpad note instead of widening; that is the
   contract that protects the system's honesty downstream.

4. **The 25 dB SNR-invariant regression test
   (INHERITED_CONTEXT.md §5.1).**
   `tests/test_snr_invariant_25db.py::test_cw_peak_vs_in_band_snr` —
   for a CW input of *known* power at *known* SNR fed through the
   salvaged `compute_rssi_in_band` and the spectrum-peak path,
   verify `peak_snr_db` and `in_band_snr_db` against closed-form
   expectations to within 0.5 dB. The relationship between them is
   *computed* in the test (it depends on the FFT bin count and the
   in-band bandwidth ratio), never assumed. Document the closed form
   in the test docstring; future maintainers must see the derivation.

5. `uv run mypy packages/rfmesh-dsp` clean.

6. `uv run ruff check packages/rfmesh-dsp` clean.

7. The package code imports *only* `numpy`, `scipy` (already pinned),
   `rfmesh_contracts`, and stdlib. **No** `rfmesh_sdr` import in
   `src/`. Tests in `tests/` may import `rfmesh_sdr`.

8. `__init__.py` re-exports `L1AmplitudeSweepEstimator` and re-exports
   the salvaged `compute_rssi_dbfs`, `compute_rssi_in_band`,
   `compute_noise_floor_dbfs`, `compute_snr_db`, `compute_fft`,
   `compute_psd`, `compute_spectrogram`, `find_spectral_peak`. List
   alphabetised in `__all__`. **Important for parallel WS-B-002:** in
   `__init__.py`, insert your additions in alphabetical position
   within `__all__`; do not touch lines you did not add. This minimises
   merge conflict with WS-B-002.

## Out of scope (explicit non-goals)

- Do NOT add fields to any `rfmesh_contracts` type. (Invariant 1.)
- Do NOT modify `packages/rfmesh-contracts/**`. (Invariant 1.)
- Do NOT modify or duplicate anything in `packages/rfmesh-sdr/**`. The
  WS-A package is read-only to you. (Invariant 2.)
- Do NOT introduce a runtime dependency beyond numpy / scipy /
  pydantic. PyArgus vendoring is allowed under
  `packages/rfmesh-dsp/vendor/` BUT IS NOT NEEDED for L1 and is OUT
  OF SCOPE for this ticket.
- Do NOT implement an L2 MUSIC estimator. That is WS-B-003 (a future
  ticket).
- Do NOT touch the array-manifold module. That is WS-B-002 (its own
  parallel ticket, will land in the same package — see the
  `__init__.py` discipline above).
- Do NOT add a `set_heading_for_next_block`-style hook to the
  `BearingEstimator` Protocol. Keep the L1-specific methods on the
  concrete L1 class; the runtime is L1-aware where it must be.

## Files you may touch (create | modify)

- `packages/rfmesh-dsp/src/rfmesh_dsp/__init__.py`              (modify)
- `packages/rfmesh-dsp/src/rfmesh_dsp/rssi.py`                  (create — salvaged)
- `packages/rfmesh-dsp/src/rfmesh_dsp/spectrum.py`              (create — salvaged)
- `packages/rfmesh-dsp/src/rfmesh_dsp/constants.py`             (create — salvaged)
- `packages/rfmesh-dsp/src/rfmesh_dsp/l1.py`                    (create — new)
- `packages/rfmesh-dsp/tests/__init__.py`                       (create)
- `packages/rfmesh-dsp/tests/conftest.py`                       (create)
- `packages/rfmesh-dsp/tests/golden_generator.py`               (create)
- `packages/rfmesh-dsp/tests/golden/l1_sweep_137deg_20db.npz`   (generate + commit)
- `packages/rfmesh-dsp/tests/golden/l1_sigma_table.npz`         (generate + commit)
- `packages/rfmesh-dsp/tests/test_l1_estimator.py`              (create)
- `packages/rfmesh-dsp/tests/test_sigma_honesty.py`             (create)
- `packages/rfmesh-dsp/tests/test_snr_invariant_25db.py`        (create)
- `packages/rfmesh-dsp/pyproject.toml`                          (modify — add `scipy`,
                                                                 `numpy`, `rfmesh-contracts`
                                                                 if not present; add
                                                                 `rfmesh-sdr` as
                                                                 *test-only* dependency)

## Files you may NOT touch

- `packages/rfmesh-contracts/**`                                (FROZEN — Invariant 1)
- `packages/rfmesh-sdr/**`                                      (other workstream — Invariant 2)
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_manifold.py`        (WS-B-002's file)
- `packages/rfmesh-dsp/src/rfmesh_dsp/array_covariance.py`      (WS-B-002's file)
- `packages/rfmesh-dsp/tests/test_array_manifold.py`            (WS-B-002's file)
- `packages/rfmesh-dsp/tests/test_array_covariance.py`          (WS-B-002's file)
- Anything outside `packages/rfmesh-dsp/`

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste the test output and `mypy` / `ruff` output into the
  conversation.
- If the σ-honesty test cannot be made to pass within ±20 % without
  widening tolerance, stop and write a scratchpad note describing
  what σ estimator was tried and what empirical / claimed spreads
  resulted. Do NOT widen tolerance to make the test pass — that is the
  exact failure mode this acceptance criterion exists to prevent.
- If you find an apparent bug in the salvaged `rssi.py` / `spectrum.py`,
  do NOT rewrite — note it in the diff and let the lead decide.
- If a contract change appears necessary, stop and write
  `docs/adr/ADR-NNN-<short>.md` (status PROPOSED) instead — do NOT
  proceed.
