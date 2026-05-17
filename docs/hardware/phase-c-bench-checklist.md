# Phase C — bench-bringup checklist (one-day, Maciej-side)

**Purpose.** First on-air execution of the bearing-scan pipeline against
real RF, against a real emitter, on real hardware. Answers the single
physics question the whole L1 stack assumes the answer to: *does
amplitude-comparison DF with the ATK-10 Yagi + RTL-SDR V4 actually
produce a clean directional response toward a known emitter at this
home site?*

Source: reconstructed from `INHERITED_CONTEXT.md` §3.1 (procedure not in
repo — original `tower_sanity_playbook.md` was a Thread-1 output never
migrated; this checklist supersedes it for the new project).

**Status.** Phase C is the **first hardware-validation gate**, run in
parallel with software development. It does **not** block software
work — the simulator-first model means B/C+D continue regardless. Phase
C calibrates *numbers* (the impairment parameter ranges the simulator
uses), not the *plan* (the architecture is sound either way).

---

## §0 What "PASS" means and what "FAIL" means

**PASS.** A manual antenna sweep against an identified reference emitter
produces a recognisable directional response with the peak within **±10°**
of the true bearing, peak prominence **≥ 6 dB** above the off-peak floor.

**FAIL.** Any of: no peak; peak in the wrong quadrant; peak prominence
< 6 dB; response so noisy that peak-fitting is meaningless.

FAIL is **diagnosis, not panic.** Five pre-enumerated failure modes
(see §5) each have a documented branch. Pick the next experiment from
that list; do not improvise.

---

## §1 Equipment for the day

| Item | Use |
|---|---|
| RTL-SDR V4 dongle | The receiver. Single-channel. |
| ATK-10 Yagi (or whatever directional antenna is at hand) | The directional element. |
| SMA cable, long enough to point the antenna without moving the PC | Connection. |
| Laptop with `rtl_sdr` + `rtl_power` installed (Linux) | Capture. |
| Magnetic compass or phone-compass app | Heading reference. |
| Map of home site with known landmark bearings | Ground truth. |
| Optional: 6 m of measured cord or laser rangefinder | Approximate distance to landmark. |

**No servo, no bracket, no firmware needed for Phase C.** Hand-rotate
the antenna. Mechanical fidelity is for later; today is about the RF
chain producing a directional response *at all*.

---

## §2 Phase A — Identify the reference emitter (~30 min)

The home site does not have a confirmed reference emitter yet — that
is an open item from the prior project (`INHERITED_CONTEXT.md` §3.2).
Today's first job is to fix it.

**Lessons learned from the 2026-05-17 Phase C run** (see
`docs/phase-c-report/findings.md` §5 for the full write-up): on that
night Maciej swept *three* candidate cellular masts before one of them
PASSed. The two failures were honest — multipath dominance at 650 m,
co-channel interference from a stronger off-axis transmitter at 2.2 km
— and produced explicit diagnostics that retroactively shaped the
checks below. Read those before starting your own first sweep.

### A.1 Wideband scan

```bash
# 800-980 MHz cellular downlink band, 60 s integration, 10 kHz bins
rtl_power -f 800M:980M:10k -g 40 -i 60 -e 60 -F 9 cell_scan.csv
```

Expect: a busy spectrum with one or more strong, near-constant lines.
Those are the GSM/LTE base-station downlinks from the nearest tower.

### A.2 Pick a stable carrier

In `cell_scan.csv` (or via a quick `python -c "..."` plot), find the
strongest line that is *temporally stable* (line is on for the whole
60 s, not bursty). Note its centre frequency to ±10 kHz.

Record:

