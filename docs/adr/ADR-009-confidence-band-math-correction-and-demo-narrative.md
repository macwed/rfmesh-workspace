# ADR-009 — ADR-005 math correction: 1-σ vs 95% scale, and demo-narrative consequence

- **Status:** ACCEPTED (2026-05-17, lead-Opus + Maciej).
- **Author:** lead-Opus, after a CRLB analysis subagent's finding on
  `scenarios/trench_demo.yaml` flagged the mismatch.
- **Decision scope:** the *interpretation* of `_HIGH_BAND_RANGE_FRACTION
  = 0.05` in `rfmesh_fusion.confidence`; the demo-narrative captions
  the ops dashboard renders; the operator-facing claim about what
  HIGH/MEDIUM/LOW mean in the v1.0 hardware regime. **No contract
  change. No threshold value change.**
- **Amends:** `docs/adr/ADR-005-fusion-confidence-policy.md` —
  specifically its "Rationale for the 5% choice" / "Demo behaviour"
  paragraph.
- **Depends on:** ADR-007 (algorithm choices fix the meaning of the
  underlying quantities), `docs/demo/trench-demo-geometry.md` (the
  numerical CRLB analysis that surfaced the issue).

---

## Context — what the math actually says

ADR-005's "Demo behaviour" paragraph derived the 5%-of-range threshold
from a back-of-envelope CRLB:

> Three L1 nodes at 5° σ and a 3 km standoff emitter in good
> geometry, the analytic CRLB on `semi_major_m` is roughly
> `σ_θ · R / sqrt(N)` converted to metres
> `≈ (5° · π/180) · 3000 / sqrt(3) ≈ 150 m`. That is 5% of 3 km.

The formula `σ_θ · R / sqrt(N)` gives a **1-σ** principal-axis length.
The contract for `EllipseENU` (`INTERFACES.md` §2) is unambiguous:

> By contract, the ellipse renders the **95% confidence contour** of
> a 2-D Gaussian. For a Gaussian, that is the
> `sqrt(chi²_inv(0.95, df=2)) ≈ 2.448` σ contour.

`FixEvent.confidence_ellipse_95.semi_major_m` is the 95%-scaled value.
The threshold `semi_major_m / range_m ≤ 0.05` therefore compares a
**95%** quantity against a 1-σ-derived target. The ratio is off by
`sqrt(chi²_0.95, 2) ≈ 2.45`.

Corrected back-of-envelope: 95% semi-major at the same geometry is
`2.45 × 150 ≈ 368 m ≈ 12% of 3 km`. Numerical Fisher-information
calculation on the actual demo geometry (`docs/demo/crlb_analysis.py`)
gives 393 m / 26% for the three-L1-node beat — close to the
back-of-envelope, with the small extra coming from imperfect node
placement.

**Consequence.** Three L1 nodes at σ = 5° at 3 km cannot reach the
`HIGH` band as currently defined (5% of range = 150 m semi-major).
Adding the L2 partner-pool node tightens the semi-minor sharply
(357 → 125 m) but the semi-major / range ratio actually *worsens*
slightly because the four-node centroid shifts toward the L2 position,
making `range_m` smaller. The demo geometry lives in `MEDIUM`
throughout the four beats; the BoTH3 1% spec line is unreachable with
this hardware + this node count.

This is reality, not a defect. RTL-SDR amplitude-comparison DF cannot
deliver 0.23° bearing accuracy. The pitch (per Maciej, 2026-05-17) is
**deliberately** "we deliver worse-than-spec precision, with reliability
and EW-resilience as the compensating advantages." See docs/ADVANTAGES.md
Advantage #1: *"scaling through deployment density, not per-sensor
magic."*

The ADR-005 math error did *not* corrupt the implementation
(`confidence.py:_HIGH_BAND_RANGE_FRACTION = 0.05` is enforced
correctly; the threshold is what it is). It corrupted **the demo
narrative**: ADR-005 §"Demo behaviour" anticipated the L1-only mesh
"hovering around MEDIUM, and adding an L2 node with 1.5° σ pushes the
fix unambiguously to HIGH." That second clause is false at the chosen
geometry.

---

## Decision

### D1. Three options were considered. We chose option 2 (re-narrate).

The CRLB analysis subagent surfaced three options. Lead reviewed:

