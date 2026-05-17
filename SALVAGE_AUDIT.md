# rf-mesh → rfmesh: Salvage Audit

**Source:** `github.com/macwed/rf-mesh` @ `main` (HEAD `1257e28`, 40 commits)
**Target:** new `rfmesh` uv-workspace monorepo (clean architecture, frozen `rfmesh-contracts` at centre)
**Date:** 2026-05-14
**Status:** DRAFT — await Maciej review/sign-off before feeds coordination docs.

---

## How to read this

Every source file gets one of three verdicts:

- **TAKE** — moves into new repo with at most mechanical changes (import paths, package location). Logic correct, architecture-neutral. Agent no redesign.
- **REFACTOR** — *logic* sound, worth keeping, but written against architectural decision new design deliberately changes (ABC→Protocol, RSSI-centric→bearing-centric messages, single-package→workspace). Agent ports logic, adapts seams. Audit says exactly what changes.
- **LEAVE** — not carried over. Either placeholder with nothing, or superseded by `rfmesh-contracts`, or so coupled to old architecture that re-deriving against new contracts cheaper/cleaner than porting.

Guiding rule: **old repo is parts donor, not thing to migrate.** Never `git mv` tree wholesale — drags forward architecture decisions new design exists to fix. Lift proven, tested components, re-seat against frozen contracts.

Salvage figure at end totals what this saves vs from-scratch build.

---

## Part 1 — Firmware (`firmware/`) — highest-value salvage

Single biggest asset in old repo; loss hurts most. **C, ESP-IDF, hardware-validated path, 58 host-buildable unit tests, byte-exact wire-protocol test vectors.** Near-zero coupling to Python architecture — talks frozen wire protocol, wire protocol no care what on other end. **Whole tree verdict: TAKE**, with one scoped already-planned port.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `firmware/main/main.c` | 161 | TAKE | `app_main`, mode-select (protocol vs linenoise), protocol main loop. Pure firmware. |
| `firmware/main/crc16.{c,h}` | 34 | TAKE | CRC-16/CCITT-FALSE. Byte-exact vs spec §7.1 vectors. Untouchable. |
| `firmware/main/cobs.{c,h}` | 101 | TAKE | COBS encode/decode. Byte-exact vs §7.2. Untouchable. |
| `firmware/main/protocol.{c,h}` | 152 | TAKE | TLV frame state machine + serialiser. Byte-exact vs §7.3. |
| `firmware/main/dispatch.{c,h}` | 279 | TAKE | CMD→handler routing. Pure firmware logic. |
| `firmware/main/servo.{c,h}` | 135 | TAKE | LEDC PWM driver + per-axis state. |
| `firmware/main/servo_math.c` | 50 | TAKE | Pulse↔angle linear interp. Pure, separately tested. |
| `firmware/main/cal_types.h` | 45 | TAKE | 12-byte calibration wire layout. |
| `firmware/main/calibration.{c,h}` | 163 | TAKE | NVS read/write + RAM cache. |
| `firmware/main/shell.{c,h}` | 251 | TAKE | linenoise REPL — human emergency-debug path. Useful in field. |
| `firmware/main/identity.h.in` | 7 | TAKE | git-SHA stamp at configure time. |
| `firmware/tests/*` (5 files + Unity) | — | TAKE | 58 C tests. Proof firmware correct. Move verbatim. |
| `firmware/CMakeLists.txt`, `partitions.csv`, `sdkconfig.defaults` | — | TAKE | Build config. |
| `firmware/README.md` | — | REFACTOR | Content good; §"On-target smoke test" rewritten — see Part 6, smoke test no longer project-blocking gate. |

**One scoped change — decided in old thread, not new work:** ESP32-C3 → ESP32-S2 port. C3 use USB-Serial-JTAG peripheral; S2 has native USB-OTG (TinyUSB CDC), so `usb_serial_jtag_*` calls become TinyUSB CDC equivalents, console driver migrates from `esp_console_dev_usb_serial_jtag_*` to matching `esp_console_dev_usb_cdc_*` path. **Wire protocol unchanged.** Bounded, well-trodden port — but **explicitly not rename of build target.** Thread 1 sizes at ~2–3 h real work touching USB peripheral layer + console driver; ticket must scope as such, not as trivial retarget, else mis-planned. Workstream-A ticket, can happen on Maciej bench in parallel; nothing in software stack waits on it.