- `f_ref_hz = <picked frequency>`
- `bearing_to_tower_deg = <from map + compass>` (heading from the
  antenna's *as-mounted* zero to the tower)
- `distance_to_tower_m = <approximate, from map>`

Sanity bound: the tower is expected at 1–5 km. If it is much closer
the signal will dominate everything and the off-peak floor estimate
will be unreliable; pick a weaker, more distant carrier instead.

**Sub-1 km is the no-fly zone for L1 DF.** Mast A at 650 m on
2026-05-17 delivered a 1.96 dB front-back ratio — well below the
6 dB prominence gate, because near-field multipath flattens any
antenna's directional response. Pick reference emitters at ≥ 2 km.

### A.2.bis Isolation check (added after the Mast B failure)

A strong, *temporally stable* carrier is necessary but not sufficient.
The candidate frequency must also be **spatially isolated** — no
co-channel transmitter close enough to dominate when the antenna
points elsewhere.

After picking `f_ref_hz`, point the antenna **perpendicular to the
expected tower bearing** (or any other off-axis direction) and re-read
RSSI at the same frequency:

```bash
# Quick off-axis RSSI sanity check at the candidate frequency.
rtl_sdr -f <f_ref_hz> -s 2048000 -n 4096000 -g 40 - | \
  python -c "import numpy as np, sys; \
             iq = np.frombuffer(sys.stdin.buffer.read(), dtype=np.uint8); \
             iq = (iq[0::2].astype(np.float32) + 1j*iq[1::2].astype(np.float32) - 127.5)/127.5; \
             print(f'mean |IQ|: {np.mean(np.abs(iq)):.4f}')"
```

Rule of thumb: off-axis RSSI should be ≥ 6 dB below on-axis RSSI at the
candidate frequency. If they are within ~3 dB, the candidate is
contaminated by co-channel interference from a different transmitter
— Mast B's failure mode. Pick a different frequency (the cellular
allocation tables list dozens of GSM-900 / E-GSM900 / LTE B8 channels;
sites at the edge of the band, e.g. 958-960 MHz, are usually less
contested than the dense 935-940 MHz core).

### A.2.ter Site selection (added after the Mast A failure)

If the home site is dense (suburban, near other buildings, near a
busy road) and the closest mast is < 2 km, **drive to a clear-line-
of-sight position** ~3 km from a known-strong tower identified via
btsearch.pl's heatmap (or equivalent operator-published map). The
trip is part of the system — site selection is not a happy accident
that good Phase C results require, it is an operational protocol.

### A.3 If the scan finds nothing usable

Two branches:

- *Real result A.3a.* No cellular carriers visible at all → the
  RTL-SDR + antenna are not connected, the gain is wrong, or the
  antenna is pointed straight up. Diagnose hardware first.
- *Real result A.3b.* Cellular present but every line is bursty → the
  home site is too far from any base station for a steady downlink.
  Skip cellular; use a known FM broadcast tower at 88–108 MHz instead
  (recompute Phase A with `rtl_power -f 88M:108M:50k …`). FM is
  vertical-polarised and continuous; just a worse fit for the
  competition frequency band, but fine for *physics validation today*.

---

## §3 Phase B — Manual bearing sweep (~30 min)

### B.1 Sweep layout

Stand or sit so the antenna can rotate freely 360° in azimuth. Pick
9 headings, 45° apart, starting from the compass reference (`0° = true
north`, clockwise positive). Use whatever crude pointing aid is at
hand — a printed compass rose on the floor works.

### B.2 Capture at each heading

For each of the 9 headings, point the antenna and run:

```bash
# 1 s capture at f_ref_hz, 1.024 MS/s
rtl_sdr -f <f_ref_hz> -s 1024000 -g 40 -n 1024000 \
  /tmp/cap_<heading_deg>.iq
```

(Substitute the real value of `<f_ref_hz>` from §A.2.)

You now have 9 raw-IQ files. Total disk: ~18 MB. Total acquisition
time: ~15 minutes including pointing.

### B.3 Compute RSSI per heading

```python
import numpy as np
import matplotlib.pyplot as plt

headings_deg = [0, 45, 90, 135, 180, 225, 270, 315]  # add 360 if you want closure
rssi_dbfs = []

for h in headings_deg:
    raw = np.fromfile(f"/tmp/cap_{h}.iq", dtype=np.uint8)
    iq = (raw[0::2].astype(np.float32) - 127.5) / 127.5 + \
         1j * (raw[1::2].astype(np.float32) - 127.5) / 127.5
    rssi_dbfs.append(10 * np.log10(np.mean(np.abs(iq) ** 2) + 1e-20))

# Polar plot
theta_rad = np.deg2rad(headings_deg)
fig, ax = plt.subplots(subplot_kw={"projection": "polar"})
ax.set_theta_zero_location("N")
ax.set_theta_direction(-1)  # CW positive to match true-north convention
ax.plot(theta_rad, rssi_dbfs, "o-")
ax.set_title(f"f={f_ref_hz/1e6:.1f} MHz, expected bearing {bearing_to_tower_deg}°")
plt.savefig("/tmp/phase_c_polar.png", dpi=120)
```

Save `/tmp/phase_c_polar.png` and the `rssi_dbfs` numbers — send them
back. That image **is** the Phase C result.

---

## §4 PASS / FAIL evaluation

Apply the §0 criteria to the polar plot.

**PASS:**

