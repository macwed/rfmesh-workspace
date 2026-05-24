# rfmesh — 2-node scan-to-UI MVP quickstart

Bench setup: laptop runs backend + 2× `rfmesh-node`. Two RTL-SDR V4 dongles
sweep with Yagi-on-servo, ship bearings to the local backend, fusion
computes the fix, browser shows the ellipse on `locate.html`.

ETA from a clean checkout: **~20 min** if hardware is plugged in and
already-calibrated.

---

## 0. What you need on the bench

| Item | Qty | Notes |
|---|---|---|
| RTL-SDR V4 dongle | 2 | Distinct serials (set with `rtl_eeprom -s <S>` once). |
| ATK-10 Yagi (868 / 915 MHz) | 2 | Plus the SMA pigtail to the dongle. |
| MG996R servo + bracket + mast | 2 | Yagi mounts on the servo horn. |
| ESP32-C6 servo controller | 2 | Calibrated + flashed per `firmware/README.md`. NVS persisted (memory: CAL_PERSIST is a cosmetic ACK glitch; calibration IS saved). |
| Laptop with 2 spare USB-A | 1 | Linux preferred; macOS works for the software, may need permission tweaks for `rtl_sdr`. |
| Controlled emitter | 1 | The LoRa beacon in `firmware-beacon/` is the easiest. Anything narrow-band in the configured center frequency works. |

---

## 1. One-time setup (~10 min, do once)

### 1a. Install `uv` if not present

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1b. Clone + workspace sync

```bash
git clone <repo> rfmesh-workspace
cd rfmesh-workspace
uv sync
```

### 1c. RTL-SDR system-side prep (Linux)

```bash
# udev: lets you talk to the dongles without root.
sudo cp deployment/node/40-rtlsdr.rules /etc/udev/rules.d/   # if file exists
sudo udevadm control --reload-rules && sudo udevadm trigger

# Blacklist the DVB kernel driver (otherwise dongles get grabbed at boot).
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtlsdr.conf
sudo rmmod dvb_usb_rtl28xxu 2>/dev/null || true

# Sanity:
rtl_test -t                # both dongles list, with serials
ls /dev/ttyACM*            # both ESP32-C6 ports list
```

### 1d. Set distinct RTL-SDR serials (one-time, per dongle)

Default serial is `00000001` on every brand-new V4. The two YAMLs assume
`00000001` and `00000002`. Set the second one:

```bash
# Unplug the FIRST dongle so eeprom-write hits the only-plugged-in one.
rtl_eeprom -d 0 -s 00000002
# Re-plug both. Verify:
rtl_test -t | grep -i serial
```

### 1e. Backend deps

Already done if `1761266` is on your branch; verify:

```bash
cd deployment/bacrtl_eeprom -d 0 -s 00000002kend
uv sync
cd -
```

---

## 2. Edit the two node YAMLs (~2 min)

Open `configs/node-laptop-01.yaml` and `configs/node-laptop-02.yaml`. Each
has 4-5 fields to set (everything else is sane defaults). The lines
flagged with `# CHANGE:` in the YAML comments:

| Field | What to put |
|---|---|
| `node.position.lat_deg/lon_deg` | Surveyed lat/lon of the **physical mast** in your bench layout (smartphone GPS ±10 m is fine). |
| `node.heading_deg` | Antenna boresight, degrees true CW from north. Point the Yagi at a known landmark, read the bearing off a map. |
| `node.sdr.serial` | `"00000001"` / `"00000002"` — match the dongle on each node. |
| `node.sdr.center_freq_hz` | Beacon center frequency, in Hz (e.g. `915.0e6` for 915 MHz). |
| `servo_port` | `/dev/ttyACM0` / `/dev/ttyACM1` — order in `ls /dev/ttyACM*`. |

**Baseline geometry tip:** for a useful triangulation, the two nodes
should be **~0.5 – 2 km apart**, with the emitter ~1 km from both, forming
a triangle (not a straight line). The default templates put node-02 ~1 km
east of node-01.

