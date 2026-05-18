# rfmesh LoRa reference beacon — flash + verify

Firmware for a controlled-emitter LoRa beacon on ESP32-DevKit v1.1 +
HopeRF SX1276 (e.g. Ra-02 breakout). RF parameters and payload
contract: `lora_beacon_spec.md` (v1.0, binding). Operator-side flash and
verify steps below.

## Build prerequisites

- [PlatformIO Core](https://docs.platformio.org/en/latest/core/installation/index.html)
  ≥ 6.0 (`pipx install platformio` or distro package).
- USB cable to the ESP32-DevKit v1.1.
- For `pio device monitor` over USB-CDC on Linux: user in the `dialout`
  group (`sudo usermod -aG dialout $USER && newgrp dialout`).

PlatformIO pulls Espressif Arduino + RadioLib (jgromes/RadioLib ≥ 6.6.0)
on first `pio run`; no manual library install.

## Wiring (per spec §4)

| Function | ESP32 GPIO | SX1276 / Ra-02 pin |
|---|---|---|
| SPI SCK | 18 | SCK |
| SPI MISO | 19 | MISO |
| SPI MOSI | 23 | MOSI |
| CS / NSS | 5 | NSS |
| Reset | 14 | RESET |
| DIO0 (TX done) | 4 | DIO0 |
| DIO1 (unused but wired) | 2 | DIO1 |
| 3.3 V | 3V3 | VCC |
| GND | GND | GND |

The Ra-02 breakout typically ships with the whip antenna already
soldered; if not, fit a 868 MHz quarter-wave whip to the u.FL or SMA.

> **Power-rail warning.** The SX1276 PA pulls ~120 mA peak at +14 dBm.
> If the USB rail sags (cheap host PC or long cable), the beacon emits
> below spec — looks like a faulty antenna at the listener. Use a known-
> good USB cable and prefer a powered hub for bench runs.

## Build

```bash
cd firmware-beacon
pio run
```

First build pulls Espressif platform and RadioLib (~2 min); subsequent
builds are cached.

## Flash

Hold the BOOT button on the DevKit while pressing RESET to enter the
bootloader, then:

```bash
pio run -t upload
```

PlatformIO auto-detects the serial port. If multiple boards are attached,
specify with `pio run -t upload --upload-port /dev/ttyUSB0`.

After flashing, press RESET once. The board re-enumerates as USB-CDC
and starts the firmware.

## Verify — listen for the beacon

### Serial console banner

```bash
pio device monitor -b 115200
```

Expected:

```
[beacon] rfmesh LoRa reference beacon v1.0
[beacon] target: 868.100 MHz, SF7, BW 125.0 kHz, CR 4/5, 14 dBm
[beacon] initialising SX1276 ... ok
[beacon] starting TX loop (1 Hz)
[beacon] tx #0 ok
[beacon] tx #1 ok
[beacon] tx #2 ok
...
```

If you see `HALT: radio.begin() failed (RadioLib state=<n>)` instead,
the wiring is wrong (SPI or DIO0 mismatch) or the SX1276 is dead. Check
the RadioLib state codes at:
<https://jgromes.github.io/RadioLib/group__status__codes.html>

### Path A — RTL-SDR + gr-lora_sdr

Capture 10 s of IQ at 868.1 MHz:

```bash
rtl_sdr -f 868100000 -s 1000000 -g 30 -n 10000000 /tmp/beacon.bin
```

Post-process with [gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr)
(GNU Radio plugin): set the file source to `/tmp/beacon.bin` and run a
SF7 / BW 125 receive chain. **Set the gr-lora_sdr sync-word parameter
to `0x12` (private network) before running** — the default `0x34`
(LoRaWAN-public) will silently drop every packet at the preamble stage
and produce a false-negative. Expected: one decoded packet per second,
8 bytes each, starting with `52 46 4D 00` (ASCII `RFM\0`) followed by
an incrementing big-endian uint32.

### Path B — gqrx waterfall sanity-check

Tune `gqrx` to 868.100 MHz, FFT zoom to ±100 kHz. Expected: a 1 Hz
LoRa chirp pattern (visible as a periodic up-chirp burst lasting ~10 ms
every second). This does not decode the payload but confirms the beacon
is transmitting at the right frequency and cadence.

### Expected signature

- RSSI at 1 m line-of-sight: roughly −30 dBm (relative; SDRs in scope
  are not power-calibrated per `INHERITED_CONTEXT.md` §1.3). Rough
  accounting at 868 MHz: free-space path loss ≈ 31 dB, whip
  antenna / impedance mismatch ≈ 6 dB, RTL-SDR AGC compression near
  PA overload ≈ 6 dB → `+14 dBm − 31 − 6 − 6 ≈ −29 dBm`. The number
  is uncalibrated; consume as a sanity-check range, not a measurement.
- SNR at 1 m: +25 to +35 dB above the RTL-SDR noise floor.
- TX cadence: visually obvious 1 Hz blink in any FFT waterfall.

> Bring the receiver 5-10 m away from the beacon antenna before
> declaring it ready for Phase C — at 1 m the SX1276 PA can overdrive
> a cheap RTL-SDR front end.

## Failure-mode quick reference

| Symptom | Likely cause | Recovery |
|---|---|---|
| `HALT: radio.begin() failed` | SPI / DIO0 wiring or dead SX1276 | Check pin map (spec §4 / table above); re-seat breakout |
| Serial banner appears but no `tx #N` lines | RadioLib `transmit()` returning non-zero state | Check antenna is connected (open-circuit PA can latch); check power rail |
| `tx #N ok` lines but listener sees nothing | Antenna missing / wrong band | Confirm 868 MHz whip fitted; check listener tuned to 868.100 MHz |
| Listener sees packets but counter resets | Beacon power-cycled | Expected — counter restarts at 0 on every power-on per spec §2 |
| Listener decodes wrong payload bytes | LoRa sync word mismatch (listener configured for LoRaWAN-public 0x34) | Set the listener to private-network sync word 0x12 |
| RSSI 20 dB lower than expected | USB power-rail sag | Use shorter / known-good USB cable; powered hub |
| Counter wraps at 2^32 | uint32 rollover at 1 Hz takes ~136 years | Not a v1.0 concern (spec §7.3); future ≥ 100 Hz revision promotes counter to uint64 |

## Out of scope

OTA firmware update; RX path (the beacon is transmit-only); LoRaWAN
gateway compatibility; FHSS / multi-frequency; non-EU region operation.
Future v2.0 may revisit; for now, spec v1.0 is the binding contract.

## Source layout

```
firmware-beacon/
    platformio.ini        PlatformIO build config (esp32dev + RadioLib)
    lora_beacon_spec.md   Binding RF + payload contract (v1.0)
    README.md             This file (flash + verify procedure)
    src/
        main.cpp          Beacon firmware (Arduino setup/loop)
        beacon_config.h   Compile-time spec values
```

No Python deps. No `rfmesh_*` package imports anything from this
directory — the beacon talks RF; the Python stack listens via the
`Receiver` Protocol.
