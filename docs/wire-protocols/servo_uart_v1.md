# Servo UART Wire Protocol v1

**Status:** frozen contract for S2-T5a-firmware and S2-T5a-host
**Last updated:** 2026-05-07 (test vectors finalised during host-driver implementation; see §11)
**Owners:** RPi host (`rfmesh.scan.servo.driver`) ↔ ESP32 controller firmware (production target ESP32-C6 per ADR-015; protocol survives MCU pivots — see §9 history)

This document is the **single source of truth** for the binary protocol carried over USB-CDC between the RPi running `rfmesh-node` and the ESP32 servo controller. Both the firmware ticket (S2-T5a-firmware) and the host driver ticket (S2-T5a-host) MUST conform to this spec exactly. Any divergence is a bug; any proposed change requires updating this document FIRST and bumping `PROTOCOL_VERSION`.

The protocol is **MCU-port-invariant**: the C3 salvage baseline, the S2 port (WS-A-007a), and the current C6 port (ADR-015) all speak it byte-for-byte. 58 host-buildable C tests pass on every target.

---

## 1. Physical layer

- **Transport:** **TCP/IP over WiFi (current, per ADR-027)** or USB-CDC (legacy bench path). The frame layer (§2 onwards) is transport-agnostic — COBS+TLV+CRC-16/CCITT-FALSE byte-for-byte either way.
  - **TCP:** ESP32-C6 in WiFi-station mode (SSID `"rfmesh"`, PSK `"karasie01"`, hardcoded in `firmware/main/wifi_sta.c`), TCP server on port 5555. Laptop side: `TcpTransport` in `packages/rfmesh-servo/src/rfmesh_servo/transport.py`. `TCP_NODELAY` on. SO_KEEPALIVE 30s/5s/3 probes.
  - **USB-CDC (legacy):** ESP32-C6 native USB-Serial-JTAG peripheral. Stays wired in firmware as the `ESP_LOG` console path (operator reads the WiFi DHCP IP off boot log). Selectable per-node on the laptop via YAML `servo_port: "/dev/ttyACM0"` vs `"tcp://host:port"`.
- **Baud rate (USB only):** 115200 (CDC framing makes this nominal — actual is bulk USB). N/A for TCP.
- **Byte order:** little-endian for all multi-byte integers
- **Flow control:** none
- **Encoding:** UTF-8 (only relevant in `linenoise` shell mode, not in protocol mode — see §6; shell mode is not reachable in the current ADR-027 firmware build, only in legacy USB-CDC builds)

---

## 2. Frame format

All protocol frames are TLV (Type-Length-Value), encoded with COBS framing, terminated by a single `0x00` byte. The COBS-encoded payload contains:

```
[CMD:u8] [LEN:u8] [PAYLOAD: LEN bytes] [CRC16:u16]
```

Pre-COBS frame layout (from logical wire perspective, before stuffing):

| Field | Size | Description |
|---|---|---|
| `CMD` | 1 byte | Command type (see §3) |
| `LEN` | 1 byte | Length of `PAYLOAD` field, 0–253 |
| `PAYLOAD` | `LEN` bytes | Command-specific data (see §3 per-command) |
| `CRC16` | 2 bytes | CRC-16/CCITT-FALSE over `CMD || LEN || PAYLOAD`, little-endian |

The CRC is computed over the un-stuffed bytes (`CMD || LEN || PAYLOAD`), NOT over the COBS-encoded form.

Total wire frame after COBS encoding: typically `LEN + 5` bytes (CMD + LEN + PAYLOAD + CRC2) plus 1–2 bytes of COBS overhead plus 1 byte terminator. Maximum logical frame size: 256 bytes (cmd+len+253 payload+crc); maximum wire size after COBS: ~258 bytes.

### 2.1 COBS framing

COBS (Consistent Overhead Byte Stuffing, RFC-style; reference: Cheshire & Baker 1999) replaces every `0x00` byte in the payload with a non-zero-stuffed sequence, then uses a single `0x00` byte as unambiguous frame delimiter. Standard implementation; both sides MUST use the same algorithm.