---

## 2b. No-servo fast-path (hand-rotated Yagi, manual sweep)

If the servo + ESP32-C6 are not on the bench (or you want the
fastest path to a fix on the map), skip §3-onwards and use
`scripts/bench_manual_sweep.py`. The script samples IQ from the
RTL-SDR, prompts the operator to point the Yagi at each commanded
geographic azimuth, finds the peak, refines it with a 3-point
parabolic fit, computes an honest 1-σ from the amplitude-DF CRLB,
and POSTs a `BearingReport` to the backend.

From three terminals at the repo root:

```bash
# Terminal 1 — backend (same as §3, no servo dep)
bash scripts/bench-2-nodes.sh backend
```

```bash
# Terminal 2 — node 1, hand-rotate Yagi #1
uv run python scripts/bench_manual_sweep.py \
    --sdr-serial 00000001 --center-freq-hz 915.0e6 \
    --start-deg 0 --end-deg 360 --step-deg 30 \
    --fusion-url http://127.0.0.1:8000 \
    --node-id node-manual-01 \
    --lat <node1-lat> --lon <node1-lon> \
    --node-label N1
```

```bash
# Terminal 3 — node 2, hand-rotate Yagi #2
uv run python scripts/bench_manual_sweep.py \
    --sdr-serial 00000002 --center-freq-hz 915.0e6 \
    --start-deg 0 --end-deg 360 --step-deg 30 \
    --fusion-url http://127.0.0.1:8000 \
    --node-id node-manual-02 \
    --lat <node2-lat> --lon <node2-lon> \
    --node-label N2
```

Per node: the script prints the prompt for each angle. Operator
rotates the Yagi to that angle (eyeball off the compass / map),
holds it steady, presses Enter. After the full arc the script
prints the table + peak + sigma, then POSTs.

Open both UI pages:

- <http://127.0.0.1:8000/link.html> — pointing arrow on each node
  plus the **Sweep peak** + **Sweep SNR / age** rows in the detail
  drawer (click a node in the sidebar). Updates on every POST.
- <http://127.0.0.1:8000/locate.html> — LOB ray per node + **fix
  ellipse** (Stansfield+MLE) once both peaks land within the same
  500 ms fusion-batch window. Click the marker for GDOP, semi-major,
  residuals — the full demo-honesty payload (B4).

Both operators can sweep again to refine — each new POST replaces
the previous bearing for that node_id. The ellipse shrinks visibly
as sigma drops (finer `--step-deg`) or the geometry improves
(operators move the nodes apart).

For one-shot stdout-only (no UI push): drop `--fusion-url` + position
flags.

---

## 3. Bring up the demo (3 terminals)

Run the preflight check once first; it loudly refuses if anything is
missing.

```bash
bash scripts/bench-2-nodes.sh preflight
```

Then, in **three terminals from the repo root**:

```bash
# Terminal 1 — backend
bash scripts/bench-2-nodes.sh backend
```

```bash
# Terminal 2 — node 1
bash scripts/bench-2-nodes.sh node 1
```

```bash
# Terminal 3 — node 2
bash scripts/bench-2-nodes.sh node 2
```

Each node terminal prints a startup banner with `arc [-90, 90] deg step
2.0` and starts ticking sweep iterations. Each sweep takes ~20 s
(91 angles × ~200 ms settle + dwell).

---

## 4. What you should see in the browser

Open both pages side-by-side:

| URL | What it shows |
|---|---|
| <http://127.0.0.1:8000/locate.html> | Map. After 1st sweep from each node: two **LOB rays**. Once both rays land within the same ~500 ms batch window, fusion fires and an **ellipse + centre dot** appear at the intersection. |
| <http://127.0.0.1:8000/link.html> | Sidebar with both nodes listed. Each node shows a **5-state badge** (initially `searching` → `acquired` once a bearing arrives), calibrated arc slider, manual-steer controls. The detail drawer's "Peer bearing" row stays `—` (peer rendezvous not configured in this MVP — emitter-only scan). |

