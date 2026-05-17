# rfmesh — Inherited Context

**Status:** binding for design decisions. Edits by the lead via ADR when new
inherited knowledge surfaces; otherwise frozen.
**Audience:** every workstream agent, every reviewer, every Claude Code
ticket touching hardware or RF physics. Read this *before* designing
anything new in those areas — the cost of re-deriving a fact already
established here is wasted hours; the cost of contradicting one is wasted
hardware.
**Date:** 2026-05-14.

This document exists because the prior project (`github.com/macwed/rf-mesh`
and its Thread 1 coordinator conversation) produced two kinds of output:
**code** (covered by `SALVAGE_AUDIT.md`) and **knowledge** (covered here).
The knowledge half is the dangerous half — it does not live in the
repository, it lived in one conversation's context, and the failure mode of
the multi-agent handoff is that this knowledge silently disappears. Thread 1
named that failure mode explicitly in its closing digest. The mitigation is
not assurance; the mitigation is a file. This is the file.

Three classes of inheritance:

- **§1 Hardware-quirk facts** — design constraints that must hold or the
  hardware breaks or under-performs. Binding.
- **§2 Decision rationale** — the *why* behind decisions whose *what* is in
  the code. Re-derivation by a fresh agent will cost time and often produces
  a different (worse) decision.
- **§3 Open questions still genuinely open** — what was *not* resolved before
  handoff. Tracked so they get resolved deliberately, not by accident.

Plus §4 on process realities and §5 on inherited regression anchors.

---

## §1 Hardware-quirk facts (binding)

### §1.1 ESP32-S2 firmware port is not a rename of the build target

The prior firmware runs on ESP32-C3 SuperMini and is byte-exact-tested
against the `servo_uart_v1` wire spec. Production hardware is ESP32-S2 mini
(3 units on hand, vs 1 C3). The port is **scoped, real work** — Thread 1
sizes it at ~2–3 hours — and the ticket that schedules it must not be
written as a trivial CMake-target swap.

The change: ESP32-C3 uses the **USB-Serial-JTAG peripheral**
(`usb_serial_jtag_*` API); ESP32-S2 has **USB-OTG with TinyUSB CDC** (no
USB-Serial-JTAG peripheral exists on S2 at all). The migration touches:

- Every `usb_serial_jtag_*` call in `firmware/main/main.c` → TinyUSB CDC
  equivalents (driver install, RX/TX, vendor strings in `sdkconfig`).
- The console driver: `esp_console_dev_usb_serial_jtag_*` →
  `esp_console_dev_usb_cdc_*` in `firmware/main/shell.c`.
- `sdkconfig.defaults` gains the TinyUSB enable flags and loses the
  USB-Serial-JTAG ones.
- The boot-mode-select logic in `main.c` (the 1-second window that detects
  three consecutive ENTERs for linenoise) stays semantically identical but
  reads from the CDC stream instead.

The **wire protocol** (`servo_uart_v1`, COBS + CRC-16/CCITT-FALSE + TLV)
**does not change**. The 58 C tests do not change. The host driver
(`rfmesh-servo`) does not care which USB stack is on the other end. So the
port is genuinely bounded and low-risk — it is just *not* zero-effort, and
a fresh agent that reads "port C3 → S2" without context will under-scope it.

**Implication for `WORKSTREAMS.md`:** Workstream A's salvage row for the
firmware names this port as a specific ~2–3 h ticket, with the C3 firmware
as proven prior art rather than "the firmware".

### §1.2 MG996R clones: pulse range is unknown until calibrated per axis

The MG996R servos in inventory are cheap clones from multiple sources. The
nominal hobby-servo PWM convention is 500–2500 µs for ±90°. **Clones
deviate, sometimes substantially, and the deviation is not documented on the
package.** Some travel a smaller arc for the same pulse range; some saturate
before reaching 2500 µs; some have asymmetric stops.

