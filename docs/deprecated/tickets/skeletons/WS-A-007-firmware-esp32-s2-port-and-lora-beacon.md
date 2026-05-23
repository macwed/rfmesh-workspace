**Status: CLOSED in 6acf482 (WS-A-007a S2 port) + 481288c (WS-A-007b LoRa beacon). SUPERSEDED by ADR-015 (C6 replaces S2 as firmware target).**

# TICKET WS-A-007: ESP32-S2 firmware port + LoRa beacon firmware

## Goal (one sentence)

Port the salvaged ESP32-C3 firmware (`macwed/rf-mesh:firmware/` —
~1378 LoC C + 58 host-buildable unit tests + byte-exact wire-protocol
vectors) to **ESP32-S2 mini** (~2-3 h scoped port per
`INHERITED_CONTEXT.md` §1.1), and ship the LoRa beacon firmware
(Arduino-ESP32 + RadioLib on ESP32-DevKit v1.1 + SX1276) per the
salvaged `lora_beacon_spec.md` so the bench has a controlled test
emitter for pre-event Phase C work.

## Context (links only, not content)

- Architecture: `WORKSTREAMS.md` §1 row A — `firmware/` and `tools/`
  servo/beacon are in Workstream A's ownership. `SALVAGE_AUDIT.md`
  Part 1 verdicts firmware tree as TAKE-whole with one scoped port.
- Wire contract: `docs/wire-protocols/servo_uart_v1.md` is frozen
  and **unchanged** by this port (only the USB peripheral underneath
  changes; the wire bytes do not). The 58 firmware C tests pass
  against the unchanged spec.
- Inherited context:
  - `INHERITED_CONTEXT.md` §1.1 — ESP32-C3 uses USB-Serial-JTAG
    peripheral (`usb_serial_jtag_*` API); ESP32-S2 has USB-OTG with
    TinyUSB CDC. The migration touches every `usb_serial_jtag_*`
    call in `main.c` + the console driver in `shell.c` +
    `sdkconfig.defaults` USB flags. Real ~2-3 h scope, NOT a CMake
    target rename.
  - `INHERITED_CONTEXT.md` §1.2 — MG996R clones; per-axis
    calibration is what `firmware/main/calibration.c` + NVS exists
    to support; no pulse-to-angle math in the new code.
  - `INHERITED_CONTEXT.md` §3.3 — controlled-emitter beacon spec
    exists (`lora_beacon_spec.md` from Thread 1 outputs); the
    firmware build is pending.
- Salvage: `SALVAGE_AUDIT.md` Part 1 names every firmware file to
  take. Tests (`firmware/tests/*`, 5 files + Unity) carry verbatim.
- Prior tickets this depends on: none. Firmware is independent of
  software workstreams. WS-A-006 (servo host driver) is the
  companion Python side; the wire protocol is the contract between
  them.

## Acceptance criteria

### Part 1 — ESP32-S2 port (the scoped change)

1. `firmware/` directory exists at the workspace root, mirroring the
   structure of `macwed/rf-mesh:firmware/`:

   ```
   firmware/
       CMakeLists.txt
       partitions.csv
       sdkconfig.defaults
       main/
           main.c
           crc16.{c,h}
           cobs.{c,h}
           protocol.{c,h}
           dispatch.{c,h}
           servo.{c,h}
           servo_math.c
           cal_types.h
           calibration.{c,h}
           shell.{c,h}
           identity.h.in
       tests/                  # 58 C unit tests + Unity
       README.md
   ```

2. **`IDF_TARGET=esp32s2`** in `CMakeLists.txt` or
   `sdkconfig.defaults` (not C3 anymore).

3. **USB stack migration** in `main/main.c` + `main/shell.c` +
   `sdkconfig.defaults`:

   - Every `usb_serial_jtag_*` API call → TinyUSB CDC equivalent.
     Driver install, RX/TX, vendor strings.
   - `esp_console_dev_usb_serial_jtag_*` →
     `esp_console_dev_usb_cdc_*` in `shell.c`.
   - `sdkconfig.defaults` gains the TinyUSB enable flags
     (`CONFIG_USB_OTG_SUPPORTED=y`, `CONFIG_TINYUSB_CDC_ENABLED=y`,
     equivalents), and loses the USB-Serial-JTAG ones.
   - The boot-mode-select logic (1-second window + three ENTERs
     for linenoise) stays semantically identical, reads from the
     CDC stream instead.

4. **Wire protocol unchanged**: the 58 C tests pass without
   modification when run on the host (Unity-based, host-buildable
   `idf.py build -p host` or whatever the salvage uses). Concretely:

   - CRC vectors (§7.1) byte-exact.
   - COBS vectors (§7.2) byte-exact.
   - TLV frame vectors (§7.3) byte-exact.
   - `cal_status` / `cal_clear` / `cal_store` flow tests pass.
   - Servo PWM driver tests pass.