Refresh once after the first fix lands if the map auto-poll feels stale.

---

## 5. Verify it's working

| Check | How |
|---|---|
| Bearings arriving | `curl http://127.0.0.1:8000/health` → `bearing_count` > 0 |
| Fix computed | Same `/health` → `fix_count` > 0 |
| Fix is honest | Open `locate.html`, click the fix marker → detail card shows `GDOP`, `Semi-major`, `Method=stansfield+mle` (or `stansfield`), `Residuals` per node. **If GDOP is "uncomputable (...)" — your two nodes are collinear with the emitter; move one.** |
| WS live push working | `link.html` status line in sidebar reads `live` (not `connecting…` / `WS closed`). |

---

## 6. Common gotchas

| Symptom | Fix |
|---|---|
| `rtl_test`: "No supported devices found" | `sudo rmmod dvb_usb_rtl28xxu` (one-shot) or re-do the modprobe blacklist (1c). |
| `no ESP32 servo port found` | Plug the ESP32-C6 in. Check `dmesg \| tail` for the assigned `/dev/ttyACM*`. |
| Backend says `port already in use` | Kill stragglers: `pkill -f "uvicorn both3_poc"`. |
| Node logs `prominence-gate failure` every sweep | Emitter too weak; bring it closer OR increase `node.sdr.gain_db` in YAML (default 30; max ~50). |
| Two LOBs but no ellipse | Probably timing — bearings arriving outside same 500 ms window. Bump `FUSION_BATCH_WINDOW_MS=1000` in the backend env (edit `scripts/bench-2-nodes.sh` backend block). |
| `node.heading_deg` wrong | Ellipse will sit at the wrong absolute position but right *direction* from the nodes. Re-survey the boresight angle, re-edit YAML, restart the node. |
| Slider on `link.html` says "Node arc unknown — awaiting handshake" | Node didn't send `node_hello`. Check `command_endpoint.enabled: true` in YAML; node logs should show `CommandChannel <id>: connected to ws://...`. |
| Slider says "Manual steering not yet enabled on this node" | Controller wired, but `controller_ready=false` — this means `servo_port` is `null` or the servo failed to open. Check the node startup log. |

---

## 7. Stop the demo

`Ctrl-C` each of the three terminals. Order doesn't matter; the node
processes drain cleanly (`shutdown` is idempotent).

To wipe the in-memory fix/bearing store between runs, just restart the
backend — `Store` is in-memory (`SEED_DEMO=false` in the script means no
synthetic data leaks in).

---

## 8. Field/Pi later (optional)

The bench YAMLs are the same shape as the Pi YAMLs. To migrate:

```bash
cp configs/node-laptop-01.yaml configs/node-pi-<cpu-serial>.yaml
# adjust position + heading + fusion_endpoint to point at your backend Pi.
sudo bash field-deploy/install-service.sh    # one-shot systemd install
```

See `field-deploy/README.md` for the SD-card-swap zero-config story.

---

## 9. What this MVP does NOT do (yet, honestly)

- **Live link margin (dB)** on `link.html` — waits on ADR-025 comms-mode (the parallel `comms/` work in this branch).
- **Motion-delta auto-flip** to RE-ACQUIRING when a node is moved — waits on IMU integration.
- **L3 emitter classification label** on the marker — `rfmesh-ml` packaged but not auto-run in this MVP.
- **Multi-peak harvest** during peer-refine sweeps — ADR-026 §Iter 1; today rendezvous emits 1 primary peak.

None of those block the 2-node scan-to-ellipse story; they're nice-to-haves
for the comms-mode slide later.

---

*Questions / gotchas not in §6: open the node log first (`journalctl -u
rfmesh-node -f` on Pi, or scroll the terminal on laptop). Every refusal
path in this codebase is loud per B3 — the answer is almost always in
the log.*
