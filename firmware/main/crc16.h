// CRC-16/CCITT-FALSE per servo_uart_v1 §2.2.
// Polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR.
//
// Implemented locally rather than via esp_crc16_be(0xFFFF, ...): the spec
// claims they are equivalent, but keeping the algorithm in-tree lets the
// host-side Unity tests catch a mismatch without flashing.

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint16_t crc16_ccitt_false(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif
