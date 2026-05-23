# L2 null-steering Monte Carlo — TBD #1 read-out

**Date:** 2026-05-17
**Source script:** `scripts/null_depth_mc_stats.py`
**Scenario:** UCA-4, signal 30° @ SNR 10 dB, jammer 100° @ SNR 20 dB, ±2° steering mismatch, ±10° per-channel phase calibration error.
**Trials:** 1000 (matches `test_null_depth_robust_under_mismatch`).
**Slide cap (ADR-008 §D8):** UI text bounded ≤ 20 dB; on-stage rehearsed band 15-20 dB.

## Null depth (dB) headline

| stat | value |
|---|---|
| valid trials | 1000 / 1000 (0 rejected) |
| mean | 47.32 dB |
| median | 46.24 dB |
| std dev | 8.03 dB |
| p5 | 36.42 dB |
| p95 | 62.62 dB |
| 90 % CI (mean) | [46.91, 47.74] dB |

## Look gain (dB) sanity

| stat | value |
|---|---|
| mean | -0.00 dB |
| std dev | 0.00 dB |

## On-day demo number (TBD #1 fill-in)

Headline reading: **-18 dB** (slide caption, ADR-008 §D8 ≤ 20 dB UI cap).
Rehearsed band: **15-20 dB typical, up to ~25 dB with fresh calibration** — ADR-bound, not derived from this MC. The MC numbers in the tables above are simulator-clean (no analog I/Q imbalance, no narrow-band fading, no frequency-selective element mismatch); real bench-side captures land below the simulator distribution, inside the ADR rehearsed band.

## Notes

* `mean` is the centre of the simulator-distribution; the on-stage rendered
  value is whatever the recorded-IQ buffer delivers and may differ within ±15.7 dB (95 % band).
* `p5 ≥ 15 dB` matches the WS-B-007 slide-budget floor that
  `test_null_depth_robust_under_mismatch` already pins as a CI gate.
* The script is **SNR-equivalent** (not byte-equivalent) to the test
  fixture in `packages/rfmesh-dsp/tests/conftest.py`. Absolute power
  scale differs (script uses `_NOISE_FLOOR_DBFS = -60.0` and
  `_RANGE_M = 1000.0`; conftest uses `-100.0` and `1500.0`), but
  `_tx_power_db_for_snr` re-targets the same per-emitter SNR at the
  receiver, so `null_depth_db` (a scale-invariant metric) follows
  the same distribution. Seed (20260517), trial count (1000),
  mismatch range (±2°), and calibration error model (±10° / channel 0
  pinned) are exact matches.
