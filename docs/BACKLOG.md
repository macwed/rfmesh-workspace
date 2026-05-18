# rfmesh — Backlog

**Last refreshed:** 2026-05-18 at `ec8740c`.
**Reading order:** sort by **Priority** descending, then by **Effort** ascending. Pick anything **Status: open** that has no unmet dependencies and is in your skill range.

**Status legend:** `open` (claimable) · `in-progress` (someone working) · `blocked-hw` (waits on bench / firmware flash) · `blocked-decision` (waits on lead sign-off) · `parking-lot` (post-BoTH3)

**Priority legend:** P0 = on critical path to BoTH3 demo. P1 = high-leverage but not critical. P2 = nice-to-have. P3 = parking lot.

**Skill legend:** PY = Python systems. DSP = signal processing. ML = classifier. EMB = ESP-IDF / Arduino C. PM = project management / docs. OPS = devops / CI. RF = RF/EW domain knowledge.

---

## Tier F — Workstream A hardware path (P0)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| WS-A-008 | Multi-node bench bring-up (3 nodes + fusion + dashboard + CoT) | blocked-hw | 1-2 days | PY + RF | **Maciej** + Lead-Opus | WS-A-005/006/007a/007b (closed); Maciej flash + Mast A/C `.iqx` |

---

## Tier E — Demo polish (P0 for BoTH3 jury)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| E5+ | Slide deck *expanded* (was E5 stub, closed) — full RF/EW-jury content set | open | 1.5-2 d | PM + RF | Lead-Opus | none (Phase 3 step 1) |

---

## Tier C — Phase C follow-ups (P0, depends on Maciej bench re-measurement)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| C1 | Capture Mast C `.iqx` + sweep JSON via `rfmesh-demo-record` | open | ½ day | RF (bench) | **Maciej** | bench prep |
| C2 | Capture Mast A `.iqx` (flat-response signature for simulator calibration) | open | ½ day | RF (bench) | **Maciej** | bench prep |
| C3 | Build `scenarios/mast_c_reference.yaml` matching the captured-data geometry | blocked-hw | 3 hr | PY + RF | Lead-Opus | C1 |
| C4 | Simulator multipath calibration vs Mast A flat response | blocked-hw | ½ day | DSP | Lead-Opus | C2 |
| C5 | Update `WS-CD-008` honest-ellipse MC with new 10m geometry (regenerate `expected_fix:` numbers in `trench_demo.yaml`) | open | 2 hr | DSP | Lead-Opus or Friend | none |

---

## Tier G — Strategic / parking-lot ideas (P1-P3)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| G1 | Phase-C-failure replay scenario (Mast A `.iqx` integration test) | **MUST-HAVE** (blocked-hw on C2) | 3 hr | DSP | Lead-Opus | C2 (Mast A `.iqx`); promoted by rf-dsp council 2026-05-18 |
| G2 | AIC/MDL source-rank detector for L2 MUSIC | open (P1, cuttable) | ½ day | DSP | Lead-Opus | needs Maciej read on criterion choice before lock |
| G3 | `GDOP_UNCOMPUTABLE` sentinel — queued for ADR-013 propagation | queued (Phase 2) | included in ADR-013 batch | PY | Lead-Opus | WS-A-008 smoke PASS (gates SCHEMA 1.2.0 bump) |
| G4 | `BearingReport.refusal_reason` + `Capability.L1_REFUSED_PROMINENCE` — queued for ADR-013 propagation | queued (Phase 2) | included in ADR-013 batch | PY | Lead-Opus | WS-A-008 smoke PASS |
| G5 | Multi-site Phase C campaign (2-3 more Mast-C-class sites) | parking-lot | 1 day per site | RF (bench) | Maciej | post-BoTH3 |
| G6 | Bearing-truth dataset library | parking-lot | incremental | RF + PY | Lead-Opus | G5 |
| G7-extend | Wire `BearingScanPanel` into `DEBUG_LAYOUT` (PanelSpec `projection="polar"`) | open (Phase 3, if slack) | ½ day | PY (matplotlib) | Lead-Opus | none |
| G8 | `rfmesh-find-reference` CLI (btsearch.pl site-selection helper) | open (P2) | ½ day | PY | Lead-Opus | none |

---

## Tier S — Simulator + infrastructure (P1)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| S3 | Demo-replay multi-process orchestrator scaffold (v1.0→v1.5 lift) | parking-lot | 1-2 days | PY | Lead-Opus | none (P2) |
| S5 | rfmesh-ml threat-profile YAML fuzzing | open | 2 hr | ML + PY | Lead-Opus | none (P2) |

---

