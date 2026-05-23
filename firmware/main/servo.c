// LEDC driver for one or more hobby servos. ESP-IDF only — pure
// pulse<->angle math is in servo_math.c so it stays host-testable.

#include "servo.h"

#include "driver/ledc.h"
#include "esp_timer.h"
#include "esp_log.h"

static const char *TAG = "servo";

// GPIO assignments per servo_uart_v1 §9.
// ESP32-C6: GPIO18 is a regular GPIO (not strapping, not JTAG-shared,
// not USB). Picked over GPIO5 because C6 GPIO4/5 are JTAG MTMS/MTCK
// strap pins — safer to keep servo line off them at reset.
static const int s_axis_gpio[SERVO_AXIS_COUNT] = { 9 };

static const ledc_channel_t s_axis_channel[SERVO_AXIS_COUNT] = {
    LEDC_CHANNEL_0,
};

static axis_state_t s_axes[SERVO_AXIS_COUNT];

void servo_init(void) {
    const ledc_timer_config_t timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_14_BIT,
        .timer_num       = LEDC_TIMER_0,
        .freq_hz         = SERVO_PWM_FREQ_HZ,
        // Pin XTAL (40 MHz) explicitly. On C6, LEDC_AUTO_CLK in low-speed
        // mode may pick RC_FAST (~17.5 MHz ±5%) — uncalibrated, would let
        // 50 Hz frame rate drift ±5%. XTAL gives deterministic timing
        // across boards and removes one source of calibration-portability
        // risk. MG996R holds position from pulse width, not frame rate,
        // so the angle output is unaffected either way — but pinning the
        // clock keeps L1 sigma honesty board-independent (B2).
        .clk_cfg         = LEDC_USE_XTAL_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));

    for (uint8_t i = 0; i < SERVO_AXIS_COUNT; i++) {
        const ledc_channel_config_t ch = {
            .gpio_num   = s_axis_gpio[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel    = s_axis_channel[i],
            .intr_type  = LEDC_INTR_DISABLE,
            .timer_sel  = LEDC_TIMER_0,
            .duty       = 0,
            .hpoint     = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&ch));
        s_axes[i] = (axis_state_t){
            .commanded_angle_deci_deg = 0,
            .last_move_us             = 0,
            .ever_moved               = false,
        };
    }
    ESP_LOGI(TAG, "LEDC ready: %u Hz, %u-bit, axis0=GPIO%d",
             (unsigned)SERVO_PWM_FREQ_HZ,
             (unsigned)SERVO_PWM_RESOLUTION_BIT,
             s_axis_gpio[0]);
}

void servo_apply_pulse_us(uint8_t axis, uint32_t pulse_us) {
    if (axis >= SERVO_AXIS_COUNT) return;
    if (pulse_us > SERVO_PWM_PERIOD_US) pulse_us = SERVO_PWM_PERIOD_US;

    // duty = pulse_us * MAX_DUTY / period. Use 64-bit to avoid overflow:
    // 20000 * 16383 = 3.27e8, still in 32-bit range, but be defensive.
    const uint64_t duty = ((uint64_t)pulse_us * (uint64_t)SERVO_PWM_MAX_DUTY)
                          / (uint64_t)SERVO_PWM_PERIOD_US;
    const ledc_channel_t ch = s_axis_channel[axis];
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, ch, (uint32_t)duty));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, ch));
}

void servo_stop(uint8_t axis) {
    if (axis >= SERVO_AXIS_COUNT) return;
    const ledc_channel_t ch = s_axis_channel[axis];
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, ch, 0));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, ch));
}

axis_state_t *servo_axis_state(uint8_t axis) {
    if (axis >= SERVO_AXIS_COUNT) return NULL;
    return &s_axes[axis];
}
