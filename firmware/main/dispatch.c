#include "dispatch.h"

#include <string.h>

#include "esp_timer.h"
#include "esp_log.h"

#include "calibration.h"
#include "identity.h"
#include "servo.h"

static const char *TAG = "disp";

// --- reply builders ------------------------------------------------------

static size_t build_ack(uint8_t original_cmd, uint8_t *wire_out) {
    return proto_serialise(CMD_ACK, &original_cmd, 1, wire_out);
}

static size_t build_error(uint8_t original_cmd, uint8_t err_code, uint8_t *wire_out) {
    const uint8_t payload[2] = { original_cmd, err_code };
    return proto_serialise(CMD_ERROR, payload, sizeof payload, wire_out);
}

static size_t build_pong(uint8_t *wire_out) {
    uint8_t payload[16] = {0};
    payload[0]  = 0x01;                  // proto_version
    payload[1]  = FW_VERSION_MAJOR;
    payload[2]  = FW_VERSION_MINOR;
    payload[3]  = FW_VERSION_PATCH;
    memcpy(&payload[4], FW_GIT_SHORT_SHA, 8);  // 8 ASCII chars, no NUL
    payload[12] = SERVO_AXIS_COUNT;
    // bytes 13..15 reserved 0
    return proto_serialise(CMD_PONG, payload, sizeof payload, wire_out);
}

size_t dispatch_build_pong(uint8_t *wire_out) {
    return build_pong(wire_out);
}

// --- handlers ------------------------------------------------------------

static size_t handle_move(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    if (len != 4) {
        return build_error(CMD_MOVE, ERR_INTERNAL, wire_out);
    }
    const uint8_t axis = p[0];
    if (axis >= SERVO_AXIS_COUNT) {
        return build_error(CMD_MOVE, ERR_NO_SUCH_AXIS, wire_out);
    }
    const int16_t angle_dd = (int16_t)((uint16_t)p[2] | ((uint16_t)p[3] << 8));
    const calibration_t *cal = calibration_get(axis);
    if (cal == NULL) {
        return build_error(CMD_MOVE, ERR_INTERNAL, wire_out);
    }
    if (angle_dd < cal->angle_min_deci_deg || angle_dd > cal->angle_max_deci_deg) {
        return build_error(CMD_MOVE, ERR_ANGLE_OUT_OF_RANGE, wire_out);
    }

    bool clamped = false;
    const uint32_t pulse_us = servo_angle_to_pulse_us(angle_dd, cal, &clamped);
    servo_apply_pulse_us(axis, pulse_us);

    axis_state_t *st = servo_axis_state(axis);
    if (st != NULL) {
        st->commanded_angle_deci_deg = angle_dd;
        st->last_move_us = esp_timer_get_time();
        st->ever_moved = true;
    }
    return build_ack(CMD_MOVE, wire_out);
}

static size_t handle_pos_query(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    if (len != 1) {
        return build_error(CMD_POS_QUERY, ERR_INTERNAL, wire_out);
    }
    const uint8_t axis = p[0];
    if (axis >= SERVO_AXIS_COUNT) {
        return build_error(CMD_POS_QUERY, ERR_NO_SUCH_AXIS, wire_out);
    }
    axis_state_t *st = servo_axis_state(axis);

    uint8_t reply[8] = {0};
    reply[0] = axis;
    reply[1] = 0;
    int16_t angle_dd = 0;
    uint32_t ms_since = 0xFFFFFFFFu;
    if (st != NULL && st->ever_moved) {
        angle_dd = st->commanded_angle_deci_deg;
        const int64_t now_us = esp_timer_get_time();
        const int64_t delta_ms = (now_us - st->last_move_us) / 1000;
        if (delta_ms < 0) {
            ms_since = 0;
        } else if (delta_ms > 0xFFFFFFFEll) {
            // Clamp one below the §3.4 "never moved" sentinel (0xFFFFFFFF):
            // after ~49.7 days the real elapsed time would otherwise alias
            // into the sentinel and the host would distrust the position.
            ms_since = 0xFFFFFFFEu;
        } else {
            ms_since = (uint32_t)delta_ms;
        }
    }
    reply[2] = (uint8_t)(angle_dd & 0xFF);
    reply[3] = (uint8_t)((angle_dd >> 8) & 0xFF);
    reply[4] = (uint8_t)(ms_since & 0xFF);
    reply[5] = (uint8_t)((ms_since >> 8) & 0xFF);
    reply[6] = (uint8_t)((ms_since >> 16) & 0xFF);
    reply[7] = (uint8_t)((ms_since >> 24) & 0xFF);

    return proto_serialise(CMD_POS_REPLY, reply, sizeof reply, wire_out);
}