5. `firmware/README.md` updated to reflect ESP32-S2 target; the
   "On-target smoke test" section adapts to TinyUSB CDC.

### Part 2 — LoRa beacon firmware (the new build)

6. New `firmware-beacon/` directory at workspace root (separate
   from `firmware/` so the two build trees do not interfere):

   ```
   firmware-beacon/
       platformio.ini   # or Arduino sketch + build instructions
       src/
           main.cpp     # ESP32-DevKit v1.1 + RadioLib + SX1276
           beacon_config.h
       README.md        # bench-side flash + verify procedure
       lora_beacon_spec.md  # the salvaged spec (TAKE verbatim)
   ```

7. **Hardware target**: ESP32-DevKit v1.1 + SX1276 LoRa module per
   `lora_beacon_spec.md`. Build with Arduino-ESP32 framework +
   RadioLib library.

8. **Beacon behaviour** per spec:
   - Periodic transmission (1 s default, configurable in
     `beacon_config.h`) at the spec-defined band (868 MHz EU ISM
     by default).
   - LoRa spreading factor, bandwidth, coding rate per spec.
   - Payload: a short fixed identifier + a 32-bit incrementing
     counter, so the listener can detect packet loss.

9. **Verify procedure** in `firmware-beacon/README.md`:
   - Flash steps.
   - Bench listen with `rtl_sdr` + a LoRa-aware decoder
     (`rpi-rtl-sdr-receiver` or similar — choice documented).
   - Expected RSSI / SNR signature on a clean bench.

10. **Software-side dependency on the beacon is zero.** The beacon
    is a *test emitter* the Python stack happens to listen to; no
    `packages/rfmesh-*` package imports anything from
    `firmware-beacon/`. The beacon talks RF; the Python side
    receives IQ via `Receiver`.

## Out of scope (explicit non-goals)

- The servo bracket, mast, antenna mount — Maciej's physical
  domain (`AGENTS.md` §2). The firmware drives the servo; it does
  not assume anything about where the servo is.
- Modifying the `servo_uart_v1` wire spec — frozen contract.
- Multi-target firmware build (the firmware targets exactly
  ESP32-S2 in this ticket; future ESP32-S3 / -C6 ports are
  parking-lot).
- An OTA update path — bench USB flash only.
- Beacon downlink / two-way protocol — beacon is transmit-only.
- Multi-frequency beacon (single band per
  `lora_beacon_spec.md` v1.0).
- Modifying any `rfmesh-contracts`. (Invariant B1.) Firmware
  ships separately; no Pydantic models cross this boundary.

## Files you may touch

### Part 1 — Firmware salvage

- `firmware/**` (TAKE-whole-with-port from `macwed/rf-mesh:firmware/`).
  Specifically:
  - `firmware/CMakeLists.txt` — `IDF_TARGET=esp32s2`.
  - `firmware/sdkconfig.defaults` — TinyUSB CDC flags; drop
    USB-Serial-JTAG flags.
  - `firmware/main/main.c` — `usb_serial_jtag_*` calls → TinyUSB
    CDC. Boot-mode-select logic stays semantically identical.
  - `firmware/main/shell.c` — `esp_console_dev_usb_serial_jtag_*`
    → `esp_console_dev_usb_cdc_*`.
  - Every other `firmware/main/*.{c,h}` — copy verbatim from
    salvage (no logic changes).
  - `firmware/tests/**` — copy verbatim; 58 tests must pass
    unchanged.
  - `firmware/README.md` — adapt USB-stack section.

### Part 2 — Beacon firmware

- `firmware-beacon/platformio.ini` (or Arduino sketch shell —
  builder picks; PlatformIO is the more reproducible choice but
  Arduino-ESP32 IDE is fine for bench flash).
- `firmware-beacon/src/main.cpp` — RadioLib + SX1276 init + the
  periodic transmit loop.
- `firmware-beacon/src/beacon_config.h` — band + spread factor +
  bandwidth + coding rate + payload identifier as `#define`s.
- `firmware-beacon/README.md` — flash + verify procedure.
- `firmware-beacon/lora_beacon_spec.md` — TAKE verbatim from the
  Thread 1 output (if not yet in repo).

## Files you may NOT touch

- `packages/**` — software workstreams' territory. This ticket
  ships firmware only.
- `docs/wire-protocols/servo_uart_v1.md` — frozen.
- `docs/adr/**` — no ADR needed for the S2 port (it is salvage
  with a scoped change, not a contract change).
- `pyproject.toml`, `uv.lock` — firmware has no Python deps.

## Stop conditions

### §A — ESP-IDF version compatibility

