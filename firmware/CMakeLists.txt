cmake_minimum_required(VERSION 3.16)

# Top-level ESP-IDF project file. Host-side unit tests live in tests/ and
# are built independently with their own CMakeLists.txt (see tests/README
# in firmware/README.md).

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(rfmesh_servo_fw)
