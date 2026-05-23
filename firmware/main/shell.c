#include "shell.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_vfs_usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "linenoise/linenoise.h"

#include "calibration.h"
#include "dispatch.h"
#include "identity.h"
#include "servo.h"

static const char *TAG = "shell";

static volatile bool s_exit_requested = false;

// ---- per-command handlers (text I/O only — no COBS, no CRC) ------------

static int cmd_move(int argc, char **argv) {
    if (argc != 3) {
        printf("usage: move <axis> <angle_deg>\r\n");
        return 1;
    }
    const int axis_i = atoi(argv[1]);
    if (axis_i < 0 || axis_i >= SERVO_AXIS_COUNT) {
        printf("error: no such axis %d\r\n", axis_i);
        return 1;
    }
    const float angle = strtof(argv[2], NULL);
    const int16_t angle_dd = (int16_t)((angle >= 0) ? (angle * 10.0f + 0.5f)
                                                    : (angle * 10.0f - 0.5f));
    const calibration_t *cal = calibration_get((uint8_t)axis_i);
    if (cal == NULL) {
        printf("error: calibration unavailable\r\n");
        return 1;
    }
    if (angle_dd < cal->angle_min_deci_deg || angle_dd > cal->angle_max_deci_deg) {
        printf("error: angle %.1f out of range [%.1f, %.1f]\r\n",
               (double)angle,
               cal->angle_min_deci_deg / 10.0,
               cal->angle_max_deci_deg / 10.0);
        return 1;
    }
    bool clamped = false;
    const uint32_t pulse_us = servo_angle_to_pulse_us(angle_dd, cal, &clamped);
    servo_apply_pulse_us((uint8_t)axis_i, pulse_us);

    axis_state_t *st = servo_axis_state((uint8_t)axis_i);
    if (st != NULL) {
        st->commanded_angle_deci_deg = angle_dd;
        st->last_move_us = esp_timer_get_time();
        st->ever_moved = true;
    }
    printf("ok: axis %d -> %.1f deg (pulse %u us)\r\n",
           axis_i, (double)angle, (unsigned)pulse_us);
    return 0;
}

static int cmd_pos(int argc, char **argv) {
    if (argc != 2) { printf("usage: pos <axis>\r\n"); return 1; }
    const int axis_i = atoi(argv[1]);
    if (axis_i < 0 || axis_i >= SERVO_AXIS_COUNT) {
        printf("error: no such axis %d\r\n", axis_i);
        return 1;
    }
    axis_state_t *st = servo_axis_state((uint8_t)axis_i);
    if (st == NULL || !st->ever_moved) {
        printf("axis %d: never moved\r\n", axis_i);
        return 0;
    }
    const int64_t ms = (esp_timer_get_time() - st->last_move_us) / 1000;
    printf("axis %d: commanded=%.1f deg, %lld ms since last move\r\n",
           axis_i, st->commanded_angle_deci_deg / 10.0, (long long)ms);
    return 0;
}

static int cmd_stop(int argc, char **argv) {
    if (argc != 2) { printf("usage: stop <axis>\r\n"); return 1; }
    const int axis_i = atoi(argv[1]);
    if (axis_i < 0 || axis_i >= SERVO_AXIS_COUNT) {
        printf("error: no such axis %d\r\n", axis_i);
        return 1;
    }
    servo_stop((uint8_t)axis_i);
    printf("ok: axis %d stopped (PWM disabled)\r\n", axis_i);
    return 0;
}

static int cmd_cal(int argc, char **argv) {
    if (argc != 6) {
        printf("usage: cal <axis> <pmin_us> <pmax_us> <amin_deg> <amax_deg>\r\n");
        return 1;
    }
    const int axis_i = atoi(argv[1]);
    if (axis_i < 0 || axis_i >= SERVO_AXIS_COUNT) {
        printf("error: no such axis %d\r\n", axis_i);
        return 1;
    }
    calibration_t cal = {
        .axis               = (uint8_t)axis_i,
        .reserved           = 0,
        .pulse_min_us       = (uint16_t)atoi(argv[2]),
        .pulse_max_us       = (uint16_t)atoi(argv[3]),
        .angle_min_deci_deg = (int16_t)(strtof(argv[4], NULL) * 10.0f),
        .angle_max_deci_deg = (int16_t)(strtof(argv[5], NULL) * 10.0f),
        .reserved2          = 0,
    };
    if (calibration_set((uint8_t)axis_i, &cal) != ESP_OK) {
        printf("error: calibration validation failed (see §3.6)\r\n");
        return 1;
    }
    printf("ok: axis %d cal updated in RAM (use cal-save to persist)\r\n", axis_i);
    return 0;
}

