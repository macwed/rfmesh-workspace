# Next session — pickup notes (2026-05-20 evening)

**Status at HEAD = `e61268d`** (sprint-log `21b3f74..e61268d` = 12 commits today).

**Tests:** 656 pass + 5 hardware-skipped + 2 deselected. mypy strict + ruff clean workspace-wide.

**Demo target:** BoTH3 Counter-Jamming Challenge 2, ~25 days out from 2026-05-20.

**Project completion (council-aggregated):** ~85 % software-side; gated on hardware + bench session for the remaining 15 %.

---

## What's done at this checkpoint

Read `docs/SPRINT_LOG.md` "2026-05-19 / 2026-05-20" row for the full eleven-commit summary. One-line condensed:

- Mast A + Mast C recapture; Mast C is now the project's empirical anchor (`scenarios/mast_c_reference.yaml`, ADR-014 PROPOSED).
- Simulator calibrated against Mast C (C4, deterministic test).
- `scenarios/trench_demo.yaml` `expected_fix:` block regenerated (C5, 5000-trial MC).
- `docs/demo/script.md` Beat B reframed LOW → MEDIUM + Beat C/D number refresh.
- `docs/demo/slide_deck.md` BoTH3 jury deck authored (~20 slides, 3× council APPROVE-WITH-FIXES, 11 fixes applied).
- Beat E preload helper + dashboard `--beat-e-cache` wiring.
- ew-specialist Council member added.
- G7-extend (BearingScanPanel polar in DEBUG layout).
- `scripts/show_dashboard.py` interactive demo launcher with optional `--cot-url` for live ATAK.

---

## What's blocked on Maciej (the critical path)

1. **WS-A-008 — 3-node bench bring-up.** The demo-blocking integration. Flash WS-A-007a (S2 servo firmware) + WS-A-007b (LoRa beacon, if SX1276 hardware on hand) and run the 3-node trench-demo scenario against real hardware. Procedure: `docs/hardware/3-node-bench-bringup.md` (if it exists) or the plan-proposition Phase 2 step 1.

2. **Mast A re-survey at 936.804 MHz.** Operator-personal procedure: `NOTES_mast_a_resurvey.md` (gitignored, local-only). The 2026-05-19 multi-height + V/H + spectrum data raised uncertainty in the original 2026-05-17 "multipath dominance" diagnosis at Mast A; re-survey at the actual dominant carrier + clinometer-derived h_tx will discriminate. Either outcome (PASS or STILL-FLAT) is publishable.

3. **ADR-014 ACCEPT / REJECT.** `docs/adr/ADR-014-mast-c-empirical-anchor.md` is PROPOSED. Maciej-decision. ACCEPTED unblocks future `ARCHITECTURE.md` §0 edits citing Mast C as the binding anchor.

4. **Slide deck render check.** Install `marp` CLI on the bench, run `marp docs/demo/slide_deck.md --pdf --output build/slide_deck.pdf`. Visually verify the 20 slides render cleanly + any iterative tightening Maciej wants on the spoken-narration side.

5. **FreeTAKServer + ATAK live wiring (optional).** Per the 2026-05-20 session end: `scripts/show_dashboard.py --cot-url tcp://<freetakserver>:8087` emits CoT to ATAK. Worth a one-off "phone-on-the-table" rehearsal before BoTH3.

---

## Solo-doable in the next session (if hardware still blocked)

The tail. Anything material requires hardware now, but if a future session arrives before the bench session, possibilities:

| Item | Effort | Notes |
|---|---|---|
| Marp render verification + slide tightening | 1-2 h | `marp` install + visual review |
| G2 AIC/MDL source-rank detector for L2 MUSIC | ~½ day | Pure DSP. Plan parks as "needs human read before locking criterion choice". Don't do without Maciej direction. |
| Beat E.2 panel widget — operator "engage null" button | ~1 day | matplotlib widgets + cache-swap logic. The cache stores both baseline + engaged patterns; button just swaps which one renders. v1.0 demo would have this. |
| `docs/threat-library.md` stub (currently broken cross-ref in `slide_deck.md` line removed but the threats/profiles/ folder could use an inline README) | 30 min | Minor cleanup; threats/profiles/ has 6 YAMLs already. |
| ADR-013 propagation Phase 2 dry-run on a feature branch | ~2 h | Gated on WS-A-008 smoke PASS per plan-proposition. Don't merge to main without that. |

