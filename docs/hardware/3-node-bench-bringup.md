# 3-Node Bench Bringup — Maciej-side recipe

**Status:** ready to execute once 3-RTL-SDR + 3-ATK-10 + 3-MG996R hardware is wired and the ESP32-S2 firmware is flashed.
**Reference scenario:** `scenarios/three_node_trench.yaml` (3 L1 nodes, no L2 overwatch).
**Prerequisite:** Phase C PASS at Mast C (✓ recorded 2026-05-17, see `docs/phase-c-report/findings.md`).

This document is the operational recipe — what to plug where, what configs to load, what to expect on the dashboard, and the troubleshooting tree for the 5 most likely surprises.

---

## §0 Hardware checklist

Before powering anything on, confirm:

- [ ] 3 × RTL-SDR V4 dongles (USB-A, distinct serials — read with `rtl_eeprom` if needed)
- [ ] 3 × ATK-10 Yagi antennas (vertical pol, ~50° HPBW at 915 MHz)
- [ ] 3 × MG996R servos + per-axis NVS calibration loaded (`rfmesh-servo-calibrate` per `docs/wire-protocols/servo_uart_v1.md` §3.7)
- [ ] 3 × ESP32-S2 mini boards, firmware flashed per `WS-A-007a` ticket
- [ ] 1 × laptop running fusion server + dashboard (the one you used for Phase C is fine)
- [ ] Wi-Fi or wired ethernet between all 3 nodes and the fusion laptop (NTP-synced clocks; mesh tolerates ~10 ms skew per `FusionConfig.batch_window_ms = 100 ms`)
- [ ] Tape measure or laser rangefinder for triangulation distances
- [ ] Compass + map for headings
- [ ] Test emitter — either Mast C reference (real RF, ~3 km clear LoS) OR LoRa beacon (`firmware/lora-beacon/` once `WS-A-007b` lands)

If any item is `[ ]` not `[x]`, stop and wire it before continuing.

---

## §1 Node configs

Per-node YAML configs live in `configs/bench/`. Three files, one per node:

### `configs/bench/node-l1-west.yaml`

```yaml
schema_version: "1.1.0"
node_id: "node-l1-west"
position:
  lat_deg: <survey result>    # phone GPS at the western forward-observation point
  lon_deg: <survey result>
  hae_m: <survey result>
  sigma_m: 8.0                # phone-GPS uncertainty
heading_deg: 90.0             # antenna boresight east (toward the emitter)
sdr:
  driver: rtlsdr               # NOT "sim" — this is live hardware
  serial: "<from rtl_eeprom>"  # the unique serial of THIS dongle
  sample_rate_hz: 2.4e6
  center_freq_hz: 915.0e6
  gain_db: 30.0                # explicit, NOT "auto" — per INHERITED §1.3
  bias_tee: false
capabilities: [l1_rssi]
bearer:
  kind: wifi
  heartbeat_interval_s: 2.0
fusion_endpoint: "udp://<fusion-laptop-ip>:9000"
```

Repeat for `node-l1-east.yaml` (heading 270, distinct serial, position from survey at NE forward-observation point) and `node-l1-south.yaml` (heading 0, distinct serial, position from survey at central south).

**Critical:** `serial` MUST be unique per node. Index-based addressing (`--device-index 0/1/2`) is fragile across reboot order; serial-based is stable. Use `rtl_eeprom -d <index>` to read each dongle's serial.

---

## §2 Launch sequence

**On the fusion laptop:**

```bash
# Terminal 1 — fusion server
cd ~/rfmesh-workspace
uv run rfmesh-fusion --config configs/bench/fusion.yaml
# expect: "FusionService listening on udp://0.0.0.0:9000"
```

```bash
# Terminal 2 — dashboard (TRENCH layout, 4×2 grid with PseudospectrumPanel)
cd ~/rfmesh-workspace
uv run rfmesh-ops-dashboard --layout TRENCH --client-url ws://localhost:9001
# expect: matplotlib window opens, all panels show "no data yet"
```

