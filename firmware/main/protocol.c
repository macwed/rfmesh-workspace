#include "protocol.h"

#include <string.h>

#include "cobs.h"
#include "crc16.h"

void proto_rx_init(proto_rx_t *rx) {
    rx->raw_len = 0;
    rx->overflowed = false;
    rx->decoded_len = 0;
}

static proto_rx_status_t finalise_frame(proto_rx_t *rx) {
    // Caller has already cleared its own state; we always reset rx->raw
    // before returning so the next frame starts fresh.
    const size_t raw_len = rx->raw_len;
    rx->raw_len = 0;
    const bool was_overflow = rx->overflowed;
    rx->overflowed = false;

    if (was_overflow) {
        return PROTO_RX_DROP;
    }
    if (raw_len == 0) {
        return PROTO_RX_IDLE;
    }

    uint8_t decoded[PROTO_LOGICAL_MAX + 1];
    const size_t decoded_len = cobs_decode(rx->raw, raw_len, decoded);
    if (decoded_len == 0) {
        return PROTO_RX_DROP; // COBS error
    }
    // Decoded layout: CMD(1) + LEN(1) + PAYLOAD(LEN) + CRC(2) = LEN + 4.
    if (decoded_len < 4) {
        return PROTO_RX_DROP;
    }
    const uint8_t len_field = decoded[1];
    if ((size_t)len_field + 4u != decoded_len) {
        return PROTO_RX_DROP; // length mismatch
    }

    const size_t covered = (size_t)len_field + 2u; // CMD + LEN + PAYLOAD
    const uint16_t want = crc16_ccitt_false(decoded, covered);
    const uint16_t got  = (uint16_t)decoded[covered]
                        | ((uint16_t)decoded[covered + 1] << 8);
    if (want != got) {
        return PROTO_RX_DROP; // CRC error
    }

    memcpy(rx->decoded, decoded, covered);
    rx->decoded_len = covered;
    return PROTO_RX_FRAME_READY;
}

proto_rx_status_t proto_rx_feed(proto_rx_t *rx, uint8_t byte) {
    if (byte == 0x00) {
        return finalise_frame(rx);
    }
    if (rx->overflowed) {
        // Already over budget; silently swallow until the next 0x00.
        return PROTO_RX_NEED_MORE;
    }
    if (rx->raw_len >= sizeof rx->raw) {
        rx->overflowed = true;
        return PROTO_RX_NEED_MORE;
    }
    rx->raw[rx->raw_len++] = byte;
    return PROTO_RX_NEED_MORE;
}

size_t proto_serialise(uint8_t cmd, const uint8_t *payload, uint8_t len,
                       uint8_t *wire_out) {
    if (len > 253u) {
        return 0;
    }

    uint8_t pre[PROTO_LOGICAL_MAX];
    pre[0] = cmd;
    pre[1] = len;
    if (len > 0 && payload != NULL) {
        memcpy(pre + 2, payload, len);
    }
    const size_t covered = (size_t)len + 2u;
    const uint16_t crc = crc16_ccitt_false(pre, covered);
    pre[covered]     = (uint8_t)(crc & 0xFFu);
    pre[covered + 1] = (uint8_t)((crc >> 8) & 0xFFu);
    const size_t pre_len = covered + 2u;

    const size_t enc_len = cobs_encode(pre, pre_len, wire_out);
    wire_out[enc_len] = 0x00;
    return enc_len + 1u;
}
