# rfmesh — Inherited Context

**Status:** binding for design decisions. Edits by lead via ADR when new inherited knowledge surfaces; otherwise frozen.
**Audience:** every workstream agent, reviewer, Claude Code ticket touching hardware or RF physics. Read *before* designing anything new in those areas — cost of re-deriving fact established here = wasted hours; cost of contradicting one = wasted hardware.
**Date:** 2026-05-14.

Doc exists because prior project (`github.com/macwed/rf-mesh` + Thread 1 coordinator conversation) produced two outputs: **code** (covered by `SALVAGE_AUDIT.md`) and **knowledge** (covered here). Knowledge half = dangerous half — not in repo, lived in one conversation's context. Multi-agent handoff failure mode = knowledge silently disappears. Thread 1 named failure mode in closing digest. Mitigation = file. This = file.

Three classes of inheritance:

- **§1 Hardware-quirk facts** — design constraints that must hold or hardware breaks/under-performs. Binding.
- **§2 Decision rationale** — *why* behind decisions whose *what* is in code. Re-derivation by fresh agent costs time, often produces different (worse) decision.
- **§3 Open questions still genuinely open** — what was *not* resolved before handoff. Tracked so resolved deliberately, not by accident.

Plus §4 on process realities, §5 on inherited regression anchors.

---

## §1 Hardware-quirk facts (binding)

### §1.1 ESP32-S2 firmware port is not a rename of the build target

Prior firmware runs on ESP32-C3 SuperMini, byte-exact-tested against `servo_uart_v1` wire spec. Production hardware = ESP32-S2 mini (3 units on hand, vs 1 C3). Port = **scoped, real work** — Thread 1 sizes at ~2–3 hours — ticket scheduling it must not be written as trivial CMake-target swap.

Change: ESP32-C3 uses **USB-Serial-JTAG peripheral** (`usb_serial_jtag_*` API); ESP32-S2 has **USB-OTG with TinyUSB CDC** (no USB-Serial-JTAG peripheral exists on S2). Migration touches:

- Every `usb_serial_jtag_*` call in `firmware/main/main.c` → TinyUSB CDC equivalents (driver install, RX/TX, vendor strings in `sdkconfig`).
- Console driver: `esp_console_dev_usb_serial_jtag_*` → `esp_console_dev_usb_cdc_*` in `firmware/main/shell.c`.
- `sdkconfig.defaults` gains TinyUSB enable flags, loses USB-Serial-JTAG ones.
- Boot-mode-select logic in `main.c` (1-second window detecting three consecutive ENTERs for linenoise) stays semantically identical but reads from CDC stream.

**Wire protocol** (`servo_uart_v1`, COBS + CRC-16/CCITT-FALSE + TLV) **does not change**. 58 C tests do not change. Host driver (`rfmesh-servo`) does not care which USB stack on other end. Port = bounded and low-risk — but *not* zero-effort. Fresh agent reading "port C3 → S2" without context will under-scope.

**Implication for `WORKSTREAMS.md`:** Workstream A's salvage row for firmware names this port as specific ~2–3 h ticket, with C3 firmware as proven prior art rather than "the firmware".

### §1.2 MG996R clones: pulse range is unknown until calibrated per axis

MG996R servos in inventory = cheap clones from multiple sources. Nominal hobby-servo PWM convention = 500–2500 µs for ±90°. **Clones deviate, sometimes substantially, deviation not documented on package.** Some travel smaller arc for same pulse range; some saturate before reaching 2500 µs; some have asymmetric stops.

**Why firmware exposes `cal` command + persists per-axis calibration in NVS** (`firmware/main/calibration.c`, `cal_types.h`). Calibration procedure documented in `docs/wire-protocols/servo_uart_v1.md` §3.7, operationalised by `rfmesh-servo-calibrate` (inherited host CLI, see `SALVAGE_AUDIT.md` Part 2).

**Binding for design:**

- Node's L1 sweep code MUST consume calibrated angle values, never raw PWM pulses, when constructing `BearingReport.azimuth_deg`. Calibration table = bracket-to-geographic mapping; without it, bearing = whatever angle nominal PWM produces on this particular clone.
- Node MUST refuse to boot into L1_RSSI mode against uncalibrated axis. (Invariant 4 from `AGENTS.md`: no silent fallbacks.) Firmware's `cal_status` reports whether stored calibration present; host driver checks it.
- **Pulse-range assumptions in code forbidden.** Constant `PULSE_MIN_US = 500` somewhere in DSP code, or hardcoded "90 degrees = this much pulse" = contract violation. DSP code does not see PWM — sees azimuth from servo driver after firmware applied calibration.

