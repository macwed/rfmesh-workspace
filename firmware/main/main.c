// app_main for the rfmesh servo controller.
//
// Boot path (ESP32-C6, WiFi-station + TCP server):
//   1. nvs_flash_init, servo_init, calibration_load_all
//   2. usb_serial_jtag_driver_install -> RX/TX ringbufs (KEPT for
//      ESP_LOG console output: operator reads the assigned DHCP IP
//      from this log to populate the laptop YAML).
//   3. wifi_sta_init_and_connect() -> blocks until DHCP lease
//   4. tcp_server_run() -> accept-one-client loop on :5555,
//      feeds bytes into proto_rx -> dispatch -> reply
//
// Wire protocol (servo_uart_v1) is UNCHANGED. The transport pivot
// from USB-Serial-JTAG to WiFi+TCP is documented in ADR-027; the
// SSID/PSK ("rfmesh" / "karasie01") are hardcoded in wifi_sta.c
// for the bench AP -- change there + reflash if the bench AP
// credentials change.

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
#include "tcp_server.h"
#include "wifi_sta.h"

static const char *TAG = "main";

#define USB_RX_BUF_BYTES   1024
#define USB_TX_BUF_BYTES   1024

static void install_usb_serial_jtag(void) {
    const usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = USB_RX_BUF_BYTES,
        .tx_buffer_size = USB_TX_BUF_BYTES,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
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

    ESP_LOGI(TAG, "rfmesh servo fw %u.%u.%u (%.8s) up; "
                  "joining WiFi + serving TCP on :%u",
             FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH,
             FW_GIT_SHORT_SHA, TCP_SERVER_PORT);

    wifi_sta_init_and_connect();
    tcp_server_run();   // never returns
}