static int cmd_cal_show(int argc, char **argv) {
    if (argc != 2) { printf("usage: cal-show <axis>\r\n"); return 1; }
    const int axis_i = atoi(argv[1]);
    const calibration_t *cal = calibration_get((uint8_t)axis_i);
    if (cal == NULL) {
        printf("error: no such axis %d\r\n", axis_i);
        return 1;
    }
    printf("axis %d: pulse %u..%u us, angle %.1f..%.1f deg\r\n",
           axis_i,
           (unsigned)cal->pulse_min_us, (unsigned)cal->pulse_max_us,
           cal->angle_min_deci_deg / 10.0, cal->angle_max_deci_deg / 10.0);
    return 0;
}

static int cmd_cal_save(int argc, char **argv) {
    uint8_t axis = CAL_PERSIST_ALL;
    if (argc == 2) {
        if (strcmp(argv[1], "all") == 0) {
            axis = CAL_PERSIST_ALL;
        } else {
            const int axis_i = atoi(argv[1]);
            if (axis_i < 0 || axis_i >= SERVO_AXIS_COUNT) {
                printf("error: no such axis %d\r\n", axis_i);
                return 1;
            }
            axis = (uint8_t)axis_i;
        }
    } else if (argc != 1) {
        printf("usage: cal-save [axis|all]\r\n");
        return 1;
    }
    if (calibration_persist(axis) != ESP_OK) {
        printf("error: NVS write failed\r\n");
        return 1;
    }
    printf("ok: persisted %s\r\n",
           (axis == CAL_PERSIST_ALL) ? "all axes" : "single axis");
    return 0;
}

static int cmd_ping(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("pong: proto v1, fw %u.%u.%u, sha %.8s, axes %u\r\n",
           FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH,
           FW_GIT_SHORT_SHA, (unsigned)SERVO_AXIS_COUNT);
    return 0;
}

static int cmd_reset(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("resetting...\r\n");
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
    return 0;  // unreachable
}

static int cmd_proto(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("entering protocol mode\r\n");
    fflush(stdout);
    s_exit_requested = true;
    return 0;
}

// ---- public entry --------------------------------------------------------

static void register_cmds(void) {
    static const esp_console_cmd_t cmds[] = {
        { .command = "move",     .help = "move <axis> <angle_deg>",                 .func = cmd_move     },
        { .command = "pos",      .help = "pos <axis>",                              .func = cmd_pos      },
        { .command = "stop",     .help = "stop <axis> (servo goes limp)",          .func = cmd_stop     },
        { .command = "cal",      .help = "cal <axis> <pmin> <pmax> <amin> <amax>", .func = cmd_cal      },
        { .command = "cal-show", .help = "cal-show <axis>",                         .func = cmd_cal_show },
        { .command = "cal-save", .help = "cal-save [axis|all]",                     .func = cmd_cal_save },
        { .command = "ping",     .help = "ping (firmware identity)",                .func = cmd_ping     },
        { .command = "reset",    .help = "reset (esp_restart)",                     .func = cmd_reset    },
        { .command = "proto",    .help = "proto (leave shell, return to protocol)", .func = cmd_proto    },
    };
    for (size_t i = 0; i < sizeof cmds / sizeof cmds[0]; i++) {
        ESP_ERROR_CHECK(esp_console_cmd_register(&cmds[i]));
    }
    ESP_ERROR_CHECK(esp_console_register_help_command());
}

void shell_run(void) {
    s_exit_requested = false;

    // ESP32-C6 has the USB-Serial-JTAG peripheral, so the IDF helper
    // esp_console_new_repl_usb_serial_jtag fits cleanly: it installs its
    // own VFS over the JTAG driver, wires linenoise to it, and returns a
    // REPL handle. This is the same pattern the original C3 salvage used.
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    repl_config.prompt              = "servo> ";
    repl_config.max_cmdline_length  = 128;
    esp_console_dev_usb_serial_jtag_config_t hw_config =
        ESP_CONSOLE_DEV_USB_SERIAL_JTAG_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_serial_jtag(&hw_config,
                                                         &repl_config, &repl));

    linenoiseSetMultiLine(1);
    linenoiseHistorySetMaxLen(16);  // RAM only; spec §6 — no NVS history

    register_cmds();

    ESP_LOGI(TAG, "linenoise shell active; type `proto` to return to protocol mode");

    ESP_ERROR_CHECK(esp_console_start_repl(repl));

    while (!s_exit_requested) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    // cmd_proto sets s_exit_requested = true from inside the REPL task.
    // We must tear the REPL down BEFORE returning, otherwise its task
    // keeps reading USB-Serial-JTAG and races protocol_main_loop for
    // incoming bytes. repl->del(repl) is the canonical cleanup per IDF
    // v5.4 esp_console_start_repl docs.
    if (repl != NULL && repl->del != NULL) {
        ESP_ERROR_CHECK(repl->del(repl));
    }
    ESP_LOGI(TAG, "shell exited");
}
