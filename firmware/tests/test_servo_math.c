// Pure-math smoke tests for servo_angle_to_pulse_us / inverse.
// Not in the ticket's required suite list, but the linear map is easy
// to get wrong silently — keeping a few sanity vectors is cheap.

#include <stdint.h>
#include <stdbool.h>

#include "unity.h"
#include "cal_types.h"
#include "servo.h"

static const calibration_t cal_default = {
    .axis               = 0,
    .reserved           = 0,
    .pulse_min_us       = CAL_DEFAULT_PULSE_MIN_US,
    .pulse_max_us       = CAL_DEFAULT_PULSE_MAX_US,
    .angle_min_deci_deg = CAL_DEFAULT_ANGLE_MIN_DECI_DEG,
    .angle_max_deci_deg = CAL_DEFAULT_ANGLE_MAX_DECI_DEG,
    .reserved2          = 0,
};

static void test_angle_endpoints(void) {
    bool clamped;
    TEST_ASSERT_EQUAL_UINT32(500,  servo_angle_to_pulse_us(-900, &cal_default, &clamped));
    TEST_ASSERT_FALSE(clamped);
    TEST_ASSERT_EQUAL_UINT32(2500, servo_angle_to_pulse_us( 900, &cal_default, &clamped));
    TEST_ASSERT_FALSE(clamped);
    TEST_ASSERT_EQUAL_UINT32(1500, servo_angle_to_pulse_us(   0, &cal_default, &clamped));
    TEST_ASSERT_FALSE(clamped);
}

static void test_angle_clamps_below(void) {
    bool clamped;
    TEST_ASSERT_EQUAL_UINT32(500, servo_angle_to_pulse_us(-2000, &cal_default, &clamped));
    TEST_ASSERT_TRUE(clamped);
}

static void test_angle_clamps_above(void) {
    bool clamped;
    TEST_ASSERT_EQUAL_UINT32(2500, servo_angle_to_pulse_us(2000, &cal_default, &clamped));
    TEST_ASSERT_TRUE(clamped);
}

static void test_inverse_round_trip(void) {
    // For every multiple of 100 deci-deg in range, angle->pulse->angle
    // must round-trip within ±1 deci-deg.
    for (int16_t a = -900; a <= 900; a += 100) {
        bool clamped = false;
        uint32_t p = servo_angle_to_pulse_us(a, &cal_default, &clamped);
        TEST_ASSERT_FALSE(clamped);
        int16_t back = servo_pulse_us_to_angle_deci_deg(p, &cal_default);
        TEST_ASSERT_INT_WITHIN(1, a, back);
    }
}

static void test_asymmetric_calibration(void) {
    // Non-symmetric angle range exercises the full linear map.
    const calibration_t cal = {
        .axis               = 0,
        .pulse_min_us       = 600,
        .pulse_max_us       = 2400,
        .angle_min_deci_deg = -450,   // -45.0 deg
        .angle_max_deci_deg = 1350,   // 135.0 deg
    };
    bool clamped = false;
    TEST_ASSERT_EQUAL_UINT32(600,  servo_angle_to_pulse_us(-450, &cal, &clamped));
    TEST_ASSERT_EQUAL_UINT32(2400, servo_angle_to_pulse_us(1350, &cal, &clamped));
    // Midpoint of angle range: -45 + (135 - -45)/2 = 45 deg = 450 deci.
    // Expected pulse: 600 + (2400-600)/2 = 1500.
    TEST_ASSERT_EQUAL_UINT32(1500, servo_angle_to_pulse_us(450, &cal, &clamped));
}

static void test_calibration_struct_size(void) {
    // Wire format depends on this; the static_assert in cal_types.h is
    // the firm guarantee — this test gives a clearer failure message if
    // it ever flips.
    TEST_ASSERT_EQUAL_size_t(12, sizeof(calibration_t));
}

void run_servo_math_tests(void) {
    RUN_TEST(test_angle_endpoints);
    RUN_TEST(test_angle_clamps_below);
    RUN_TEST(test_angle_clamps_above);
    RUN_TEST(test_inverse_round_trip);
    RUN_TEST(test_asymmetric_calibration);
    RUN_TEST(test_calibration_struct_size);
}
