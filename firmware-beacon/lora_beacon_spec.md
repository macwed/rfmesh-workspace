# LoRa beacon — firmware specification v1.0

**Status:** binding for the beacon firmware build. Lead-only changes.
**Audience:** the firmware author (Lead-Opus / Maciej), the bench
operator who flashes and verifies, and the Phase C scribe who annotates
which beacon is "the reference" for a given session.
**Date:** 2026-05-18.
**Target hardware:** ESP32-DevKit v1.1 (ESP32-WROOM-32) +
HopeRF SX1276 LoRa module (e.g. Ra-02 breakout) on the EU-868 ISM band.

This spec is the **controlled-emitter contract** for all pre-event Phase
C work: a known-frequency, known-position, known-payload reference
emitter that the `rfmesh` node stack can listen to as it would listen
to any other emitter, while the operator has bit-level certainty about
what is being transmitted. The receiver side of the demo does *not* know
about this beacon — to the `Receiver` Protocol implementation it is just
RF energy at a known centre. The beacon's value is for the operator's
sanity-check loop (`docs/phase-c-bench-checklist.md`).

---

## §1 RF parameters

These values are the v1.0 contract. Changing any is a spec bump (ADR).

| Parameter | Value | Rationale |
|---|---|---|
| Carrier frequency | **868.100 MHz** | EU ISM band, channel LC1 (per ETSI EN 300 220); below the LoRaWAN gateway uplink channels (867.1/.3/.5/.7/.9, 868.1/.3/.5) to avoid colliding with any nearby gateway. Picks 868.100 specifically because it is the lowest legal EU LoRa channel and most cleanly separated from the 868.65 MHz "Listen Before Talk" pool. |
| Bandwidth | **125 kHz** | LoRaWAN default. Achievable on every SX1276 breakout. Wide enough that the ATK-10 Yagi's bandpass does not roll off measurably across the symbol. |
| Spreading factor | **SF7** | Lowest SF (highest data rate, shortest air-time per packet). SF7 + 125 kHz + CR 4/5 → ~5470 bps payload throughput. 8-byte payload (see §2) flies in ~10 ms, well inside the 1 s tx period. |
| Coding rate | **4/5** | Minimum CR. We want the beacon to be loud and short, not maximally robust — the L1 demo's job is to detect *presence*, not decode. |
| TX power | **+14 dBm** | Within the EU 868 MHz +14 dBm (25 mW) limit for the LC1 channel. The SX1276 datasheet's PA_BOOST path supports this directly. |
| Preamble length | **8 symbols** | LoRaWAN default. Long enough for a generic LoRa-aware decoder to lock. |
| Sync word | **0x12** | LoRaWAN private-network sync. *Not* `0x34` (public LoRaWAN); the beacon is our own emitter, not a LoRaWAN node, and we want a third-party LoRaWAN gateway listening on this frequency to ignore our preamble. |
| Header mode | **explicit** | The receiver does not know the payload length a priori. Explicit header carries it. |
| CRC | **enabled** | Standard packet CRC. Decode failures surface as packet loss, not as silent bit-flips into the counter field (§2). |

### Why these specific choices

SF7 + BW 125 + CR 4/5 + 8-byte payload = ~10 ms air-time. At a 1 Hz TX
rate, the duty cycle is ~1 %, well below the EU ISM 1 % duty cycle limit
for the 868.0-868.6 MHz sub-band — important so the beacon can sit on
Maciej's bench for hours without the regulator caring.

The sync word `0x12` keeps the beacon *invisible* to any LoRaWAN
gateway physically near the bench (apartment, neighbour, or pico-cell
testbed); the beacon does not pretend to be a LoRaWAN node and a
gateway will not even try to decode our header.

---

## §2 Payload contract

Every TX is exactly **8 bytes**, big-endian, structured as:

```
  byte 0  byte 1  byte 2  byte 3  byte 4  byte 5  byte 6  byte 7
  +-----+ +-----+ +-----+ +-----+ +-----+ +-----+ +-----+ +-----+
  |  R  | |  F  | |  M  | |  0  | |  c3 | |  c2 | |  c1 | |  c0 |
  +-----+ +-----+ +-----+ +-----+ +-----+ +-----+ +-----+ +-----+
   identifier 'R' 'F' 'M' \0       counter (uint32 big-endian)
```

- Bytes 0..3 — the literal ASCII string `"RFM\0"`. Acts as a magic value
  for a third-party LoRa decoder operator-side cross-check. Not a sync
  word (LoRa already has one at the modulator layer per §1).
- Bytes 4..7 — `uint32` counter, big-endian, incrementing by 1 per TX
  with wrap at 2^32. Lets the listener detect packet loss directly
  (consecutive received counters differ by > 1 ⇒ N - 1 packets lost in
  the gap; same counter twice ⇒ duplicate; counter goes backward ⇒
  receiver framing fault or beacon reset).

The counter is **not** persisted across reboot — every power cycle
restarts the sequence at 0. This is deliberate: the operator wants to
know how many packets have been emitted since the last power-on, not
since the last firmware flash six weeks ago.

---

## §3 Timing

- TX cadence: **1.000 s** between TX starts (not between TX ends —
  `delay(1000 - last_air_time_ms)` style; or simpler: just `delay(1000)`
  and accept the small extra gap from air-time). v1.0 implementation
  uses the simple `delay(1000)` form; the resulting effective rate is
  about 0.99 Hz, fine for a reference emitter. The ~10 ms air-time
  plus the 1000 ms `delay()` puts the effective period at ~1.01 s,
  giving roughly 1 % duty cycle on the high side of the per-period
  count but with natural margin below the EU 868 MHz LC1 1 % limit
  — the beacon is **not** sitting exactly at the regulatory ceiling.