**Frame delimiter:** `0x00` (after COBS encoding, the encoded payload contains no `0x00` bytes; the trailing `0x00` ends the frame).

**Receiver behaviour:** read bytes until `0x00`, then COBS-decode, then validate CRC. On any of (decode error, CRC fail, length mismatch, unknown CMD), DROP the frame silently and resync at the next `0x00`. Do NOT reply with an error frame on decode failures — that risks amplifying noise into a feedback loop. Do reply with an error frame on semantic failures (unknown axis, out-of-range angle, etc — see §3, error codes §5).

### 2.2 CRC-16/CCITT-FALSE

Polynomial: `0x1021`, initial value: `0xFFFF`, no reflection, no final XOR. Reference implementation:

```c
uint16_t crc16_ccitt_false(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= ((uint16_t)data[i]) << 8;
        for (int j = 0; j < 8; j++) {
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
        }
    }
    return crc;
}
```

ESP-IDF: use `esp_crc16_be(0xFFFF, data, len)` — equivalent. Both sides MUST agree on result for the test vector in §7.

---

## 3. Commands

### 3.1 Command map

| CMD | Name | Direction | Description |
|---|---|---|---|
| `0x01` | `MOVE` | host → fw | Set target angle for an axis |
| `0x02` | `POS_QUERY` | host → fw | Query current commanded position + ms-since-move |
| `0x03` | `POS_REPLY` | fw → host | Reply to `POS_QUERY` |
| `0x04` | `STOP` | host → fw | Disable PWM on an axis (servo goes limp) |
| `0x05` | `CAL_SET` | host → fw | Write calibration for an axis |
| `0x06` | `CAL_QUERY` | host → fw | Read current calibration for an axis |
| `0x07` | `CAL_REPLY` | fw → host | Reply to `CAL_QUERY` |
| `0x08` | `CAL_PERSIST` | host → fw | Persist current RAM calibration to NVS |
| `0x10` | `PING` | host → fw | Liveness check |
| `0x11` | `PONG` | fw → host | Reply to `PING`, includes firmware identity |
| `0x20` | `RESET` | host → fw | Soft-reboot the firmware (esp_restart) |
| `0xF0` | `ACK` | fw → host | Generic success ack for `MOVE`/`STOP`/`CAL_SET`/`CAL_PERSIST`/`RESET` |
| `0xFF` | `ERROR` | fw → host | Error reply (see §5) |

`PROTOCOL_VERSION = 1` (carried in `PONG`; see §3.10).

### 3.2 `MOVE` (0x01)

Payload (4 bytes):

| Offset | Field | Type | Description |
|---|---|---|---|
| 0 | `axis` | u8 | Axis ID, 0 = pan (only axis defined for v1) |
| 1 | reserved | u8 | Must be 0 |
| 2 | `angle_deci_deg` | i16 LE | Target angle × 10, range determined by calibration |

Firmware behaviour:
- Validate `axis` is configured. If not, reply `ERROR(ERR_NO_SUCH_AXIS)`.
- Validate `angle_deci_deg / 10.0` is within `[angle_min_deg, angle_max_deg]` from calibration. If not, reply `ERROR(ERR_ANGLE_OUT_OF_RANGE)`.
- Compute pulse width via linear map `pulse_us = pulse_min + (angle - angle_min) * (pulse_max - pulse_min) / (angle_max - angle_min)`.
- Update LEDC duty cycle.
- Record `commanded_angle = angle_deci_deg / 10.0` and `last_move_timestamp_ms = esp_timer_get_time() / 1000`.
- Reply `ACK(0xF0)` with empty payload.

The firmware does NOT wait for settling. The host is responsible for timing.

### 3.3 `POS_QUERY` (0x02)

Payload (1 byte): `axis: u8`.

### 3.4 `POS_REPLY` (0x03)

Payload (8 bytes):

| Offset | Field | Type | Description |
|---|---|---|---|
| 0 | `axis` | u8 | Echoed |
| 1 | reserved | u8 | 0 |
| 2 | `commanded_angle_deci_deg` | i16 LE | Last commanded angle × 10 |
| 4 | `ms_since_move` | u32 LE | Milliseconds since last `MOVE` on this axis |

