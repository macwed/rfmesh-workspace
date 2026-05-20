# rfmesh — User Manual

How to set up the workspace, run the dev gates, drive the demo, and build /
flash the firmware. Companion to `README.md` (orientation) and `AGENTS.md`
(governance). Where this manual and a package-local README disagree, the
package README wins — those track the code closer.

**Audience:** anyone new to the repo (operator, contributor, agent) who
wants the shortest path from a fresh clone to a running dashboard or a
green `just verify`.

---

## §1 Prerequisites

Required for the Python side:

- **Python 3.12.** Enforced by every package's `requires-python = ">=3.12"`.
- **`uv`** (≥ 0.5). Install per
  <https://docs.astral.sh/uv/getting-started/installation/>. `uv` manages
  the workspace `.venv` and the lockfile.
- **`just`** for the task recipes. macOS: `brew install just`. Linux:
  `cargo install just` or your distro package.
- Linux preferred. Windows works but firmware tooling assumes Linux paths.

Optional, only needed for specific tasks:

- **ESP-IDF v5.1+** (firmware salvo) — see `firmware/README.md`.
- **PlatformIO Core ≥ 6.0** (LoRa beacon) — see `firmware-beacon/README.md`.
- **PyQt5/PyQt6** (alternative matplotlib backend if `TkAgg` lags) —
  `pip install PyQt5`, then `MPLBACKEND=QtAgg`.
- **SDR libraries** (`SoapySDR`, `librtlsdr`, `libbladeRF`, `pyadi-iio`) —
  only when running against real hardware. The simulator path needs none.

---

## §2 First-time setup

From the repo root:

```bash
uv sync
```

That creates `.venv/`, resolves the lockfile, and installs every
workspace package (everything under `packages/*` and `apps/*`) editable
into the shared venv. After this, any `uv run …` command sees every
package importable as Python and exposes the console scripts listed in
§4.

Verify the install:

```bash
just verify
```

Runs ruff + ruff format check + import-linter contracts, then `mypy --strict`
workspace-wide, then `pytest -m "not hardware"`. Takes a few minutes on
first run (mypy populates its cache). On a green main branch this exits 0.

If `just verify` fails on a fresh clone, that is news — open an issue rather
than papering over it (per `docs/ONBOARDING.md` §4).

---

## §3 The `just` recipes

All recipes live in `justfile`. Run from the repo root.

| Recipe | What it does |
|---|---|
| `just` (no arg) | Alias for `just verify`. |
| `just verify` | The full gate: `lint` + `type` + `test`. The command `AGENTS.md` §5 names; run before declaring any ticket done. |
| `just lint` | `ruff check .` + `ruff format --check .` + `lint-imports` (the six `import-linter` contracts from `pyproject.toml`). |
| `just fmt` | `ruff format .` + `ruff check --fix .`. Run this before committing if `just lint` complains about formatting. |
| `just type` | `mypy packages/` (workspace-wide, strict). |
| `just test` | `pytest -m "not hardware"`. Treats pytest exit 5 (no tests collected) as success during early scaffolding. |
| `just test-cov` | Same as `just test` with `--cov --cov-report=term-missing`. |
| `just test-hardware` | `pytest -m hardware`. Requires real SDR / servo plugged in; **will fail in CI**. |
| `just ticket-verify <pkg>` | Scoped verify for one package: `pytest packages/<pkg>` + `mypy packages/<pkg>` + `ruff check packages/<pkg>`. Example: `just ticket-verify rfmesh-dsp`. |
| `just clean` | Removes `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.coverage`, `htmlcov`, `dist`, `build`, plus `__pycache__/` and `*.egg-info/` trees. Source and git state untouched. |

When a ticket is declared done, paste the `just verify` output into the
conversation — that's the contract with reviewers.

---

## §4 Console scripts (installed by `uv sync`)

Each is a `[project.scripts]` entry in some package's `pyproject.toml`.
Run with `uv run <script> --help` for the canonical help.

### 4.1 `rfmesh-node` — node runtime (`rfmesh-node`)

```bash
uv run rfmesh-node --config configs/<node>.yaml [--log-level INFO]
```

