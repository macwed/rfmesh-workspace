# ADR-027 — Servo control transport over TCP/IP (WiFi station)

**Status:** ACCEPTED (2026-05-24)
**Date:** 2026-05-24
**Author:** lead-Opus
**Accepted by:** Maciej (@macwed), 2026-05-24.
**SCHEMA_VERSION change:** none (no contract surface touched).
**Depends on:** ADR-015 (firmware target ESP32-C6), ADR-024 (NodeController single-writer servo invariant).

## Context

The 2-node bench MVP has been blocked by USB-CDC instability when two ESP32-C6 servo controllers are plugged into the same laptop simultaneously. Symptoms are intermittent: enumeration races, autosuspend, hub contention, occasional disconnect events visible in `dmesg`. The protocol-layer wire format (`docs/wire-protocols/servo_uart_v1.md`) is transport-agnostic by construction — COBS+TLV+CRC-16/CCITT-FALSE over any byte stream — so the failure is below the protocol layer, in the host's USB-CDC driver stack, and is not addressable from rfmesh code.

Diagnostic options (autosuspend tweaks, separate USB controllers, powered hubs) were rejected by the operator after past intermittent failures consumed bench time without root-causing. The decision is to switch to a transport that does not depend on the laptop's USB driver behaviour at all.

The C6 has WiFi 6 / BLE 5 / 802.15.4 built in. WiFi station mode is the lowest-friction option:

- The C6 joins a laptop-hosted hotspot (SSID `"rfmesh"`, PSK `"karasie01"` — bench-grade hardcoded credentials, see Operational below).
- The C6 hosts a TCP server on port 5555.
- The laptop talks via `socket()` — no `pyserial`, no `udev`, no `/dev/ttyACM*`.
- The wire format is unchanged. Only the byte pipe differs.

This is a bench/demo-time decision. EW-survivability of the servo control link is out of scope for this ADR; the same wire codec runs over USB-CDC unchanged, and operators retain that option per Phase 1 (`TcpTransport` is parallel to `SerialTransport`, not a replacement).

## Decision

Add WiFi-station + TCP-server transport for the laptop ↔ ESP32-C6 servo link. Wire format unchanged. USB-CDC stays in the firmware as the console-log path (operator reads the DHCP-assigned IP off the boot log) but stops being a protocol transport.

### Change A — Python `TcpTransport` (Phase 1, landed in commit `4caf7a8`)

New `rfmesh_servo.transport.TcpTransport` class, sibling of `SerialTransport`, implementing the existing `Transport` Protocol byte-for-byte:

- `__init__(host, port=5555, connect_timeout_s=5.0)` connects via `socket.create_connection`. `TCP_NODELAY` on (small request-reply frames, latency-sensitive, not throughput-sensitive).
- `read(n, timeout_s)` returns `b""` on timeout (B3, matches `SerialTransport.read` short-read semantics).
- `write(data)` uses `sendall` — no silent short-write fallback.
- `reset_input_buffer()` drains pending bytes non-blocking.
- `close()` is idempotent via `contextlib.suppress(OSError)`.

`run_node._build_servo` detects the URL form on the existing `servo_port` YAML field:

- `servo_port: "/dev/ttyACM0"` → `SerialTransport` (unchanged behaviour).
- `servo_port: "tcp://host:port"` → `TcpTransport(host, port)`.

No new YAML field. No `runtime_config` schema bump. Backward-compatible by construction: every existing config that uses `/dev/ttyACM*` keeps working.

### Change B — Firmware `wifi_sta` + `tcp_server` (Phase 2, this ADR)

Two new firmware modules under `firmware/main/`:

**`wifi_sta.c/.h`** — WiFi station init. Hardcoded SSID + PSK (see Operational). `wifi_sta_init_and_connect()` blocks until DHCP lease is obtained; the lease IP is logged loud via `ESP_LOGI` so the operator can copy it into the laptop YAML. Disconnect events log loud with the reason byte and trigger an `esp_wifi_connect` retry after a 500 ms backoff (B3, loud-not-silent recovery).

