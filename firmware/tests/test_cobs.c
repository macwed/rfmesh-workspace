// Host-side tests for COBS encode/decode against servo_uart_v1 §7.2
// vectors and a randomised round-trip property test.

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "unity.h"
#include "cobs.h"

static void check_encode(const uint8_t *src, size_t src_len,
                         const uint8_t *expected, size_t expected_len) {
    uint8_t buf[512];
    size_t n = cobs_encode(src, src_len, buf);
    TEST_ASSERT_EQUAL_size_t(expected_len, n);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, buf, expected_len);
}

static void test_cobs_spec_vectors(void) {
    // §7.2 row 1: 0x01 -> 0x02 0x01
    static const uint8_t in1[]  = { 0x01 };
    static const uint8_t out1[] = { 0x02, 0x01 };
    check_encode(in1, sizeof in1, out1, sizeof out1);

    // §7.2 row 2: 0x00 -> 0x01 0x01
    static const uint8_t in2[]  = { 0x00 };
    static const uint8_t out2[] = { 0x01, 0x01 };
    check_encode(in2, sizeof in2, out2, sizeof out2);

    // §7.2 row 3: 0x01 0x00 0x02 -> 0x02 0x01 0x02 0x02
    static const uint8_t in3[]  = { 0x01, 0x00, 0x02 };
    static const uint8_t out3[] = { 0x02, 0x01, 0x02, 0x02 };
    check_encode(in3, sizeof in3, out3, sizeof out3);
}

static void test_cobs_empty_input(void) {
    // Empty plaintext encodes to a single 0x01 code byte (a "run of 0
    // non-zero bytes" group).
    uint8_t buf[4] = {0};
    size_t n = cobs_encode(NULL, 0, buf);
    TEST_ASSERT_EQUAL_size_t(1, n);
    TEST_ASSERT_EQUAL_HEX8(0x01, buf[0]);
}

static void test_cobs_decode_spec_vectors(void) {
    static const uint8_t enc1[] = { 0x02, 0x01 };
    static const uint8_t pl1[]  = { 0x01 };
    static const uint8_t enc2[] = { 0x01, 0x01 };
    static const uint8_t pl2[]  = { 0x00 };
    static const uint8_t enc3[] = { 0x02, 0x01, 0x02, 0x02 };
    static const uint8_t pl3[]  = { 0x01, 0x00, 0x02 };
    static const struct {
        const uint8_t *enc; size_t enc_len;
        const uint8_t *plain; size_t plain_len;
    } cases[] = {
        { enc1, sizeof enc1, pl1, sizeof pl1 },
        { enc2, sizeof enc2, pl2, sizeof pl2 },
        { enc3, sizeof enc3, pl3, sizeof pl3 },
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        uint8_t out[8];
        size_t n = cobs_decode(cases[i].enc, cases[i].enc_len, out);
        TEST_ASSERT_EQUAL_size_t(cases[i].plain_len, n);
        TEST_ASSERT_EQUAL_HEX8_ARRAY(cases[i].plain, out, cases[i].plain_len);
    }
}

static void test_cobs_roundtrip_random(void) {
    // Deterministic PRNG so failures are reproducible.
    unsigned seed = 0xC0B5u;
    uint8_t plain[300];
    uint8_t enc[COBS_ENCODE_MAX_LEN(sizeof plain)];
    uint8_t dec[sizeof plain];

    for (int trial = 0; trial < 200; trial++) {
        size_t len = (rand_r(&seed) % 254) + 1; // 1..254
        for (size_t i = 0; i < len; i++) {
            plain[i] = (uint8_t)rand_r(&seed);
        }
        size_t enc_len = cobs_encode(plain, len, enc);
        // Encoded payload must contain no zero bytes (delimiter is added
        // by the caller, not by cobs_encode).
        for (size_t i = 0; i < enc_len; i++) {
            TEST_ASSERT_NOT_EQUAL_HEX8(0x00, enc[i]);
        }
        size_t dec_len = cobs_decode(enc, enc_len, dec);
        TEST_ASSERT_EQUAL_size_t(len, dec_len);
        TEST_ASSERT_EQUAL_HEX8_ARRAY(plain, dec, len);
    }
}

static void test_cobs_roundtrip_long_run(void) {
    // Buffer with > 254 consecutive non-zero bytes exercises the 0xFF
    // run cap path.
    uint8_t plain[600];
    for (size_t i = 0; i < sizeof plain; i++) {
        plain[i] = (uint8_t)((i % 0xFE) + 1); // never zero
    }
    uint8_t enc[COBS_ENCODE_MAX_LEN(sizeof plain)];
    uint8_t dec[sizeof plain];
    size_t enc_len = cobs_encode(plain, sizeof plain, enc);
    for (size_t i = 0; i < enc_len; i++) {
        TEST_ASSERT_NOT_EQUAL_HEX8(0x00, enc[i]);
    }
    size_t dec_len = cobs_decode(enc, enc_len, dec);
    TEST_ASSERT_EQUAL_size_t(sizeof plain, dec_len);
    TEST_ASSERT_EQUAL_HEX8_ARRAY(plain, dec, sizeof plain);
}

static void test_cobs_decode_rejects_zero_byte(void) {
    // A 0x00 inside the input is illegal (the caller is supposed to
    // strip the terminator first).
    uint8_t bad[] = { 0x02, 0x00, 0x01 };
    uint8_t out[8];
    TEST_ASSERT_EQUAL_size_t(0, cobs_decode(bad, sizeof bad, out));
}

static void test_cobs_decode_rejects_truncated(void) {
    // Code byte 0x05 promises 4 data bytes following, but we only have 2.
    uint8_t bad[] = { 0x05, 0x01, 0x02 };
    uint8_t out[8];
    TEST_ASSERT_EQUAL_size_t(0, cobs_decode(bad, sizeof bad, out));
}

void run_cobs_tests(void) {
    RUN_TEST(test_cobs_spec_vectors);
    RUN_TEST(test_cobs_empty_input);
    RUN_TEST(test_cobs_decode_spec_vectors);
    RUN_TEST(test_cobs_roundtrip_random);
    RUN_TEST(test_cobs_roundtrip_long_run);
    RUN_TEST(test_cobs_decode_rejects_zero_byte);
    RUN_TEST(test_cobs_decode_rejects_truncated);
}
