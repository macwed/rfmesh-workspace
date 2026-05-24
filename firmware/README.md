# rfmesh servo controller firmware

> **2026-05-24 (ADR-027 / ADR-015):** Actual build target is **ESP32-C6**
> (per `firmware/sdkconfig.defaults` line 6 and ADR-015), not the S2 the
> body of this README still describes. The transport is **WiFi-station +
> TCP server on port 5555** (per ADR-027), not USB-CDC; USB-CDC stays
> wired only as the `ESP_LOG` console path so the operator can read the
> DHCP-assigned IP off the boot log. See "WiFi bring-up" immediately
> below; the rest of the README is stale and pending a sweep.

ESP32-C6 firmware implementing the device side of `servo_uart v1`
(`../docs/wire-protocols/servo_uart_v1.md`). Drives one MG996 hobby
servo (axis 0 = pan) over LEDC PWM and takes binary protocol frames
over **TCP socket** (laptop side: `TcpTransport` in
`packages/rfmesh-servo/src/rfmesh_servo/transport.py`). The wire
protocol (COBS+TLV+CRC-16/CCITT-FALSE) is byte-identical to the
historical USB-CDC build — only the transport differs.

## WiFi bring-up (ADR-027)

The bench AP credentials are **hardcoded in `firmware/main/wifi_sta.c`**:

```c
#define WIFI_SSID  "macwed-hotspot"
#define WIFI_PSK   "D7A39F23C909575A31848A0D53"
```

Change there + reflash if the bench AP credentials ever change.

**First-boot sequence (one-time per node):**

1. Start a WiFi hotspot on the laptop with SSID `"macwed-hotspot"` /
   PSK `"D7A39F23C909575A31848A0D53"`. On Linux + NetworkManager:
   ```bash
   nmcli connection add type wifi ifname '*' con-name macwed-hotspot \
       autoconnect no ssid macwed-hotspot mode ap
   nmcli connection modify macwed-hotspot 802-11-wireless.band bg \
       ipv4.method shared
   nmcli connection modify macwed-hotspot wifi-sec.key-mgmt wpa-psk \
       wifi-sec.psk D7A39F23C909575A31848A0D53
   nmcli connection up macwed-hotspot
   ```
2. Flash the ESP32-C6 (USB plugged in, see "Flash" below).
3. Watch the boot log:
   ```bash
   idf.py -p /dev/ttyACM0 monitor   # Ctrl+] to exit
   ```
   After ~3-5 s of association you will see:
   ```
   I (4523) wifi_sta: got IP: 192.168.4.11 (write into laptop YAML as
   servo_port: "tcp://192.168.4.11:5555")
   ```
4. Copy the IP into your node YAML:
   ```yaml
   # configs/node-laptop-01.yaml
   servo_port: "tcp://192.168.4.11:5555"
   ```
5. Unplug USB — the C6 keeps the WiFi link up. (You may keep USB
   plugged for the console log if you prefer; it does not interfere.)

**Reconnect behaviour.** Disconnect events (AP down, wrong PSK,
range) are logged loud per B3 and trigger an `esp_wifi_connect`
retry after 500 ms. The node will reassociate without reboot as
soon as the AP is back. If the laptop AP IP pool gives out a
different lease, you must update the YAML — set a static DHCP lease
on the AP (keyed on the C6 MAC) if you want stable IPs across
reboots.

**Flash.** ESP-IDF on `$PATH`, then:

