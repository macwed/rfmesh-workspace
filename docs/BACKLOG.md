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
| WS-A-005 | RTLSDRDevice → Receiver Protocol port | open | ½-1 day | PY | **Friend** | SALVAGE_AUDIT.md Part 4d |
| WS-A-006 | Servo host driver port (6 modules + firmware-C-test parity) | open | 1 day | PY + EMB | **Friend** | WS-A-005 (sets pattern for Receiver/Protocol seating) |
| WS-A-007a | ESP32-S2 firmware port (USB-Serial-JTAG → TinyUSB CDC) | open | 3-4 hr (source); **flash blocked on Maciej** | EMB | **Lead-Opus** (source) + Maciej (flash) | INHERITED_CONTEXT §1.1 |
| WS-A-007b | LoRa beacon firmware (RadioLib + SX1276) | open | ½ day (source); **flash blocked on Maciej** | EMB | **Lead-Opus** (source) + Maciej (flash) | `lora_beacon_spec.md` |
| WS-A-008 | Multi-node bench bring-up (3 nodes + fusion + dashboard + CoT) | blocked-hw | 1-2 days | PY + RF | **Maciej** + Friend | WS-A-005, WS-A-006, Maciej bench prep |

---

## Tier E — Demo polish (P0 for BoTH3 jury)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| E1 | L1-refused dashboard caption (minimum-viable G7) | open | 2-3 hr | PY | Lead-Opus | RF-DSP audit F2 |
| E2 | ADR-001/002/003 backfill (monorepo, contracts-as-Protocol, no-GNSS/TDOA/magnetometer) | open | 2 hr | PM | Lead-Opus | Architect F6 |
| E3 | Jury Q&A rehearsal entries — latency, FHSS, MDS, broadband-jammer, Phase C site selection (3 from `findings.md`) | open | 3-4 hr | RF + PM | Lead-Opus | Demo-integrity F6 |
| E4 | Golden test for `l2_null_steering.compute_receive_pattern` | open | 1 hr | DSP | Lead-Opus | Architect F4 |
| E5 | Slide deck stub OR step-3-only contingency re-cut | open | 1 day | PM + RF | Friend (PM hat) OR Lead-Opus | Demo-integrity F7 |

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
| G1 | Phase-C-failure replay scenario (Mast A `.iqx` integration test) | blocked-hw | 3 hr | DSP | Lead-Opus | C2 |
| G2 | AIC/MDL source-rank detector for L2 MUSIC | open | ½ day | DSP | Friend (DSP hat) or Lead-Opus | none (P1) |
| G3 | `GDOP_UNCOMPUTABLE` sentinel + SCHEMA_VERSION 1.2.0 ADR | blocked-decision | 1 day | PY + PM | Lead-Opus + Maciej-sign-off | ADR for SCHEMA bump |
| G4 | `BearingReport.method` extended for L1 refusal (wire-level diagnostic) | blocked-decision | 1 day | PY + PM | Lead-Opus + Maciej-sign-off | ADR for SCHEMA bump |
| G5 | Multi-site Phase C campaign (2-3 more Mast-C-class sites) | parking-lot | 1 day per site | RF (bench) | Maciej | post-BoTH3 |
| G6 | Bearing-truth dataset library | parking-lot | incremental | RF + PY | Lead-Opus | G5 |
| G7 | Full polar diagnostic panel (live RSSI-vs-azimuth + prominence ring) | open | ½ day | PY (matplotlib) | Lead-Opus | none (P1 — pairs with E1) |
| G8 | `rfmesh-find-reference` CLI (btsearch.pl site-selection helper) | open | ½ day | PY | Friend (PM hat) | none (P2) |

---

## Tier S — Simulator + infrastructure (P1)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| S1 | `--channel-override` CLI flag for `rfmesh-demo-replay` (A3 NEXT-2) | open | 1 hr | PY | Lead-Opus | none |
| S2 | 3-node trench scenario YAML (`scenarios/three_node_trench.yaml`) | open | 2 hr | PY + RF | Lead-Opus | none |
| S3 | Demo-replay multi-process orchestrator scaffold (v1.0→v1.5 lift) | parking-lot | 1-2 days | PY | Lead-Opus or Friend | none (P2) |
| S4 | `rfmesh-contracts` round-trip tests (`model_validate(model_dump())` per message) | open | 2 hr | PY | Friend (PM hat) or Lead-Opus | none (P2) |
| S5 | rfmesh-ml threat-profile YAML fuzzing | open | 2 hr | ML + PY | Friend (ML hat) | none (P2) |

---

## Tier H — Hardening + Bar 2 items (P2-P3, post-BoTH3)

| ID | Title | Status | Effort | Skill | Suggested owner | Depends on |
|---|---|---|---|---|---|---|
| H1 | bladeRF / Pluto+ `CoherentReceiver` implementation | blocked-hw | 2-3 days | PY + EMB | Lead-Opus | Pluto+ delivery OR partner-pool bladeRF |
| H2 | Per-array L2 calibration tool (phase/gain offsets table) | open | 1 day | DSP | Lead-Opus | H1 (for hardware validation) |
| H3 | WinTAK / iTAK / non-FTS C2 integration | parking-lot | 1-2 days | PY | Friend | none (P3) |
| H4 | L3 threat library training pipeline (real-IQ for POLE21 / VOLNOREZ stubs) | parking-lot | weeks | ML | Friend (ML hat) | real-IQ captures |

---

## Recommended ordering — next 4 days

Assuming friend starts tonight, Maciej bench prep continues, lead-Opus works non-stop in parallel:

```
Day 1 (Friend)         Day 1 (Lead-Opus)      Day 1 (Maciej)
WS-A-005 part 1        E2 (ADR backfill)      Bench prep
WS-A-005 part 2        E4 (golden test)       Bench prep
                       E1 (L1-refused caption)
                       S1 (--channel-override)
                       S2 (3-node scenario YAML)

Day 2 (Friend)         Day 2 (Lead-Opus)      Day 2 (Maciej)
WS-A-005 review/merge  E3 (jury Q&A)          Bench prep / Phase C re-capture
WS-A-006 part 1        E5 (slide stub)        C1 + C2 (capture .iqx)
                       C5 (re-MC at 10m)

Day 3 (Friend)         Day 3 (Lead-Opus)      Day 3 (Maciej)
WS-A-006 part 2        G7 (polar panel)       Bench bring-up
                       C3 (Mast-C scenario)   WS-A-007a flash
                       WS-A-007a source

Day 4 (Friend)         Day 4 (Lead-Opus)      Day 4 (Maciej)
WS-A-006 review/merge  C4 (sim calibration)   WS-A-008 multi-node
G8 (find-reference)    WS-A-007b source       WS-A-007b flash
or G2 (AIC/MDL)        S4 (contract round-trip tests)
```

After Day 4: integration rehearsal + bug fix iteration cycle. Demo day is at +25 from 2026-05-18.

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