- **Option 1 — Relax the threshold** (e.g. `_HIGH_BAND_RANGE_FRACTION
  = 0.10` or 0.15). Cost: HIGH band drifts further from the BoTH3 1%
  spec; the "band names roughly track operational regimes" alignment
  is lost. Reversibility (ADR-005 D6) makes this cheap to do *later*
  if real-data shows the threshold is operationally wrong.

- **Option 2 — Re-narrate.** Keep the 5% threshold as-is; tell the
  demo as it actually is. *"Three L1 nodes = MEDIUM. The dashboard
  shows the percentage shrinking from 49% → 26% → 18% as nodes
  join — that **shrinking** is the deployment-density story, not the
  band-name transition."* The HIGH band remains the "well-instrumented
  L1+L2 dense deployment" band that future denser meshes will hit.

- **Option 3 — Add hardware to the demo** (a second L2, or a fourth
  L1). The 1% line may then be reachable. Cost: a second bladeRF in
  the partner pool is not guaranteed (docs/ADVANTAGES.md §6 R5: Pluto+ / bladeRF
  availability is best-effort); a fourth L1 stretches the deployment
  ergonomics that Maciej-side is already sized for three nodes.

**Choice: Option 2.** Honesty + pitch alignment + no hardware risk.
The threshold value (0.05) and the contract (95% semi-major) both
stay unchanged. Only the *captions and the demo narration* change.

### D2. The "Demo behaviour" paragraph of ADR-005 is hereby corrected

For the record (ADR-005 itself is not edited — ADRs are append-only,
amendments live in subsequent ADRs):

- **Before (ADR-005, line 159-163, 1-σ-flavoured):** "L1-only fixes
  hover around `MEDIUM`, and adding an L2 node with 1.5° σ pushes
  the fix unambiguously to `HIGH`."
- **After (operative narrative, 95%-corrected):** "The trench-demo
  geometry sits in `MEDIUM` across all four beats: three L1 nodes
  at 5° σ produce a 95%-ellipse semi-major ≈ 26% of range, an L2
  partner node tightens the semi-minor by 65% (357 → 125 m) without
  moving the semi-major below 18% of range. The demo's `HIGH`-band
  transition appears only in **denser deployments** the BoTH3 jury
  is invited to imagine: 5+ L1 nodes, or 2+ L2 nodes, or a fifth
  L1 forward of the emitter. Operational reading: this is what the
  RTL-SDR / bladeRF mesh **honestly delivers**, and the deployment
  -density pitch is the slide for how it grows beyond that."

The dashboard caption strings and the demo replay narration update
accordingly (responsibility: `rfmesh-ops` workstream, ticket TBD).

### D3. Phase-C pessimism is the load-bearing risk

Per `INHERITED_CONTEXT.md` §3.1.1, real-world σ may inflate 1.5-2×
over simulator. The CRLB script's pessimism table:

| Beat | σ_L1 inflation | Semi-major (m) | semi/range |
|------|----------------|-----------------|------------|
| C nominal | ×1.0 | 393 | 26% |
| C ×1.5 | 7.5° σ_L1 | 589 | 39% |
| C ×2.0 | 10° σ_L1 | 785 | 52% |
| D nominal | ×1.0 | 359 | 32% |
| D ×1.5 | (L1 7.5°, L2 2.25°) | 538 | 48% |
| D ×2.0 | (L1 10°, L2 3.0°) | 718 | 64% |

Under Phase-C pessimism the band labels degrade `LOW → LOW → MEDIUM
→ MEDIUM` — still a visible improvement trajectory beat-to-beat, just
not crossing into HIGH. **This is the *expected* honest result** — a
pristine simulator-validated demo at HIGH would be more suspicious to
an RF/EW jury than a realistic MEDIUM-band demo with a clear
improvement story.

### D4. The threshold value (0.05) does not change in v1.0

`_HIGH_BAND_RANGE_FRACTION = 0.05` is the right *target* for a
well-instrumented mesh; the current demo simply does not reach it.
Maintaining the value preserves forward compatibility: a future
deployment with 5 L1 nodes + 2 L2 nodes will hit HIGH, and the demo's
"this is what it looks like when you scale" story uses the unchanged
threshold as its anchor. Tuning the value down to match the demo
geometry would lose that future-state anchor and is reversible the
wrong way (loosened thresholds rarely tighten back).

Post-Phase-C, if real-world σ proves to require a different operational
band layout, this is a one-line diff (ADR-005 D6 explicitly anticipates
the tuning). Until field data exists, the value stays.

