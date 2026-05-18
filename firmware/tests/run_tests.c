// Host-side Unity runner. Each test module exposes one
// `void run_<name>_tests(void)` symbol, called from main().

#include "unity.h"

void setUp(void) {}
void tearDown(void) {}

void run_crc16_tests(void);
void run_cobs_tests(void);
void run_protocol_parse_tests(void);
void run_servo_math_tests(void);

int main(void) {
    UNITY_BEGIN();
    run_crc16_tests();
    run_cobs_tests();
    run_protocol_parse_tests();
    run_servo_math_tests();
    return UNITY_END();
}