- Peak heading within `bearing_to_tower_deg ± 10°` (ATK-10 has ~50°
  HPBW, so a 9-point sweep can localise the peak to one bin = ±22.5°
  in the worst case; ±10° is the *tightening* expected once a finer
  sweep follows, but the coarse sweep should at least put the peak
  in the right quadrant).
- Peak prominence ≥ 6 dB above the median of the off-peak headings.

**FAIL:** any of the §0 FAIL conditions.

Either way: **record the result.** PASS unblocks tightening the loop
(servo, finer sweep, σ honesty against this antenna). FAIL routes to
§5.

---

## §5 Pre-enumerated failure modes (the diagnostic tree)

Source: `INHERITED_CONTEXT.md` §3.1.1.

### 5.1 Multipath dominance

**Signature.** Peak in the wrong direction *and* the shape of the
response is asymmetric or has multiple lobes.
**Diagnostic.** Move the rig 5–10 m. If the peak position tracks the
true bearing while the multipath component does not, that confirms
multipath dominance.
**Architectural consequence.** Real-world σ is wider than simulator σ;
WS-B retunes `MultipathFIRChannel` tap configuration and re-runs the
±20% honesty MC. The 5% HIGH-band threshold in ADR-005 may need
re-tuning post-Phase-C (already flagged in ADR-005 caveat).
**Demo consequence.** Show this honestly. "Real multipath inflates σ
by 1.5–2× over simulator-clean; the ellipse reflects that — and the
mesh still produces a fix because more nodes compensate for worse
per-node σ" lands well with an EW jury.

### 5.2 Polarisation mismatch

**Signature.** Strong response from no direction; the antenna seems
"deaf".
**Diagnostic.** Rotate the antenna 90° (vertical → horizontal). If
the response *strengthens*, the deployment polarisation assumption is
wrong (`INHERITED_CONTEXT.md` §1.4 says vertical is the binding
choice — confirm it is what is actually happening physically).
**Fix.** Re-mount with correct polarisation. Re-run the sweep.

### 5.3 Antenna pattern deformation from the rig

**Signature.** Peak in roughly the right direction but the response
is asymmetric — rising edge differs sharply from falling edge.
**Diagnostic.** Move the RTL-SDR away from the antenna boom (longer
cable). If the asymmetry reduces, the SDR's metal case is
deforming the pattern.
**Fix.** Document the minimum cable length the rig requires for a
clean pattern. Will go into the eventual `NodeConfig` deployment
guide.

### 5.4 Servo backlash / cogging (N/A today — no servo)

Phase C today uses manual pointing, so this mode cannot surface yet.
Re-checked later when the servo is integrated.

### 5.5 RF-chain / gain issue

**Signature.** Phase A sees nothing, or Phase B RSSI is at the noise
floor everywhere.
**Diagnostic.** Replace the antenna with a known-working one
(rubber-duck whip that came with the dongle), repeat Phase A. If
RSSI rises, the ATK-10 connection or the antenna itself is the issue.
If RSSI does not rise, the RTL-SDR is the issue.
**Fix.** Hardware-side, not software.

---

## §6 What to report back

A short note in the project chat, the §3 polar plot attached:

- `f_ref_hz`, `bearing_to_tower_deg`, `distance_to_tower_m`.
- The 9 RSSI values.
- Overall PASS / FAIL.
- If FAIL: which §5 branch best matches, and the next diagnostic
  intended.

That's the whole report. Phase C is a single experiment; the
*architectural* response to its outcome is what matters, and that is
the lead's call (not yours on the bench).

---

## §7 What Phase C does NOT cover today, and why that is fine

- **Real servo-driven sweep.** Servo + bracket + clone-calibration is
  hours of work; not Day-1. Today is the *physics question* answered
  with manual pointing.
- **σ honesty against this antenna.** That is the *follow-up* experiment
  once Phase C passes — a finer sweep (say 18 headings, 20° apart) on
  the same emitter, comparing the measured peak-fit σ to what L1 says
  the σ should be given the SNR. That experiment retunes the simulator
  if the numbers diverge.
- **LoRa beacon as controlled emitter.** Phase A may give us a good
  cellular reference; the LoRa beacon is the *controlled* test
  source we will build later. Today, we use whatever the home site
  actually emits.

---

## §8 If Phase C cannot run tomorrow

Software development continues. The architecture is structured so that
Phase C is a *gate on numerical calibration*, not a *gate on
progress*. Worst case: ship the demo with simulator-calibrated σ
numbers and a candid asterisk: "validated against the modelled
multipath profile; real-world σ will be retuned after the first
on-site Phase C." A Belgian Defence jury will respect that more than
fabricated field validation.
