// Servo UART v1 frame parser/serialiser per docs/wire-protocols/servo_uart_v1.md.
//
// This file deliberately contains *only* the pure framing logic so the
// host-side Unity suite can build it without ESP-IDF. Command-handler
// dispatch and ESP32-specific I/O live elsewhere (see main.c, servo.c,
// calibration.c).

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Logical (un-stuffed) frame upper bound: CMD + LEN + 253 payload + CRC2.
#define PROTO_LOGICAL_MAX 257u

// Wire (post-COBS, including terminator) upper bound. Conservative — see
// COBS_ENCODE_MAX_LEN: ceil(257/254) = 2 overhead bytes + 257 + 1 term.
#define PROTO_WIRE_MAX    (PROTO_LOGICAL_MAX + 3u)

// Maximum COBS-encoded payload length (i.e. wire bytes between two 0x00
// delimiters). Must accept any frame that fits in PROTO_LOGICAL_MAX.
#define PROTO_RAW_MAX     (PROTO_LOGICAL_MAX + 2u)

typedef enum {
    PROTO_RX_NEED_MORE = 0,  // byte consumed; frame not yet complete
    PROTO_RX_FRAME_READY,    // complete frame in rx->decoded[0..decoded_len)
    PROTO_RX_DROP,           // a frame was dropped (CRC/COBS/length); silently resync
    PROTO_RX_IDLE,           // 0x00 seen with no accumulated bytes (link idle)
} proto_rx_status_t;

typedef struct {
    uint8_t raw[PROTO_RAW_MAX];        // accumulated COBS bytes, no terminator
    size_t  raw_len;
    bool    overflowed;                // current frame already too long; drop on next 0x00
    uint8_t decoded[PROTO_LOGICAL_MAX]; // post-COBS, post-CRC-verify
    size_t  decoded_len;               // length of CMD || LEN || PAYLOAD (CRC stripped)
} proto_rx_t;

void              proto_rx_init(proto_rx_t *rx);
proto_rx_status_t proto_rx_feed(proto_rx_t *rx, uint8_t byte);

// Serialise CMD + payload into a complete wire frame: CMD || LEN || PAYLOAD
// || CRC16(LE), COBS-encoded, with a trailing 0x00 terminator.
//
// Returns the number of bytes written to wire_out (including terminator),
// or 0 if `len > 253` (overflows the LEN field).
//
// wire_out must be at least PROTO_WIRE_MAX bytes.
size_t proto_serialise(uint8_t cmd, const uint8_t *payload, uint8_t len,
                       uint8_t *wire_out);

#ifdef __cplusplus
}
#endif
