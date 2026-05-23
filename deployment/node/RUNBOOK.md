# Field Node Runbook — L1 (Pi + RTL-SDR + ESP32 servo + Yagi)

From bare hardware to a live line-of-bearing on the map. One node, the L1
amplitude-comparison path: the servo sweeps the directional antenna, the node
finds the RSSI peak, and POSTs the bearing to the backend, which draws it.

```
 ESP32 (servo ctrl) ──USB──┐
                           ├── Raspberry Pi ──(wifi/eth)──► Docker backend ──► map (GET /bearings)
 RTL-SDR + Yagi ─────USB──┘     runs rfmesh-node                POST /bearings
```

> **Verified data path.** The node POSTs `BearingReport.model_dump(mode="json")`;
> the backend `POST /bearings` runs `BearingReport.model_validate(...)` on the
> *same* `rfmesh_contracts.BearingReport` (schema 1.1.0). The serialize→validate
> round-trip is exact by construction — the node sends data the Docker backend
> accepts and renders. No format adapter needed.
>
> Hardware (mast, cabling, polarisation, antenna mounting, the servo's own
> 5–6 V supply) is the operator's domain — out of scope here (AGENTS.md B6).
> This runbook is the **software** bring-up.

---

## Part A — Builder: prep one node (do this before the field)

### A1. Provision the Pi (once)
```bash
git clone <repo> rfmesh-workspace && cd rfmesh-workspace
sudo bash deployment/node/setup-node.sh
# reboot so plugdev/dialout group membership takes effect
```
`setup-node.sh` installs the `rtl-sdr` userland, **blacklists the kernel DVB
driver** (`dvb_usb_rtl28xxu` — it otherwise grabs the dongle), adds udev rules
for non-root access, and runs `uv sync` (fetches Python 3.12 + deps).

### A2. Prove each link, one at a time (so a fault points at one thing)
```bash
rtl_test -t                                                   # SDR: "Found 1 device(s)"
uv run python scripts/servo_bringup.py --port /dev/ttyACM0 --set-default-cal   # servo swings
uv run python scripts/firstlight_l1.py --servo-port /dev/ttyACM0 \
    --freq 915e6 --rate 2.4e6 --gain 30 --boresight 90        # whole chain, no backend
```
`firstlight_l1.py` sweeps for real and prints `OK bearing 1xx.x +/- y.y deg`.
This is the gate: a sane bearing toward a known emitter = the hardware works.
(Run `--sim` first on any laptop to learn the output.)

### A3. Fill the config, assign a landmark
Edit the ALL-CAPS placeholders in `configs/node-pi-01.yaml`:

| field | value |
|---|---|
| `position.lat_deg/lon_deg` | surveyed node location (phone GPS is fine; bearing error dominates) |
| `heading_deg` | **antenna boresight azimuth** — the bearing of a landmark the soldier will aim at. Survey-and-align; servo 0° == boresight. Set this at prep, **not** in the field. |
| `sdr.center_freq_hz` | emitter band under test |
| `fusion_endpoint` | `http://<docker-host>:8000` |

Validate before shipping the node:
```bash
uv run python -c "import yaml; from rfmesh_contracts import NodeConfig; \
NodeConfig.model_validate(yaml.safe_load(open('configs/node-pi-01.yaml'))); print('config OK')"
```

### A4. (Optional) make it power-on-and-walk-away
Only after A3 (the config has real values):
```bash
sudo bash deployment/node/setup-node.sh --enable-autostart
```
Installs a systemd unit running `node-up.sh` at boot, `Restart=on-failure`.
Manage / watch: `systemctl status rfmesh-node`, `journalctl -u rfmesh-node -f`.

---

## Part B — Soldier: put it up (no jargon, ~3 minutes)

You have: the box (Pi), the dongle with the pigtail, the controller stick, the
pointer motor with the aerial, the power lead.

1. **Stand the aerial level**, pointing at the **assigned landmark** (you were
   told which one). That aim is what makes the readings true — don't guess.
2. **Plug the three USB cables into the box** (they only fit one way): dongle,
   controller stick, then power.
3. **Power on. Wait.** The motor swings the aerial left-to-right by itself —
   **that swing means it's alive.** No swing in 30 s → unplug power, replug, wait.
4. **Confirm and report.** Tell command: the **landmark you aimed at** and your
   **position/grid**. You're done — leave it running.

If it never swings: a cable is loose or the controller isn't flashed — that's a
builder fix, flag it. Do not open the box or change settings.

> Builder note: with A4 enabled, step 4 is the soldier's whole job — no terminal.
> A 3-LED indicator (green/amber/red, GPIO off the node log) would make status
> readable without a screen; that wiring is the one remaining gap to fully
> screen-free. Until then, status is the map and `journalctl -u rfmesh-node -f`.

---

## Part C — Commander: read the map, make the call

What you see and what you may act on. The point is **honesty** — the screen
never overstates how sure it is.

- **One node up → one line** from that sensor outward: a *direction*, not a
  place. Reads "something that way." Decision: **task another sensor that way.**