**Why matters:** servo hardware bring-up cost weeks last time. Firmware for it *done and tested*. Weeks of work inherited, not re-spent.

---

## Part 2 — Servo host driver (`rfmesh/scan/servo/`) — TAKE almost whole

Python side of servo link. Built in parallel against same frozen `servo_uart_v1` spec, full test suite. Already structured around `Transport` **Protocol** (not ABC) — *already matches* new architecture structural-typing philosophy. Little to do.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `scan/servo/crc.py` | 31 | TAKE | `crc16_ccitt_false`. Mirrors firmware C; byte-exact. |
| `scan/servo/cobs.py` | 82 | TAKE | In-tree COBS (PyPI `cobs` package abandoned — old `CLAUDE.md` flags explicitly; keep in-tree). |
| `scan/servo/protocol.py` | 144 | TAKE | Frame codec, `Cmd`/`ErrorCode` IntEnums, wire constants. |
| `scan/servo/messages.py` | 323 | TAKE | Typed payload dataclasses with `pack`/`unpack`. Frozen, slotted. |
| `scan/servo/transport.py` | 99 | TAKE | `Transport` Protocol + `SerialTransport` (lazy-imports pyserial). Already Protocol-based — no refactor. |
| `scan/servo/driver.py` | 348 | TAKE | `ServoDriver` sync client. Single-outstanding-command, master/slave. |
| `tools/servo_repl.py` | — | TAKE | Operator REPL. Lands in new repo `scripts/` or `apps/` thin entrypoint. |

**Mechanical changes only:** import paths shift (`rfmesh.scan.servo` → `rfmesh_sdr.servo` or dedicated `rfmesh-servo` package — placement decision in Part 7), servo-specific exceptions in monolithic `rfmesh/exceptions.py` move with code. **No logic changes.** Wire contract `docs/wire-protocols/servo_uart_v1.md` moves verbatim, stays frozen source of truth — exactly artefact new governance model built around.

One **new, small** piece of work this enables (not salvage, flagged here because belongs near servo code): L1 bearing-scan orchestration — sweep pattern, dwell timing, RSSI integration per angle — never written in old repo (was pending `S2-T5b`). Workstream-B deliverable in new plan, sits *on top of* this driver, now inherits ready-made.

---

## Part 3 — DSP (`rfmesh/dsp/`) — TAKE lot, your L1 foundation

Pure functions over `complex64` arrays. 99–100% test coverage in old repo. No I/O, no hardware coupling, no architecture coupling — definition of portable. This *is* L1 detection maths.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `dsp/rssi.py` | 265 | TAKE | `compute_rssi_dbfs`, `compute_rssi_in_band`, `compute_noise_floor_dbfs` (median with strongest-bin exclusion — exactly what L1 peak-find needs), `compute_snr_db`, `detect_clipping` + `ClippingReport`. |
| `dsp/spectrum.py` | 194 | TAKE | `compute_fft`, `compute_psd` (Welch), `compute_spectrogram` (STFT), `find_spectral_peak`. STFT also front end L3 classifier will want. |
| `dsp/constants.py` | 3 | TAKE | `EPSILON`. Trivial. |

**Placement:** lands in `rfmesh-dsp/` as `l1_*` foundation. **Mechanical changes only** — import paths, logging call sites move from `loguru` to whatever new repo standardises on (decision in Part 7; if stays `loguru`, zero-diff move).

**One thing to be explicit about:** `detect_clipping` / `ClippingReport` is ADC-saturation guard. First research briefing flagged ADC saturation as top-three risk. Old repo *already solved + tested it*. Comes across intact. Risk retired by inheritance.

---

## Part 4 — IQ I/O (`rfmesh/io/`) — split verdict

Package mixes three things: hardware abstraction, file I/O, calibration. Different verdicts because different coupling to architecture.

