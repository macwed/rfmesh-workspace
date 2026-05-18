#include "cobs.h"

size_t cobs_encode(const uint8_t *src, size_t src_len, uint8_t *dst) {
    // Two-pass-style single pass: keep a pointer to the most recent code
    // byte; rewrite it whenever we hit a zero in src or the run reaches
    // the 0xFF cap.
    size_t out = 0;
    size_t code_idx = out;
    dst[out++] = 0; // placeholder for code byte
    uint8_t code = 1;

    for (size_t i = 0; i < src_len; i++) {
        const uint8_t b = src[i];
        if (b == 0) {
            dst[code_idx] = code;
            code_idx = out;
            dst[out++] = 0;
            code = 1;
        } else {
            dst[out++] = b;
            code++;
            if (code == 0xFF) {
                dst[code_idx] = code;
                code_idx = out;
                dst[out++] = 0;
                code = 1;
            }
        }
    }
    dst[code_idx] = code;
    return out;
}

size_t cobs_decode(const uint8_t *src, size_t src_len, uint8_t *dst) {
    if (src_len == 0) {
        return 0;
    }
    size_t in = 0;
    size_t out = 0;
    while (in < src_len) {
        const uint8_t code = src[in++];
        if (code == 0) {
            // 0x00 in COBS-encoded payload is invalid (caller strips
            // the terminator before calling us).
            return 0;
        }
        // The code byte says "this group has (code-1) data bytes, then
        // an implicit zero (unless code == 0xFF, in which case the run
        // hit the cap and there's no implicit zero)".
        const size_t data_bytes = (size_t)(code - 1);
        if (in + data_bytes > src_len) {
            return 0;
        }
        for (size_t k = 0; k < data_bytes; k++) {
            const uint8_t b = src[in++];
            if (b == 0) {
                // 0x00 inside a COBS-encoded payload is invalid (the
                // 0x00 byte is reserved as frame delimiter).
                return 0;
            }
            dst[out++] = b;
        }
        // Emit the implicit zero unless we hit the cap or this was the
        // last group.
        if (code != 0xFF && in < src_len) {
            dst[out++] = 0;
        }
    }
    return out;
}
