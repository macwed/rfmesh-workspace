// SPDX-License-Identifier: MIT
// rfmesh LoRa reference beacon -- compile-time configuration.
//
// All values here are bound by firmware-beacon/lora_beacon_spec.md v1.0.
// Changing any of them is a spec bump (ADR). The defaults match the EU
// 868 MHz LC1 channel, SF7/BW125/CR4/5, +14 dBm TX power, 1 Hz cadence,
// and the 8-byte payload schema ("RFM\0" magic + uint32 big-endian
// counter).

#ifndef RFMESH_BEACON_CONFIG_H_
#define RFMESH_BEACON_CONFIG_H_

#include <stdint.h>

// ---------------------------------------------------------------------------
// RF parameters (spec §1)
// ---------------------------------------------------------------------------

// Carrier frequency, MHz. EU 868 MHz ISM channel LC1.
constexpr float BEACON_FREQ_MHZ = 868.100f;

// LoRa bandwidth, kHz. LoRaWAN default.
constexpr float BEACON_BANDWIDTH_KHZ = 125.0f;

// Spreading factor. SF7 = lowest SF, highest data rate.
constexpr uint8_t BEACON_SPREADING_FACTOR = 7;

// Coding rate denominator -- "4/5" -> value 5.
constexpr uint8_t BEACON_CODING_RATE_DENOM = 5;

// TX output power, dBm. +14 dBm = EU 868 MHz LC1 legal max.
constexpr int8_t BEACON_TX_POWER_DBM = 14;

// Preamble length, symbols. LoRaWAN default.
constexpr uint16_t BEACON_PREAMBLE_SYMBOLS = 8;

// LoRa sync word. 0x12 = private-network (deliberately NOT 0x34
// LoRaWAN-public so any nearby LoRaWAN gateway ignores us at the modem
// layer; see spec §1 rationale).
constexpr uint8_t BEACON_SYNC_WORD = 0x12;

// ---------------------------------------------------------------------------
// Payload (spec §2)
// ---------------------------------------------------------------------------

// Exactly 8 bytes: 4-byte ASCII identifier + 4-byte uint32 big-endian
// counter incrementing per TX (wraps at 2^32, restarts at 0 on reboot).
constexpr uint8_t BEACON_PAYLOAD_LEN = 8;

// 4-byte identifier prefix. ASCII "RFM\0" -- a magic value for operator
// cross-check on the listener side, NOT a LoRa modem sync word.
constexpr uint8_t BEACON_ID_BYTES[4] = {'R', 'F', 'M', 0x00};

// ---------------------------------------------------------------------------
// Timing (spec §3)
// ---------------------------------------------------------------------------

// Period between TX starts, milliseconds. ~1 Hz.
constexpr uint32_t BEACON_TX_PERIOD_MS = 1000;

// Power-on grace before the first TX, milliseconds. Lets the operator
// see the serial-console banner before the radio activates.
constexpr uint32_t BEACON_BOOT_DELAY_MS = 2000;

// ---------------------------------------------------------------------------
// Pin map (spec §4) -- ESP32-DevKit v1.1 + Ra-02 (SX1276) breakout
// ---------------------------------------------------------------------------

constexpr int BEACON_PIN_NSS = 5;
constexpr int BEACON_PIN_RESET = 14;
constexpr int BEACON_PIN_DIO0 = 4;
constexpr int BEACON_PIN_DIO1 = 2;

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

// UART baud for the serial console (firmware banner, per-TX log lines,
// error codes from RadioLib).
constexpr uint32_t BEACON_SERIAL_BAUD = 115200;

#endif  // RFMESH_BEACON_CONFIG_H_