```bash
cd firmware/
idf.py set-target esp32c6        # only on a fresh checkout
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

The C6's native USB-Serial-JTAG auto-triggers download mode on
reset, so no BOOT/RESET button dance is needed.

---

*The body of the README below describes the prior ESP32-S2 build and
is preserved for now; the salvaged 29 host-buildable C tests for the
framing layer still apply (the wire codec is unchanged).*

---

## Hardware

| Function                     | Pin              | Notes                                           |
|------------------------------|------------------|-------------------------------------------------|
| Servo PWM out (axis 0 = pan) | **GPIO5**        | LEDC channel 0, 50 Hz, 14-bit                   |
| Reserved (axis 1 = tilt)     | GPIO6            | Not wired in v1                                 |
| USB D-                       | **GPIO19**       | Native USB-OTG D-; not user-configurable        |
| USB D+                       | **GPIO20**       | Native USB-OTG D+; not user-configurable        |

**Servo power: external 5–6 V PSU. NEVER run an MG996 from the ESP32 5V
or RPi 5V — it will brown out under stall current. Common ground between
the PSU and the ESP32 is mandatory; without it the PWM signal is
floating and the servo will twitch or refuse to move.**

```
              +----------------+
              |  ESP32-S2      |
   USB <----> |  mini          |
              |        GPIO5 ──┼─── signal ───┐
              |          GND ──┼─── GND ──────┼──── GND ── PSU GND
              +----------------+              │             │
                                              │           +──+
                                              ├── signal ─┤  │  MG996
                                          5-6 V ──── V+ ──┤  │  servo
                                                          +──+