```bash
# Terminal 3 — CoT publisher (optional, only if running ATAK round-trip)
uv run rfmesh-cot --fix-stream tcp://localhost:9001/fixes --cot-endpoint tcp://<atak-tablet-ip>:8087
```

**On each of the 3 nodes (in parallel, ~10s apart):**

```bash
ssh node-l1-west
uv run rfmesh-node --config configs/bench/node-l1-west.yaml
# expect: "Receiver opened, capabilities: l1_rssi, fusion connected"
# expect: NodeStatus heartbeat every 2.0s on the dashboard
```

Repeat on `node-l1-east` and `node-l1-south`.

**Expected dashboard state after ~10 seconds:**

- NodeStatusPanel: 3 green rows (one per node), no `status_detail` text (or "LoRa bearer down, Wi-Fi only" only if you wired LoRa)
- FixPanel: still "no fix yet" — the test emitter is not on yet, or no bearings have arrived

---

## §3 Test sequence

### Beat A — single-node sanity (~2 minutes)

Power on the test emitter at the surveyed target position (3 km north, 915 MHz). On node-l1-south, the antenna sweep should show RSSI peak in the expected direction.

**Expected:** BearingsPanel renders one wedge from `node-l1-south` pointing roughly north (0 ± 10°). PseudospectrumPanel empty (L2 not deployed). FixPanel still "no fix" — one bearing is a ray, not a fix.

**Caption to read aloud:** *"Single observation post. One bearing is a ray, not a fix. Add nodes."*

### Beat B — two-node cross-fix (~3 minutes)

Bring `node-l1-east` online. Two wedges now on BearingsPanel, intersecting roughly at the target.

**Expected:**
- FixPanel: position appears, ellipse roughly 500 m × 400 m, `confidence_level: LOW`, GDOP ~1.5
- ClassificationOverlayPanel: "UNKNOWN, classifier did not run" (no L3 on these nodes)
- ResidualsPanel: 2 nodes, residuals close to zero (over-determined geometry, residuals are not informative with N=2)

**Caption:** *"Two posts. Cross-fix appears. Ellipse is wide because two bearings only constrain along one axis."*

### Beat C — three-node bench geometry (~5 minutes)

Bring `node-l1-west` online. Three wedges, all intersecting near the target.

**Expected:**
- FixPanel: ellipse shrinks to roughly 300 m × 250 m, `confidence_level: MEDIUM`, GDOP ~1.16
- ResidualsPanel: 3 nodes, residuals visible, none should exceed 3× their reported sigma (no outliers)
- BearingsPanel: red X marker at the fused position visible

**Caption:** *"Three posts. Ellipse halves. GDOP under 1.2. Geometry is good. This is the bench-bringup endpoint."*

Hold this state for 1-2 minutes to confirm stability (no flapping confidence_level, no growing ellipse, no NodeStatus going red).

---

## §4 Troubleshooting tree

If something goes wrong, work through these in order. Most issues are in #1-#3.

### §4.1 NodeStatus stays grey on dashboard

- Check: `rfmesh-node` process is running on the node (`ps aux | grep rfmesh`).
- Check: Wi-Fi connectivity (`ping <fusion-laptop-ip>` from the node).
- Check: `fusion_endpoint` in the node's YAML matches the laptop's actual IP.
- Check: firewall on the fusion laptop allows UDP/9000 inbound.

### §4.2 RSSI sweep shows flat response (~2 dB front-back)

This is **Mast A's failure mode** (multipath dominance). Either:

