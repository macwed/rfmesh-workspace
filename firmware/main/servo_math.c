// Pure pulse<->angle math, broken out from servo.c so the host test
// suite can build it without ESP-IDF.

#include "servo.h"

#include <stdint.h>

uint32_t servo_angle_to_pulse_us(int16_t angle_deci_deg,
                                 const calibration_t *cal,
                                 bool *out_clamped) {
    bool clamped = false;
    int32_t a = angle_deci_deg;
    if (a < cal->angle_min_deci_deg) { a = cal->angle_min_deci_deg; clamped = true; }
    if (a > cal->angle_max_deci_deg) { a = cal->angle_max_deci_deg; clamped = true; }
    if (out_clamped) *out_clamped = clamped;

    const int32_t a_min = cal->angle_min_deci_deg;
    const int32_t a_max = cal->angle_max_deci_deg;
    const int32_t p_min = (int32_t)cal->pulse_min_us;
    const int32_t p_max = (int32_t)cal->pulse_max_us;
    const int32_t span_a = a_max - a_min;
    if (span_a == 0) {
        return (uint32_t)p_min; // degenerate but defined
    }
    // Round-half-to-even-ish via +span/2 before dividing for non-negative.
    const int32_t span_p = p_max - p_min;
    const int32_t num    = (a - a_min) * span_p;
    const int32_t bias   = (num >= 0) ? (span_a / 2) : -(span_a / 2);
    const int32_t pulse  = p_min + (num + bias) / span_a;
    return (uint32_t)pulse;
}

int16_t servo_pulse_us_to_angle_deci_deg(uint32_t pulse_us,
                                         const calibration_t *cal) {
    int32_t p = (int32_t)pulse_us;
    if (p < (int32_t)cal->pulse_min_us) p = cal->pulse_min_us;
    if (p > (int32_t)cal->pulse_max_us) p = cal->pulse_max_us;
    const int32_t a_min = cal->angle_min_deci_deg;
    const int32_t a_max = cal->angle_max_deci_deg;
    const int32_t p_min = cal->pulse_min_us;
    const int32_t p_max = cal->pulse_max_us;
    const int32_t span_p = p_max - p_min;
    if (span_p == 0) {
        return (int16_t)a_min;
    }
    const int32_t span_a = a_max - a_min;
    const int32_t num    = (p - p_min) * span_a;
    const int32_t bias   = (num >= 0) ? (span_p / 2) : -(span_p / 2);
    return (int16_t)(a_min + (num + bias) / span_p);
}
