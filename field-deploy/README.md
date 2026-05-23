# field-deploy — Orange Pi autostart for rfmesh field nodes

Systemd service and startup script for deploying rfmesh L1 nodes on Orange Pi
hardware. Config is selected automatically from the Pi's hardware serial — SD
cards can be swapped between units with zero manual changes.

Does not touch anything in `deployment/` or the rest of the workspace.

---

## Files

| File | Purpose |
|---|---|
| `rfmesh-node.sh` | The long-running process systemd supervises (`ExecStart`). Runs pre-flight checks then hands off to `rfmesh-node` via `exec`. |
| `rfmesh-node.service` | Systemd unit template. `@USER@` and `@REPO_ROOT@` are substituted by the installer. |
| `rfmesh-node.env.example` | Optional overrides (copy to `/etc/rfmesh-node.env` only if you need to force a specific config or serial port). |
| `install-service.sh` | One-time root installer: fills in the unit template, installs it, and enables it. |

---

## How config selection works

On every boot, `rfmesh-node.sh` reads the Pi's CPU serial from
`/proc/cpuinfo`, strips leading zeros, and loads:

```
configs/node-<serial>.yaml
```

**This is what makes SD card swaps zero-config.** Each physical Pi has its
own YAML named after its serial. The SD card carries the same script on every
unit; the right config is picked automatically based on which Pi it boots on.

To find out a Pi's serial before creating its config:

```bash
grep -m1 '^Serial' /proc/cpuinfo | awk '{print $3}' | sed 's/^0*//'
# e.g. a3f81c2d
```

Then create `configs/node-a3f81c2d.yaml` for that Pi. Repeat for each unit.

If `/proc/cpuinfo` has no Serial entry (unusual), the script falls back to
`hostname -s`.

---

## Prerequisites

`deployment/node/setup-node.sh` must have been run first — it installs
`rtl-sdr`, blacklists the DVB kernel driver, sets udev rules for the RTL-SDR
and ESP32, and runs `uv sync`. See that script for details.

---

## One-time setup per Pi

```bash
# 1. Run setup-node.sh if not done yet
sudo bash deployment/node/setup-node.sh

# 2. Find this Pi's serial and create its config
grep -m1 '^Serial' /proc/cpuinfo | awk '{print $3}' | sed 's/^0*//'
# → e.g. a3f81c2d
cp configs/node-pi-01.yaml configs/node-a3f81c2d.yaml
# edit the new file: node_id, position, heading_deg, fusion_endpoint, etc.

# 3. Install the autostart service
sudo bash field-deploy/install-service.sh

# 4. Start immediately (also starts automatically on every subsequent boot)
systemctl start rfmesh-node
```

---

## SD card swap

No changes needed on the card itself. The target Pi's serial determines which
config is loaded. As long as `configs/node-<serial>.yaml` exists for that Pi,
it just works.

---

## Operational commands

```bash
journalctl -u rfmesh-node -f      # live logs
systemctl status  rfmesh-node     # current state
systemctl restart rfmesh-node     # after editing a config YAML
systemctl stop    rfmesh-node     # for maintenance
systemctl disable rfmesh-node     # remove from boot
```

---

## Overrides (optional)

If you need to force a specific config or pin the servo port, copy the env
example and uncomment the relevant lines:

```bash
sudo cp field-deploy/rfmesh-node.env.example /etc/rfmesh-node.env
sudo nano /etc/rfmesh-node.env
```

Available overrides:

| Variable | Default | Notes |
|---|---|---|
| `CONFIG` | `configs/node-<serial>.yaml` | Force a specific YAML. |
| `SERVO_PORT` | auto-detect `/dev/ttyACM*` | Pin a port when two CDC devices are present. |

---

## What the startup script checks

1. Config YAML exists — fails loudly with the expected filename if not.
2. RTL-SDR present (`rtl_test -t`) — fails, systemd restarts after 15 s.
3. ESP32 servo port found (`/dev/ttyACM*`) — fails the same way.
4. Backend HTTP `/health` reachable — **warns only**, node stays up and
   continues forwarding bearings once the backend comes up.

All output goes to the systemd journal (`journalctl -u rfmesh-node`).
