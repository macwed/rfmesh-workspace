# ADR-015 — Firmware production target ESP32-C6 (supersedes S2)

**Status:** ACCEPTED (2026-05-23)
**Date:** 2026-05-23 (PROPOSED and ACCEPTED same day; operator-driven, hardware-forced)
**Author:** lead-Opus (drafting at operator request after hardware loss in transit)
**SCHEMA_VERSION change:** none. Firmware-internal; the `servo_uart_v1` wire protocol and `rfmesh-contracts` are untouched.

## Context

The firmware production target trajectory so far:

1. **ESP32-C3 SuperMini** — original `macwed/rf-mesh` salvage baseline. USB-Serial-JTAG peripheral, byte-exact-tested against `servo_uart_v1` (58 host-buildable C tests, all PASS). 1× unit on hand at the time of salvage.
2. **ESP32-S2 mini** — production target chosen 2026-05-14, recorded as binding fact in `INHERITED_CONTEXT.md` §1.1. 3× units on hand, salvage author scoped the C3→S2 port at ~2-3 h (commit `6acf482`, "WS-A-007a — ESP32-S2 port (TinyUSB CDC), wire spec unchanged"). USB-OTG via TinyUSB CDC (no USB-Serial-JTAG peripheral on S2).
3. **ESP32-C6** — this ADR. Hardware-forced pivot.

**Trigger:** All three ESP32-S2 units sustained physical damage during transit between operator's home bench and the BoTH3 staging site (2026-05-23). Mid-flight bench-bringup blocked. Event organisers offered ESP32-C6 stock from their on-site pool; operator accepted.

The C6 is architecturally closer to the original C3 than to the S2:

- **USB transport:** C6 has the native USB-Serial-JTAG peripheral (same family as C3). No TinyUSB stack required. The C3 salvage's `usb_serial_jtag_*` API works directly. The S2 detour added a managed component (`espressif/esp_tinyusb ~1.4.0`) and a custom linenoise loop in `shell.c` because no symmetric `esp_console_new_repl_usb_cdc` exists for TinyUSB. The C6 port restores the cleaner `esp_console_new_repl_usb_serial_jtag` pattern.
- **Core:** RISC-V (vs S2 Xtensa LX7; vs C3 RISC-V). Wire-protocol code is endian-agnostic and target-independent; no impact.
- **GPIO:** C6 has GPIO4/5/8/9/15 as strapping pins. The C3-era servo PWM line on GPIO5 (preserved through the S2 port) lands on a strap pin on C6. Moved to GPIO18 (regular GPIO, no peripheral conflict, not USB, not flash).
- **Radios:** C6 has Wi-Fi 6 / BLE 5 / 802.15.4. All disabled in `sdkconfig.defaults` — the servo node uses none of them. Kept off for binary size and to remove the coexistence layer.
- **LEDC clock:** C6's low-speed LEDC can source XTAL (40 MHz, deterministic) or RC_FAST (~17.5 MHz ±5%, uncalibrated). `LEDC_AUTO_CLK` is non-deterministic across boards. Pinned `LEDC_USE_XTAL_CLK` explicitly in `firmware/main/servo.c` to keep L1 sigma honesty (Invariant B2) board-independent.

## Decision

The production target for the servo-node firmware is **ESP32-C6**, effective 2026-05-23. This supersedes the S2 designation in `INHERITED_CONTEXT.md` §1.1.

The C3 salvage baseline (commit history pre-`6acf482`) remains the architectural reference for the USB-Serial-JTAG transport pattern. The S2 port (`6acf482`) is preserved in history for the wire-protocol port-invariance evidence (same `servo_uart_v1` ran on two different USB stacks; same 58 C tests, all PASS) — useful when defending against future "but TinyUSB does X" objections.

### Pin map (canonical, ESP32-C6)

| Function | Pin | Notes |
|---|---|---|
| Servo PWM (axis 0 / pan) | **GPIO18** | LEDC channel 0, 50 Hz, XTAL-pinned. Moved off C3-era GPIO5 (strap on C6). |
| USB D- / D+ | GPIO12 / GPIO13 | Hardware-fixed by USB-Serial-JTAG peripheral. |
| Reserved axis 1 | GPIO19 | Future tilt or multi-mast. |

### Files affected by the port

- `firmware/sdkconfig.defaults` — target `esp32s2` → `esp32c6`; TinyUSB stanza removed; USB-Serial-JTAG console enabled; C6-specific radios (Wi-Fi/BLE/Thread/Zigbee) explicitly disabled.
- `firmware/main/main.c` — TinyUSB CDC helpers ripped (`tinyusb_cdcacm_*`, `tud_cdc_n_connected`, `esp_tusb_init_console`); USB-Serial-JTAG API restored (`usb_serial_jtag_driver_install`, `usb_serial_jtag_read_bytes`, `usb_serial_jtag_write_bytes`); CDC-enumeration wait removed (JTAG peripheral is always-on).
- `firmware/main/shell.c` — custom linenoise loop replaced with `esp_console_new_repl_usb_serial_jtag` IDF helper; cleanup via `repl->del(repl)` per IDF v5.4 contract.
- `firmware/main/CMakeLists.txt` — REQUIRES `esp_tinyusb` → `esp_driver_usb_serial_jtag` + `vfs`.
- `firmware/main/idf_component.yml` — `espressif/esp_tinyusb` managed component dropped; only IDF ≥ 5.1 floor remains.
- `firmware/main/servo.c` — `s_axis_gpio` GPIO5 → GPIO18; LEDC `clk_cfg` `LEDC_AUTO_CLK` → `LEDC_USE_XTAL_CLK`.
- `docs/wire-protocols/servo_uart_v1.md` §9 — pin table updated to ESP32-C6 (servo GPIO18, USB GPIO12/13).
- `docs/demo/pitch_deck.md` L207 — "ESP32-S2" → "ESP32-C6".
- `docs/demo/slide_deck.md` L257 — bill-of-materials row "ESP32-S2 servo controller" → "ESP32-C6 servo controller" (kept €15 per-unit to preserve €205 BOM total).
- `scripts/flash_servo_c6.sh`, `scripts/calibrate_servo.sh` — new operator helpers (build/flash/monitor + guided calibration with port-busy + ModemManager pre-flight checks).
- `INHERITED_CONTEXT.md` §1.1 — amended to reference this ADR.

