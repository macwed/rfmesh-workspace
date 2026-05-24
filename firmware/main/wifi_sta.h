// WiFi station setup for the rfmesh servo controller.
//
// Hardcoded for the bench AP (SSID "macwed-hotspot", PSK
// "D7A39F23C909575A31848A0D53"); the
// node has no provisioning UX -- it joins the same AP every boot, gets
// a DHCP lease, and exposes the servo wire protocol over TCP (see
// tcp_server.h). Loud-fail-and-retry on every disconnect (B3 -- the
// laptop's boot log shows reconnect events instead of silent recovery).
//
// Call wifi_sta_init_and_connect() exactly once from app_main, AFTER
// nvs_flash_init() (esp_wifi internals need NVS). The call blocks
// until a DHCP lease is obtained; the lease IP is logged via
// ESP_LOGI("wifi_sta", ...) so the operator can write it into the
// laptop YAML.

#pragma once

void wifi_sta_init_and_connect(void);
