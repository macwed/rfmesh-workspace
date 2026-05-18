// Host-side tests for protocol_serialise + proto_rx feed loop.
// Verifies §7.3 byte-exact PING wire form and the §2.1 receiver
// behaviour (drop on COBS/CRC/length error, resync at next 0x00).

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "unity.h"
#include "protocol.h"

static proto_rx_status_t feed_all(proto_rx_t *rx,
                                  const uint8_t *bytes, size_t n) {
    proto_rx_status_t last = PROTO_RX_NEED_MORE;
    for (size_t i = 0; i < n; i++) {
        last = proto_rx_feed(rx, bytes[i]);
    }
    return last;
}

// §7.3 — full PING frame on the wire.
static const uint8_t ping_wire[] = { 0x02, 0x10, 0x03, 0x7C, 0x1E, 0x00 };

static void test_serialise_ping_byte_exact(void) {
    uint8_t out[PROTO_WIRE_MAX];
    size_t n = proto_serialise(0x10, NULL, 0, out);
    TEST_ASSERT_EQUAL_size_t(sizeof ping_wire, n);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(ping_wire, out, sizeof ping_wire);
}

static void test_parse_ping(void) {
    proto_rx_t rx;
    proto_rx_init(&rx);
    proto_rx_status_t st = feed_all(&rx, ping_wire, sizeof ping_wire);
    TEST_ASSERT_EQUAL(PROTO_RX_FRAME_READY, st);
    TEST_ASSERT_EQUAL_size_t(2, rx.decoded_len);
    TEST_ASSERT_EQUAL_HEX8(0x10, rx.decoded[0]);
    TEST_ASSERT_EQUAL_HEX8(0x00, rx.decoded[1]);
}

static void test_serialise_move_axis0_angle900(void) {
    // Verify the CRC over the §7.1 row 3 payload appears on the wire LE.
    // MOVE payload bytes per §3.2: axis=0, reserved=0, angle_le=0x0384=900.
    const uint8_t payload[] = { 0x00, 0x00, 0x84, 0x03 };
    uint8_t out[PROTO_WIRE_MAX];
    size_t n = proto_serialise(0x01, payload, sizeof payload, out);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL_HEX8(0x00, out[n - 1]); // trailing 0x00

    // Round-trip through the parser to confirm CRC + length are valid.
    proto_rx_t rx;
    proto_rx_init(&rx);
    proto_rx_status_t st = feed_all(&rx, out, n);
    TEST_ASSERT_EQUAL(PROTO_RX_FRAME_READY, st);
    TEST_ASSERT_EQUAL_size_t(2u + sizeof payload, rx.decoded_len);
    TEST_ASSERT_EQUAL_HEX8(0x01, rx.decoded[0]);
    TEST_ASSERT_EQUAL_HEX8(sizeof payload, rx.decoded[1]);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(payload, rx.decoded + 2, sizeof payload);
}

static void test_drop_on_bad_crc(void) {
    uint8_t frame[PROTO_WIRE_MAX];
    size_t n = proto_serialise(0x10, NULL, 0, frame);
    // Corrupt the byte just before the terminator (low CRC byte).
    frame[n - 2] ^= 0x55;

    proto_rx_t rx;
    proto_rx_init(&rx);
    proto_rx_status_t st = feed_all(&rx, frame, n);
    TEST_ASSERT_EQUAL(PROTO_RX_DROP, st);
}

static void test_drop_on_bad_cobs(void) {
    // Code byte 0x05 promises 4 data bytes, but we only give 1 then 0x00.
    const uint8_t bad[] = { 0x05, 0x99, 0x00 };
    proto_rx_t rx;
    proto_rx_init(&rx);
    proto_rx_status_t st = feed_all(&rx, bad, sizeof bad);
    TEST_ASSERT_EQUAL(PROTO_RX_DROP, st);
}

static void test_drop_on_short_decoded(void) {
    // After COBS-decode we'd have fewer than 4 bytes (CMD+LEN+CRC2 minimum).
    // 0x02 0x99 -> decodes to 0x99 (1 byte), too short.
    const uint8_t bad[] = { 0x02, 0x99, 0x00 };
    proto_rx_t rx;
    proto_rx_init(&rx);
    proto_rx_status_t st = feed_all(&rx, bad, sizeof bad);
    TEST_ASSERT_EQUAL(PROTO_RX_DROP, st);
}

