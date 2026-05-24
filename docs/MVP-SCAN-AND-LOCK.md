# Minimal 2-node scan-and-lock proof

Smallest possible demo: prove each node can **sweep, find the strongest
RSSI peak, point the servo at it, and stay**. No backend, no UI, no
fusion, no network. Two independent terminal sessions, one per node.

ETA from working hardware: **~2 minutes per node**.

---

## What you need

- 2× RTL-SDR V4 with distinct serials (`rtl_test -t` to verify).
- 2× ATK-10 Yagi + MG996R servo + calibrated ESP32-C6 controller.
- 1× emitter (LoRa beacon, signal generator, anything narrowband on
  the configured center freq).
- 1× laptop, `uv sync` already done.

---

## Run, terminal 1 (node 1)

```bash
uv run python scripts/bench_lock.py \
    --servo-port /dev/ttyACM0 \
    --sdr-serial 00000001 \
    --center-freq-hz 915.0e6 \
    --gain-db 30 \
    --heading-deg 90
```

## Run, terminal 2 (node 2)

```bash
uv run python scripts/bench_lock.py \
    --servo-port /dev/ttyACM1 \
    --sdr-serial 00000002 \
    --center-freq-hz 915.0e6 \
    --gain-db 30 \
    --heading-deg 270
```

---

## What to expect

Each terminal logs:

```
INFO bench_lock: bench_lock: arc [-90, 90] step 2.0, boresight 90.0, freq 915.000 MHz
INFO bench_lock: sweep 1 — parking at -90.0 deg
INFO bench_lock: LOCKED  az 142.7 deg  ± 3.2 deg  SNR 18.4 dB  servo 52.7 deg  (Ctrl-C to release)
```

Then the servo stays pointing. The node is done. Ctrl-C to release.

If no peak found (signal too weak / off-band), the script logs
`sweep N: no peak (<reason>); retrying` and keeps sweeping until you
fix the signal or Ctrl-C.

---

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--servo-port` | required | `/dev/ttyACM0` etc. |
| `--sdr-serial` | required | from `rtl_test -t`. Use `rtl_eeprom -d 0 -s <S>` to set. |
| `--center-freq-hz` | required | emitter freq, e.g. `915.0e6`. |
| `--heading-deg` | required | surveyed antenna boresight, true deg CW from N. |
| `--sweep-min-deg` / `--sweep-max-deg` | -90 / +90 | servo arc relative to boresight. |
| `--sweep-step-deg` | 2.0 | angles. Arc / step >= 7 (parabola fit). |
| `--gain-db` | 30 | RTL-SDR fixed gain. Bump to 40-50 if signal weak. |
| `--peak-prominence-db-min` | 6 | drop to 3-4 if estimator refuses on a real peak. |
| `--log-level` | INFO | DEBUG if you want every sweep step. |

---

## Stop / reset

Ctrl-C in each terminal. Receiver + servo close cleanly. Re-run to
re-lock (e.g. emitter moved).

---

## Failure modes + fixes

| Symptom | Cause / fix |
|---|---|
| `LOCKED` but servo at wrong arc end | `--heading-deg` wrong; re-survey boresight. |
| `peak az X deg maps to servo Y deg outside arc` | Same. Heading-deg survey wrong by > arc/2. |
| `sweep N: no peak (prominence-gate failure ...)` repeats | Signal too weak. Bump `--gain-db` or `--peak-prominence-db-min 3.0`. |
| `RTLSDRDevice: ...rtl_sdr failed` | DVB driver grabbed dongle: `sudo rmmod dvb_usb_rtl28xxu`. |
| `servo.connect(): timeout` | Wrong `--servo-port`; check `ls /dev/ttyACM*` + `dmesg \| tail`. |

---

## When you want the full pipeline back

`docs/MVP-2-NODES-QUICKSTART.md` brings up backend + fusion + UI on
top of the same nodes (just `bash scripts/bench-2-nodes.sh ...`). The
`bench_lock.py` script and the full `rfmesh-node` CLI both produce the
same scan + servo motion; the difference is what happens to the
bearing after lock (this script: nothing; full path: ships to
backend → fusion → ellipse on `locate.html`).