**This is why the firmware exposes a `cal` command and persists per-axis
calibration in NVS** (`firmware/main/calibration.c`, `cal_types.h`). The
calibration procedure is documented in `docs/wire-protocols/servo_uart_v1.md`
§3.7 and operationalised by `rfmesh-servo-calibrate` (the inherited host CLI,
see `SALVAGE_AUDIT.md` Part 2).

**Binding for design:**

- A node's L1 sweep code MUST consume calibrated angle values, never raw
  PWM pulses, when it constructs the `BearingReport.azimuth_deg`. The
  calibration table is the bracket-to-geographic mapping; without it the
  bearing is whatever angle nominal PWM happens to produce on this
  particular clone.
- A node MUST refuse to boot into L1_RSSI mode against an uncalibrated
  axis. (Invariant 4 from `AGENTS.md`: no silent fallbacks.) The firmware's
  `cal_status` reports whether a stored calibration is present; the host
  driver checks it.
- **Pulse-range assumptions in code are forbidden.** A constant
  `PULSE_MIN_US = 500` somewhere in DSP code, or a hardcoded "90 degrees =
  this much pulse", is a contract violation. The DSP code does not see PWM
  — it sees an azimuth from the servo driver after the firmware has
  applied calibration.

This is also why no field test had been done yet in the prior project:
calibration of each deployed axis is the *first* step of any field session,
and the procedure depends on a mechanical zero-stop that the bracket design
must provide. (See §1.2.)

### §1.3 SDR amplitude readings are not absolute power

Restated from `INTERFACES.md` §0 and `ARCHITECTURE.md` §7 because it is the
single most common credibility failure mode in amateur DF demos: **no SDR
in the project's scope is power-calibrated.** Not RTL-SDR V4, not HackRF,
not bladeRF 2.0 micro, not Pluto+.

- The contracts therefore carry no `dBm` field. SNR is always
  `dB above this node's noise-floor estimate`.
- `ReceiverCapabilities.is_power_calibrated` is `False` for every
  implementer; this is the load-bearing flag.
- Any display labelling a number "dBm" is a UX bug, not a typo. The
  dashboard and CoT remarks use "RSSI (relative)" or "SNR (dB above
  noise floor)".
- The honest path to absolute power, if ever wanted, is a one-point
  on-site calibration against a signal generator. Until that step happens
  on the bench, `is_power_calibrated` is False everywhere and the labelling
  follows.

---

## §2 Decision rationale (so it is not re-litigated)

### §2.1 Why AoA cross-fixing, not TDOA

Documented in `ARCHITECTURE.md` §6 as the binding decision. The rationale,
restated so a fresh agent does not "improve" the system by adding TDOA:

- TDOA requires ns-level inter-node clock synchronisation. The only
  practical path to that on a hackathon budget is **GPS-disciplined
  oscillators with PPS distribution and known cable / USB latencies** —
  all of which are jammed first in EW (GNSS) or require infrastructure
  (PTP-aware switches) we do not have.
- AoA cross-fixing needs only that the *batch window* for collecting
  bearings is wider than the inter-node clock skew. NTP over the local
  mesh gives ~10 ms. The default `FusionConfig.batch_window_ms` is 100 ms.
  This is a factor of ~10 margin, with the **system's whole timing
  requirement met by software that already exists.**
- TDOA also requires *coherent* time-of-arrival measurements that survive
  multipath. Multipath is the *known* problem in the deployment
  environment (forests, trenches, concrete). AoA degrades to "ellipse a
  bit wider" in multipath; TDOA degrades to "fix is wrong".

The clean line: AoA accepts what we cannot fix (multipath), exploits what
we can have (NTP over Wi-Fi), and refuses dependency on what is jammed
first (GNSS).

### §2.2 Why no magnetometer for heading

Documented in `ARCHITECTURE.md` §6. The rationale, restated:

