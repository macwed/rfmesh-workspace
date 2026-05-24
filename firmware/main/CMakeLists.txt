idf_component_register(
    SRCS
        "main.c"
        "crc16.c"
        "cobs.c"
        "protocol.c"
        "servo.c"
        "servo_math.c"
        "calibration.c"
        "dispatch.c"
        "shell.c"
    INCLUDE_DIRS
        "."
        "${CMAKE_CURRENT_BINARY_DIR}"
    REQUIRES
        driver
        # Was esp_driver_usb_serial_jtag on the C3 salvage. ESP32-S2 has no
        # USB-Serial-JTAG peripheral; the managed component below provides
        # TinyUSB CDC instead. See main/idf_component.yml.
        esp_tinyusb
        nvs_flash
        console
        esp_timer
)

# --- identity.h ----------------------------------------------------------
# Stamped at configure time (no .git path dependency — works inside a
# worktree where .git is a pointer file). To pick up a new SHA, run
# `idf.py reconfigure`. Major/minor/patch live in the .in file.
set(_identity_in  "${CMAKE_CURRENT_LIST_DIR}/identity.h.in")
set(_identity_out "${CMAKE_CURRENT_BINARY_DIR}/identity.h")

set(FW_GIT_SHORT_SHA "unknown0")
find_package(Git QUIET)
if(GIT_FOUND)
    execute_process(
        COMMAND "${GIT_EXECUTABLE}" rev-parse --short=8 HEAD
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}/.."
        OUTPUT_VARIABLE _sha
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _rc
        ERROR_QUIET
    )
    if(_rc EQUAL 0 AND _sha)
        # Pad/truncate to exactly 8 chars (PONG payload field is fixed-width).
        string(LENGTH "${_sha}" _len)
        if(_len LESS 8)
            string(REPEAT "0" 8 _pad)
            set(_sha "${_sha}${_pad}")
        endif()
        string(SUBSTRING "${_sha}" 0 8 FW_GIT_SHORT_SHA)
    endif()
endif()
configure_file("${_identity_in}" "${_identity_out}" @ONLY)
