// TCP server -- see tcp_server.h.
//
// Single-client accept loop. The servo is single-writer per ADR-024
// (only one controller owns the servo at a time) so refusing a
// second concurrent client is correct behaviour: the second
// connect()er blocks in the listen backlog until the first closes.
// We do not implement multi-client multiplexing.
//
// On client disconnect we close + loop back to accept. On
// POST_ACTION_RESET (from dispatch_handle) we delay 50ms to let the
// TCP layer flush the reply, then esp_restart() -- same semantics as
// the USB path.

#include "tcp_server.h"

#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"

#include "dispatch.h"
#include "protocol.h"

static const char *TAG = "tcp_server";

#define RX_CHUNK_BYTES 64

static void send_wire_tcp(int sock, const uint8_t *wire, size_t len) {
    if (len == 0) return;
    // send() returns -1 on error or partial on EAGAIN; we want
    // all-or-error semantics matching USB send_wire(). The lwip
    // default blocking send loops until the kernel queue drains, so
    // a single send call is sufficient on a healthy socket. A short
    // send is logged loud (B3 -- never silently truncate a frame).
    const int written = send(sock, wire, len, 0);
    if (written < 0 || (size_t)written != len) {
        ESP_LOGW(TAG, "short send: %d / %u", written, (unsigned)len);
    }
}

static void serve_client(int sock) {
    proto_rx_t rx;
    proto_rx_init(&rx);

    uint8_t wire_out[PROTO_WIRE_MAX];

    // Unsolicited startup PONG, mirroring the USB protocol_main_loop
    // contract (servo_uart_v1 §4.5). Lets the laptop driver confirm
    // the firmware version + uptime as soon as the TCP link is up.
    const size_t pong_len = dispatch_build_pong(wire_out);
    send_wire_tcp(sock, wire_out, pong_len);

    uint8_t buf[RX_CHUNK_BYTES];
    while (true) {
        const int n = recv(sock, buf, sizeof buf, 0);
        if (n == 0) {
            ESP_LOGI(TAG, "client closed (EOF)");
            return;
        }
        if (n < 0) {
            ESP_LOGI(TAG, "client recv error: errno=%d", errno);
            return;
        }
        for (int i = 0; i < n; i++) {
            const proto_rx_status_t st = proto_rx_feed(&rx, buf[i]);
            if (st != PROTO_RX_FRAME_READY) continue;
            post_action_t post = POST_ACTION_NONE;
            const size_t reply_len = dispatch_handle(
                rx.decoded, rx.decoded_len, wire_out, &post);
            send_wire_tcp(sock, wire_out, reply_len);
            if (post == POST_ACTION_RESET) {
                // Give the TCP stack a moment to flush the reply
                // before we yank the rug. Mirrors the USB §3.11
                // pre-restart settle. The client will see a
                // connection-reset on its next read, which is the
                // honest signal that the firmware is restarting.
                vTaskDelay(pdMS_TO_TICKS(50));
                esp_restart();
            }
        }
    }
}

void tcp_server_run(void) {
    while (true) {
        const int listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listen_sock < 0) {
            ESP_LOGE(TAG, "socket() failed: errno=%d", errno);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        const int yes = 1;
        setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);

        struct sockaddr_in addr = {
            .sin_family = AF_INET,
            .sin_port = htons(TCP_SERVER_PORT),
            .sin_addr.s_addr = htonl(INADDR_ANY),
        };
        if (bind(listen_sock, (struct sockaddr *)&addr, sizeof addr) < 0) {
            ESP_LOGE(TAG, "bind(:%d) failed: errno=%d", TCP_SERVER_PORT,
                     errno);
            close(listen_sock);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        if (listen(listen_sock, 1) < 0) {
            ESP_LOGE(TAG, "listen() failed: errno=%d", errno);
            close(listen_sock);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        ESP_LOGI(TAG, "listening on :%d (one client at a time)",
                 TCP_SERVER_PORT);

        while (true) {
            struct sockaddr_in client_addr;
            socklen_t addr_len = sizeof client_addr;
            const int client_sock = accept(listen_sock,
                                           (struct sockaddr *)&client_addr,
                                           &addr_len);
            if (client_sock < 0) {
                ESP_LOGE(TAG, "accept() failed: errno=%d", errno);
                break;
            }
            ESP_LOGI(TAG, "client %s:%u connected",
                     inet_ntoa(client_addr.sin_addr),
                     (unsigned)ntohs(client_addr.sin_port));

            const int nodelay = 1;
            setsockopt(client_sock, IPPROTO_TCP, TCP_NODELAY, &nodelay,
                       sizeof nodelay);
            // Keepalive at 30s/5s/3 probes: detect a dead laptop
            // (Wi-Fi roam, OS sleep) within ~45s without us having
            // to invent our own heartbeat layer.
            const int ka_on = 1;
            const int ka_idle = 30, ka_intvl = 5, ka_cnt = 3;
            setsockopt(client_sock, SOL_SOCKET, SO_KEEPALIVE, &ka_on,
                       sizeof ka_on);
            setsockopt(client_sock, IPPROTO_TCP, TCP_KEEPIDLE, &ka_idle,
                       sizeof ka_idle);
            setsockopt(client_sock, IPPROTO_TCP, TCP_KEEPINTVL, &ka_intvl,
                       sizeof ka_intvl);
            setsockopt(client_sock, IPPROTO_TCP, TCP_KEEPCNT, &ka_cnt,
                       sizeof ka_cnt);

            serve_client(client_sock);
            close(client_sock);
        }
        close(listen_sock);
    }
}