---

## Things that should NOT be done in a future session (project-decided rails)

- **Edit `docs/phase-c-report/findings.md`** — operator decision earlier this session: tracked file stays as the 2026-05-17 record. The corrected analysis lives in `NOTES_phase_c_amendment_2026-05-20.md` (operator-personal, gitignored). ADR-014 captures the architectural impact without leaking the embarrassment.
- **Edit `ARCHITECTURE.md` §0** until ADR-014 is ACCEPTED — the 2-5 km envelope claim's empirical anchor citation change is gated on the ADR's status transition.
- **Run ADR-013 SCHEMA 1.2.0 propagation** until WS-A-008 bench smoke passes — the plan-proposition's Phase 2 ordering. Layering a SCHEMA bump on an un-validated bench conflates two debug surfaces.
- **Touch `packages/rfmesh-contracts/src/`** — frozen at 1.1.0 (Invariant B1). Any contract change goes through an ADR + `SCHEMA_VERSION` bump + lead approval first.
- **Add deployment-density N≥5 promises**, **claim sub-degree L1 angular accuracy**, **claim absolute dB / dBm**, **frame null-steering as ECM** — all the "Honesty caps" rails on `slide_deck.md` apply to any future deck or doc edit.

---

## Where to find context on cold start (read order)

1. `CLAUDE.md` — short project reference.
2. `AGENTS.md` — agent rules + Seven Binding Invariants.
3. `ARCHITECTURE.md` — the *why*. Binding invariants.
4. `INTERFACES.md` — contract semantics.
5. `WORKSTREAMS.md` — ownership.
6. `docs/SPRINT_LOG.md` — latest row (this session = 2026-05-19/20).
7. `docs/BACKLOG.md` — open ticket table.
8. `docs/plan-proposition-18-05-2026.md` — binding 25-day schedule.
9. `docs/demo/script.md` + `docs/demo/slide_deck.md` — demo materials.
10. `docs/adr/ADR-014-mast-c-empirical-anchor.md` — latest architectural decision.
11. Operator-personal (NOT in git): `NOTES_phase_c_amendment_2026-05-20.md`, `NOTES_mast_a_resurvey.md`, `NOTES_mast_recapture_2026-05-19.md` — corrected Phase C analysis + future-bench-session procedures.

---

## Operator's interactive demo, today

The one thing landed this session that's *visible* without hardware:

```bash
# Window pops, ~60 s of beats render live:
uv run python scripts/show_dashboard.py

# With ATAK on phone + FreeTAKServer on laptop:
docker run -d -p 8087:8087 fts4/fts-server:latest
sudo firewall-cmd --add-port=8087/tcp
uv run python scripts/show_dashboard.py --cot-url tcp://localhost:8087
```

Existing rendered artefacts for offline viewing:

- `docs/demo/artifacts/trench_demo_beat_{A,B,C,D}.png` — per-beat dashboard PNGs.
- `docs/demo/artifacts/3node/three_node_final.png` — 3-node sim-side smoke render.
- `docs/demo/artifacts/trench_demo_fix_{01,02,03}.xml` — CoT XML samples (the bytes that go to ATAK).
- `recordings/2026-05-19-mast-{a,c}/*.png` — bench-side polar plots + spectrum baselines (gitignored; operator-local).

---

## Notes for a future Maciej-tired or context-clear pickup

- **Treat this doc as authoritative for "what's next" but not for "what's true".** The `SPRINT_LOG.md` 2026-05-19/20 row + the ADRs + the tracked YAML are authoritative; this file is a hand-off shortcut.
- If you're starting a fresh session and unsure where to read, this doc points you. If you've already done a session and this doc is stale, **delete or rewrite it** — don't preserve stale handoff notes.
- This file gets rewritten or removed at session boundaries; nothing else should reference it (no other doc cites `docs/NEXT_SESSION.md`).