A magnetometer at the mast of an SDR rig measures the **local field**
produced by: the metal bracket, the MG996R servo's permanent magnets and
PWM-driven coils, the RTL-SDR (USB power-supply noise has a magnetic
signature), the antenna feed currents, and the host's PSU. Earth field is
a fraction of that local field, and the local field is *position-dependent
inside the same mount* — moving a cable changes the calibration. Hard-iron
/ soft-iron calibration partially addresses this, but the calibration is
per-site and per-cabling.

The heading-by-aim procedure (point the Yagi at a known visible landmark,
read azimuth off a map, write to `NodeConfig.heading_deg`) delivers
~1–2° with no extra hardware, no per-site calibration, and is robust
against the mast's electromagnetic environment. It is also faster to do
than a magnetometer calibration. The mechanical zero-stop in the servo
firmware (§1.3) gives the absolute angular reference *within the bracket*;
the heading-by-aim links bracket-frame to geographic.

The 6-axis IMUs in inventory (GY-6500 / MPU-6500) are repositioned as
**optional tilt-monitoring** for `NodeStatus.healthy` — not heading, not
critical path.

### §2.3 GNSS is observed, not depended on

Documented in `ARCHITECTURE.md` §6. The rationale, restated as a single
slide-worthy line: **the mesh has no critical dependency on GNSS, which
is jammed first on the front.** `NodeStatus.gnss_locked` exists as a
separate flag so the system can *observe* GNSS jamming as an EW indicator
while continuing to function. This is a pitch point, not a workaround.

The corollary is the survey-at-deployment positioning procedure, which a
workstream agent who designs deployment tooling must support natively
(YAML-first, GNSS-optional) — not as a degraded mode.

---

## §3 Open questions still genuinely open

These are tracked here because Thread 1 explicitly flagged them as **not
resolved before handoff**. A workstream that needs an answer here should
not assume one; it should escalate to the lead, who decides or schedules
the experiment.

### §3.1 Phase C smoke test has never been executed

**The most important open question in the system.**

The Phase C procedure (`tower_sanity_playbook.md`, inherited from Thread 1's
outputs — see `SALVAGE_AUDIT.md` Part 6) asks the single physics question
this project's entire L1/L2 stack assumes the answer to: *does the
bearing-scan pipeline — ATK-10 Yagi on MG996R servo under the §1.2 bracket
design, RTL-SDR V4 capture, `dsp/rssi` peak-finding — produce a clean
parabolic RSSI peak in the correct direction toward a known emitter?*

It has not been run. Not once. The previous project was scheduled to run
it on 2026-05-16 (the planned field test); the schedule slipped on hardware
bring-up, and the test never happened.

**Architectural response (binding for the new structure):**

- Phase C runs as the **first hardware-validation gate**, on Maciej's
  bench, in parallel with software development. It does not block software
  — the simulator (`ARCHITECTURE.md` §4) unblocks all of B/C/D.
- The procedure is already written: `tower_sanity_playbook.md` moves into
  the new repo's `docs/` and is executed as-is. No new procedure is
  designed.
- **Phase C produces a binary outcome with documented branches:**
    - PASS: parabolic peak at the expected azimuth ±10°, peak prominence
      ≥ 6 dB above off-axis floor. L1 baseline confirmed; the system's
      core assumption holds; Workstream A's hardware path continues
      uninterrupted.
    - FAIL: peak is absent, peak is in the wrong direction, or the
      response is too noisy to peak-fit reliably. **This is an
      architectural event.** The candidates for *why* are pre-enumerated
      in `INHERITED_CONTEXT.md` §3.1.1 below; the next ticket is a Phase
      C diagnostic, not a new sweep. Lead decides on architectural
      response (additional ground-plane reflector; site change; alternate
      antenna; revert to a controlled emitter without a reference tower).
- Phase C does not gate the simulator-driven software path. It gates
  *trust in the simulator's noise/multipath/pattern models against
  reality*. If Phase C reveals a mode the simulator does not capture, the
  simulator's models update — that is part of the workstream A
  deliverable.