The firmware does NOT measure actual servo position (MG996 has no exposed feedback). The reply states only what was commanded and how long ago. Host code uses `ms_since_move` to decide if servo has settled (typically ≥ 250 ms after MG996 movement of >10°).

If axis was never moved since boot, `commanded_angle_deci_deg = 0` and `ms_since_move = u32::MAX` (sentinel for "never moved"). Host MUST treat sentinel as "do not trust this position".

### 3.5 `STOP` (0x04)

Payload (1 byte): `axis: u8`.

Firmware: disable LEDC output on the GPIO; servo goes limp. Useful for power saving and emergency.

Reply: `ACK(0xF0)`.

### 3.6 `CAL_SET` (0x05)

Payload (12 bytes):

| Offset | Field | Type | Description |
|---|---|---|---|
| 0 | `axis` | u8 | Axis ID |
| 1 | reserved | u8 | 0 |
| 2 | `pulse_min_us` | u16 LE | PWM pulse width at angle_min, typical 500 µs |
| 4 | `pulse_max_us` | u16 LE | PWM pulse width at angle_max, typical 2500 µs |
| 6 | `angle_min_deci_deg` | i16 LE | Mechanical angle at pulse_min × 10 |
| 8 | `angle_max_deci_deg` | i16 LE | Mechanical angle at pulse_max × 10 |
| 10 | reserved | u16 LE | 0 |

Stored in RAM only. Use `CAL_PERSIST` to write to NVS (flash).

Validation:
- `pulse_min_us` ∈ [400, 2600], `pulse_max_us` ∈ [400, 2600], `pulse_min_us < pulse_max_us`.
- `angle_min_deci_deg < angle_max_deci_deg`.
- On violation, reply `ERROR(ERR_BAD_CALIBRATION)`.

Reply: `ACK(0xF0)`.

### 3.7 `CAL_QUERY` (0x06) / `CAL_REPLY` (0x07)

`CAL_QUERY` payload: 1 byte `axis: u8`.

`CAL_REPLY` payload: same 12-byte layout as `CAL_SET` payload, with `axis` echoed and current calibration values.

If axis has no calibration set (cold boot, no NVS entry), reply with calibration set to defaults: `pulse_min_us=500, pulse_max_us=2500, angle_min_deci_deg=-900, angle_max_deci_deg=900` (i.e. ±90° mapped to standard hobby servo range). Calibration is `is_default = (any(*) values match defaults exactly)` from host's perspective; firmware does not flag this explicitly.

### 3.8 `CAL_PERSIST` (0x08)

Payload (1 byte): `axis: u8` (or `0xFF` for "all axes").

Firmware: write current RAM calibration to NVS namespace `servo_cal`, key `axis_<n>` (or all keys for `0xFF`).

Reply: `ACK(0xF0)` on success, `ERROR(ERR_NVS_FAIL)` on failure.

### 3.9 `PING` (0x10)

Payload: empty.

### 3.10 `PONG` (0x11)

Payload (16 bytes):

| Offset | Field | Type | Description |
|---|---|---|---|
| 0 | `proto_version` | u8 | `0x01` for v1 |
| 1 | `fw_major` | u8 | Firmware major version |
| 2 | `fw_minor` | u8 | Firmware minor version |
| 3 | `fw_patch` | u8 | Firmware patch version |
| 4 | `git_short_sha` | u8[8] | First 8 chars of git short SHA, ASCII (no null term) |
| 12 | `axis_count` | u8 | Number of configured axes (1 for v1) |
| 13 | reserved | u8[3] | 0 |

Host MUST validate `proto_version == 1` before sending other commands. Mismatch → driver raises `IncompatibleProtocolError` and refuses to operate.

### 3.11 `RESET` (0x20)

Payload: empty. Firmware sends `ACK(0xF0)` immediately, then calls `esp_restart()` after a 50 ms delay (so the ACK has time to flush over USB-CDC). Host should expect a USB enumeration glitch and reconnect.

### 3.12 `ACK` (0xF0)

