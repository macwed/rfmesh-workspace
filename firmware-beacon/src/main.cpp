// SPDX-License-Identifier: MIT
// rfmesh LoRa reference beacon -- ESP32-DevKit v1.1 + SX1276 (Ra-02).
//
// Implements firmware-beacon/lora_beacon_spec.md v1.0:
//   868.100 MHz / SF7 / BW 125 kHz / CR 4/5 / +14 dBm / sync 0x12,
//   8-byte payload ("RFM\0" + uint32 BE counter), 1 Hz TX cadence.
//
// Build with PlatformIO via firmware-beacon/platformio.ini. Flash + verify
// procedure for the operator is in firmware-beacon/README.md.
//
// Invariant-B3 (no silent fallbacks) is enforced at the firmware layer:
//   * SX1276 init failure -> halt with a serial-logged RadioLib state code
//     (does NOT silently proceed and emit nothing).
//   * TX failure -> log the RadioLib state code and continue (next TX
//     re-attempts with the next counter); a future revision may HALT on
//     persistent failure.
//   * No silent payload truncation: the payload buffer is a fixed
//     8-byte stack array and we always transmit() the full length.

#include <Arduino.h>
#include <RadioLib.h>

#include "beacon_config.h"

// SX1276 driver instance. RadioLib's Module constructor takes
// (CS, IRQ=DIO0, RESET, GPIO=DIO1).
static SX1276 radio = new Module(BEACON_PIN_NSS, BEACON_PIN_DIO0,
                                 BEACON_PIN_RESET, BEACON_PIN_DIO1);

// Monotonic TX counter. Restarts at 0 on every power-on (spec §2).
static uint32_t tx_counter = 0;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Pack the 8-byte beacon payload per spec §2 into the caller-supplied
// buffer. Returns the byte count (always BEACON_PAYLOAD_LEN).
static uint8_t pack_beacon_payload(uint8_t* buf, uint32_t counter) {
    buf[0] = BEACON_ID_BYTES[0];
    buf[1] = BEACON_ID_BYTES[1];
    buf[2] = BEACON_ID_BYTES[2];
    buf[3] = BEACON_ID_BYTES[3];
    // uint32 big-endian -- most significant byte first.
    buf[4] = static_cast<uint8_t>((counter >> 24) & 0xFF);
    buf[5] = static_cast<uint8_t>((counter >> 16) & 0xFF);
    buf[6] = static_cast<uint8_t>((counter >> 8) & 0xFF);
    buf[7] = static_cast<uint8_t>(counter & 0xFF);
    return BEACON_PAYLOAD_LEN;
}

// Halt the firmware with a clearly logged reason. Used by the B3
// surfaces below.
static void halt_with_reason(const char* reason, int radiolib_state) {
    Serial.print(F("[beacon] HALT: "));
    Serial.print(reason);
    Serial.print(F(" (RadioLib state="));
    Serial.print(radiolib_state);
    Serial.println(F(")"));
    while (true) {
        // Blink a fault pattern on the built-in LED if available, but
        // do not silently retry. The operator must power-cycle and
        // diagnose.
        delay(1000);
    }
}

// ---------------------------------------------------------------------------
// setup() / loop()
// ---------------------------------------------------------------------------

void setup() {
    Serial.begin(BEACON_SERIAL_BAUD);
    delay(BEACON_BOOT_DELAY_MS);

    Serial.println(F("[beacon] rfmesh LoRa reference beacon v1.0"));
    Serial.print(F("[beacon] target: "));
    Serial.print(BEACON_FREQ_MHZ, 3);
    Serial.print(F(" MHz, SF"));
    Serial.print(BEACON_SPREADING_FACTOR);
    Serial.print(F(", BW "));
    Serial.print(BEACON_BANDWIDTH_KHZ, 1);
    Serial.print(F(" kHz, CR 4/"));
    Serial.print(BEACON_CODING_RATE_DENOM);
    Serial.print(F(", "));
    Serial.print(BEACON_TX_POWER_DBM);
    Serial.println(F(" dBm"));

    Serial.print(F("[beacon] initialising SX1276 ..."));
    int state = radio.begin(BEACON_FREQ_MHZ, BEACON_BANDWIDTH_KHZ,
                            BEACON_SPREADING_FACTOR, BEACON_CODING_RATE_DENOM,
                            BEACON_SYNC_WORD, BEACON_TX_POWER_DBM,
                            BEACON_PREAMBLE_SYMBOLS);
    if (state != RADIOLIB_ERR_NONE) {
        halt_with_reason("radio.begin() failed", state);
    }
    Serial.println(F(" ok"));

    // CRC on, explicit header on -- spec §1.
    state = radio.setCRC(true);
    if (state != RADIOLIB_ERR_NONE) {
        halt_with_reason("radio.setCRC(true) failed", state);
    }
    state = radio.explicitHeader();
    if (state != RADIOLIB_ERR_NONE) {
        halt_with_reason("radio.explicitHeader() failed", state);
    }

    Serial.println(F("[beacon] starting TX loop (1 Hz)"));
}

void loop() {
    uint8_t payload[BEACON_PAYLOAD_LEN];
    pack_beacon_payload(payload, tx_counter);

    int state = radio.transmit(payload, BEACON_PAYLOAD_LEN);
    if (state == RADIOLIB_ERR_NONE) {
        Serial.print(F("[beacon] tx #"));
        Serial.print(tx_counter);
        Serial.println(F(" ok"));
    } else {
        // B3-equivalent at firmware layer: do not silently retry with a
        // partial payload. Log the state code so the operator can
        // diagnose; increment the counter so the listener can spot the
        // gap.
        Serial.print(F("[beacon] tx #"));
        Serial.print(tx_counter);
        Serial.print(F(" FAILED (state="));
        Serial.print(state);
        Serial.println(F(")"));
    }

    tx_counter += 1;  // wraps at 2^32 per spec §2

    delay(BEACON_TX_PERIOD_MS);
}
