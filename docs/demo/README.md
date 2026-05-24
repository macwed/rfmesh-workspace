# docs/demo/

BoTH3 Counter-Jamming Challenge 2 jury materials. Post-pivot rewrite
per ADR-021 (directional-comms primary) + ADR-025 (DSSS comms surface).

## Files

| File | Purpose |
|---|---|
| [`pitch_deck.md`](pitch_deck.md) | 10-slide Marp jury pitch (5-min slot + 5-min Q&A). Lead = Advantage #4 null-steering per ADR-021 §1.5 pitch order. |
| [`soldier_grade_checklist.md`](soldier_grade_checklist.md) | 10-item demo-integrity acceptance gates. Demo cannot run until every box green on the actual field tablet. |
| `script.md` (future) | Operator spoken narration + demo-flow timing. Lifts language from `docs/deprecated/demo/script.md` where still accurate. |
| `slide_deck.md` (future) | Engineering walkthrough (~20 slides) for Expert Panel check-in + Q&A depth backup. |

Render the pitch deck:

```bash
marp docs/demo/pitch_deck.md --pdf  --output build/pitch_deck.pdf
marp docs/demo/pitch_deck.md --pptx --output build/pitch_deck.pptx
```

## Binding honesty caps (every deck edit must respect)

- ATK-10 Yagi: **~+8 dBi end-to-end** (manufacturer claim minus cable + connector + bracket pattern). NEVER claim +20 dB.
- Null depth: **−18 dB receive-pattern attenuation at θ_jammer relative to look-direction gain at θ_link**. Rehearsed band 15-20 dB; up to ~25 dB with fresh cal. ADR-008 §D8 caps UI text at ≤20 dB.
- **Anti-desense, not ECM.** We don't transmit through the L2 DF array.
- No sub-degree L1 claims. No absolute dBm anywhere (B.2). No N≥5 deployment-density promises.
- DSSS processing gain quoted as **~30 dB at 10 Mchip/s + length-1023** (ADR-025 §B), not a tested field number until WS-A-008 hardware smoke tests measure it.
- GDOP renders "uncomputable (reason)" not a fabricated float when geometry degenerate (ADR-013 §G3).

## Old deprecated materials

Pre-pivot deck (geolocation-first framing) lives at `docs/deprecated/demo/`. The triangulation honesty story, the null-steering A/B math, the €250 BOM all survive into the new pitch — but the front framing is replaced.

`docs/deprecated/demo/pitch_deck.md` and `slide_deck.md` are reference-readable for language. Do NOT cite them as binding.

## Pitch order (ADR-021 §1.5)

#4 null-steering → #2 GNSS-denied → #6 honesty payload → #8 €250 budget → #9 self-locating directional mesh → #7 simulator-first → #3 heterogeneous mesh → #1 density scaling → #5 open threat library.

Advantage numbering is preserved across the codebase + ADRs; only the deck's slide order changes.