### 4a. File I/O and format helpers — TAKE

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `io/iq_reader.py` | 142 | TAKE | `IQReader` + `_to_complex64`. uint8-interleaved → normalised `complex64`. Architecture-neutral. |
| `io/iq_recorder.py` | 154 | TAKE | `IQRecorder`. Stage-1 writer. Useful for capturing replay/golden-file data in new repo test strategy. |
| `io/constants.py` | 12 | TAKE | `RTL_SDR_DC_OFFSET`, `BYTES_PER_SAMPLE`, etc. Move into `rfmesh-sdr` constants. |

`_to_complex64` duplicated in both `io/iq_reader.py` and `io/devices/rtlsdr.py` in old repo — new repo de-duplicates into one place in `rfmesh-sdr`. Minor cleanup, flag in ticket.

### 4b. `io/formats.py` — REFACTOR (split it)

`IQMetadata` (sidecar format) is **TAKE** — clean, useful Pydantic model for capture metadata, lands in `rfmesh-sdr`.

But `formats.py` also contains `RSSIMeasurement`, `NodeConfig`, `AggregatorConfig` — **superseded by `rfmesh-contracts`**. New `NodeConfig`/`FusionConfig` richer (capability lists, array config, bearer config, frozen `schema_version`), bearing-centric. Old `RSSIMeasurement` subsumed by `BearingReport`. **Verdict for those three classes: LEAVE** — no port, they are old architecture. Only `IQMetadata` survives from this file.

### 4c. `io/calibration.py` — REFACTOR

`DongleCalibration` / `CalibrationData` — per-dongle RSSI/freq-offset model. *Logic* (offsets relative to reference dongle, JSON persist/load, applied per capture) sound + field-validated. But keyed by `"rtlsdr_<index>"` device-id strings and old `architecture.md` §7 itself documents `--device-serial` foot-gun this creates. **REFACTOR:** port offset-application logic, re-seat against new SDR abstraction — calibration data attaches to `Receiver` by serial, not by fragile index-keyed map. Genuine improvement new structure makes room for; old repo own docs already flagged as future work.

### 4d. `io/device.py` + `io/devices/` — REFACTOR (one real architectural seam)

Only place where old + new architectures genuinely disagree. Worth being precise.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `io/device.py` | 155 | REFACTOR | `Device` **ABC** + `CaptureRequest`/`CaptureResult`/`DeviceCapabilities`. |
| `io/devices/rtlsdr.py` | 415 | REFACTOR | `RTLSDRDevice` — subprocess around `rtl_sdr`, plus `stream()` generator. |
| `io/devices/bladerf.py` | 74 | LEAVE | Capabilities-only stub. New `rfmesh-sdr` writes real bladeRF `CoherentReceiver` against new Protocol from scratch; stub of old shape not useful start. |

Old repo uses **ABC** (`Device`); new `rfmesh-contracts` deliberately uses **`typing.Protocol`** (`Receiver` / `CoherentReceiver`) — decision from architecture research, for structural typing + clean dependency star with no inheritance coupling. So:

- **`RTLSDRDevice` logic is gold, comes across** — `rtl_sdr` subprocess pattern, `stream()` generator reading `rtl_sdr -` stdout in chunks, capability bounds (RTL-SDR V4 freq/rate limits), serial resolution via `rtl_eeprom`, cleanup discipline. Agent ports all of that.
- What changes is **declaration + seams**: `class RTLSDRDevice(Device)` → class that structurally satisfies `Receiver` Protocol; `CaptureRequest`/`CaptureResult`/`DeviceCapabilities` → contract `SDRConfig` + `ReceiverCapabilities`; lifecycle (`__enter__`/`capture`/`stream`) → Protocol `open`/`configure`/`read`/`capabilities`/`close`.
- `DeviceCapabilities` in old repo already has `coherent_channels` field — old design *reaching toward* L1/L2 split. New `ReceiverCapabilities` formalises. Intent carries over; shape re-cut.

Effort: one REFACTOR with real design content, but bounded — porting ~570 LoC of working, understood subprocess logic behind new (simpler) Protocol surface. Half day agent work with old code open beside new contract, not redesign.

---

