// Command dispatch: takes a parsed (un-stuffed, CRC-verified) frame from
// proto_rx_t and produces a wire-encoded reply. Implements the CMD ->
// behaviour mapping from servo_uart_v1 §3.

#pragma once

#include <stddef.h>
#include <stdint.h>

#include "protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

// servo_uart_v1 §3 command bytes
#define CMD_MOVE          0x01u
#define CMD_POS_QUERY     0x02u
#define CMD_POS_REPLY     0x03u
#define CMD_STOP          0x04u
#define CMD_CAL_SET       0x05u
#define CMD_CAL_QUERY     0x06u
#define CMD_CAL_REPLY     0x07u
#define CMD_CAL_PERSIST   0x08u
#define CMD_PING          0x10u
#define CMD_PONG          0x11u
#define CMD_RESET         0x20u
#define CMD_ACK           0xF0u
#define CMD_ERROR         0xFFu

// servo_uart_v1 §5 error codes
#define ERR_NO_SUCH_AXIS         0x01u
#define ERR_ANGLE_OUT_OF_RANGE   0x02u
#define ERR_BAD_CALIBRATION      0x03u
#define ERR_NVS_FAIL             0x04u
#define ERR_NOT_CALIBRATED       0x05u
#define ERR_BUSY                 0x06u
#define ERR_UNKNOWN_CMD          0xFEu
#define ERR_INTERNAL             0xFFu

typedef enum {
    POST_ACTION_NONE = 0,
    POST_ACTION_RESET,   // delay 50ms, then esp_restart() — see §3.11
} post_action_t;

// Process one decoded frame; emit a complete wire reply (COBS-encoded,
// 0x00 terminated). frame[0]=CMD, frame[1]=LEN, frame[2..frame_len)=payload.
//
// Returns the number of bytes written to wire_out (>= 0), or 0 if the
// command produced no reply (e.g. an out-of-band unsolicited PONG used
// the dispatch_build_pong() path directly).
//
// wire_out must be PROTO_WIRE_MAX bytes.
size_t dispatch_handle(const uint8_t *frame, size_t frame_len,
                       uint8_t *wire_out, post_action_t *post_action);

// Build an unsolicited startup PONG (§4.5). Returns wire-encoded length.
size_t dispatch_build_pong(uint8_t *wire_out);

#ifdef __cplusplus
}
#endif