### §3.1.1 Pre-enumerated Phase C failure modes (not bugs — physics)

Listed so a Phase C FAIL is not panic but diagnosis:

- **Multipath dominance:** ground reflection or wall scatter at the test
  site adds a coherent secondary path that pulls the peak off-axis or
  flattens it. Diagnostic: repeat at different node heights / locations;
  the peak position should track the true bearing, multipath should not.
- **Polarization mismatch in the actual deployment** (against §1.4):
  unlikely if the bracket follows §1.2, but check by rotating the antenna
  to horizontal and watching the peak collapse — confirms vertical *is*
  correct.
- **Antenna pattern deformation from the bracket:** the RTL-SDR's metal
  case sitting next to the ATK-10 boom alters the radiation pattern.
  Diagnostic: pattern check by sweeping past a known emitter slowly,
  looking for asymmetry in the rising / falling edges of the peak.
- **Servo backlash / cogging:** the MG996R clone has more mechanical
  hysteresis than nominal; the angle reported by the firmware does not
  match the angle the antenna actually points to. Diagnostic: sweep both
  directions, compare reported-vs-actual peak position. Solution:
  always-sweep-from-the-same-side discipline in the L1 sweep code.
- **RF chain / gain issue:** the RTL-SDR is not seeing the emitter at
  detectable SNR. Diagnostic: pure RSSI capture with `rtl_power` (Phase B
  of the smoke playbook); confirms the signal is there before chasing
  bearing issues.

The first three modes are *architectural*: they affect what the system
can promise. The last two are operational: they have known fixes.

### §3.2 Cellular reference frequency at the home site — not identified

The tower-sanity playbook assumes a specific cellular carrier at the home
site (a known tower at ~1 km) as the reference emitter. The Phase B step
(`rtl_power` scan, 800–980 MHz) that identifies which carrier and at what
exact frequency has **not been run**. Until it is, there is no validated
reference emitter for the home-bench Phase C.

**Workaround / interim:** the LoRa beacon (§3.3) is a controlled,
known-frequency, known-position alternative. Phase C *can* be run against
the beacon first; the tower then serves as a 5-km-range validation later.

### §3.3 Controlled-emitter beacon — design exists, hardware build pending

The LoRa beacon (`lora_beacon_spec.md` from Thread 1's outputs) is the
controlled test emitter for all pre-BoTH3 work in Poland. The
specification is complete; the firmware build (Arduino-ESP32 + RadioLib on
ESP32-DevKit v1.1 + SX1276) is **pending**. Until it is built, the only
controlled emitter is a tower of unknown exact frequency (§3.2). The new
beacon-firmware ticket is in Workstream A's salvage column; lead schedules
it early.

### §3.4 Pluto+ delivery for *pre-BoTH3* testing remains uncertain

Thread 1's epilog correctly notes that the shared-pool on-site (Senhive /
ALX) lifts the L2 gate *for the event*. It does **not** lift the gate for
pre-event L2 development in Poland — there, Pluto+ is still the only
phase-coherent SDR Maciej would have. Status: still delivery-uncertain.

**Architectural response (binding):**

- L2 software (DSP, calibration handshake, MUSIC, MVDR) is developed and
  tested against the *coherent simulator mode* of `SyntheticReceiver`. No
  Pluto+, no L2 development held up.
- If Pluto+ arrives before the event, the L2 path gets a hardware-validation
  pass on the bench. If it does not, L2 is validated for the first time
  on borrowed bladeRF on-site at BoTH3 — and the architecture is structured
  so that this is acceptable risk, not catastrophic risk (L1+fusion+CoT is
  already a credible submission, per `ARCHITECTURE.md` §5).

## §4 Process realities inherited from the prior project

These shape the *plan*, not the code.

### §4.1 The four-thread strategy was logged but never executed

