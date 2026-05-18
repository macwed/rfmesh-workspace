// Per-axis servo calibration record. Wire-compatible with the
// CAL_SET / CAL_REPLY payload (servo_uart_v1 §3.6 / §3.7), so the
// firmware can read NVS bytes straight into this struct and the host
// can deserialise replies without an intermediate hop.

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct __attribute__((packed)) {
    uint8_t  axis;
    uint8_t  reserved;
    uint16_t pulse_min_us;
    uint16_t pulse_max_us;
    int16_t  angle_min_deci_deg;
    int16_t  angle_max_deci_deg;
    uint16_t reserved2;
} calibration_t;
_Static_assert(sizeof(calibration_t) == 12, "calibration_t must be 12 bytes");

// Default calibration per §3.7: ±90° mapped to 500..2500 us.
#define CAL_DEFAULT_PULSE_MIN_US        500u
#define CAL_DEFAULT_PULSE_MAX_US       2500u
#define CAL_DEFAULT_ANGLE_MIN_DECI_DEG -900
#define CAL_DEFAULT_ANGLE_MAX_DECI_DEG  900

static inline calibration_t cal_default_for_axis(uint8_t axis) {
    return (calibration_t){
        .axis               = axis,
        .reserved           = 0,
        .pulse_min_us       = CAL_DEFAULT_PULSE_MIN_US,
        .pulse_max_us       = CAL_DEFAULT_PULSE_MAX_US,
        .angle_min_deci_deg = CAL_DEFAULT_ANGLE_MIN_DECI_DEG,
        .angle_max_deci_deg = CAL_DEFAULT_ANGLE_MAX_DECI_DEG,
        .reserved2          = 0,
    };
}

#ifdef __cplusplus
}
#endif
