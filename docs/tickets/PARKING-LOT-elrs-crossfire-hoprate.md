# PARKING LOT — ELRS vs Crossfire hop-rate discriminator (v1.5+)

**Status:** PARKED, not for v1.0. Filed per rf-dsp-council NOTE 2 on the
2026-05-17 post-batch audit.

## Context

WS-B-006 (`packages/rfmesh-ml/src/rfmesh_ml/threats/`) v1.0 ships an
honest multi-match → `EmitterClass.UNKNOWN` for the 868 MHz LoRa-FHSS
case because ELRS and Crossfire both operate on the same band with the
same coarse modulation family (LoRa). The honest v1.0 answer is
documented in both profile YAMLs' `notes` fields and in the demo
narrative (`docs/demo/script.md`).

A v1.5+ improvement is to consult the **hop-rate** as a discriminator:

- ELRS: ~250 Hz hop rate (default 250 Hz packet rate, one hop per packet)
- Crossfire: ~150 Hz hop rate (50 Hz default packet rate × 3-frame
  frequency rotation, mode-dependent)

## What v1.5+ does

1. WS-B-005 already exposes a `feature_vector` field on
   `ClassificationResult`. The hop-rate detector peak typically lives
   at `feature_vector[5]` per the WS-B-005 builder's intent; confirm
   with `rf-dsp-specialist` before committing.
2. Extend `rfmesh_ml.threats.profile.MatchRule` with an optional
   `hop_rate_hz: tuple[float, float] | None` field (Pydantic, additive
   schema bump — does NOT touch `rfmesh-contracts`, so no
   `SCHEMA_VERSION` bump).
3. Extend `enrich_to_emitter_class` to consult
   `modulation_result.feature_vector[index]` against the rule's
   `hop_rate_hz` window when both are present. Multi-match remains
   the fallback when the hop-rate is absent or ambiguous.
4. Update `elrs.yaml` to add `hop_rate_hz: [200.0, 300.0]` and
   `crossfire.yaml` to add `hop_rate_hz: [100.0, 200.0]`. Test
   `test_enrich_crossfire_868mhz` asserts the priority-driven outcome.

## When to do it

- After Phase C tomorrow surfaces no more pressing simulator/hardware
  issues.
- After Maciej captures (or has captured for him) at least one real
  ELRS waveform and one real Crossfire waveform for ground-truth
  validation. Synthesised IQ alone is not enough — the hop-rate
  detector's empirical accuracy must be verified against real captures.

## Why not v1.0

The current honest `UNKNOWN` answer is a credible jury line ("we
honestly cannot distinguish ELRS from Crossfire on modulation +
frequency alone; the threat library shows both candidates; the
operator decides"). Adding an honest hop-rate discriminator only
pays off if the hop-rate detector itself is honest. Without real-IQ
validation, an inflated hop-rate-based classifier risks the same
demo-credibility own-goal that ADR-005's % display correction fixed.

## Owner / scheduling

To be assigned when a v1.5 release is planned. Estimated effort: ~2
days (schema extension + 4 new tests + 2 YAML edits + rf-dsp-specialist
review of the hop-rate band bounds).