Also why no field test done yet in prior project: calibration of each deployed axis = *first* step of any field session, procedure depends on mechanical zero-stop that bracket design must provide. (See §1.2.)

### §1.3 SDR amplitude readings are not absolute power

Restated from `INTERFACES.md` §0 and `ARCHITECTURE.md` §7 because single most common credibility failure mode in amateur DF demos: **no SDR in project's scope is power-calibrated.** Not RTL-SDR V4, not HackRF, not bladeRF 2.0 micro, not Pluto+.

- Contracts carry no `dBm` field. SNR always = `dB above this node's noise-floor estimate`.
- `ReceiverCapabilities.is_power_calibrated` = `False` for every implementer; load-bearing flag.
- Any display labelling number "dBm" = UX bug, not typo. Dashboard and CoT remarks use "RSSI (relative)" or "SNR (dB above noise floor)".
- Honest path to absolute power, if ever wanted = one-point on-site calibration against signal generator. Until that step happens on bench, `is_power_calibrated` = False everywhere, labelling follows.

---

## §2 Decision rationale (so it is not re-litigated)

### §2.1 Why AoA cross-fixing, not TDOA

Documented in `ARCHITECTURE.md` §6 as binding decision. Rationale, restated so fresh agent does not "improve" system by adding TDOA:

- TDOA requires ns-level inter-node clock synchronisation. Only practical path on hackathon budget = **GPS-disciplined oscillators with PPS distribution and known cable/USB latencies** — all jammed first in EW (GNSS) or require infrastructure (PTP-aware switches) we lack.
- AoA cross-fixing needs only that *batch window* for collecting bearings wider than inter-node clock skew. NTP over local mesh gives ~10 ms. Default `FusionConfig.batch_window_ms` = 100 ms. Factor ~10 margin, with **system's whole timing requirement met by software that already exists.**
- TDOA also requires *coherent* time-of-arrival measurements that survive multipath. Multipath = *known* problem in deployment environment (forests, trenches, concrete). AoA degrades to "ellipse a bit wider" in multipath; TDOA degrades to "fix is wrong".

Clean line: AoA accepts what we cannot fix (multipath), exploits what we can have (NTP over Wi-Fi), refuses dependency on what is jammed first (GNSS).

### §2.2 Why no magnetometer for heading

Documented in `ARCHITECTURE.md` §6. Rationale, restated:

Magnetometer at mast of SDR rig measures **local field** produced by: metal bracket, MG996R servo's permanent magnets and PWM-driven coils, RTL-SDR (USB power-supply noise has magnetic signature), antenna feed currents, host's PSU. Earth field = fraction of local field, local field = *position-dependent inside same mount* — moving cable changes calibration. Hard-iron/soft-iron calibration partially addresses this, but calibration = per-site and per-cabling.

Heading-by-aim procedure (point Yagi at known visible landmark, read azimuth off map, write to `NodeConfig.heading_deg`) delivers ~1–2° with no extra hardware, no per-site calibration, robust against mast's electromagnetic environment. Also faster than magnetometer calibration. Mechanical zero-stop in servo firmware (§1.3) gives absolute angular reference *within bracket*; heading-by-aim links bracket-frame to geographic.

6-axis IMUs in inventory (GY-6500 / MPU-6500) repositioned as **optional tilt-monitoring** for `NodeStatus.healthy` — not heading, not critical path.

### §2.3 GNSS is observed, not depended on

Documented in `ARCHITECTURE.md` §6. Rationale, restated as single slide-worthy line: **mesh has no critical dependency on GNSS, which is jammed first on front.** `NodeStatus.gnss_locked` exists as separate flag so system can *observe* GNSS jamming as EW indicator while continuing to function. Pitch point, not workaround.

Corollary = survey-at-deployment positioning procedure, which workstream agent designing deployment tooling must support natively (YAML-first, GNSS-optional) — not as degraded mode.

---

## §3 Open questions still genuinely open

Tracked here because Thread 1 explicitly flagged as **not resolved before handoff**. Workstream needing answer here should not assume one; escalate to lead, who decides or schedules experiment.

### §3.1 Phase C smoke test has never been executed

**Most important open question in system.**

