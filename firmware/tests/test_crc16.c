// Host-side tests for crc16_ccitt_false() against servo_uart_v1 §7.1
// vectors. All four MUST pass before any further firmware code is touched.

#include <stdint.h>
#include <string.h>

#include "unity.h"
#include "crc16.h"

static void test_crc_empty(void) {
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16_ccitt_false(NULL, 0));
}

static void test_crc_check_string(void) {
    // §7.1 row 2 (corrected to 0x29B1 in spec changelog 2026-05-07).
    const uint8_t s[] = "123456789";
    TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16_ccitt_false(s, sizeof s - 1));
}

static void test_crc_move_payload(void) {
    // §7.1 row 3: MOVE axis=0 angle=900 payload.
    const uint8_t move[] = { 0x01, 0x04, 0x00, 0x00, 0x84, 0x03 };
    TEST_ASSERT_EQUAL_HEX16(0x2589, crc16_ccitt_false(move, sizeof move));
}

static void test_crc_ping_header(void) {
    // §7.3 step 2: CRC over CMD||LEN of a PING frame.
    const uint8_t ping[] = { 0x10, 0x00 };
    TEST_ASSERT_EQUAL_HEX16(0x1E7C, crc16_ccitt_false(ping, sizeof ping));
}

static void test_crc_byte_at_a_time_matches_bulk(void) {
    // Sanity: incremental over single bytes vs bulk should match if we ever
    // refactor to a streaming form. Today the function is one-shot, but this
    // guards future changes.
    const uint8_t blob[] = { 0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0xFF, 0x42, 0x13 };
    uint16_t bulk = crc16_ccitt_false(blob, sizeof blob);
    uint16_t step = 0xFFFF;
    for (size_t i = 0; i < sizeof blob; i++) {
        // Inline the algorithm for the cross-check (don't call the SUT here).
        step ^= ((uint16_t)blob[i]) << 8;
        for (int j = 0; j < 8; j++) {
            step = (step & 0x8000u) ? (uint16_t)((step << 1) ^ 0x1021u)
                                    : (uint16_t)(step << 1);
        }
    }
    TEST_ASSERT_EQUAL_HEX16(step, bulk);
}

void run_crc16_tests(void) {
    RUN_TEST(test_crc_empty);
    RUN_TEST(test_crc_check_string);
    RUN_TEST(test_crc_move_payload);
    RUN_TEST(test_crc_ping_header);
    RUN_TEST(test_crc_byte_at_a_time_matches_bulk);
}