- **Two nodes → lines cross,** a fix with an **uncertainty ellipse**. Wide
  ellipse = rough place. Decision: **cue more collection, don't action alone.**
- **Three+ nodes → ellipse shrinks,** per-node residuals + GDOP (geometry
  quality) shown. Tight ellipse + low GDOP + consistent nodes = **HIGH, a
  location you can hand to a shooter.** A disagreeing node is *flagged by name*
  (multipath / bad aim), not hidden.

Trust moments that make it credible:
- **Lose a node** (cable cut) → ellipse **grows** at once. It degrades, it
  doesn't pretend. "Re-establish before acting."
- **Add a node** → ellipse shrinks. You watch precision bought by density.
- **GNSS jammed** → shown as a separate flag; the mesh keeps working (no GPS
  dependency) and the jamming itself is intel.
- **No fake numbers** — "RSSI/SNR (relative)", never absolute dBm it can't claim.

> Honesty caveat for the commander: the **cross-fix ellipse from live `/bearings`
> is not wired yet** (needs the fusion ingest — see limits). Today the map shows
> trustworthy **lines of bearing per node**; the ellipse story is the next step.

---

## Part D — Operate

### Start a node by hand
```bash
deployment/node/node-up.sh
# overrides: CONFIG=configs/node-west.yaml   SERVO_PORT=/dev/ttyACM1
```
Pre-flight checks, in order: config present → RTL-SDR enumerated → servo port
found → **backend `/health` reachable** (warns, doesn't fail — `HttpBearer` is
best-effort and you may bring the backend up after). Then it streams; one log
line per sweep:
```
L1 sweep: bearing 102.4 +/- 1.8 deg (SNR 21.3 dB)     # or an honest refusal
```

### Bring up the Docker backend (once, reachable from the nodes)
```bash
cd deployment && docker compose up -d backend
curl http://<docker-host>:8000/health        # {"status":"ok", ...}
```

### Confirm bearings are landing
```bash
curl http://<docker-host>:8000/bearings | python -m json.tool
```
A GeoJSON node Point + LOB LineString per node = success. Open the map; the
line-of-bearing is there. Repeat Part A3–B with a new `node_id`/`position` for a
second node off to the side (not collinear with the first).

---

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `rtl_test`: no device | dongle unplugged, or DVB driver still bound → `sudo modprobe -r dvb_usb_rtl28xxu`; re-run `setup-node.sh`, reboot |
| `usb_claim_interface error -6` | kernel DVB driver holds the dongle — same fix as above |
| servo never moves | ESP32 not flashed (`cd firmware && idf.py -p <port> flash`), wrong `--servo-port`, or servo not on its own 5–6 V supply (browns out) |
| `L1 sweep: no bearing (prominence … < gate)` | peak doesn't clear the off-axis floor — **widen the arc beyond the antenna HPBW** (default ±90°), aim closer/stronger, raise gain. This is the Phase-C physics question (INHERITED_CONTEXT.md §3.1), an honest refusal, not a bug |
| node "up" but nothing on the map | check the start-up `[WARN] backend NOT reachable`; verify `fusion_endpoint` host/port and `curl …/health`; confirm `POST /bearings` returns `{"accepted":1}` |
| bearing points wrong way | `heading_deg` (boresight) wrong, or aimed at the wrong landmark; precise sweep-scale needs per-axis servo cal (default cal is "moves," not "points true") |

---

## Scope (honest)

- **Is:** one real L1 node → one honest bearing line, end to end, verified into
  the Docker backend.
- **Isn't yet:** multi-node cross-fix → `FixEvent`/ellipse (needs fusion
  ingest), LoRa bearer, precise per-axis servo calibration, GPS auto-position,
  on-device LED status. See the plan's "out of scope" list.

### Transport note
The bearer is chosen by the `fusion_endpoint` **scheme** (no schema change):
`http(s)://` → `HttpBearer` POSTs JSON to the backend `POST /bearings`;
`udp://host:port` → the salvaged `WifiBearer` (UDP/msgpack) toward UDP fusion.
For lighting up the Docker map, use `http://`.

## Quick reference

| step | command |
|---|---|
| provision Pi | `sudo bash deployment/node/setup-node.sh` |
| check SDR | `rtl_test -t` |
| check servo | `uv run python scripts/servo_bringup.py --port /dev/ttyACM0 --set-default-cal` |
| first light | `uv run python scripts/firstlight_l1.py --servo-port /dev/ttyACM0 --freq 915e6` |
| validate config | `python -c "import yaml,rfmesh_contracts as c; c.NodeConfig.model_validate(yaml.safe_load(open('configs/node-pi-01.yaml')))"` |
| enable boot start | `sudo bash deployment/node/setup-node.sh --enable-autostart` |
| run node | `deployment/node/node-up.sh` |
| backend up | `cd deployment && docker compose up -d backend` |
| see bearings | `curl http://<docker-host>:8000/bearings` |
