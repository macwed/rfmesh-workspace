// WiFi station setup -- see wifi_sta.h.
//
// Stays inside the ESP-IDF event-loop pattern (esp_wifi + esp_netif +
// esp_event), no custom RTOS task: the WiFi driver runs its own
// internal task and posts events to the default event loop. We block
// in wifi_sta_init_and_connect() on an EventGroup bit that the GOT_IP
// handler sets, so app_main can call us synchronously and only
// proceed to tcp_server_run() once the link is up.

#include "wifi_sta.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/ip_addr.h"

static const char *TAG = "wifi_sta";

// Hardcoded bench credentials. The whole point of the 2-node MVP is
// that the operator runs one AP on the laptop ("macwed-hotspot",
// "D7A39F23C909575A31848A0D53") and every node joins it. No NVS
// provisioning; no per-node secrets. Change here + reflash if the
// bench AP credentials ever change.
#define WIFI_SSID  "macwed-hotspot"
#define WIFI_PSK   "D7A39F23C909575A31848A0D53"

#define WIFI_CONNECTED_BIT BIT0

static EventGroupHandle_t s_wifi_event_group = NULL;

static void on_event(void *arg, esp_event_base_t base, int32_t id,
                     void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "STA started; connecting to '%s'", WIFI_SSID);
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        // Loud-and-retry per B3. The disconnect reason byte helps the
        // operator distinguish wrong-PSK (reason 15/202) from AP-down
        // (reason 200) without rebuilding firmware.
        const wifi_event_sta_disconnected_t *e = data;
        ESP_LOGW(TAG, "disconnected from '%s' (reason=%u); reconnecting",
                 WIFI_SSID, (unsigned)e->reason);
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        // Backoff a tick so we don't busy-loop the radio when the AP
        // is genuinely gone.
        vTaskDelay(pdMS_TO_TICKS(500));
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "got IP: " IPSTR " (write into laptop YAML as "
                      "servo_port: \"tcp://" IPSTR ":5555\")",
                 IP2STR(&e->ip_info.ip), IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

void wifi_sta_init_and_connect(void) {
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    const wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &on_event, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &on_event, NULL, NULL));

    wifi_config_t wifi_config = {0};
    // strncpy fills the destination with NUL on the tail, which is
    // exactly what the SDK expects (the .ssid / .password fields are
    // NUL-terminated C strings).
    strncpy((char *)wifi_config.sta.ssid, WIFI_SSID,
            sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, WIFI_PSK,
            sizeof(wifi_config.sta.password) - 1);
    // Accept WPA2 OR WPA3 mixed-mode: nmcli's `wifi-sec.key-mgmt
    // wpa-psk` provisions WPA2 on older NetworkManager builds and
    // WPA2-or-WPA3 transition mode on newer ones. Either way the
    // C6 association succeeds. PMF capable but not required so the
    // station can still associate with the bench AP if the AP has
    // PMF off (defaults vary across NM releases).
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_WPA3_PSK;
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "waiting for DHCP lease...");
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);
}