Loads a `NodeConfig` from YAML, builds a `Receiver` + `Bearer`, runs
`Node.run()` until `SIGINT`/`SIGTERM`. v1.0 only accepts
`sdr.driver: "sim"`; real-hardware drivers raise `NotImplementedError`
with a loud message pointing to `rfmesh-demo-replay`. Use this for the
plumbing smoke-test, not the demo. Exit codes: `0` clean, `2` for any
config or capability error.

### 4.2 `rfmesh-fusion-server` — fusion service (`rfmesh-node`)

```bash
uv run rfmesh-fusion-server --config configs/<fusion>.yaml [--log-level INFO]
```

Loads `FusionConfig`, builds `StansfieldMLEFuser`, optionally wires a CoT
publisher (`cot_url` set + `rfmesh-cot` installed) and a `DashboardPubSub`
WebSocket channel, then runs `FusionService.run()` until `SIGINT`.
Headless when `cot_url` is omitted — useful for bench runs that only
watch the dashboard.

### 4.3 `rfmesh-ops` — live ops dashboard (`rfmesh-ops`)

```bash
uv run rfmesh-ops \
    --connect ws://10.0.0.1:9001 \
    --layout {trench,debug,minimal} \
    [--beat-e-cache cache/beat_e_trench.npz] \
    [--log-level INFO]
```

Subscribes to a running `FusionService`'s WebSocket dashboard channel,
renders a matplotlib figure with the named layout. Layouts:

- `trench` — the 4×2 BoTH3 jury layout (default).
- `debug` — all 9 panels, bench-side.
- `minimal` — `FixPanel` only, for screenshots.

`--beat-e-cache` pre-populates `NullSteeringPanel` from a Beat-E
pre-render so the engaged-null pattern appears instantly at stage time
(see §4.7). Backend defaults to whatever `MPLBACKEND` resolves to; force
`QtAgg` for laptops where `TkAgg` lags.

### 4.4 `rfmesh-servo-calibrate` — guided servo calibration (`rfmesh-servo`)

```bash
uv run rfmesh-servo-calibrate --port /dev/ttyACM0 --axis 0
```

Walks the operator through aligning a servo's mechanical sweep to its
PWM range and persists the resulting `Calibration` to the ESP32-S2's
NVS (procedure §3.7 of `docs/wire-protocols/servo_uart_v1.md`).
Required before an L1 node can ship honest `azimuth_deg` (Invariant
B3 — pulse-range assumptions in DSP code are forbidden, see
`INHERITED_CONTEXT.md` §1.2).

### 4.5 `rfmesh-demo-replay` — full demo orchestrator (`rfmesh-demo-replay`)

```bash
uv run rfmesh-demo-replay \
    --scenario scenarios/trench_demo.yaml \
    [--pessimism 1.0|1.5|2.0] \
    [--no-cot] [--headless] \
    [--channel-override free_space|two_ray_ground|multipath_fir|log_normal_shadowing|composite] \
    [--log-level INFO]
```

The BoTH3 jury demo path. Loads a scenario, builds an in-process
`ReplayOrchestrator` (N node tasks + 1 fusion + 1 dashboard, all
asyncio in one process), walks through the beats. Prints a one-line
summary per beat. `--pessimism` inflates the simulator noise floor by
`10·log10(F)` dB for Phase-C overlays. `--channel-override` forces a
single atomic channel-model kind regardless of what the scenario YAML
declares. `--headless` disables the in-process dashboard subscriber.

### 4.6 `rfmesh-demo-record` — IQ capture (`rfmesh-demo-replay`)

```bash
uv run rfmesh-demo-record \
    --scenario scenarios/trench_demo.yaml \
    --output recordings/<session>/ \
    --duration-s 10 \
    [--beat-id <id>] [--log-level INFO]
```

Builds one `SyntheticReceiver` per node in the scenario, captures
`--duration-s` seconds in parallel, writes `<node_id>.iqx` (interleaved
complex64 little-endian) plus `<node_id>.json` (`ReplayMetadata`
sidecar) per node. v1.0 drives the simulator only; the hardware capture
path lands when `rfmesh-sdr` ships its native `Receiver` implementations.

### 4.7 `rfmesh-beat-e-preload` — Beat E cache (`rfmesh-demo-replay`)