## Part 5 — Mesh (`rfmesh/mesh/`) — REFACTOR: keep transport, re-cut schemas

Old mesh is **working, tested infrastructure**: length-prefixed MsgPack over TCP, `MeshNode` (4-thread: capture/process/publish/heartbeat) with reconnect-backoff, `Aggregator` (asyncio, per-node ring buffer, stale detection, `subscribe_measurements()` fan-out). 1714 LoC across three files, well-documented concurrency model. None throwaway. But carries old RSSI-centric message schema, exactly what `rfmesh-contracts` replaces.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `mesh/protocol.py` | 327 | REFACTOR | **Framing/codec TAKE** — length-prefixed MsgPack, sync+async IO shims (`encode_message`/`decode_message`/`read_message`/`write_message`), `MAX_MESSAGE_BYTES` guard, protocol-version check. **Schemas LEAVE** — `NodeHello`/`NodeStatus`/`Measurement`/`MeasurementBatch` superseded by contract `BearingReport`/`FixEvent`/`NodeStatus`. So: keep envelope machinery, swap payload types for frozen contracts. |
| `mesh/node.py` | 784 | REFACTOR | **Threading skeleton + connection machinery TAKE-grade** — 4-thread model, lock-serialised `_AggregatorConnection`, reconnect loop, drop-oldest queues with counters. What changes: *process* thread currently computes RSSI-centric `Measurement`; in new design runs `BearingEstimator` (from `rfmesh-dsp`) + emits `BearingReport`. Runtime becomes new `rfmesh-node` workstream, this file proven start skeleton not blank page. |
| `mesh/aggregator.py` | 603 | REFACTOR | **Asyncio server, per-client tasking, stale detection, `subscribe_*` fan-out TAKE-grade**. What changes: consumes `BearingReport`s, feeds new `rfmesh-fusion` `Fuser` instead of just logging measurements; subscriber stream now carries `FixEvent`s. Proven server scaffold, new payloads. |

**Framing decision worth callout:** new contracts already standardise messages as Pydantic models. Old repo "length-prefixed MsgPack envelope with `{type, payload}` dict" perfectly good wire format for them — `model_dump` out, `model_validate` in. Agent doing `rfmesh-node`/transport workstream inherits *working, tested* framing layer, only re-points at new types. Large de-risk on networking, otherwise fiddly to get right under time pressure.

**Two named regression anchors carry over with this code.** Thread 1 review caught two real bugs in old mesh impl: 25 dB SNR-invariant error in `S2-T3.1` (proposed `in_band_snr >= peak_snr - 5` invariant wrong by 25 dB for CW signal), lazy subscriber-registration race in `S2-T4`. When `node.py` + `aggregator.py` refactored, those two failures must come across as **explicit, named regression tests** — not left as process anecdotes. Cheapest possible insurance refactor not silently reintroduce bug already paid for once.

---

## Part 6 — CLI, configs, docs, infrastructure

### CLI (`rfmesh/cli/`) — mostly LEAVE, selectively REFACTOR

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `cli/record_iq.py` | 195 | REFACTOR-lite | Wraps `IQRecorder`. Useful as capture tool for building golden/replay test data. Port as thin `apps/` entrypoint once I/O layer settled. |
| `cli/calibrate_dongles.py` | 375 | REFACTOR-lite | Sequential multi-dongle calibration. Ports once `io/calibration.py` re-seated (Part 4c). |
| `cli/servo_calibrate.py` | 296 | TAKE-lite | Servo calibration CLI. Follows servo driver across (Part 2). |
| `cli/run_node.py` | 292 | LEAVE | Assembles *old* `MeshNodeConfig`. New `rfmesh-node` has own config-from-`NodeConfig` entrypoint. Re-derive. |
| `cli/run_aggregator.py` | 176 | LEAVE | Same reasoning — re-derive against new `FusionConfig`. |

CLI layer where old architecture most visible (old config shapes, old message types), so most re-derived not ported. Cheap — CLI entrypoints thin by design — and *right* place to absorb change not fight it.

### Configs (`configs/`) — LEAVE (re-derive)