**`tcp_server.c/.h`** — single-client accept loop on TCP port 5555 (matches the Python default). On accept, sets `TCP_NODELAY` + SO_KEEPALIVE (30 s idle / 5 s interval / 3 probes → dead-peer detection in ~45 s). Bytes are fed into the existing `proto_rx_feed()` byte-for-byte; on `PROTO_RX_FRAME_READY` the existing `dispatch_handle()` runs and the reply is `send()`'d on the same socket. Unsolicited startup PONG (servo_uart_v1 §4.5) is sent on every new client connect, preserving the USB-CDC contract.

Single-client-at-a-time is correct per ADR-024 (NodeController single-writer servo invariant). A second concurrent connector blocks in the listen backlog until the first closes.

**`main.c`** is reduced to: NVS init → `servo_init` → `calibration_load_all` → install USB-Serial-JTAG (kept for `ESP_LOG`) → `wifi_sta_init_and_connect()` → `tcp_server_run()` (never returns). The mode-select window + linenoise shell call sites are removed; `shell.c` stays compiled but unreferenced (linker drops with `--gc-sections`, costs zero flash).

**`CMakeLists.txt`** adds `wifi_sta.c` + `tcp_server.c` to `SRCS` and `esp_wifi esp_netif esp_event lwip` to `REQUIRES`.

**`sdkconfig.defaults`** flips `CONFIG_ESP_WIFI_ENABLED=y` (was `n`) and adds WiFi RX-buffer + lwip-keepalive defaults. BLE, OpenThread, 802.15.4 stay off — no coexistence layer needed when one radio is active.

### Change C — Doc updates

- `firmware/README.md` — new "WiFi bring-up" section.
- `docs/MVP-2-NODES-QUICKSTART.md` — new "§2c — WiFi servo path" section.
- `docs/wire-protocols/servo_uart_v1.md` — physical-layer line updated to "USB-CDC (legacy) | TCP/IP over WiFi (current per ADR-027)".

## Operational

**SSID / PSK.** Hardcoded as `"rfmesh"` / `"karasie01"` in `firmware/main/wifi_sta.c`. Changing requires a re-flash. This is bench-grade: the laptop AP is operator-controlled and the credentials live in a comment one paragraph above the `#define`, so the failure mode of "I changed the AP and forgot to rebuild firmware" is loud (no association) and fast (rebuild + flash takes < 60 s on the C6 build).

**Per-node addressing.** DHCP. Each node logs its assigned IP on the USB-Serial-JTAG console at first boot:

```
I (4523) wifi_sta: got IP: 192.168.4.11 (write into laptop YAML as servo_port: "tcp://192.168.4.11:5555")
```

The operator copies the IP into the YAML. Static DHCP leases on the laptop AP (keyed on the node's MAC) make the IPs stable across reboots; not required but recommended.

**Single-client invariant.** A second `rfmesh-node` instance attempting to connect to the same C6 will block in the listen backlog until the first disconnects. This is the firmware enforcing ADR-024's single-writer servo rule at the transport layer (one more line of defence below the controller).

## Consequences

**Solved.**
- USB-CDC enumeration / autosuspend / hub contention removed from the critical path.
- 2x C6 on one laptop no longer share a USB driver stack.
- mDNS-free addressing: operator reads the IP from the boot log; no Avahi dependency.
- Scales to N nodes without new code (TCP listen + accept).

**Carried.**
- USB-CDC stays wired in the firmware as the ESP_LOG console path. Operator plugs USB on first boot to read the IP, can unplug afterwards.
- The `SerialTransport` path is unchanged in Python. Any existing YAML using `/dev/ttyACM*` keeps working — drop into either transport on a per-node basis by editing one YAML field.

**Out of scope (parking lot, future ADRs).**
- EW-survivability of the servo control link. WiFi 2.4 GHz is jammable; an EW-contested deployment that depends on continuous servo control would route this differently (LoRa command bearer, wired fallback, mast-local controller). For BoTH3 bench/demo, irrelevant.
- WiFi provisioning UX (per-node SSID/PSK via NVS instead of hardcoded). Out of scope; reflash is the bench answer.
- Multi-client servo dispatch (priority queueing, observer mode). Out of scope; ADR-024 requires single-writer and we enforce it at the transport.

**Council review.** All four (architect / code-reviewer / rf-dsp-specialist / demo-integrity) before merge to `main` per CLAUDE.md autonomy policy.
