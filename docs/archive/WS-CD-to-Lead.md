Opus-CD — status 2026-05-16

SPRINT 1 PROGRESS: 2/8 tickets landed.

Landed:
  WS-CD-001  + 001b: rfmesh-fusion ENU projection helpers (projection.py)
                     mypy namespace_packages config monorepo-wide
  WS-CD-002:         geometry.py + stansfield.py + exceptions.py
                     Closed-form weighted-LS Stansfield seed.
                     24 tests, all pass; mypy + ruff clean per-package.

Next in sprint:
  WS-CD-003  MLE refinement (Gauss-Newton, analytic Jacobian)
  WS-CD-004  Covariance from Fisher info + 95% ellipse
  WS-CD-005  GDOP (unweighted H, per ADR-007 D4)
  WS-CD-006  Residuals + is_outlier flag (per ADR-005 D3)
  WS-CD-007  StansfieldMLEFuser — wires the Protocol
  WS-CD-008  Honest-ellipse Monte Carlo (95% +/- 3% inclusion rate)

ADRs PROPOSED, awaiting your review:
  ADR-007    Fusion algorithm choices (Stansfield variant, GN vs LM, GDOP def)
  ADR-005    ConfidenceLevel policy; HIGH band at 0.05 * range_m
             — single scalar, tunable post-trench_demo dry-run

Decisions locked at workstream level (within freedom zone):
  - No new runtime deps in sprint 1. numpy only. scipy/pyproj would
    need an ADR + /uvadd-request.
  - GDOP defined unweighted, matches GNSS/surveying convention. Per-σ
    weighting lives in covariance, not GDOP. Two separable diagnostic
    axes for the ops dashboard.
  - Sprint 1: honesty-over-robustness. No outlier rejection inside the
    solver; outliers are flagged via residuals.is_outlier and
    downgrade HIGH→MEDIUM. Sprint 2 IRLS requires UI ticket as
    pre-condition (ADR-005 §7.2).

Open items needing your input:
  - Ratify 0.05 in ADR-005 D1 against trench_demo.yaml geometries
    before sprint-1 demo. Single constant; one-line diff to retune.
  - Pre-existing mypy duplicate-conftest collision between
    rfmesh-sdr/tests/ and rfmesh-dsp/tests/ on main. WS-CD-001b only
    extended rfmesh-fusion; rfmesh-dsp needs the same tests/__init__.py
    pattern. Either WS-A owner extends, or I open WS-CD-001c.

Risks I'm tracking:
  - Gauss-Newton convergence on real geometries (sprint 1 measure;
    if >1% fail rate, ADR + scipy.optimize).
  - 0.05 HIGH-band threshold may need re-tuning post-Phase-C real data
    if multipath inflates real-world σ vs simulated.

Cadence:
  Aim WS-CD-003 (MLE) landing within ~2 days at current velocity.
  Sprint 1 acceptance gate is WS-CD-008 honest-ellipse Monte Carlo —
  the load-bearing demo-honesty test.

No blockers requiring escalation. The two items in "open items" are
ratifications/clarifications, not work stoppers.
