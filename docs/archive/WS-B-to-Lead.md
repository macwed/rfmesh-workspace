# WS-B → Lead status, 2026-05-16

End-of-sprint summary from Workstream B (DSP). All four planned tickets
this sprint shipped and verified per-package; honesty Monte-Carlo bands
hold across the relevant SNRs.

## Ticket status

| Ticket    | Deliverable                            | Tests | Honesty band (SNR=10/20/30 dB) | Status   |
|-----------|----------------------------------------|-------|---------------------------------|----------|
| WS-B-001  | L1 amplitude-sweep BearingEstimator    | 40    | 1.12 / 1.04 / 1.04              | MERGED   |
| WS-B-002  | Array manifold + sample covariance     | 16    | n/a (no sigma path)             | MERGED   |
| WS-B-003  | L2 MUSIC BearingEstimator              | 41    | 1.02 / 1.00 / 1.06              | MERGED   |
| WS-B-004  | L2 Capon (MVDR-spectrum) Estimator     | 42    | 0.98 / 0.97 / 0.98              | REVIEWED, ready to merge |

`uv run pytest packages/rfmesh-dsp -m "not hardware"` is clean for every
ticket. `mypy packages/rfmesh-dsp`, `ruff check`, and `lint-imports`
("DSP is pure" contract) all green per ticket. Workspace-wide
`just verify` is broken on a pre-existing conftest collision — see
"Items pending" below.

## Sigma-path learning (load-bearing for any future BearingEstimator)

Three estimators, three sigma mechanisms — and this is not accidental.
The mechanism is dictated by where the stochasticity lives:

- **L1 (RSSI sweep).** Per-heading IQ blocks are independent. Each
  fit point carries fresh thermal noise. `polyfit(cov=True)` reads
  honest residual variance and scales correctly with SNR. Single
  chi-square median-bias correction (1/0.8392) cleans up the median-vs-
  mean asymmetry of the estimator.
- **L2 MUSIC.** The pseudospectrum is asymptotically *exactly* parabolic
  near the peak. polyfit residuals shrink toward zero; cov estimate
  reports a ~1500x underbiased sigma. Switched to Stoica-Nehorai 1989
  asymptotic MUSIC variance, reading the curvature term `c2` off the
  same parabolic fit but combining it with the eigendecomposition's
  signal/noise eigenvalues. Calibration constant 1.127 absorbs
  finite-T/SNR residual.
- **L2 Capon.** All 7 fit samples come from one R. Within-realization
  residuals are dominated by higher-order non-quadratic shape, not by
  statistical noise. polyfit cov off by ~3000x and SNR-blind. Switched
  to Fisher-information variance `c0 / (2T · c2)` with split diagonal
  loading: heavy (1e-3) on R for peak stability, light (1e-6) on R for
  curvature evaluation (avoids loading bias inflating c0 at high SNR).
  No calibration constant needed.

The unifying principle: sigma honesty is empirical, not prescribable.
Future BearingEstimator tickets should specify the +/-20% honesty band
as the acceptance gate and leave the mechanism to be calibrated against
that gate, not prescribe polyfit-cov by default.

## Items pending your attention

1. **Conftest.py mypy collision** — `packages/rfmesh-sdr/tests/conftest.py`
   and `packages/rfmesh-dsp/tests/conftest.py` both resolve to module
   "conftest" under workspace-wide `mypy packages/`. ADR-006 fixed the
   analogous `__init__.py` case by deleting the file; conftest.py can't
   be deleted (pytest needs it). Already tracked as
   `WS-CD-001b-mypy-namespace-packages.md`. Flagging in this summary
   because every WS-B ticket since WS-B-001 has had to manually mark
   this as a "pre-existing flake" — once it's resolved that noise goes
   away. Suggested workspace [tool.mypy] change in my detail report on
   WS-B-003.

2. **ADR-004 array calibration file format** — I submitted three review
   points to Opus-A via Maciej on 2026-05-16:
   - `complex_offsets` -> `complex_corrections` semantics (align on-disk
     shape with WS-A's in-memory `Calibration` record; one convention,
     not two).
   - Explicit paragraph stating loader lives only in `rfmesh-sdr` and
     DSP never imports `ArrayCalibration` (Invariants 2 and 5
     reinforcement; without this someone will write it eventually).
   - `captured_t_unix_ns` is redundant with `captured_utc` ISO-8601 and
     hits JSON number precision; remove.

   I have not seen a revised ADR-004 yet. If those points landed and I
   missed it, no action — if not, please nudge Opus-A. My sign-off on
   ADR-004 is conditional on points (1) and (2); point (3) is nice-to-
   have. None of this blocks WS-B-005/006 (L3) — DSP consumes calibrated
   IQ via the CoherentReceiver Protocol regardless of how the on-disk
   file looks.

3. **`Capability.L2_MVDR_NULL` naming.** Frozen contract enum reads
   `L2_MVDR_NULL` but the v0.1 estimator is a Capon spectrum (not
   null-steering). Agent used the enum as-is per Invariant 1. The
   semantic mismatch is documented in `l2_mvdr.py`'s module docstring.
   If/when the contract goes through a `SCHEMA_VERSION` bump, consider
   renaming to `L2_MVDR` or `L2_CAPON`; for v0.1 the misnomer is
   harmless and the alternative (separate enum values for spectrum vs
   pure null-steering) is over-engineered until pure null-steering is
   actually requested.

## What's next for WS-B

After WS-B-004 merge, two remaining bootstrap-B deliverables:

- **WS-B-005** — L3 classifier (CW vs FHSS vs FSK vs LoRa) from the
  spectrum / waterfall features. Pure DSP, no new contracts, salvage
  candidate: the rfmesh-sdr legacy `dsp/classifier.py` if any survived
  the rewrite (SALVAGE_AUDIT Part 3 noted partial). Output is enum
  membership + a confidence number for fusion's `EmitterClass`
  attribution.
- **WS-B-006** — threat library: structured mapping from
  `EmitterClass` to threat-relevance metadata used by the dashboard
  /CoT path. Mostly a data deliverable; one Python module + a YAML
  asset.

Both are independent and equal-weight. WS-B-005 has more substance
(real DSP) so it should go first; WS-B-006 is parallelisable with
WS-B-005 if you want a 2-worktree sprint again.

Waiting on:
- Your nod on ADR-004 review status (item 2 above) — informational only,
  does not block.
- Your call on conftest mypy fix landing — informational only, does not
  block ticket drafting.
- Your priority signal: WS-B-005 + WS-B-006 next, or pivot to assist
  WS-C+D fusion coordination (their fusion engine needs to consume L1
  / L2 MUSIC / L2 Capon BearingReports with their now-different sigma
  characteristics — there's a coordination conversation worth having
  about how fusion weights three different-quality estimators sanely).

Drafting paused until I hear from you on which direction.
