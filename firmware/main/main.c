// app_main + mode select per servo_uart_v1 §6 / spec §3.10.
//
// Boot path (ESP32-C6, USB-Serial-JTAG peripheral):
//   1. nvs_flash_init, servo_init, calibration_load_all
//   2. usb_serial_jtag_driver_install -> RX/TX ringbufs
//   3. stdio (printf, ESP_LOG) is already routed to USB-Serial-JTAG by
//      CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG in sdkconfig.defaults
//   4. wait up to 1 s for either three consecutive ENTERs (-> linenoise)
//      or any non-line-ending byte (-> protocol; stashed for re-feed)
//   5. linenoise: shell_run uses esp_console_new_repl_usb_serial_jtag
//   6. send unsolicited PONG (§4.5)
//   7. protocol_main_loop: feed bytes -> proto_rx -> dispatch -> reply
//
// Wire protocol (servo_uart_v1) is UNCHANGED. The USB transport peripheral
// moves from ESP32-S2's USB-OTG (TinyUSB CDC) back to ESP32-C6's native
// USB-Serial-JTAG (driver/usb_serial_jtag.h), which is the same family of
// peripheral the original C3 salvage used.

#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "calibration.h"
#include "dispatch.h"
#include "identity.h"
#include "protocol.h"
#include "servo.h"
#include "shell.h"

static const char *TAG = "main";

#define USB_RX_BUF_BYTES   1024
#define USB_TX_BUF_BYTES   1024
#define MODE_SELECT_WINDOW_US (1 * 1000 * 1000)

static void install_usb_serial_jtag(void) {
    const usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = USB_RX_BUF_BYTES,
        .tx_buffer_size = USB_TX_BUF_BYTES,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
}

// Polls USB-Serial-JTAG RX for up to 1 s. Returns true if linenoise was
// requested (three line-ending characters seen, CRLF counts as one
// ENTER). If a non-line-ending byte is read, it is stashed in
// *first_proto_byte and false is returned so the protocol loop can
// re-feed it.
static bool wait_for_mode_select_1s(uint8_t *first_proto_byte,
                                    bool *have_first_byte) {
    *have_first_byte = false;
    int enters = 0;
    bool last_was_cr = false;
    const int64_t deadline = esp_timer_get_time() + MODE_SELECT_WINDOW_US;

    while (esp_timer_get_time() < deadline) {
        uint8_t b;
        const int n = usb_serial_jtag_read_bytes(&b, 1, pdMS_TO_TICKS(20));
        if (n <= 0) continue;
        if (b == '\r') {
            enters++;
            last_was_cr = true;
            if (enters >= 3) return true;
        } else if (b == '\n') {
            if (!last_was_cr) {
                enters++;
                if (enters >= 3) return true;
            }
            last_was_cr = false;
        } else {
            *first_proto_byte = b;
            *have_first_byte = true;
            return false;
        }
    }
    return false;
}

static void send_wire(const uint8_t *wire, size_t len) {
    if (len == 0) return;
    const int written = usb_serial_jtag_write_bytes(wire, len,
                                                    pdMS_TO_TICKS(100));
    if (written < 0 || (size_t)written != len) {
        ESP_LOGW(TAG, "short write: %d / %u", written, (unsigned)len);
    }
}

static void process_byte(proto_rx_t *rx, uint8_t b, uint8_t *wire_out) {
    const proto_rx_status_t st = proto_rx_feed(rx, b);
    if (st != PROTO_RX_FRAME_READY) return;
    post_action_t post = POST_ACTION_NONE;
    const size_t reply_len = dispatch_handle(rx->decoded, rx->decoded_len,
                                             wire_out, &post);
    send_wire(wire_out, reply_len);
    if (post == POST_ACTION_RESET) {
        vTaskDelay(pdMS_TO_TICKS(50));   // give USB TX time to flush, §3.11
        esp_restart();
    }
}

static void protocol_main_loop(uint8_t first_byte, bool have_first_byte) {
    proto_rx_t rx;
    proto_rx_init(&rx);

    uint8_t wire_out[PROTO_WIRE_MAX];

    // Unsolicited startup PONG (§4.5).
    const size_t pong_len = dispatch_build_pong(wire_out);
    send_wire(wire_out, pong_len);

    if (have_first_byte) {
        process_byte(&rx, first_byte, wire_out);
    }

    uint8_t buf[64];
    while (true) {
        const int n = usb_serial_jtag_read_bytes(buf, sizeof buf,
                                                 pdMS_TO_TICKS(50));
        if (n <= 0) continue;
        for (int i = 0; i < n; i++) {
            process_byte(&rx, buf[i], wire_out);
        }
    }
}

void app_main(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS needs erase (%s); doing it", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    servo_init();
    calibration_load_all();
    install_usb_serial_jtag();

    ESP_LOGI(TAG, "rfmesh servo fw %u.%u.%u (%.8s) up; mode-select 1s window",
             FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH,
             FW_GIT_SHORT_SHA);

    uint8_t first_proto_byte = 0;
    bool have_first_byte = false;
    const bool linenoise = wait_for_mode_select_1s(&first_proto_byte,
                                                   &have_first_byte);
    if (linenoise) {
        ESP_LOGI(TAG, "triple-ENTER seen; entering linenoise shell");
        shell_run();
        ESP_LOGI(TAG, "shell exit; back to protocol mode");
    }

    protocol_main_loop(first_proto_byte, have_first_byte);
}