The salvage was built against the ESP-IDF version the old repo
pinned. If the current ESP-IDF on Maciej's bench is incompatible
(e.g. a major release bumped the TinyUSB CDC API), STOP and
write a SCRATCHPAD. Do not silently swap APIs.

### §B — Host-buildable test suite

Unity host-build (`idf.py -p host` or the equivalent the salvage
sets up) must pass all 58 tests on **Linux** before declaring the
S2 port done. If the host build is broken by the port, STOP — the
fix is in the firmware, not in the tests.

### §C — Beacon spec drift

`lora_beacon_spec.md` is the source of truth for the beacon. If
the builder finds a hardware reality the spec contradicts (e.g.
a different SX1276 breakout pinout), STOP and write a
SCRATCHPAD. Do not silently change the spec.

### §D — Hardware availability gating

Per `INHERITED_CONTEXT.md` §3.4, hardware availability is
documented at risk. The S2 port can land **without** an ESP32-S2
in hand (host-build tests + careful code review); the live flash
+ smoke test is a follow-on bench session. The beacon firmware
build requires SX1276 hardware to verify; if SX1276 is not yet
available, ship the firmware source + flash instructions + defer
the live-verify to Maciej's bench.

### §E — Standard ticket stop pattern

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation:
  - The host-build test output (58 / 58 pass).
  - The S2 build output (`idf.py build` clean).
  - The beacon `pio run` output (clean compile).
- If a wire-protocol change appears necessary (e.g. the S2 USB
  stack drops a CDC packet that USB-Serial-JTAG did not), STOP
  and write an ADR — the wire protocol is the contract; firmware
  does not change it unilaterally.

## Implementation notes (non-binding, guidance)

### USB stack migration steps (the actual work)

1. Read every reference to `usb_serial_jtag_*` in
   `firmware/main/` (about a dozen call sites).
2. Map each to its TinyUSB CDC equivalent:
   - `usb_serial_jtag_driver_install(...)` →
     `tinyusb_driver_install(...)` + `tusb_cdc_acm_register_callback(...)`.
   - `usb_serial_jtag_write_bytes(...)` →
     `tinyusb_cdcacm_write_queue(...)` + `tinyusb_cdcacm_write_flush(...)`.
   - `usb_serial_jtag_read_bytes(...)` →
     CDC RX callback into a FreeRTOS queue.
3. `sdkconfig.defaults` adjusts:
   - `CONFIG_USB_OTG_SUPPORTED=y`
   - `CONFIG_TINYUSB_CDC_ENABLED=y`
   - `CONFIG_TINYUSB_CDC_RX_BUFSIZE` + `CONFIG_TINYUSB_CDC_TX_BUFSIZE`
     sized to match the salvage's USB-Serial-JTAG buffer sizes.
   - Remove `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` (C3) and
     friends.
   - Add `CONFIG_ESP_CONSOLE_USB_CDC=y` (S2 / TinyUSB).
4. `shell.c` swaps the console driver:
   - `esp_console_new_repl_usb_serial_jtag(...)` →
     `esp_console_new_repl_usb_cdc(...)`.

### Beacon code sketch (RadioLib)

```cpp
#include <RadioLib.h>

// SX1276 pin map for ESP32-DevKit v1.1 (per beacon spec)
SX1276 radio = new Module(/*CS=*/5, /*DIO0=*/4, /*RST=*/14, /*DIO1=*/2);

void setup() {
  Serial.begin(115200);
  // 868 MHz EU ISM, SF7, BW 125 kHz, CR 4/5 — per spec
  int state = radio.begin(868.1, 125.0, 7, 5);
  // Check + halt on failure (Invariant B3 equivalent at firmware layer)
}

uint32_t counter = 0;
void loop() {
  uint8_t payload[8];
  // 4-byte identifier "RFM\0" + 4-byte counter (big-endian)
  payload[0] = 'R'; payload[1] = 'F'; payload[2] = 'M'; payload[3] = 0;
  payload[4] = (counter >> 24) & 0xFF;
  payload[5] = (counter >> 16) & 0xFF;
  payload[6] = (counter >> 8) & 0xFF;
  payload[7] = counter & 0xFF;
  radio.transmit(payload, 8);
  counter++;
  delay(1000);  // 1 Hz default
}
```

### Why the order matters

ESP32-S2 firmware port lands first (the existing C3 firmware is
already tested; the port is mechanical USB-stack swap). LoRa
beacon firmware second (it requires the SX1276 hardware on hand to
verify; the build can compile without hardware but the
live-emit test is gated on hardware).

A working LoRa beacon makes Phase C reproducible (`cell_scan.csv`
fallback per `phase-c-bench-checklist.md` §A.3 is replaced with a
known-frequency, known-power, known-position reference emitter).