Payload (1 byte): the CMD byte being acked. Allows host to disambiguate if multiple commands are in flight. Driver MUST send only one outstanding command at a time in v1, but firmware echoes the CMD anyway for sanity.

### 3.13 `ERROR` (0xFF)

Payload (variable, ≥ 2 bytes):

| Offset | Field | Type | Description |
|---|---|---|---|
| 0 | `original_cmd` | u8 | The command that caused the error |
| 1 | `error_code` | u8 | See §5 |
| 2+ | optional UTF-8 message | u8[] | Up to 64 bytes, no null terminator |

---

## 4. Sequencing rules

1. The host is the master. Firmware never initiates a frame except as a reply.
2. The host MUST NOT send a new command until receiving either `ACK`, the appropriate `*_REPLY`, or `ERROR` for the previous command. (Single-outstanding pipeline.)
3. The host SHOULD timeout after 500 ms of no reply and treat the link as broken: close the serial port, reopen, send `PING`, restart command sequence.
4. After connection (or `RESET`), the host MUST send `PING` and validate `proto_version` before any other command.
5. The firmware SHOULD send a startup `PONG` immediately after USB-CDC enumeration completes (unsolicited), to help the host detect (re)connection. Host treats unsolicited `PONG` the same as a `PING` reply for version negotiation purposes.

---

## 5. Error codes

| Code | Name | Meaning |
|---|---|---|
| `0x01` | `ERR_NO_SUCH_AXIS` | Axis ID is not configured |
| `0x02` | `ERR_ANGLE_OUT_OF_RANGE` | Requested angle is outside calibration range |
| `0x03` | `ERR_BAD_CALIBRATION` | Calibration validation failed (see §3.6) |
| `0x04` | `ERR_NVS_FAIL` | NVS read/write failed |
| `0x05` | `ERR_NOT_CALIBRATED` | Operation requires calibration; none set |
| `0x06` | `ERR_BUSY` | Firmware is mid-operation (reserved; not used in v1, prep for future) |
| `0xFE` | `ERR_UNKNOWN_CMD` | Unrecognised CMD byte |
| `0xFF` | `ERR_INTERNAL` | Catch-all (firmware bug or hardware fault) |

---

## 6. Mode: protocol vs linenoise shell

The firmware boots in protocol mode by default. To enter linenoise shell mode (interactive emergency debugging via plain `screen`/`minicom`), the host (or human via `screen`) sends three consecutive `ENTER` (`\r\n` or `\n`) within 1 second on a fresh connection BEFORE any binary frame arrives.

In linenoise mode:
- Echo on
- Backspace handling
- Up/down arrow history (last 16 commands, RAM only, lost on reboot)
- Tab completion of command names
- Commands: `move <axis> <angle_deg>`, `pos <axis>`, `stop <axis>`, `cal <axis> <pmin> <pmax> <amin> <amax>`, `cal-show <axis>`, `cal-save [axis|all]`, `ping`, `reset`, `proto` (exits linenoise mode and re-enters protocol mode), `help`
- All output is human-readable text, no COBS, no CRC

To exit linenoise mode and return to protocol, type `proto` and press ENTER. Firmware drains the line, prints `entering protocol mode\r\n`, and switches dispatcher.

The host driver MUST NOT enter linenoise mode programmatically; it is purely for human emergency debug via terminal. The driver detects accidental linenoise mode entry by failing to parse the unsolicited startup `PONG` as a valid frame, and raising `LinenoiseDetectedError` with a hint to send `proto\n` manually or power-cycle.

---

## 7. Test vectors

These vectors MUST pass on both firmware and host implementations.

### 7.1 CRC-16/CCITT-FALSE

| Input bytes | Expected CRC (LE) |
|---|---|
| (empty) | `0xFF 0xFF` (`0xFFFF`) |
| `0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39` (`"123456789"` ASCII) | `0xB1 0x29` (`0x29B1`) |
| `0x01 0x04 0x00 0x00 0x84 0x03` (a `MOVE axis=0 angle=900` payload) | `0x89 0x25` (`0x2589`) |

### 7.2 COBS

