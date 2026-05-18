// Per-axis calibration: RAM cache + NVS persistence.
// Blob layout matches calibration_t in cal_types.h, byte-for-byte (also
// the wire payload of CAL_SET / CAL_REPLY — see servo_uart_v1 §3.6, §8).
//
// On boot: try to read each axis from NVS; if absent, install §3.7
// defaults in RAM. NEVER auto-write defaults to NVS — flash wear hygiene.

#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "cal_types.h"
#include "servo.h"  // SERVO_AXIS_COUNT

#ifdef __cplusplus
extern "C" {
#endif

#define CAL_NVS_NAMESPACE  "servo_cal"
#define CAL_PERSIST_ALL    0xFFu

void                 calibration_load_all(void);
const calibration_t *calibration_get(uint8_t axis);

// Validate per §3.6: pulse_min/max in [400, 2600], pulse_min < pulse_max,
// angle_min < angle_max, axis matches request. Pure function — exposed
// so command-dispatch can return ERR_BAD_CALIBRATION cleanly.
bool                 calibration_validate(uint8_t axis, const calibration_t *cal);

// Update RAM cache only. Returns ESP_OK on success, ESP_ERR_INVALID_ARG
// on validation failure.
esp_err_t            calibration_set(uint8_t axis, const calibration_t *cal);

// Write current RAM cache to NVS. axis == CAL_PERSIST_ALL writes all axes.
// Returns ESP_OK on success, ESP_FAIL on NVS error.
esp_err_t            calibration_persist(uint8_t axis);

#ifdef __cplusplus
}
#endif