- Power-on delay before first TX: **2.0 s** so the operator has a
  chance to see the serial console banner before the radio starts.

---

## §4 Pin map (ESP32-DevKit v1.1 + SX1276 / Ra-02 breakout)

| Function | ESP32 GPIO | SX1276 pin |
|---|---|---|
| SPI SCK | 18 | SCK |
| SPI MISO | 19 | MISO |
| SPI MOSI | 23 | MOSI |
| CS / NSS | 5 | NSS |
| Reset | 14 | RESET |
| DIO0 (TX done IRQ) | 4 | DIO0 |
| DIO1 (RX timeout IRQ — unused but wired) | 2 | DIO1 |
| 3.3 V | 3V3 rail | VCC |
| GND | GND | GND |

Note: the SX1276 antenna pin uses the breakout's onboard u.FL or
SMA connector; pigtail to a 868 MHz quarter-wave whip. The Ra-02
breakout typically ships with the whip soldered already.

---

## §5 Verification — bench listen procedure

The beacon is verified end-to-end with a separate SDR (RTL-SDR V4) and
a LoRa-aware decoder. Two equivalent tooling paths are documented; pick
whichever the bench has installed.

### Path A — `rtl_sdr` + `gr-lora_sdr`

```bash
rtl_sdr -f 868100000 -s 1000000 -g 30 -n 10000000 /tmp/beacon.bin
# Then post-process /tmp/beacon.bin with gr-lora_sdr (GNU Radio plugin):
#   gnuradio-companion examples/rx_chain_125k_sf7.grc
# adjust the file source to /tmp/beacon.bin and run.
```

### Path B — `gqrx` + manual inspection

`gqrx` to 868.100 MHz, FFT zoom to ±100 kHz, watch for the 1 Hz LoRa
chirp signature. Not a full decode, but cheap "is the beacon
transmitting at all" verification.

### Expected signature on a clean bench

- RSSI at 1 m line-of-sight from a bench antenna: **~-30 dBm**
  (relative to the receiver's noise floor — SDRs in scope are NOT
  power-calibrated per `INHERITED_CONTEXT.md` §1.3, so this is
  uncalibrated relative power, useful only as a sanity-check).
- SNR at 1 m: **+25 to +35 dB** above noise floor.
- TX cadence: visually obvious 1 Hz blink in any FFT waterfall display.

Bring the receiver further from the antenna (5-10 m) before declaring
the beacon ready for Phase C — at 1 m the SX1276's PA can overdrive a
cheap RTL-SDR's front end.

---

## §6 Out of scope (v1.0)

- Downlink / two-way protocol. The beacon is **transmit-only**; v1.0
  does not implement RX.
- Multi-frequency hopping. One channel (868.100 MHz), one set of LoRa
  parameters. A future v2.0 might add FHSS for jamming-resistance
  demos; not v1.0.
- LoRaWAN compliance. The beacon is **not** a LoRaWAN node. The sync
  word is deliberately the LoRa-private one (`0x12`) precisely to
  avoid confusion with LoRaWAN gateways.
- OTA firmware update. Bench USB flash only. The beacon is a fixed,
  short, well-defined firmware — there is no operational reason to
  ever update it remotely.
- US-915 / AS-923 / other region operation. EU-868 only; relocating
  the beacon to a non-EU site is an ADR.
- GPS time-stamping. The packet counter is the timing reference;
  wall-clock TX time is the listener's job.

---

## §7 Failure modes the firmware must NOT exhibit

These are the silent-fallback patterns Invariant B3 forbids, mapped to
this layer. The firmware author cross-checks against this list before
shipping.

1. **Silent SX1276-init failure.** If `radio.begin(...)` returns a
   non-zero error code, the firmware must HALT with a clearly logged
   error code on the serial console, not silently proceed and emit
   nothing.
2. **Silent transmit failure.** If `radio.transmit(...)` returns a
   non-zero error code, the firmware must log it (not silently retry
   with a partial payload).
3. **Counter rollover misreporting.** The counter is `uint32`,
   wrapping at 2^32 ≈ 4.3 × 10^9. At 1 Hz that is ~136 years — no
   practical concern. But if a future v2.0 raises the TX rate to,
   e.g., 100 Hz, the wrap is reachable in ~1.4 years and the spec
   bump must promote the counter to `uint64`.
4. **Power-rail sag during TX.** The SX1276 PA pulls ~120 mA peak at
   +14 dBm. If the USB rail sags (cheap host PC, long cable), the
   beacon may emit at lower power than the listener expects, looking
   like a faulty antenna or a distance-mismatch. The firmware itself
   cannot fix this — the operator's `firmware-beacon/README.md` must
   document the failure mode.

---

## §8 Provenance

This spec was authored from Thread 1's `lora_beacon_spec.md` outline,
expanded with the SX1276 datasheet (Semtech, rev 7), the SX1276
RadioLib API documentation, and the EU ETSI EN 300 220 channel
allocation table for 868 MHz LC1.

The firmware implementing this spec lives at
`firmware-beacon/src/main.cpp` (Arduino-ESP32 + RadioLib), and the
PlatformIO build config at `firmware-beacon/platformio.ini`. The
flash + verify procedure for the operator is at
`firmware-beacon/README.md`.
