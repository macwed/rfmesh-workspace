cmake_minimum_required(VERSION 3.16)
project(rfmesh_servo_fw_host_tests C)

# Pure-host build of cobs/crc16/protocol-parse logic. Does NOT depend on
# ESP-IDF — sources are picked individually from ../main/ so we can leave
# the ESP-IDF-only files out.
#
# Build:
#   cmake -S . -B ../build_tests
#   cmake --build ../build_tests
#   ../build_tests/run_tests
#
# Layout:
#   unity/       — vendored Unity (MIT, see unity/LICENSE.txt)
#   test_*.c     — one file per host-testable module
#   run_tests.c  — Unity runner that calls each suite

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Werror -Wno-unused-function)

add_executable(run_tests
    run_tests.c
    unity/unity.c
    test_crc16.c
    ../main/crc16.c
    test_cobs.c
    ../main/cobs.c
    test_protocol_parse.c
    ../main/protocol.c
    test_servo_math.c
    ../main/servo_math.c
)

target_include_directories(run_tests PRIVATE
    unity
    ../main
)

# Tests are added to SRCS and to run_tests.c's RUN_TEST list one-by-one
# in subsequent atomic commits (crc16 → cobs → protocol_parse).