```bash
uv run rfmesh-beat-e-preload \
    --out cache/beat_e_trench.npz \
    --array=ULA --n=2 --spacing=0.164 --freq=915e6 \
    --signal-deg=306 --jammer-deg=126 \
    [--signal-snr-db 30] [--jammer-to-signal-db 20] \
    [--scan-step-deg 0.5]
```

Pre-computes the Beat E.0 baseline (uniform weights) and Beat E.1
engaged-null (MVDR weights against a synthetic `R` with a jammer at
`--jammer-deg`) receive patterns, saves as a portable `.npz` cache
(no pickle). Loaded by `rfmesh-ops --beat-e-cache` in < 200 ms (vs
2–3 s for the full DSP recompute) so the operator's "Engage null"
button registers as instant to the audience. Run once before stage
time. The trench-demo canonical invocation is the example above.

---

## §5 Quick-look helper scripts (under `scripts/`)

Not console scripts — invoke directly with `uv run python …`.

### 5.1 `scripts/show_dashboard.py`

```bash
uv run python scripts/show_dashboard.py \
    [--scenario scenarios/trench_demo.yaml] \
    [--layout {trench,debug,minimal}] \
    [--cot-url tcp://localhost:8087]
```

Bolts the orchestrator + dashboard into one Python process so the
operator can see the live dashboard against the simulator without
running `rfmesh-fusion-server` and `rfmesh-ops` in two terminals.
Pumps the matplotlib GUI event loop on a 20 Hz task so the figure
actually refreshes. After the beats finish the figure stays open until
the operator closes it or hits Ctrl-C. With `--cot-url`, every
`FixEvent` is also serialised as CoT XML and shipped to a running
FreeTAKServer / ATAK device (`tcp://localhost:8087`, `tcp://<phone>:8087`,
or a TAK multicast group).

### 5.2 `scripts/capture_demo_artifacts.py`

```bash
uv run python scripts/capture_demo_artifacts.py
```

Runs the trench-demo scenario end-to-end, snapshots every
`BearingReport` and `FixEvent`, and after each beat:

- renders the trench-demo dashboard with accumulated state and saves a
  PNG under `docs/demo/artifacts/`;
- appends the fix (if any) to a per-beat fixes JSON;
- serialises the fix to CoT XML.

Used to regenerate the demo deck's screenshots from canon scenario data.

### 5.3 `scripts/null_depth_mc_stats.py`

```bash
uv run python scripts/null_depth_mc_stats.py
```

Re-runs the 1000-trial null-depth Monte Carlo from the
`test_l2_null_steering.py` robustness test, prints the full
distribution (mean, median, p5/p50/p95, std-dev, parametric 90 % CI),
and appends a Markdown-formatted section to
`docs/demo/null_depth_mc_stats.md`. Read-out script for the demo
slide's headline number.

---

## §6 Configuration files

### 6.1 Scenarios (`scenarios/`)

YAML files describing a demo geometry: ENU origin, emitter (position,
frequency, modulation, expected class), channel model (free-space /
two-ray-ground / multipath FIR / log-normal shadowing / composite),
nodes (position, SDR config, optional array config, capabilities,
bearer), and the beat-list that drives `rfmesh-demo-replay`.

Available scenarios:

- `scenarios/trench_demo.yaml` — the canonical four-beat BoTH3 jury demo (3 L1 + 1 L2 around a 3 km emitter).
- `scenarios/three_node_trench.yaml` — three-L1-node smoke variant.
- `scenarios/trench_demo_artifact.yaml` — free-space variant for the artifact-capture run.
- `scenarios/mast_c_reference.yaml` — Mast-C empirical-anchor geometry (per ADR-014).

Schema is strict (`extra="forbid"`); the loader carries small ergonomic
bridges (`demo_beats` → `beats`, `UNKNOWN` → `unknown`).

### 6.2 Node / fusion configs (`configs/`)

YAML files validated against `NodeConfig` / `FusionConfig` from
`rfmesh-contracts`. Used by `rfmesh-node` and `rfmesh-fusion-server`.
The directory is currently empty (placeholder `.keep`) — drop YAMLs
here when wiring a deployment. `INTERFACES.md` §4 documents every field
and validator.

---