## Tier H — Hardening + Bar 2 items (P2-P3, post-BoTH3)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| H1 | bladeRF / Pluto+ `CoherentReceiver` implementation | blocked-hw | 2-3 days | PY + EMB | Lead-Opus | Pluto+ delivery OR partner-pool bladeRF |
| H2 | Per-array L2 calibration tool (phase/gain offsets table) | open | 1 day | DSP | Lead-Opus | H1 (for hardware validation) |
| H3 | WinTAK / iTAK / non-FTS C2 integration | parking-lot | 1-2 days | PY | Friend | none (P3) |
| H4 | L3 threat library training pipeline (real-IQ for POLE21 / VOLNOREZ stubs) | parking-lot | weeks | ML | Friend (ML hat) | real-IQ captures |

---

## Recommended ordering

Superseded by `docs/plan-proposition-18-05-2026.md` (council-reviewed 4×
APPROVE, 2026-05-18 evening). The plan-proposition doc carries the
binding 3-phase / 25-day schedule + per-phase council acceptance
criteria. The "next 4 days" block that lived here is retired —
Phase 0 + Phase 1 source are 100 % complete as of `HEAD = 21b3f74`
(WS-A-005, WS-A-006, WS-A-007a, WS-A-007b all merged); the project is
in **Phase 1.5 — Maciej's hardware enablement window** (flash + Mast
A/C `.iqx` re-capture), which unblocks Phase 2 (WS-A-008 smoke +
ADR-013 propagation).

---

## Closed (this sprint)

Sub-list of items shipped at this sprint's commits, for reference. **Do not re-open without lead approval.**

- A1 + A2 — council re-audit + WS-A-005/006/007 ticket drafts (commits `f47fe46`, ticket files under `docs/tickets/`)
- A3 — demo-replay end-to-end exercise + artifact capture (`1932aa1`)
- B1-B4 — SPRINT_LOG enrichment, memory compaction, hypothesis property tests, null-depth MC pre-compute (`c4eeebb`)
- C1-C2 of Tier C (the earlier Tier C, drift audit + tower_sanity decision) — `826d2c2`
- Phase C bench result PASS — `6cf0a87`
- Tier D D1-D5 (Literal[SCHEMA_VERSION] tripwire, headless relay fix, dead assert removal, trench heights 10m, PseudospectrumPanel) — `ec8740c`
- ADR-012 schema_version Literal type — `ec8740c`
- E1 L1-refused caption (`last_refusal_reason`) — `bee8413`
- E2 ADR-001/002/003 backfill — `dee3779`
- E3 jury Q&A entries Q10-Q15 — `27926ea`
- E4 l2_null_steering golden test — `839fb49`
- E5 step-4 contingency re-anchored to A3 + Phase C artifacts — `53eda24`
- G7 BearingScanPanel (polar L1 sweep diagnostic; not yet in default layout — G7-extend) — `59d6acc`
- S1 `--channel-override` CLI flag + S2 three_node_trench.yaml — `8b402e5`
- S4 contract round-trip tests (29 messages) — `839fb49`
- ADR-013 PROPOSED — `8350ec9`
- 3-node simulator smoke artifacts — `794fa75`
- ADR-013 ACCEPTED (propagation deferred to post-smoke) — `1d4c592`
- chore(settings) claude permissions allowlist — `a76d240`
- WS-A-006 servo host driver port (3286 +, 100 unit tests) — `defd4d4`
- WS-A-007a ESP32-S2 firmware port (6679 +, 29 host C tests, wire spec unchanged) — `6acf482`
- WS-A-005 RTLSDR Receiver Protocol port (831 +, 16 unit tests, B3 short-read surface) — `a4576d3`
- WS-A-007b LoRa reference beacon firmware (622 +, SX1276 + RadioLib + PlatformIO) — `481288c`
- Plan-proposition council fold (G1 → MUST-HAVE, E5 → 1.5-2 d, C4 framing, refusal_reason render) — `cbf5958`
- 2026-05-18 evening sprint-log row — `21b3f74`

---

## Bug tracker

Currently no open bugs. The two `🔴` findings from the 2026-05-18 council audit (CLI assert + headless relay) were both fixed in `ec8740c`.

If you find a bug: file as a new `B<NNN>` row above (Tier S section for simulator bugs, Tier F for hardware path bugs, etc.), with severity and reproduction steps. Then open a PR.

---

## Notes for the backlog owner (Maciej-as-lead)

- This backlog is **flat by design**. No deep tree. If you find yourself wanting epic-feature-task hierarchy, that is scope creep; refuse.
- Effort estimates assume Claude-amplified work. Halve them for "with Claude", double for "without Claude or unfamiliar territory".
- When a ticket lands, move its row from the active table to the "Closed" section with the merge commit ID.
- ADRs (decision-blocked items) are the bottleneck class — surface them in your weekly review.
