# ADR-014 — Mast C single empirical anchor for the 2-5 km envelope claim

**Status:** PROPOSED (2026-05-20)
**Date:** 2026-05-20
**Author:** lead-Opus (drafting per the 2026-05-20 council pass on the BoTH3 slide deck + the C3 / C4 / C5 ticket close-outs).
**SCHEMA_VERSION change:** none. This ADR is documentation / architectural-rationale change-control; no contract field, enum, or wire-format byte is touched.

## Context

The 2026-05-17 Phase C bench (`docs/phase-c-report/findings.md`) was the project's first hardware validation of L1 amplitude-comparison DF. It tested three masts:

- **Mast A** — 650 m, 936.568 MHz. FAIL: 1.96 dB front-back ratio.
- **Mast B** — 2.2 km, 936.0 MHz. FAIL: 8.5 dB F/B at the wrong azimuth (co-channel interferer dominating).
- **Mast C** — ~3 km, 958.7 MHz. **PASS**: 14.9 dB F/B at 9° from expected bearing (later resolved to 1.4° by a 2026-05-19 fine-sweep recapture; see §"Subsequent measurements" below).

The original `findings.md` interpreted all three as honest demonstrations of the system's operating envelope: the two FAILs as "below 2-5 km" and "contested-spectrum", the PASS as "inside 2-5 km on an isolated carrier". `ARCHITECTURE.md` §0 binds the 2-5 km standoff envelope; the original Phase C bench was cited as the empirical anchor for that envelope.

Since then:

- **2026-05-19 recapture** at Mast A + Mast C with an expanded protocol (multi-height sweep, V/H polarisation cross-check, sky-pointing instrument-noise reference, 10°-resolution fine sweep, 60-s stationarity captures, `rtl_power` spectrum baselines). Captures live at `recordings/2026-05-19-mast-{a,c}/` (operator-personal, gitignored).
- **C3 ticket close-out** (commit `547a5ea`): `scenarios/mast_c_reference.yaml` codifies the Mast C measured behaviour as the project's empirical anchor for the simulator. Numbers include +11.4 dB SNR vs site-local sky-noise floor, 0.17 dB std over 60 s of continuous capture, peak heading 305° (parabolic fit 307°) within angular grid resolution of expected 306°.
- **C4 ticket close-out** (commit `780195d`): a deterministic test (`packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py`) asserts the project's simulator (free-space + log-normal-shadowing, sigma=0.20) reproduces the Mast C anchor within explicit tolerance bands. Simulator now calibrated to a real measurement, not just plausible.
- **2026-05-20 BoTH3 slide deck** (commit `34d8bc6`, then `b24d573` follow-up): the public demo deck cites the Mast C empirical anchor as the load-bearing Phase C evidence; the original three-mast composite is internally caveated and not exposed as positive demo claims.

This ADR formalises the architectural rationale change: the project's empirical anchor for the 2-5 km envelope is now **Mast C** specifically, codified in a tracked scenario YAML and pinned by a tracked calibration test. Earlier Phase C measurements remain in the historical record (`docs/phase-c-report/findings.md`) as the bench's first execution; they do not bind architectural claims going forward.

## Decision

**Adopt `scenarios/mast_c_reference.yaml` as the canonical empirical anchor for `ARCHITECTURE.md` §0's "2-5 km standoff envelope" claim.**

Specifically:

1. The empirical evidence backing the envelope claim is the Mast C measurement at 3 km, +11.4 dB SNR above site-local sky-noise floor, 0.17 dB stationarity std, peak heading within angular grid of map-derived ground truth. Numbers are codified in `scenarios/mast_c_reference.yaml` `measured_behaviour` block.

2. The simulator-side validation that the anchor is reproducible is the C4 calibration test at `packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py` — six tolerance assertions against the simulator's free-space + light-shadowing channel.

3. Tightening the empirical foundation (fewer anchors, all binding, all tracked) reflects ADVANTAGES.md §3 binding-rule discipline: *"tightening or extending an advantage is fine; weakening or eliminating one is not"*. The 2-5 km envelope claim is unchanged; its empirical anchor is tightened from a three-mast composite (in which the FAIL diagnoses had uncertain attribution) to a single measurement with explicit reproducibility tolerances.

4. The original `findings.md` remains in tracked history as the bench's first execution log. New work (slide deck, ARCHITECTURE.md updates, new ADRs) cites `scenarios/mast_c_reference.yaml` and the C4 calibration test as the binding anchor.

## Subsequent measurements (operational note, not in scope for this ADR)

A 2026-05-19 recapture at Mast A surfaced uncertainties in the original "multipath dominance" diagnosis for that FAIL — specifically, an in-band stronger carrier 236 kHz off the configured target, an unmeasured mast-antenna height that makes the two-ray geometry assumption brittle, and a sub-noise V/H cross-check that does not discriminate emitter polarisation. The disposition of that measurement is operator-personal (`NOTES_mast_a_resurvey.md`, gitignored); the project decision is to not amend the public `findings.md` against the 2026-05-17 record. A future Mast A re-survey at the actual dominant carrier (936.804 MHz) plus a clinometer-derived h_tx will discriminate the alternative causes. This ADR does **not** depend on that re-survey's outcome.

## Consequences

Positive:

- Empirical anchor for the envelope claim is now testable. `uv run pytest packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py` passes iff the simulator still reproduces Mast C; CI catches drift.
- Future demo materials (slide deck, jury Q&A, sigma honesty discussions) cite a single named anchor with stable file paths, not a multi-mast composite.
- Tightens Advantage #6 (honesty payload) — the project's load-bearing empirical claim points to a single, well-instrumented measurement with stationarity statistics, not to a single-evening composite of three measurements with mixed quality.

Negative / costs:

- Operator-side bench retesting must include the §7.6-style protocol (spectrum baseline + V/H + sky-pointing + fine sweep + 60-s stationarity) at any future site that wants to update or extend the anchor. The protocol is more expensive than the 2026-05-17 8-heading-only bench.
- Future Mast A re-survey results (whichever direction they fall) will likely justify a new ADR (ADR-015?) extending the anchor set; this ADR-014 is the first such measurement, not the final.

## Cross-references

- `ARCHITECTURE.md` §0 — 2-5 km standoff envelope. **Not edited by this ADR**; this ADR establishes the empirical anchor cited there.
- `docs/phase-c-report/findings.md` — 2026-05-17 bench log; retained as historical record.
- `scenarios/mast_c_reference.yaml` — the empirical anchor (C3 close-out, commit `547a5ea`).
- `packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py` — the simulator-side calibration assertion (C4 close-out, commit `780195d`).
- `docs/demo/slide_deck.md` — the BoTH3 jury deck that cites this anchor in the "Phase C empirical anchor" slide.
- `docs/demo/script.md` — the operator spoken narration whose Beat numbers are anchored to `scenarios/trench_demo.yaml` (C5 close-out, commit `28d7e88`).
- `docs/ADVANTAGES.md` §3 binding rule — the tightening-not-weakening discipline this ADR honours.
- `NOTES_mast_a_resurvey.md` (gitignored, operator-personal) — the procedure for the future Mast A re-survey at 936.804 MHz.

## Rollback contingency

If a future bench measurement materially contradicts the Mast C anchor (e.g. cannot reproduce +11.4 dB SNR at the same site; new co-channel emitter at 958.7 MHz; site no longer line-of-sight to the mast), this ADR is superseded by a new ADR identifying the replacement anchor. `findings.md` is the canonical record of the *first* bench; new ADRs are the canonical record of *which measurement currently anchors* the envelope claim. The contract is: there is always exactly one tracked, named, reproducible anchor.