- The target emitter is too close (< 1 km — sub-Phase-C). Move it further away.
- The site has dense nearby reflectors (buildings within 20 m of the antenna). Move the node to a clearer spot.
- The antenna is not vertical-polarised (check the Yagi's mounting orientation; cross-pol loss is 20-30 dB).

The L1 estimator will return `None` and the dashboard will show "L1 refused — prominence X dB < 6 dB gate" (per Tier E1 caption). This is honest behaviour, not a bug. Fix the site, not the threshold.

### §4.3 Bearings point in the wrong direction (Mast B's failure mode)

- Co-channel interferer dominating the target. Check `rtl_power -f <target_freq ± 5 MHz>:1k -i 30 ...` for off-axis transmitters within 5 km.
- Move to a less contested frequency (the LoRa beacon at 868 MHz is unambiguous; cellular ~958 MHz upper edge is usually cleaner than 935-940 MHz core).
- Or accept the co-channel and rely on multi-node disagreement to catch the mis-bearing (deployment density advantage).

### §4.4 Fix appears but `confidence_level` stays LOW

- Check GDOP value on FixPanel. If > 6, geometry is degenerate — node positions are nearly collinear from the target's perspective. Move one node off the baseline.
- Check `residuals_deg` — if any is > 3× its node's `azimuth_sigma_deg`, that node is mis-bearing (likely co-channel or multipath at that specific site). ResidualsPanel highlights outliers.
- Check `confidence_ellipse_95.semi_major_m` vs the target's expected range. If `semi_major / range > 30%`, you are at MEDIUM ceiling per ADR-005 §D5(a).

### §4.5 Dashboard crashes / matplotlib backend error

- Use `MPLBACKEND=Agg` for headless rendering (writes PNG snapshots instead of live).
- Or switch to `MPLBACKEND=QtAgg` if TkAgg is misbehaving.
- The dashboard's `_dispatch_one` catches `ValueError/TypeError/RuntimeError` per panel; one bad panel does not crash the whole dashboard, but mpl-backend errors at the figure level do.

---

## §5 Capture artifacts during bring-up

Run `rfmesh-demo-record` in parallel during Beat C for 60 seconds. This captures `.iqx` + sweep JSON per node:

```bash
uv run rfmesh-demo-record --scenario scenarios/three_node_trench.yaml --output recordings/bench-bringup-<date>/ --duration-s 60
```

The recording becomes:
- Demo-replay artifact for slide deck rehearsal (G1)
- Simulator calibration reference (against the channel parameters in `scenarios/three_node_trench.yaml`)
- Regression test fixture for future fusion changes

---

## §6 After bring-up — what to send Lead-Opus

When bring-up completes, paste into the chat:

1. The FixPanel screenshot at Beat C steady-state.
2. The `residuals_deg` values for the 3 nodes.
3. Any `status_detail` text on NodeStatusPanel.
4. Confirmation of the `confidence_level` (HIGH / MEDIUM / LOW).
5. Any troubleshooting-tree branch you hit + the resolution.

Lead-Opus will:
- Update `docs/SPRINT_LOG.md` with the bench-bringup result.
- Calibrate the simulator's channel model against the real-world residuals (closes Tier C C4).
- Tighten `scenarios/three_node_trench.yaml`'s `expected_fix:` numbers against the measured ellipse.

---

## §7 Rollback

If bring-up fails catastrophically (e.g. one of the RTL-SDRs is dead, one node refuses to boot), the demo fallback is:

- `rfmesh-demo-replay --scenario scenarios/three_node_trench.yaml --headless` — the simulator-side equivalent runs end-to-end without hardware. Demo-script step 3 contingency (see `docs/demo/script.md` §5).

The demo is not blocked by single-node hardware failure. Three honest nodes deliver MEDIUM; two deliver LOW-with-ellipse; one delivers "single bearing, no fix" + the honest refusal narrative.

---

## §8 Cross-references

- Phase C bench checklist (single-node, the foundation for this 3-node recipe): `docs/hardware/phase-c-bench-checklist.md`
- Phase C report (the empirical PASS that lets us trust the L1 baseline): `docs/phase-c-report/findings.md`
- Trench-demo geometry (the 4-node BoTH3 scenario this is a subset of): `docs/demo/trench-demo-geometry.md`
- Demo script (the operator-facing narrative; this recipe is the engineering reality): `docs/demo/script.md`
- ADR-005 (confidence policy): `docs/adr/ADR-005-fusion-confidence-policy.md`
- ADR-007 (fusion algorithm choices — Stansfield + MLE + unweighted GDOP): `docs/adr/ADR-007-fusion-algorithm-choices.md`