| Plaintext | COBS-encoded |
|---|---|
| `0x01` | `0x02 0x01` |
| `0x00` | `0x01 0x01` |
| `0x01 0x00 0x02` | `0x02 0x01 0x02 0x02` |

(Standard COBS test vectors; agent verifies against `cobs` Python package.)

### 7.3 Full frame example: `PING`

Logical: `CMD=0x10, LEN=0x00, PAYLOAD=(empty), CRC16=crc16([0x10, 0x00])`

Step-by-step:
1. `CMD || LEN || PAYLOAD = 0x10 0x00`
2. `CRC16(0x10 0x00) = 0x1E7C`
3. Pre-COBS frame: `0x10 0x00 0x7C 0x1E` (4 bytes; CRC LE)
4. COBS-encoded + `0x00` terminator: `0x02 0x10 0x03 0x7C 0x1E 0x00` (6 bytes on the wire)

Agent MUST emit exact byte sequence match in firmware loopback test and host serialiser unit test.

---

## 8. NVS layout

Namespace: `servo_cal`

Keys (one per axis):
- `axis_0`: blob, 12 bytes, layout matches `CAL_SET` payload from offset 0–11 (i.e. axis byte first, then calibration fields)

If a key is missing on boot, axis uses defaults from §3.7 and is in "uncalibrated" state (`CAL_QUERY` returns defaults).

---

## 9. GPIO pin assignments (ESP32-C6)

| Function | Pin | Notes |
|---|---|---|
| Servo PWM output (axis 0 / pan) | GPIO18 | LEDC channel 0, 50 Hz. Chosen over C3-era GPIO5 because C6 GPIO4/5 are JTAG MTMS/MTCK strap pins. |
| Status LED (optional) | GPIO8 | Strap pin on C6 — boot-mode select. OK as output post-boot. |
| Reserved for future axis 1 | GPIO19 | Not used in v1 |

USB-Serial-JTAG uses native USB pins (GPIO12/13 on C6), not configurable.

Servo power: separate 5–6 V supply, NOT from ESP32 5V or RPi 5V. Common ground between PSU and ESP32. Document this in the firmware README.

---

## 10. Future extensions (NOT in v1, but reserve numbers)

- `0x09` `CAL_RESET` — restore axis to defaults
- `0x12–0x1F` reserved for telemetry (e.g. `STREAM_START`, `STREAM_STOP`, `TELEMETRY` for periodic angle pushes)
- `0x21–0x2F` reserved for system control (e.g. `SET_LOG_LEVEL`, `SLEEP`)
- Axis IDs 1+ for future tilt or multi-mast setups

Adding any of these requires bumping `PROTOCOL_VERSION` to `2` and updating this document.

---

## 11. Changelog

### 2026-05-07 — initial host-driver implementation

Changes resolved while implementing `rfmesh.scan.servo` (host driver):

- **§7.1 row 2 (CRC of `"123456789"`):** corrected from `0x29B0` to `0x29B1`. The original value was a one-bit typo; the §2.2 algorithm (canonical CRC-16/CCITT-FALSE) yields `0x29B1` and matches every published reference for that test string.
- **§7.1 row 3 (CRC of the `MOVE axis=0 angle=900` payload):** filled in as `0x2589` (wire LE `0x89 0x25`).
- **§7.3 (full PING frame):** filled in. CRC of `0x10 0x00` is `0x1E7C`; pre-COBS frame is `0x10 0x00 0x7C 0x1E`; full wire bytes after COBS plus terminator are `0x02 0x10 0x03 0x7C 0x1E 0x00`.

No semantics or wire layouts changed; these were edits to test-vector values that the spec itself flagged as TBD or, in the CRC case, contradicted its own algorithm definition. `PROTOCOL_VERSION` remains `1`.

Driver-side clarification (not a wire change): the host driver promotes "bytes accumulated without ever seeing a `0x00` terminator" to `LinenoiseDetectedError` rather than `ServoTimeoutError`, on the same rationale as §2.1's existing handling of frames that fail to decode. The spec already calls out failed-decode-on-startup-PONG as the linenoise tell; this extends the same diagnosis to terminator-less byte streams (linenoise echoes printable ASCII + CR/LF and never emits `0x00`).