## §7 Repository layout (where to look for what)

```
rfmesh-workspace/
├── packages/
│   ├── rfmesh-contracts/  Frozen interface layer (lead-only). Pydantic models +
│   │                      Protocols. Everyone depends on this.
│   ├── rfmesh-sdr/        Receivers + simulator (Workstream A).
│   ├── rfmesh-servo/      Servo host driver + CLI (Workstream A).
│   ├── rfmesh-dsp/        L1 RSSI, L2 MUSIC / Capon / MVDR null-steering. Pure
│   │                      (no I/O, no subprocess, no network). Workstream B.
│   ├── rfmesh-ml/         L3 modulation classifier + threat profiles. Pure.
│   ├── rfmesh-fusion/     Stansfield WLS + MLE refinement + GDOP + ellipse.
│   │                      Pure. Workstream C+D.
│   ├── rfmesh-cot/        PyTAK adapter — FixEvent → CoT XML. Workstream C+D.
│   ├── rfmesh-node/       Asyncio node runtime + fusion service + bearer
│   │                      transports. Composition root. Workstream C+D.
│   └── rfmesh-ops/        Live dashboard (matplotlib panels). Workstream C+D.
├── apps/
│   └── demo-replay/       Top-level orchestrator app (the BoTH3 jury-demo path).
├── firmware/              ESP32-S2 servo controller firmware (ESP-IDF).
├── firmware-beacon/       ESP32-DevKit v1.1 + SX1276 LoRa beacon (PlatformIO).
├── scenarios/             Demo geometry YAMLs.
├── configs/               Node / fusion config YAMLs (deployment-side).
├── scripts/               Operator quick-look helpers (not console scripts).
├── tests/                 Workspace-level test trees (golden / property /
│                          integration / unit / hardware — package tests live
│                          under packages/<pkg>/tests/).
├── docs/                  Coordination + design + ADRs + demo + bootstrap docs.
├── tools/                 Reserved for non-Python tooling.
├── justfile               Task recipes (this manual §3).
├── pyproject.toml         Workspace root + tool config (ruff, mypy, pytest,
│                          import-linter contracts).
└── uv.lock                Committed lockfile — reproducibility.
```

---

## §8 Typical workflows

### 8.1 "I just cloned the repo. What now?"

```bash
uv sync
just verify
```

Then read in order: `README.md`, `ARCHITECTURE.md`, `INTERFACES.md`,
`docs/ONBOARDING.md`.

### 8.2 "Show me the demo right now, on the simulator, with a live dashboard."

Easiest single-process path:

```bash
uv run python scripts/show_dashboard.py
```

A window opens, the trench-demo beats play through (~60 s), the figure
stays open until you close it. Add `--cot-url tcp://localhost:8087` if
you have a FreeTAKServer running.

### 8.3 "Run the demo orchestrator headless and dump artifacts."

```bash
uv run rfmesh-demo-replay --scenario scenarios/trench_demo.yaml --headless --no-cot
```

For the artifact-bundle variant that also saves PNGs + JSON + CoT XML
per beat:

```bash
uv run python scripts/capture_demo_artifacts.py
```

### 8.4 "Capture per-node IQ from the simulator for later replay."

```bash
uv run rfmesh-demo-record \
    --scenario scenarios/trench_demo.yaml \
    --output recordings/dry_run_01/ \
    --duration-s 10
```

Produces `recordings/dry_run_01/<node_id>.iqx + .json` per node. The
hardware capture path arrives when `rfmesh-sdr` ships native receivers
(see `rfmesh-sdr/README.md`).

### 8.5 "Pre-stage the Beat E null demo for the jury."

Once, before the demo:

```bash
uv run rfmesh-beat-e-preload \
    --out cache/beat_e_trench.npz \
    --array=ULA --n=2 --spacing=0.164 --freq=915e6 \
    --signal-deg=306 --jammer-deg=126
```

Then at stage time:

```bash
uv run rfmesh-ops \
    --connect ws://10.0.0.1:9001 \
    --layout trench \
    --beat-e-cache cache/beat_e_trench.npz
```

### 8.6 "Two-process style: separate fusion server + dashboard."

Terminal A:

```bash
uv run rfmesh-fusion-server --config configs/fusion.yaml
```