Phase C procedure (`tower_sanity_playbook.md`, inherited from Thread 1's outputs — see `SALVAGE_AUDIT.md` Part 6) asks single physics question entire L1/L2 stack assumes answer to: *does bearing-scan pipeline — ATK-10 Yagi on MG996R servo under §1.2 bracket design, RTL-SDR V4 capture, `dsp/rssi` peak-finding — produce clean parabolic RSSI peak in correct direction toward known emitter?*

Not run. Not once. Previous project scheduled to run on 2026-05-16 (planned field test); schedule slipped on hardware bring-up, test never happened.

**Architectural response (binding for new structure):**

- Phase C runs as **first hardware-validation gate**, on Maciej's bench, in parallel with software development. Does not block software — simulator (`ARCHITECTURE.md` §4) unblocks all of B/C/D.
- Procedure already written: `tower_sanity_playbook.md` moves into new repo's `docs/`, executed as-is. No new procedure designed.
- **Phase C produces binary outcome with documented branches:**
    - PASS: parabolic peak at expected azimuth ±10°, peak prominence ≥ 6 dB above off-axis floor. L1 baseline confirmed; system's core assumption holds; Workstream A's hardware path continues uninterrupted.
    - FAIL: peak absent, peak in wrong direction, or response too noisy to peak-fit reliably. **Architectural event.** Candidates for *why* pre-enumerated in `INHERITED_CONTEXT.md` §3.1.1 below; next ticket = Phase C diagnostic, not new sweep. Lead decides architectural response (additional ground-plane reflector; site change; alternate antenna; revert to controlled emitter without reference tower).
- Phase C does not gate simulator-driven software path. Gates *trust in simulator's noise/multipath/pattern models against reality*. If Phase C reveals mode simulator does not capture, simulator's models update — part of workstream A deliverable.

### §3.1.1 Pre-enumerated Phase C failure modes (not bugs — physics)

Listed so Phase C FAIL = diagnosis, not panic:

- **Multipath dominance:** ground reflection or wall scatter at test site adds coherent secondary path that pulls peak off-axis or flattens it. Diagnostic: repeat at different node heights/locations; peak position should track true bearing, multipath should not.
- **Polarization mismatch in actual deployment** (against §1.4): unlikely if bracket follows §1.2, but check by rotating antenna to horizontal and watching peak collapse — confirms vertical *is* correct.
- **Antenna pattern deformation from bracket:** RTL-SDR's metal case sitting next to ATK-10 boom alters radiation pattern. Diagnostic: pattern check by sweeping past known emitter slowly, looking for asymmetry in rising/falling edges of peak.
- **Servo backlash/cogging:** MG996R clone has more mechanical hysteresis than nominal; angle reported by firmware does not match angle antenna actually points to. Diagnostic: sweep both directions, compare reported-vs-actual peak position. Solution: always-sweep-from-same-side discipline in L1 sweep code.
- **RF chain/gain issue:** RTL-SDR not seeing emitter at detectable SNR. Diagnostic: pure RSSI capture with `rtl_power` (Phase B of smoke playbook); confirms signal there before chasing bearing issues.

First three modes = *architectural*: affect what system can promise. Last two = operational: have known fixes.

### §3.2 Cellular reference frequency at the home site — not identified

Tower-sanity playbook assumes specific cellular carrier at home site (known tower at ~1 km) as reference emitter. Phase B step (`rtl_power` scan, 800–980 MHz) identifying which carrier and at what exact frequency **has not been run**. Until run, no validated reference emitter for home-bench Phase C.

**Workaround/interim:** LoRa beacon (§3.3) = controlled, known-frequency, known-position alternative. Phase C *can* run against beacon first; tower then serves as 5-km-range validation later.

### §3.3 Controlled-emitter beacon — design exists, hardware build pending

LoRa beacon (`lora_beacon_spec.md` from Thread 1's outputs) = controlled test emitter for all pre-BoTH3 work in Poland. Specification complete; firmware build (Arduino-ESP32 + RadioLib on ESP32-DevKit v1.1 + SX1276) **pending**. Until built, only controlled emitter = tower of unknown exact frequency (§3.2). New beacon-firmware ticket in Workstream A's salvage column; lead schedules early.

### §3.4 Pluto+ delivery for *pre-BoTH3* testing remains uncertain

Thread 1's epilog correctly notes shared-pool on-site (Senhive/ALX) lifts L2 gate *for event*. Does **not** lift gate for pre-event L2 development in Poland — there, Pluto+ still only phase-coherent SDR Maciej would have. Status: still delivery-uncertain.

**Architectural response (binding):**

- L2 software (DSP, calibration handshake, MUSIC, MVDR) developed and tested against *coherent simulator mode* of `SyntheticReceiver`. No Pluto+, no L2 development held up.
- If Pluto+ arrives before event, L2 path gets hardware-validation pass on bench. If not, L2 validated for first time on borrowed bladeRF on-site at BoTH3 — architecture structured so this = acceptable risk, not catastrophic risk (L1+fusion+CoT already credible submission, per `ARCHITECTURE.md` §5).

## §4 Process realities inherited from the prior project

Shape *plan*, not code.

### §4.1 The four-thread strategy was logged but never executed

Prior project's decision log records planned split into four Opus threads: Thread 1 coordinator, Thread 2 hardware smoke-test specialist, Thread 3 pitch deck, Thread 4 algorithm research. **Only Thread 1 ever existed and produced work.** Thread 2/3/4 never started.

**Implication for `WORKSTREAMS.md` and new bootstraps:** no workstream inherits any *intended Thread 2/3/4 work* as in-progress. Inherits only what Thread 1 + repository produced. Hardware smoke test, pitch deck, algorithm research — none exist as prior deliverables; all = work to do. Bootstrap saying "as Thread 4 established …" = hallucinating; no Thread 4.

### §4.2 The agent's authority to refuse misleading instructions, with
        evidence

Prior project's `CLAUDE.md` established agent *should* push back, with evidence, when instruction or test spec wrong. **Not** procedural quirk to drop; quality gate. Thread 1's review caught two real bugs that way (see §5). New `AGENTS.md` preserves authority explicitly; this section names lineage.

### §4.3 Ticket size and shape — proven model

Prior project's ticket format (Goal, Non-goals, Interface spec, Depends on, Blocks, Acceptance criteria, Pre-flight questions) produced shippable code. New `AGENTS.md` adopts slightly more constrained variant (with explicit `Files-you-may-touch` and `Files-you-may-NOT-touch` lists per architecture research). Lineage acknowledged so agents who have seen prior format recognise descendant.

---

## §5 Inherited regression anchors

Two specific bugs caught by Thread 1's reviews. **Not anecdotes**; paid-for knowledge. When salvaged code (`mesh/node.py`, `mesh/aggregator.py`) refactored — both REWRITE-with-reference in `SALVAGE_AUDIT.md` Part 5 — these must come across as *named regression tests*, not folklore.

### §5.1 The 25 dB SNR-invariant error (S2-T3.1)

Proposed test invariant: `in_band_snr >= peak_snr - 5`. For CW signal wrong by **25 dB** — peak SNR concentrates all signal power into one FFT bin, while in-band SNR spreads it across bin width; correct relationship depends on bin/bandwidth ratio. Mistaken invariant would have masked real DSP regression.

**Regression test (binding for rewritten `rfmesh-dsp`):** CW input at known SNR validates `peak_snr_db` and `in_band_snr_db` against closed-form expectations to within 0.5 dB. Numeric relationship between them *computed*, not assumed.

### §5.2 The subscriber-registration race (S2-T4)

Aggregator's `subscribe_measurements()` had window where subscriber registered after measurement arrived but before dispatch loop iterated would miss just-arrived measurement. Fix: register subscriber's queue *before* first dispatch, with lock.

**Regression test (binding for rewritten fusion-server ingest in `rfmesh-node` / `rfmesh-fusion`):** subscriber registering immediately before known burst of `BearingReport`s arrives receives every report in burst. No first-N-dropped, no race-window.

Both tests = workstream deliverables explicitly named in `WORKSTREAMS.md` (Workstreams B and C-fusion, respectively).

---

## §6 What is in this file and what is not

**In:** facts about hardware, RF physics, deployment site, prior project's decisions and gaps that **cannot be derived from reading new code**. Knowledge that would cost time to re-learn or money to re-discover by trial.

**Not in:**

- Anything *in* new code (`rfmesh-contracts`, salvaged modules). Canonical there.
- Anything in `ARCHITECTURE.md` or `INTERFACES.md`. Cross-referenced where rationale here informs invariant there, not duplicated.
- Speculation, future work, parking lot. Home in `docs/adr/`.

Read once cover-to-cover by every workstream agent on bootstrap; consulted by name in tickets touching any sections; amended by lead via ADR when new inherited knowledge surfaces — not edited by anyone else.