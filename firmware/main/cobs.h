// Consistent Overhead Byte Stuffing (Cheshire & Baker 1999) per
// servo_uart_v1 §2.1. Output is guaranteed to contain no 0x00 bytes;
// the caller appends the 0x00 frame delimiter (encode) or strips it
// before calling decode.

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Worst-case encoded length for src_len input bytes (excluding the 0x00
// terminator the caller appends): src_len + ceil(src_len / 254) + 1.
#define COBS_ENCODE_MAX_LEN(src_len) ((src_len) + ((src_len) / 254u) + 1u)

// Encode src into dst. Returns number of bytes written to dst. dst must
// be at least COBS_ENCODE_MAX_LEN(src_len) bytes. Does NOT append 0x00.
size_t cobs_encode(const uint8_t *src, size_t src_len, uint8_t *dst);

// Decode src into dst. src is the COBS-encoded payload WITHOUT the 0x00
// terminator. Returns number of bytes written, or 0 on decode error
// (which includes: src_len == 0, leading 0x00 byte in input, or a code
// byte that points past the end of the buffer).
size_t cobs_decode(const uint8_t *src, size_t src_len, uint8_t *dst);

#ifdef __cplusplus
}
#endif
