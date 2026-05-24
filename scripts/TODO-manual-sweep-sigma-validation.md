# TODO: validate `bench_manual_sweep.py` σ formula vs ground truth

`scripts/bench_manual_sweep.py` reports a 1-σ azimuth uncertainty per
sweep via the amplitude-monopulse CRLB approximation
`sigma ≈ HPBW / (1.6 * sqrt(2 * SNR))` (Sherman & Barton eq. 5.20),
clamped to `[step_deg/2, step_deg]`.

The formula is textbook and the clamps are honest, but it has **not**
been Monte-Carlo'd against the simulator the way `rfmesh-dsp`'s
in-package estimators are (see
`packages/rfmesh-dsp/tests/test_sigma_honesty.py`, B2 invariant, ±20%
band at SNR ∈ {10, 20, 30} dB).

The B2 invariant binds in-package `BearingEstimator` classes; this
script is an operator-paced bench tool where the dominant error term
is pointing variance (operator-induced), not SNR-induced. The CRLB
formula is therefore a *lower bound* on the realistic sigma, and the
discretisation-floor clamp keeps the reported sigma honest at the
step grain.

**Follow-up (non-blocking):**

1. Write `scripts/tests/test_manual_sweep_sigma.py` that drives the
   amplitude-DF simulator through `_honest_sigma_deg` at three SNRs
   and asserts the reported sigma falls within ±20 % of the
   Monte-Carlo bearing-error std-dev.
2. If the bench formula systematically over-/under-claims, widen the
   `1.6` prefactor or add a regime-specific term, document in this
   file + update the docstring in `_honest_sigma_deg`.
3. Add an operator-pointing-noise term (~2-5° σ from hand rotation
   vs commanded heading) RSS-combined with the CRLB σ -- the dominant
   error for hand sweeps may actually be pointing, not SNR.

Priority: low for v1.0 demo (the clamp keeps claims honest); high if
the manual sweep ships beyond bench use.
