// app_main + mode select per servo_uart_v1 §6 / spec §3.10.
//
// Boot path (ESP32-S2, TinyUSB CDC):
//   1. nvs_flash_init, servo_init, calibration_load_all
//   2. install TinyUSB driver + CDC ACM interface 0
//   3. esp_tusb_init_console(0) -> stdio (printf, ESP_LOG) routes through CDC
//   4. wait up to ~500 ms for the host to enumerate the CDC device
//   5. wait up to 1 s for either three consecutive ENTERs (-> linenoise)
//      or any non-line-ending byte (-> protocol; stashed for re-feed)
//   6. linenoise: shell_run uses linenoise on stdio (already routed to CDC),
//      no driver swap needed; on `proto` exit, stdio stays routed (interleaves
//      with protocol bytes; COBS framing handles it on the host side)
//   7. send unsolicited PONG (§4.5)
//   8. protocol_main_loop: feed bytes -> proto_rx -> dispatch -> reply
//
// Wire protocol (servo_uart_v1) is UNCHANGED from the C3 build. The only
// thing that moves is the USB transport peripheral underneath: ESP32-C3's
// USB-Serial-JTAG (driver/usb_serial_jtag.h) -> ESP32-S2's USB-OTG with
// TinyUSB CDC (tinyusb.h + tusb_cdc_acm.h).

#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "tinyusb.h"
#include "tusb_cdc_acm.h"
#include "tusb_console.h"

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
// Cap on how long we wait for the host to enumerate the CDC endpoint after
// driver install. On a clean bench this completes in ~50-200 ms; the cap is
// generous so a slow host doesn't eat the user's mode-select budget.
#define ENUM_WAIT_WINDOW_US (500 * 1000)

// --- USB CDC helpers ----------------------------------------------------
//
// The C3 salvage used usb_serial_jtag_read_bytes / write_bytes for the
// binary protocol path. On S2 with TinyUSB CDC, the equivalents are
// tinyusb_cdcacm_read (non-blocking, reads what's in the RX ringbuf) and
// tinyusb_cdcacm_write_queue + write_flush. Wrap them in helpers that
// preserve the salvage's "read up to N bytes, waiting at most T ticks"
// semantics so the call sites read close to the original.

static int usb_cdc_read_bytes(uint8_t *buf, size_t want, TickType_t timeout_ticks) {
    size_t got = 0;
    const TickType_t start = xTaskGetTickCount();
    while (got < want) {
        size_t rx = 0;
        const esp_err_t err = tinyusb_cdcacm_read(TINYUSB_CDC_ACM_0,
                                                  buf + got, want - got, &rx);
        if (err == ESP_OK) {
            got += rx;
        }
        if (got >= want) break;
        const TickType_t elapsed = xTaskGetTickCount() - start;
        if (elapsed >= timeout_ticks) break;
        vTaskDelay(1);
    }
    return (int)got;
}

static int usb_cdc_write_bytes(const uint8_t *buf, size_t len,
                               TickType_t timeout_ticks) {
    if (len == 0) return 0;
    size_t queued = 0;
    while (queued < len) {
        const size_t n = tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0,
                                                    buf + queued, len - queued);
        if (n == 0) break;  // TX ringbuf full; flush below
        queued += n;
    }
    const esp_err_t err = tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0,
                                                     timeout_ticks);
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) return -1;
    return (int)queued;
}

static void install_usb_cdc(void) {
    const tinyusb_config_t tusb_cfg = {
        .device_descriptor = NULL,
        .string_descriptor = NULL,
        .external_phy      = false,
        .configuration_descriptor = NULL,
    };
    ESP_ERROR_CHECK(tinyusb_driver_install(&tusb_cfg));

    const tinyusb_config_cdcacm_t acm_cfg = {
        .usb_dev    = TINYUSB_USBDEV_0,
        .cdc_port   = TINYUSB_CDC_ACM_0,
        .rx_unread_buf_sz             = USB_RX_BUF_BYTES,
        .callback_rx                  = NULL,
        .callback_rx_wanted_char      = NULL,
        .callback_line_state_changed  = NULL,
        .callback_line_coding_changed = NULL,
    };
    ESP_ERROR_CHECK(tusb_cdc_acm_init(&acm_cfg));
}

static void wait_for_cdc_enumeration(void) {
    const int64_t deadline = esp_timer_get_time() + ENUM_WAIT_WINDOW_US;
    while (esp_timer_get_time() < deadline) {
        if (tud_cdc_n_connected(TINYUSB_CDC_ACM_0)) return;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    // Not connected; continue anyway. Boot logs queue in the TX ringbuf
    // and flush when the host eventually attaches.
}

// Polls CDC RX for up to 1 s. Returns true if linenoise was requested
// (three line-ending characters seen, CRLF counts as one ENTER). If a
// non-line-ending byte is read, it is stashed in *first_proto_byte and
// false is returned so the protocol loop can re-feed it. Semantics are
// byte-for-byte identical to the C3 salvage; only the underlying read
// API moves from usb_serial_jtag_read_bytes to tinyusb_cdcacm_read via
// the usb_cdc_read_bytes wrapper above.
static bool wait_for_mode_select_1s(uint8_t *first_proto_byte,
                                    bool *have_first_byte) {
    *have_first_byte = false;
    int enters = 0;
    bool last_was_cr = false;
    const int64_t deadline = esp_timer_get_time() + MODE_SELECT_WINDOW_US;

    while (esp_timer_get_time() < deadline) {
        uint8_t b;
        const int n = usb_cdc_read_bytes(&b, 1, pdMS_TO_TICKS(20));
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
    const int written = usb_cdc_write_bytes(wire, len, pdMS_TO_TICKS(100));
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
        const int n = usb_cdc_read_bytes(buf, sizeof buf,
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
    install_usb_cdc();
    // Route stdio (printf, ESP_LOG) through CDC interface 0. After this
    // call, boot banner + any subsequent ESP_LOGI go to the host over USB.
    // shell.c reuses this routing for the linenoise REPL.
    ESP_ERROR_CHECK(esp_tusb_init_console(TINYUSB_CDC_ACM_0));
    wait_for_cdc_enumeration();

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