### Wire protocol unchanged

`servo_uart_v1` (COBS + CRC-16/CCITT-FALSE + TLV) is untouched. Host driver `rfmesh-servo` works against C6 firmware without changes — it never knew which USB stack the MCU exposed, only that it spoke the spec. The 58 host-buildable C tests in `firmware/tests/` (CRC, COBS, protocol, dispatch, servo_math) are target-independent and unchanged.

This is the same port-invariance argument the S2 port earned the project: the wire protocol is the contract; the MCU underneath is fungible.

## Consequences

### Positive

- **Architectural simplification.** TinyUSB stack out of the binary (~estimated 30-40 KB flash saved, depending on IDF build). One fewer managed component dependency. Cleaner shell-init path.
- **Restored symmetry with the C3 salvage.** Future agents reading the code path find the same `esp_console_new_repl_usb_serial_jtag` pattern the prior project established.
- **L1 sigma honesty hardened.** Explicit XTAL clock pinning (servo.c) removes a board-dependent LEDC clock-source variable. Sigma test (`packages/rfmesh-dsp/tests/test_sigma_honesty.py`) remains valid as-is (it is driven by `SyntheticReceiver`, not real servo).
- **Hardware availability.** C6 boards came from organiser pool; no procurement risk for BoTH3 demo.

### Negative

- **Calibration restart required per C6 board.** NVS calibration from the damaged S2 boards is unrecoverable; each C6 must be calibrated from scratch via `scripts/calibrate_servo.sh`. INHERITED_CONTEXT.md §1.2 already mandates per-axis NVS calibration as binding, so this is operational time, not new code.
- **Phase C re-validation required.** The Mast C empirical anchor (ADR-014) was captured against an S2-driven servo. Whether C6+XTAL-pinned LEDC reproduces the same servo angular jitter is empirically unknown until re-validated. Operator must run a Phase C polar capture on the C6 against the Mast C reference (958.7 MHz) before BoTH3, confirming ≥6 dB peak prominence and peak within 10° of map bearing. If this re-validation fails, the C6 servo chain enters the same diagnostic ladder as the original Phase C failure modes (INHERITED_CONTEXT.md §3.1.1) and the demo falls back to recorded IQ (per `docs/demo/script.md` contingency ladder, which is already the load-bearing demo path — live servo is rehearsal-only).
- **Third MCU in 6 weeks.** C3 → S2 → C6. If a fourth pivot becomes necessary, the cost is now bounded (port is well-trodden) but the operator pays the calibration cost again.

### Risk mitigations (operator punch list pre-BoTH3)

1. Flash + calibrate ≥2 C6 boards via `scripts/flash_servo_c6.sh` then `scripts/calibrate_servo.sh`; verify NVS persistence post-power-cycle.
2. Phase C polar capture on C6 against Mast C (958.7 MHz); confirm peak prominence ≥6 dB, peak within 10° of map bearing.
3. Physical-label each C6 with board ID and calibration date.
4. Carry one pre-calibrated C6 spare to BoTH3 (no hot-swap inside 5-min jury window without re-calibration; spare must be pre-cal'd).
5. Rehearse Beat A/B end-to-end once with live C6 → recorded-IQ handoff to confirm dashboard captions unchanged.

## Alternatives considered

- **Procure replacement S2 units before BoTH3.** Rejected: lead time vs event date does not permit; even expedited shipping risks missing the demo window.
- **Use organiser C6 stock at the event only, keep S2 as bench reference.** Rejected: no working S2 units remain; cannot maintain two firmware variants without operator time the schedule does not have.
- **Switch to a different ESP32 family from organiser pool (S3, P4).** Rejected: C6 is the closest architectural match to the C3 salvage (USB-Serial-JTAG); other targets reintroduce the TinyUSB detour or worse. C6 stock was confirmed available; alternatives were not.

## References

- `INHERITED_CONTEXT.md` §1.1 (amended by this ADR)
- `SALVAGE_AUDIT.md` Part 1 (firmware salvage, naming S2 as the scoped port destination — now historical)
- ADR-014 (Mast C empirical anchor — the Phase C re-validation gate this ADR depends on)
- `firmware/main/main.c`, `firmware/main/shell.c`, `firmware/main/servo.c`, `firmware/sdkconfig.defaults` (changed by this port)
- `docs/wire-protocols/servo_uart_v1.md` §9 (canonical pin map, updated)
- Memory entry `project_hardware_roles.md` (lead amended to reflect C6 production target post-ADR)
