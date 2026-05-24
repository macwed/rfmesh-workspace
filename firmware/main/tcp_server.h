// TCP server for the rfmesh servo controller.
//
// Accept-one-client-at-a-time loop on TCP port 5555 (TCP_SERVER_PORT).
// Bytes received are fed into the existing protocol layer
// (proto_rx_feed -> dispatch_handle -> reply), byte-for-byte identical
// to the previous USB-Serial-JTAG path -- only the byte pipe differs.
//
// The wire format (COBS+TLV+CRC-16/CCITT-FALSE) is unchanged from
// servo_uart_v1.md. The laptop side reaches us via TcpTransport
// (packages/rfmesh-servo/src/rfmesh_servo/transport.py).
//
// Call tcp_server_run() AFTER wifi_sta_init_and_connect() has
// returned (we need a live STA interface to bind on). The function
// never returns; it runs the accept loop forever.

#pragma once

#define TCP_SERVER_PORT 5555

void tcp_server_run(void);
