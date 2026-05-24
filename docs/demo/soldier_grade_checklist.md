# Soldier-grade UX checklist — BoTH3 demo acceptance gates

**Status:** binding for `docs/demo/pitch_deck.md`. Edits by lead-Opus via PR + 4× council APPROVE.
**Date:** 2026-05-24.
**Audience:** anyone preparing the BoTH3 jury demo. Each item is a YES/NO gate.

ADR-021 council demo-integrity rec listed 10 soldier-grade UX must-haves before the deck can ship. This file pins them as acceptance gates the demo run must pass on a real field tablet (10" Android, sunlit Marche-les-Dames courtyard, gloves on) **before** the deck slot.

The demo cannot run for the jury until **every box below is green**. If a box is yellow/red, the slot is rehearsed but flagged in the spoken script with "this surface is honest but unfinished — here's the roadmap."

---

## §1 The ten gates

| # | Gate | Status | Anchor in code/docs |
|---|------|--------|---------------------|
| 1 | **5-state badge legend strip** visible above node list — chips ordered by operator action priority (fault → manual → linked → sweeping → parked). | ✅ shipped (`d47ab2c` + `f6480dd`) | `deployment/frontend/link.html` + `link.css` |
| 2 | **`MANUAL_HOLD` countdown** ticks `manual · 7s` → `0s` in operator's peripheral vision. | ✅ shipped (`d47ab2c`) | `deployment/frontend/link.js` `badgeLabel` + 1s `setInterval` |
| 3 | **ALL-STOP two-tap confirm**, panic button always present in sidebar. | ✅ shipped pre-pivot | `link.html` `panel-allstop` + `link.js` |
| 4 | **Calibration provenance label** per node (`cal_provenance: "config"` v1, `"firmware-nvs"` follow-up). | ✅ shipped (`2db406b`) | `link.js` `mergeHello` + `node_hello` payload |
| 5 | **Link margin in dB** above local noise floor (NEVER absolute dBm — no SDR in scope is power-calibrated, B.2). | ⚠ shipped surface; live measurement TODO | `link.js` `detailMarginEl`; backend wiring lands with ADR-025 comms-mode |
| 6 | **Last-acquired age** on every selected node ("just now" / "12 s ago" / "2 min ago"). | ✅ shipped pre-pivot | `link.js` 1s `setInterval` legacy-state branch |
| 7 | **WS-offline banner** + controls disabled when backend WS drops; reconnect with exponential backoff. | ✅ shipped (`2db406b`) | `link.js` `connectWs` + `scheduleReconnect` + `canSteer` gate |
| 8 | **Hard-clamped manual-steer slider** at `calibrated_geographic_arc_deg` from `node_hello`. Out-of-arc command refused loudly by node (`ManualSteerOutOfArcError`). | ✅ shipped (`d47ab2c`) | `link.js` `renderDetail` clamp + `controller.py` `_validate_manual_angle` |
| 9 | **Refusal toast** when node refuses a command — `command_refused` WS payload kind surfaces a red message on the detail drawer. | ✅ shipped (`d47ab2c`) | `link.js` `showRefusal` + backend `ws.py` forwarder |
| 10 | **"Node moved" → RE-ACQUIRING** — when a peer goes silent or moves >5° / >2 m, badge flips to RE-ACQUIRING with countdown. | ⚠ surface ready; trigger logic lands with ADR-019 follow-up (motion-delta detector) | `link.js` `mergeControllerState`; node-side IMU motion-delta TODO |

**Acceptance:** 8 of 10 are demoable today (`d187c12` head of `feature/directional-comms`). Gates 5 (live link margin) and 10 (motion-delta trigger) ship surface but lack live data — the spoken demo script names them with "this measurement is honest; today the trigger is operator-initiated, the IMU-delta autoflip is roadmap."

---

## §2 Field-tablet checks (pre-demo, day-of)

These don't fit cleanly as binary gates because they depend on the specific tablet + environment, but they're the second-pass jury-kill-shot list:

- [ ] Slider scrubs smoothly under glove input (no missed steps, no accidental two-finger zoom). Verify on the actual tablet.
- [ ] FAULT badge contrast survives full midday sun (compare against `link.css` `#c0392b` red on the tablet's brightness-max screen).
- [ ] Amber `sweeping` vs orange-bold `manual_hold` chips are distinguishable side-by-side under direct sun (demo-integrity rec).
- [ ] Two-tap ALL-STOP cannot be triggered accidentally by the operator's elbow / armoured glove finger.
- [ ] Cellular link drop → backend offline banner appears in <3 s.
- [ ] WS reconnect after backend restart completes in <10 s; bearing fan-out resumes without page reload.

---

## §3 What's NOT in scope of this checklist

- Engineering panels (`rfmesh-ops` matplotlib): dev/diagnostic only per ADR-021 §"UI mandate". The jury sees the browser UI; the matplotlib panels are for our own bench debugging.
- ATAK marker styling (CoT publisher): tracked separately. Currently ships a hostile-emitter marker with `<shape><ellipse>` polygon; the "GDOP uncomputable" / "L1 refused" honesty fields from ADR-013 do NOT yet surface in CoT remarks (demo-integrity rec on `53140df`). Tracked as a follow-up.
- L3 classification label rendering: future enhancement; v1 demo runs without classifier output on the operator UI.

---

## §4 Sign-off

Before the deck ships to print:

1. Lead-Opus walks the demo on the actual field tablet under target conditions.
2. Every ✅ gate above is verified live, not on a dev laptop.
3. Every ⚠ gate has a one-sentence script entry naming the limit honestly.
4. Demo-integrity council reviews the recorded walkthrough (no live council on demo day; the recording is the gate).

The honest-payload story is the differentiator with this jury. A green checklist is what makes "engineering, not magic" defensible under cross-examination.