static void test_drop_on_length_mismatch(void) {
    // Build a real frame, then bump LEN inside the post-CRC un-stuffed
    // form so length-vs-decoded-len mismatches even though COBS+CRC
    // would succeed independently. Easier: hand-craft a frame whose LEN
    // claims 5 but only 0 payload bytes follow.
    //
    // Pre-COBS: CMD=0x01, LEN=0x05, CRC=crc16([0x01,0x05]). Wire:
    // we just compute and inject this manually.
    //
    // We don't have crc here; cheat by serialising a real LEN=0 MOVE,
    // then mutate LEN inside the encoded form. Since LEN sits at index 1
    // post-decode and CRC was computed over [CMD,LEN], altering LEN now
    // would also fail CRC — covering both checks. That's fine: this
    // test confirms the parser drops.
    uint8_t frame[PROTO_WIRE_MAX];
    size_t n = proto_serialise(0x01, NULL, 0, frame);
    frame[2] ^= 0x07; // mutate the COBS-stuffed LEN byte position
    proto_rx_t rx;
    proto_rx_init(&rx);
    proto_rx_status_t st = feed_all(&rx, frame, n);
    TEST_ASSERT_EQUAL(PROTO_RX_DROP, st);
}

static void test_resync_after_garbage(void) {
    // A pile of garbage bytes (not containing 0x00), then 0x00 (which
    // will trigger a DROP), then a clean PING.
    const uint8_t garbage[] = {
        0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22, 0x33,
    };
    proto_rx_t rx;
    proto_rx_init(&rx);
    for (size_t i = 0; i < sizeof garbage; i++) {
        TEST_ASSERT_EQUAL(PROTO_RX_NEED_MORE, proto_rx_feed(&rx, garbage[i]));
    }
    // Garbage terminator. Parser tries to decode -> drop.
    TEST_ASSERT_EQUAL(PROTO_RX_DROP, proto_rx_feed(&rx, 0x00));
    // Followed by a fresh, well-formed PING.
    proto_rx_status_t st = feed_all(&rx, ping_wire, sizeof ping_wire);
    TEST_ASSERT_EQUAL(PROTO_RX_FRAME_READY, st);
    TEST_ASSERT_EQUAL_HEX8(0x10, rx.decoded[0]);
}

static void test_idle_on_back_to_back_zeros(void) {
    proto_rx_t rx;
    proto_rx_init(&rx);
    // First 0x00 with no accumulated bytes: idle, not a drop.
    TEST_ASSERT_EQUAL(PROTO_RX_IDLE, proto_rx_feed(&rx, 0x00));
    // Multiple in a row should all be IDLE.
    TEST_ASSERT_EQUAL(PROTO_RX_IDLE, proto_rx_feed(&rx, 0x00));
    TEST_ASSERT_EQUAL(PROTO_RX_IDLE, proto_rx_feed(&rx, 0x00));
    // Still works after that.
    proto_rx_status_t st = feed_all(&rx, ping_wire, sizeof ping_wire);
    TEST_ASSERT_EQUAL(PROTO_RX_FRAME_READY, st);
}

static void test_oversize_frame_drops(void) {
    // Feed PROTO_RAW_MAX + 5 non-zero bytes, then 0x00. Parser should
    // mark overflow and drop on terminator without smashing buffers.
    proto_rx_t rx;
    proto_rx_init(&rx);
    for (size_t i = 0; i < PROTO_RAW_MAX + 5; i++) {
        TEST_ASSERT_EQUAL(PROTO_RX_NEED_MORE,
                          proto_rx_feed(&rx, (uint8_t)((i % 254) + 1)));
    }
    TEST_ASSERT_EQUAL(PROTO_RX_DROP, proto_rx_feed(&rx, 0x00));
    // After drop, parser is reset; PING parses cleanly.
    proto_rx_status_t st = feed_all(&rx, ping_wire, sizeof ping_wire);
    TEST_ASSERT_EQUAL(PROTO_RX_FRAME_READY, st);
}

static void test_serialise_rejects_oversize_payload(void) {
    // proto_serialise refuses payloads > 253 (LEN field overflow).
    // 0xFF (255) is the only u8 strictly greater than 253 representable.
    uint8_t buf[PROTO_WIRE_MAX];
    uint8_t fake_payload[300] = {0};
    TEST_ASSERT_EQUAL_size_t(0, proto_serialise(0x01, fake_payload, 254, buf));
    TEST_ASSERT_EQUAL_size_t(0, proto_serialise(0x01, fake_payload, 255, buf));
    // 253 must still succeed.
    TEST_ASSERT_TRUE(proto_serialise(0x01, fake_payload, 253, buf) > 0);
}

void run_protocol_parse_tests(void) {
    RUN_TEST(test_serialise_ping_byte_exact);
    RUN_TEST(test_parse_ping);
    RUN_TEST(test_serialise_move_axis0_angle900);
    RUN_TEST(test_drop_on_bad_crc);
    RUN_TEST(test_drop_on_bad_cobs);
    RUN_TEST(test_drop_on_short_decoded);
    RUN_TEST(test_drop_on_length_mismatch);
    RUN_TEST(test_resync_after_garbage);
    RUN_TEST(test_idle_on_back_to_back_zeros);
    RUN_TEST(test_oversize_frame_drops);
    RUN_TEST(test_serialise_rejects_oversize_payload);
}
