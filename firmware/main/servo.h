// Servo control: LEDC PWM at 50 Hz on GPIO5, plus pure-math
// pulse<->angle conversion. Spec §3.6.

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "cal_types.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SERVO_AXIS_COUNT  1u  // v1: pan only

// LEDC bits up; if you change either of these, double-check the duty math.
#define SERVO_PWM_FREQ_HZ        50u
#define SERVO_PWM_PERIOD_US      20000u
#define SERVO_PWM_RESOLUTION_BIT 14u
#define SERVO_PWM_MAX_DUTY       ((1u << SERVO_PWM_RESOLUTION_BIT) - 1u)

#define SERVO_PULSE_MIN_HARD_US  400u
#define SERVO_PULSE_MAX_HARD_US  2600u

// --- ESP-IDF-only LEDC bits ----------------------------------------------
// Defined in servo.c; not callable from the host test suite.

void servo_init(void);
void servo_apply_pulse_us(uint8_t axis, uint32_t pulse_us);
void servo_stop(uint8_t axis);

// --- Per-axis state for POS_REPLY ----------------------------------------

typedef struct {
    int16_t commanded_angle_deci_deg;
    int64_t last_move_us;   // esp_timer_get_time() at last MOVE
    bool    ever_moved;     // false until first MOVE; ms_since_move sentinel
} axis_state_t;

axis_state_t *servo_axis_state(uint8_t axis);  // NULL if axis out of range

// --- Pure pulse<->angle math (host-buildable, see servo_math.c) ---------

// Linear interpolation between (pulse_min_us, angle_min_deci_deg) and
// (pulse_max_us, angle_max_deci_deg). Inputs that fall outside [angle_min,
// angle_max] are clamped to the endpoints; `out_clamped` (if non-NULL)
// reports whether clamping occurred.
uint32_t servo_angle_to_pulse_us(int16_t angle_deci_deg,
                                 const calibration_t *cal,
                                 bool *out_clamped);

int16_t  servo_pulse_us_to_angle_deci_deg(uint32_t pulse_us,
                                          const calibration_t *cal);

#ifdef __cplusplus
}
#endif