`node_example.json` / `dongle_calibration.example.json` old flat config shape. New repo `configs/*.yaml` written fresh against `rfmesh-contracts` `NodeConfig`/`FusionConfig` — richer, validated, `extra="forbid"`. Re-derive; old files reference for *what fields node needs*, not thing to port.

### Docs — split

| Item | Verdict | Notes |
|---|---|---|
| `docs/wire-protocols/servo_uart_v1.md` | TAKE | Frozen wire contract. Moves verbatim, stays source of truth. |
| `docs/architecture.md` | LEAVE | Describes old single-package architecture. New repo `ARCHITECTURE.md` written fresh — but *valuable reference* while writing. |
| `docs/PROJECT_PLAN.md`, `docs/tickets/**` | LEAVE (reference) | Old staged-ticket plan. Superseded by new workstream plan, but *ticket format* proven model new `WORKSTREAMS.md` borrows from. |
| `docs/hardware_calibration.md` | TAKE | Empirical calibration sign-convention notes. Field-verified knowledge — keep. |
| `docs/STAGE1_RETROSPECTIVE.md`, `docs/memory.md` | LEAVE (reference) | History. Read once for hard-won discoveries, no port. |
| `notebooks/` | LEAVE | Stage-1 exploration. Not part of deliverable. |

### Thread-1-produced documents (in `/mnt/user-data/outputs/`, not in GitHub repo)

Audit above covers GitHub repo. Thread 1 also produced several standalone docs never committed to repo, easy to lose in transition. Thread 1 closing digest named explicitly:

| Document | Verdict | Notes |
|---|---|---|
| `tower_sanity_playbook.md` | **TAKE** | This *is* Phase C smoke-test procedure — `rtl_power` scan to identify reference cellular carrier, then manual sweep + RSSI-vs-angle check for parabolic peak in known direction. Still valid, **still unexecuted** — see inherited-context document, Phase C tracked as first hardware-validation gate. Moves into new repo `docs/` as hardware-validation procedure. |
| `lora_beacon_spec.md` | **TAKE (as reference)** | Firmware spec for 868 MHz LoRa beacon — controlled test emitter for all pre-BoTH3 work in Poland. Building beacon firmware itself workstream item; this spec its input. |
| `field_test_plan.md` | **REFERENCE** | Old field-test plan assumed 2 nodes. New architecture N≥3, plan partially superseded — but geometry analysis (notably observation N1–N2 baseline gives only ~10° bearing differential to distant tower, collapsing triangulation) is exactly kind of GDOP reasoning new fusion design needs, carries over as input. |
| `mks_config_playbook.md` | **LEAVE (dead)** | MKS SERVO42D dropped; MG996R is servo. Doc dead. Recorded here only so nobody resurrects. |
| `CONVERSATION_BOOTSTRAP_2.md` | **REFERENCE** | Thread 2 bootstrap doc. Superseded by new coordination package bootstraps, but useful format reference. |

### Infrastructure — REFACTOR (patterns, not files)

| Item | Verdict | Notes |
|---|---|---|
| `pyproject.toml` | REFACTOR | Old single-package layout becomes workspace-root + per-package `pyproject.toml` set. But *tool config* — `ruff` rules, `mypy --strict`, `pytest --cov` — proven, carries straight over. |
| `.github/workflows/ci.yml` | TAKE-pattern | ruff + mypy + pytest + codecov. Exact shape new repo wants; re-point paths at workspace. |
| `.pre-commit-config.yaml` | TAKE-pattern | ruff + mypy + pytest-on-push. Carries over; add new repo contract-protection hook (fails if `rfmesh-contracts/src` touched without bumping `version.py`). |
| `CLAUDE.md` | REFACTOR | Becomes `AGENTS.md` + `CLAUDE.md` under new governance model. Old "hard rules" section direct ancestor of new "Five Invariants". |
| `.claudeignore`, `.python-version`, `LICENSE` | TAKE | Mechanical. |

---

## Part 7 — Open placement decisions (need Maciej call, or lead, before tickets)

Not "take/leave" — decisions audit surfaces because they affect how salvaged code packaged. None blocks starting; all should be settled before relevant workstream first ticket.

