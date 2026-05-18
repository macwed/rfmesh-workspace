#include "calibration.h"

#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "cal";

static calibration_t s_cal[SERVO_AXIS_COUNT];

static void axis_key(uint8_t axis, char out[16]) {
    // "axis_<n>" — keys must fit in NVS_KEY_NAME_MAX_SIZE (16 incl. NUL).
    snprintf(out, 16, "axis_%u", (unsigned)axis);
}

bool calibration_validate(uint8_t axis, const calibration_t *cal) {
    if (cal == NULL) return false;
    if (cal->axis != axis) return false;
    if (cal->pulse_min_us < SERVO_PULSE_MIN_HARD_US) return false;
    if (cal->pulse_max_us > SERVO_PULSE_MAX_HARD_US) return false;
    if (cal->pulse_min_us >= cal->pulse_max_us) return false;
    if (cal->angle_min_deci_deg >= cal->angle_max_deci_deg) return false;
    return true;
}

void calibration_load_all(void) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(CAL_NVS_NAMESPACE, NVS_READONLY, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        // Namespace doesn't exist yet — first boot. Install defaults.
        for (uint8_t i = 0; i < SERVO_AXIS_COUNT; i++) {
            s_cal[i] = cal_default_for_axis(i);
        }
        ESP_LOGI(TAG, "no NVS namespace yet; using defaults for all axes");
        return;
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "nvs_open failed (%s); using defaults", esp_err_to_name(err));
        for (uint8_t i = 0; i < SERVO_AXIS_COUNT; i++) {
            s_cal[i] = cal_default_for_axis(i);
        }
        return;
    }

    for (uint8_t i = 0; i < SERVO_AXIS_COUNT; i++) {
        char key[16];
        axis_key(i, key);
        size_t len = sizeof(calibration_t);
        calibration_t buf;
        err = nvs_get_blob(h, key, &buf, &len);
        if (err == ESP_OK && len == sizeof(calibration_t)
            && calibration_validate(i, &buf)) {
            s_cal[i] = buf;
            ESP_LOGI(TAG, "axis %u: loaded from NVS (pulse %u..%u us, angle %d..%d ddeg)",
                     (unsigned)i,
                     (unsigned)buf.pulse_min_us, (unsigned)buf.pulse_max_us,
                     (int)buf.angle_min_deci_deg, (int)buf.angle_max_deci_deg);
        } else {
            s_cal[i] = cal_default_for_axis(i);
            ESP_LOGI(TAG, "axis %u: NVS miss/invalid; using defaults", (unsigned)i);
        }
    }
    nvs_close(h);
}

const calibration_t *calibration_get(uint8_t axis) {
    if (axis >= SERVO_AXIS_COUNT) return NULL;
    return &s_cal[axis];
}

esp_err_t calibration_set(uint8_t axis, const calibration_t *cal) {
    if (axis >= SERVO_AXIS_COUNT) return ESP_ERR_INVALID_ARG;
    if (!calibration_validate(axis, cal)) return ESP_ERR_INVALID_ARG;
    s_cal[axis] = *cal;
    return ESP_OK;
}

static esp_err_t persist_one(nvs_handle_t h, uint8_t axis) {
    char key[16];
    axis_key(axis, key);
    return nvs_set_blob(h, key, &s_cal[axis], sizeof(calibration_t));
}

esp_err_t calibration_persist(uint8_t axis) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(CAL_NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open(rw) failed: %s", esp_err_to_name(err));
        return ESP_FAIL;
    }

    if (axis == CAL_PERSIST_ALL) {
        for (uint8_t i = 0; i < SERVO_AXIS_COUNT; i++) {
            err = persist_one(h, i);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "persist axis %u failed: %s", (unsigned)i, esp_err_to_name(err));
                nvs_close(h);
                return ESP_FAIL;
            }
        }
    } else if (axis < SERVO_AXIS_COUNT) {
        err = persist_one(h, axis);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "persist axis %u failed: %s", (unsigned)axis, esp_err_to_name(err));
            nvs_close(h);
            return ESP_FAIL;
        }
    } else {
        nvs_close(h);
        return ESP_ERR_INVALID_ARG;
    }

    err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_commit failed: %s", esp_err_to_name(err));
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "persisted axis=%u", (unsigned)axis);
    return ESP_OK;
}