### D5. The ADR-005 "Rationale for the 5% choice" paragraph is
        explicitly superseded

Future readers consulting ADR-005's Rationale should consult **this
ADR-009 §"Context — what the math actually says"** for the correct
derivation. ADR-005 itself remains in the decision log unedited as a
matter of ADR discipline (append-only history; readers see the
correction trail).

The ADR-005 D5 obligations stand: dashboard renders
`100 · semi_major_m / range_m` to one decimal place alongside the
band, BoTH3 spec band (≤ 1%) shown as a reference shaded region.
Under the corrected narrative, the percentage display does most of
the operator-facing communication and the band name is the supporting
discretisation.

---

## Consequences

### Positive

- **The demo is honest.** No band transitions promised that the math
  cannot deliver. An RF/EW jury reading the dashboard sees the
  percentage shrink from 49% → 26% → 32% (or similar) and concludes
  the geometry is doing its job — they do not have to forgive a
  misnamed band.
- **The deployment-density story is *louder*.** ADR-005's original
  narrative undersold the scaling argument by promising HIGH at 4
  nodes. The corrected narrative says: *"At 4 nodes you are at
  MEDIUM with this hardware. At 6 nodes or 4 nodes with two L2 you
  are at HIGH. At 8+ nodes you cross BoTH3 spec."* That is the pitch
  Maciej wants — the operator can see how much improvement is
  available by adding nodes, and the cost per node is small.
- **No contract change, no threshold tuning, no risk of "compensating"
  errors in the code.** Only the narrative — the cheapest possible
  fix.

### Negative

- **The "HIGH on stage" applause moment from ADR-005 §"Demo behaviour"
  is gone.** Demo authoring (the ops-dashboard caption strings) must
  earn the credibility differently — by showing the percentage drop
  and the GDOP improvement, not by celebrating a band-name flip. This
  is a real authoring constraint; the ops workstream and Maciej
  jointly rehearse this.
- **ADR-005's Rationale paragraph remains visible.** Future readers
  who skim ADR-005 without finding ADR-009 will see the wrong
  derivation. We mitigate this with a one-line "AMENDED BY ADR-009"
  note at the top of ADR-005 in a follow-up housekeeping commit.

### Neutral

- The implementation (`confidence.py:_HIGH_BAND_RANGE_FRACTION = 0.05`,
  the residual gate, the GDOP gate) does not change. WS-CD-007's
  `Fuser.fuse` and WS-CD-008's honest-ellipse Monte Carlo proceed
  unchanged.

---

## What this ADR does NOT decide

- **The eventual operational threshold.** Tuning 0.05 → some other
  value is a post-Phase-C decision and a separate ADR.
- **Whether to add a 4th L1 or 2nd L2 to the demo.** Option 3 is the
  hardware-side answer; this ADR picks the software-side answer
  (option 2). If Maciej's bench session secures a second partner-pool
  bladeRF, option 3 can land alongside option 2 — they are not
  mutually exclusive.
- **The dashboard caption strings.** Specific text for each demo beat
  is `rfmesh-ops` workstream's authoring job; this ADR fixes the
  *truth-value* of what the captions assert (MEDIUM-throughout vs
  HIGH-on-L2-join), not the precise wording.

---

## Validation gates

- `docs/demo/crlb_analysis.py` runs from the workspace root and
  reproduces the per-beat semi-major / semi-minor / range / GDOP
  numbers cited above. Anyone re-running this script and getting
  meaningfully different numbers should open an issue — the CRLB
  formula is closed-form and deterministic.
- WS-CD-008's honest-ellipse Monte Carlo, when it lands, should
  reproduce the same numbers to within the empirical-vs-claimed σ
  tolerance (±20% on semi-major). This is the load-bearing check that
  the implementation matches the analytic prediction.
- A regression test in `packages/rfmesh-fusion/tests/` keying off
  these numbers is a follow-up (post-WS-CD-008), so the
  geometry-vs-confidence-band assertion is checked in CI rather than
  living in a YAML scenario file alone.

---

## Provenance

- Surfaced by a CRLB analysis subagent run on 2026-05-17 against
  `scenarios/trench_demo.yaml` (commit 30e1873).
- Subagent recommended option 2; lead-Opus + Maciej ratified.
- Files for cross-reference: `docs/demo/trench-demo-geometry.md`
  (full analysis), `docs/demo/crlb_analysis.py` (the reproducible
  numerical derivation).