static size_t handle_stop(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    if (len != 1) {
        return build_error(CMD_STOP, ERR_INTERNAL, wire_out);
    }
    const uint8_t axis = p[0];
    if (axis >= SERVO_AXIS_COUNT) {
        return build_error(CMD_STOP, ERR_NO_SUCH_AXIS, wire_out);
    }
    servo_stop(axis);
    return build_ack(CMD_STOP, wire_out);
}

static size_t handle_cal_set(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    if (len != sizeof(calibration_t)) {
        return build_error(CMD_CAL_SET, ERR_BAD_CALIBRATION, wire_out);
    }
    calibration_t cal;
    memcpy(&cal, p, sizeof(calibration_t));
    if (cal.axis >= SERVO_AXIS_COUNT) {
        return build_error(CMD_CAL_SET, ERR_NO_SUCH_AXIS, wire_out);
    }
    if (calibration_set(cal.axis, &cal) != ESP_OK) {
        return build_error(CMD_CAL_SET, ERR_BAD_CALIBRATION, wire_out);
    }
    return build_ack(CMD_CAL_SET, wire_out);
}

static size_t handle_cal_query(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    if (len != 1) {
        return build_error(CMD_CAL_QUERY, ERR_INTERNAL, wire_out);
    }
    const uint8_t axis = p[0];
    if (axis >= SERVO_AXIS_COUNT) {
        return build_error(CMD_CAL_QUERY, ERR_NO_SUCH_AXIS, wire_out);
    }
    const calibration_t *cal = calibration_get(axis);
    if (cal == NULL) {
        return build_error(CMD_CAL_QUERY, ERR_INTERNAL, wire_out);
    }
    return proto_serialise(CMD_CAL_REPLY, (const uint8_t *)cal,
                           sizeof(calibration_t), wire_out);
}

static size_t handle_cal_persist(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    if (len != 1) {
        return build_error(CMD_CAL_PERSIST, ERR_INTERNAL, wire_out);
    }
    const uint8_t axis = p[0];
    if (axis != CAL_PERSIST_ALL && axis >= SERVO_AXIS_COUNT) {
        return build_error(CMD_CAL_PERSIST, ERR_NO_SUCH_AXIS, wire_out);
    }
    if (calibration_persist(axis) != ESP_OK) {
        return build_error(CMD_CAL_PERSIST, ERR_NVS_FAIL, wire_out);
    }
    return build_ack(CMD_CAL_PERSIST, wire_out);
}

static size_t handle_ping(const uint8_t *p, uint8_t len, uint8_t *wire_out) {
    (void)p;
    if (len != 0) {
        return build_error(CMD_PING, ERR_INTERNAL, wire_out);
    }
    return build_pong(wire_out);
}

static size_t handle_reset(const uint8_t *p, uint8_t len, uint8_t *wire_out,
                           post_action_t *post_action) {
    (void)p;
    if (len != 0) {
        return build_error(CMD_RESET, ERR_INTERNAL, wire_out);
    }
    if (post_action != NULL) {
        *post_action = POST_ACTION_RESET;
    }
    return build_ack(CMD_RESET, wire_out);
}

// --- public entry point --------------------------------------------------

size_t dispatch_handle(const uint8_t *frame, size_t frame_len,
                       uint8_t *wire_out, post_action_t *post_action) {
    if (post_action != NULL) {
        *post_action = POST_ACTION_NONE;
    }
    if (frame_len < 2) {
        return 0; // malformed; parser should not have produced this
    }
    const uint8_t cmd = frame[0];
    const uint8_t len = frame[1];
    const uint8_t *payload = (len > 0) ? &frame[2] : NULL;

    switch (cmd) {
        case CMD_MOVE:        return handle_move(payload, len, wire_out);
        case CMD_POS_QUERY:   return handle_pos_query(payload, len, wire_out);
        case CMD_STOP:        return handle_stop(payload, len, wire_out);
        case CMD_CAL_SET:     return handle_cal_set(payload, len, wire_out);
        case CMD_CAL_QUERY:   return handle_cal_query(payload, len, wire_out);
        case CMD_CAL_PERSIST: return handle_cal_persist(payload, len, wire_out);
        case CMD_PING:        return handle_ping(payload, len, wire_out);
        case CMD_RESET:       return handle_reset(payload, len, wire_out, post_action);
        default:
            ESP_LOGW(TAG, "unknown CMD 0x%02X", cmd);
            return build_error(cmd, ERR_UNKNOWN_CMD, wire_out);
    }
}