Terminal B:

```bash
uv run rfmesh-ops --connect ws://127.0.0.1:9001 --layout trench
```

Terminal C (one or more nodes):

```bash
uv run rfmesh-node --config configs/node-01.yaml
```

The v1.0 `rfmesh-node` CLI only accepts `sdr.driver: "sim"` for a
plumbing smoke test; for the real demo path use `rfmesh-demo-replay`
(§4.5) or `scripts/show_dashboard.py` (§5.1).

### 8.7 "Run only one package's tests."

```bash
uv run pytest packages/rfmesh-dsp -v
```

Or full package gate (tests + mypy + ruff):

```bash
just ticket-verify rfmesh-dsp
```

Single test by name:

```bash
uv run pytest -k test_sigma_honesty -v
```

Hardware tests (need an SDR / servo plugged in):

```bash
uv run pytest -m hardware
```

### 8.8 "I want to calibrate a servo on a flashed ESP32-S2."

```bash
uv run rfmesh-servo-calibrate --port /dev/ttyACM0 --axis 0
```

Follow the prompts: the tool drives the servo to its `pulse_min_us`
and `pulse_max_us` endpoints; you read off the actual mechanical
angles; it builds a `Calibration` and asks before persisting to NVS.
DSP / node code refuses to emit L1 bearings from an uncalibrated axis.

---

## §9 Firmware

### 9.1 Servo controller (`firmware/`)

ESP32-S2 mini, ESP-IDF v5.1+. Full procedure in `firmware/README.md`;
short version:

```bash
cd firmware/
idf.py set-target esp32s2          # only on a fresh checkout
idf.py build
idf.py -p /dev/ttyACM0 flash
idf.py -p /dev/ttyACM0 monitor     # Ctrl+] to exit
```

S2 download mode: hold **BOOT** (GPIO0), tap **RESET** (EN), release
**BOOT**. Press **RESET** once after flashing to run the new image.

Host-side framing tests (no hardware):

```bash
cd firmware/
cmake -S tests -B build_tests
cmake --build build_tests
./build_tests/run_tests
```

Expected: `29 Tests 0 Failures 0 Ignored / OK`.

### 9.2 LoRa beacon (`firmware-beacon/`)

ESP32-DevKit v1.1 + HopeRF SX1276 (Ra-02). PlatformIO. Full procedure
in `firmware-beacon/README.md`:

```bash
cd firmware-beacon
pio run
pio run -t upload
pio device monitor -b 115200
```

Listen-side verification (RTL-SDR + gr-lora_sdr) documented in the
same README.

---

## §10 Tests and coverage

Pytest config lives in workspace `pyproject.toml`:

- `testpaths = ["packages", "apps", "tests"]`.
- `addopts = ["-ra", "--strict-markers", "--strict-config"]`.
- Markers: `hardware` (opt-in), `slow` (skippable with `-m "not slow"`).
- `asyncio_mode = "auto"` — async tests run without per-fixture decoration.

Coverage:

```bash
just test-cov
```

Branch coverage on `packages/`, with vendor + tests + `__init__.py`
omitted (per `[tool.coverage.run]`).

---

## §11 Governance reminders (cross-references, not duplication)

- **Contracts are frozen.** `packages/rfmesh-contracts/src/` is lead-only,
  edits via ADR + `SCHEMA_VERSION` bump. See `AGENTS.md` §1 (Invariant B1).
- **DSP / Fusion / ML must be pure.** No network, no file I/O, no
  subprocess. Enforced by `import-linter` contracts in `pyproject.toml`.
- **No silent fallbacks.** Every path that could fail silently must fail
  loudly (`AGENTS.md` §1 Invariant B3).
- **Sigma honesty.** Every `BearingEstimator` gated by a sigma-honesty
  test against Monte-Carlo ground truth within a ±20 % band
  (`AGENTS.md` §1 Invariant B2).
- **Software agents do software.** Hardware mounts / cables / antennas /
  polarisation / mast design are out of scope for agents
  (`AGENTS.md` §2).
- **Before declaring done:** `just verify`, paste the output.

For the full set of binding rules: `AGENTS.md` (the Seven Binding
Invariants + workspace disciplines).