The prior project's decision log records a planned split into four Opus
threads: Thread 1 coordinator, Thread 2 hardware smoke-test specialist,
Thread 3 pitch deck, Thread 4 algorithm research. **Only Thread 1 ever
existed and produced work.** Thread 2/3/4 were never started.

**Implication for `WORKSTREAMS.md` and the new bootstraps:** no workstream
inherits any of the *intended Thread 2/3/4 work* as in-progress. They
inherit only what Thread 1 + the repository produced. The hardware smoke
test, the pitch deck, the algorithm research — none of those exist as
prior deliverables; they are work to do. A bootstrap that says "as Thread 4
established …" is hallucinating; there was no Thread 4.

### §4.2 The agent's authority to refuse misleading instructions, with
        evidence

The prior project's `CLAUDE.md` established that an agent *should* push
back, with evidence, when an instruction or test spec is wrong. This is
**not** a procedural quirk to drop; it is a quality gate. Thread 1's review
caught two real bugs that way (see §5). The new `AGENTS.md` preserves the
authority explicitly; this section names the lineage.

### §4.3 Ticket size and shape — proven model

The prior project's ticket format (Goal, Non-goals, Interface spec,
Depends on, Blocks, Acceptance criteria, Pre-flight questions) produced
shippable code. The new `AGENTS.md` adopts a slightly more constrained
variant (with explicit `Files-you-may-touch` and `Files-you-may-NOT-touch`
lists per the architecture research). The lineage is acknowledged so
agents who have seen the prior format recognise the descendant.

---

## §5 Inherited regression anchors

Two specific bugs caught by Thread 1's reviews. They are **not anecdotes**;
they are paid-for knowledge. When the salvaged code (`mesh/node.py`,
`mesh/aggregator.py`) is refactored — both REWRITE-with-reference in
`SALVAGE_AUDIT.md` Part 5 — these must come across as *named regression
tests*, not as folklore.

### §5.1 The 25 dB SNR-invariant error (S2-T3.1)

A proposed test invariant was `in_band_snr >= peak_snr - 5`. For a CW
signal this is wrong by **25 dB** — the peak SNR concentrates all signal
power into one FFT bin, while the in-band SNR spreads it across the bin
width; the correct relationship depends on the bin/bandwidth ratio. The
mistaken invariant would have masked a real DSP regression.

**Regression test (binding for the rewritten `rfmesh-dsp`):** a CW input
at known SNR validates `peak_snr_db` and `in_band_snr_db` against
closed-form expectations to within 0.5 dB. The numeric relationship
between them is *computed*, not assumed.

### §5.2 The subscriber-registration race (S2-T4)

The aggregator's `subscribe_measurements()` had a window where a
subscriber registered after a measurement arrived but before the dispatch
loop iterated would miss the just-arrived measurement. The fix: register
the subscriber's queue *before* the first dispatch, with a lock.

**Regression test (binding for the rewritten fusion-server ingest in
`rfmesh-node` / `rfmesh-fusion`):** a subscriber that registers
immediately before a known burst of `BearingReport`s arrives receives
every report in the burst. No first-N-dropped, no race-window.

Both tests are workstream deliverables explicitly named in `WORKSTREAMS.md`
(Workstreams B and C-fusion, respectively).

---

## §6 What is in this file and what is not

**In:** facts about hardware, RF physics, the deployment site, and the
prior project's decisions and gaps that **cannot be derived from reading
the new code**. Knowledge that would cost time to re-learn or money to
re-discover by trial.

**Not in:**

- Anything that *is* in the new code (`rfmesh-contracts`, salvaged
  modules). That is canonical there.
- Anything in `ARCHITECTURE.md` or `INTERFACES.md`. Those are
  cross-referenced where the rationale here informs an invariant there,
  but not duplicated.
- Speculation, future work, parking lot. Those have a home in `docs/adr/`.

This document is read once cover-to-cover by every workstream agent on
bootstrap; consulted by name in tickets that touch any of its sections;
amended by the lead via ADR when new inherited knowledge surfaces — not
edited by anyone else.