```

### Board variants

The firmware is built for the **ESP32-S2 mini** (3 units on hand;
production target per `INHERITED_CONTEXT.md` §1.1). It also works on
any other S2 board exposing the native USB pins (GPIO19/GPIO20):

- **Wemos S2 Mini** — same pinout; tested as the primary target
- **ESP32-S2-DevKitM-1** — same pinout, larger footprint
- **ESP32-S2-Saola-1** — same pinout, exposes more GPIOs

The status-LED line (`STATUS_LED_GPIO` in `main/main.c`) is currently
a stub; protocol behaviour does not depend on it.

## Build prerequisites

- ESP-IDF v5.1 or later (the C3 salvage was developed on **v5.4.3**)
- `idf.py` on `$PATH` after sourcing the IDF export script
- ESP-IDF component manager fetches `espressif/esp_tinyusb` on first build
  (pinned to `~1.4.0` in `main/idf_component.yml`)

If your IDF python venv directory is named for a Python version other
than the one currently first on `$PATH`, set `IDF_PYTHON_ENV_PATH`
explicitly before sourcing `export.sh`:

```bash
export IDF_PYTHON_ENV_PATH=$HOME/.espressif/python_env/idf5.4_py3.14_env
. ~/esp/esp-idf-v5.4/export.sh
```

(Needed inside the rfmesh dev shell because the project's `.venv`
puts Python 3.12 first, but IDF's bundled venv on this machine targets
Python 3.14.)

## Build, flash, monitor

```bash
cd firmware/
idf.py set-target esp32s2        # only on a fresh checkout
idf.py build                     # produces build/rfmesh_servo_fw.bin
idf.py -p /dev/ttyACM0 flash     # native USB-CDC, enumerates as /dev/ttyACM*
idf.py -p /dev/ttyACM0 monitor   # Ctrl+] to exit
```

**Note on S2 flashing.** Unlike the C3 build, the ESP32-S2 native USB
does not auto-trigger download mode on reset. To enter download mode:

1. Hold **BOOT** (GPIO0) on the board.
2. Press and release **RESET** (EN).
3. Release **BOOT**.

`idf.py flash` then completes normally. After a successful flash, press
**RESET** once to run the new image. Boards that expose the USB through
a USB-UART bridge instead of native USB can use the auto-flash path.

## Host-side unit tests

The framing logic (`crc16`, `cobs`, `protocol`) and the pulse↔angle math
(`servo_math`) are pure C and built independently of ESP-IDF. The S2
port did not touch any of this code; the salvaged 29-test suite ports
verbatim and passes byte-for-byte.

```bash
cd firmware/
cmake -S tests -B build_tests
cmake --build build_tests
./build_tests/run_tests
```

Expected: `29 Tests 0 Failures 0 Ignored / OK`.

The required-by-spec suites (`crc16`, `cobs`, `protocol_parse`) cover
the §7.1 / §7.2 / §7.3 vectors byte-exactly, plus a randomised round-
trip property test, deliberately corrupted frames (bad CRC / bad COBS /
length mismatch / oversized), and resync after garbage. `servo_math` is
an additional sanity suite for the pulse↔angle linear interpolation.

## On-target smoke test (§4.2)

Manual; requires hardware. Run by Maciej after `idf.py flash` succeeds
on an ESP32-S2 mini.

1. Wire ESP32-S2 GPIO5 to the MG996 signal lead. Servo +V from external
   5 V PSU. **Common GND** between the PSU and the ESP32. Do **not**
   power the servo from the ESP32 5V.
2. Flash and open the monitor:
   ```bash
   idf.py -p /dev/ttyACM0 flash monitor
   ```
   Reset the board. The boot log should show `nvs_flash_init` ok,
   `LEDC ready: 50 Hz, 14-bit, axis0=GPIO5`, TinyUSB CDC enumeration
   messages, and `mode-select 1s window`.

   **First-boot timing.** On S2, USB-CDC enumeration takes ~50-200 ms
   after `tinyusb_driver_install`. The firmware waits up to 500 ms for
   the host to attach before opening the 1-second mode-select window,
   so the user gets the full 1 s of input budget even on slow hosts.
3. Within 1 s, no terminal input is sent — the firmware enters protocol
   mode automatically. Expect the unsolicited PONG (binary; will look
   like garbage in `idf.py monitor`).
4. Exit monitor (`Ctrl+]`). In a separate terminal, run the host driver
   REPL:
   ```bash
   cd ../  # back to repo root
   uv run python -m rfmesh_servo.repl --port /dev/ttyACM0
   ```
   Send `PING`. Expect `PONG proto_version=1, fw=0.1.0, sha=<8 chars>,
   axis_count=1`.
5. `MOVE axis=0 angle=0` — servo moves to centre (~1500 µs).
6. `MOVE axis=0 angle=45` — servo moves ~45° one way.
7. `MOVE axis=0 angle=-45` — servo swings to the other side.
8. `CAL_SET` axis=0 with custom values (e.g. `pulse_min=600, pulse_max=2400,
   angle_min=-450, angle_max=1350`); then `CAL_PERSIST axis=0`. Power-
   cycle the ESP32. After it re-enumerates, send `CAL_QUERY axis=0` and
   verify the values came back from NVS.
9. Disconnect the host driver. Open a plain terminal:
   ```bash
   screen /dev/ttyACM0 115200
   ```
   Press `ENTER` three times within 1 s of fresh enumeration. Expect a
   `servo>` prompt (linenoise). Type `move 0 0` — servo moves. Type
   `proto`. The shell prints `entering protocol mode` and exits. Kill
   `screen` with `Ctrl+a, k`.

If steps 1–9 all pass, the firmware is hardware-validated on S2.

## Troubleshooting

**"linenoise prompt never appears."** The 1-second window starts at the
moment the firmware finishes booting (after the boot log finishes
streaming over USB), not at USB enumeration. If the host's terminal
program sends any non-line-ending byte before three ENTERs, the
firmware switches to protocol mode immediately. Cleanest path: open
`screen` first, then power-cycle the board, then quickly press ENTER
three times.

**"Host does not see /dev/ttyACM* after flashing."** S2 doesn't
auto-strap on USB activity the way C3 does. Press **RESET** once after
flash completes; the board reboots into the application image and the
native USB-CDC re-enumerates as `/dev/ttyACM0` (or `/dev/cu.usbmodemNNN`
on macOS).

**"servo jitters at rest."** Almost always a power issue. Confirm the
servo's V+ comes from the external PSU (not the ESP32 5V), confirm
PSU GND and ESP32 GND are tied, and confirm the PSU can supply at
least 1.2 A continuous (MG996 stall current is ~2.5 A; the supply
sagging on every command will look like jitter).

**"servo doesn't move at all."** Re-check the GPIO assignment matches
your board (default: GPIO5). On Wemos S2 Mini the silkscreen GPIO
labels match the chip's GPIO numbers; on other boards verify against
the schematic.

**"NVS failed: ESP_ERR_NVS_NO_FREE_PAGES."** First boot or after a
partition-table change. The firmware auto-erases NVS on this error and
re-initialises; the next boot is clean. If it loops, run
`idf.py erase-flash` and re-flash.

**"TinyUSB component fetch failed."** First build needs network access
to pull `espressif/esp_tinyusb~1.4.0` via the IDF Component Manager.
If the bench is offline, vendor the component locally per the IDF
Component Manager docs (`idf.py update-dependencies --keep-versions`).