1. **Where does servo code live?** Options: (a) own `rfmesh-servo` package, (b) subpackage of `rfmesh-sdr`. Logically peripheral-control concern, not SDR concern — lean (a), small dedicated package, keeps `rfmesh-sdr` focused. Lead recommendation: **(a)**.
2. **Logging: keep `loguru` or standardise on `structlog`?** Old repo all-in on `loguru`. Architecture research suggested `structlog` for structured/CI-friendly logs. Keep `loguru` → DSP/IO salvage zero-diff move; switch → every salvaged file gets log call sites touched. Lead recommendation: **keep `loguru`** — salvage cost of switching real, benefit marginal for hackathon deliverable.
3. **`scipy-stubs` / `types-msgpack`** — old repo pins these for `mypy --strict`. Carry same pins into workspace dev-dependencies. No decision really, just don't lose them.
4. **`_to_complex64` duplication** — exists twice in old repo. New repo: one home in `rfmesh-sdr`. Trivial, don't replicate duplication.

---

## Salvage figure — what this inherits

Rough LoC of *proven, tested* code coming across rather than written from scratch:

| Block | Verdict | ~LoC | Tests inherited |
|---|---|---|---|
| Firmware (C) | TAKE | ~1378 | 58 C tests |
| Servo host driver (Py) | TAKE | ~1027 | servo test suite |
| DSP (Py) | TAKE | ~462 | ~99–100% covered |
| IQ file I/O + `IQMetadata` | TAKE | ~310 | covered |
| Calibration logic | REFACTOR | ~93 | covered |
| `Device`/`RTLSDRDevice` logic | REFACTOR | ~570 | covered |
| Mesh framing + node/aggregator skeletons | REFACTOR | ~1400 of 1714 | mesh test suite |
| Wire-protocol spec + CI/pre-commit patterns | TAKE | — | — |

**Headline:** ~**3200 LoC moves at TAKE quality** (firmware, servo, DSP, file I/O) — near-mechanical, logic untouched. Another **~2000 LoC moves at REFACTOR quality** — logic kept, seams re-cut against contracts. Critically, **two things that hurt most to get wrong** — hardware-validated servo firmware, working mesh transport — both inherited not re-risked.

Genuinely *new* work, not salvage: `rfmesh-contracts` (done), L1 bearing-scan orchestration, all L2 (MUSIC, MVDR, coherent `Receiver` impls), all fusion (Stansfield/MLE/GDOP/ellipses), all L3 (classifier), CoT/PyTAK output, simulator. Real scope of multi-agent build — meaningfully smaller because foundation underneath inherited.

---

## What this audit does NOT decide

- No assign salvaged files to workstreams — that `WORKSTREAMS.md` job, this audit becomes its input (each workstream row carry "salvage" column pointing back here).
- No write refactor tickets — scopes them. REFACTOR verdicts each describe *what changes*; actual ticket owning Opus agent job.
- No re-confirm test pass/coverage on old code — old repo CI badge + old thread reports taken at face value. New repo re-establishes own CI from line one regardless. One specific number to *not* trust blindly: Thread 1 digest reports unreconciled discrepancy between "377 Python tests" and agent-reported "398 collected" on 2026-05-13. New repo CI makes moot — but if salvage ticket cites inherited test count as target, use count new CI actually produces, not either old figure.

---

## Sign-off

Maciej: review verdicts. Ones most worth second look:

- **Part 4d** — `Device` ABC → `Receiver` Protocol refactor. One real architectural seam. Confirm happy *logic* of `RTLSDRDevice` worth porting (~415 LoC subprocess-wrangling you would otherwise rewrite) rather than re-deriving from scratch.
- **Part 5** — keeping mesh transport. Confirm agree framing/threading/asyncio machinery worth inheriting, given payload schemas swapped out. Alternative: write networking fresh; audit position is old one tested, worth lot under time pressure.
- **Part 7.1 and 7.2** — two placement decisions (servo package location, logging library). Lead recommendations stated; override if disagree.

Once sign off, feeds straight into coordination package: `ARCHITECTURE.md` references it, `WORKSTREAMS.md` gets salvage column from it, each workstream bootstrap points its Opus agent at relevant parts.