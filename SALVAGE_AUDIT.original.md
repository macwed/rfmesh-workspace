# rf-mesh → rfmesh: Salvage Audit

**Source:** `github.com/macwed/rf-mesh` @ `main` (HEAD `1257e28`, 40 commits)
**Target:** new `rfmesh` uv-workspace monorepo (clean architecture, frozen `rfmesh-contracts` at the centre)
**Date:** 2026-05-14
**Status:** DRAFT — awaiting Maciej's review and sign-off before it feeds the coordination documents.

---

## How to read this

Every source file gets one of three verdicts:

- **TAKE** — moves into the new repo with at most mechanical changes (import paths, package location). The logic is correct and architecture-neutral. An agent does not redesign it.
- **REFACTOR** — the *logic* is sound and worth keeping, but it was written against an architectural decision the new design deliberately changes (ABC→Protocol, RSSI-centric→bearing-centric messages, single-package→workspace). An agent ports the logic and adapts the seams. The audit says exactly what changes.
- **LEAVE** — not carried over. Either a placeholder with nothing in it, or superseded by `rfmesh-contracts`, or so coupled to the old architecture that re-deriving against the new contracts is cheaper and cleaner than porting.

The guiding rule: **the old repo is a parts donor, not a thing to migrate.** We never `git mv` a tree wholesale, because that drags forward the very architecture decisions the new design exists to fix. We lift proven, tested components and re-seat them against the frozen contracts.

A salvage figure at the end totals what this saves versus a from-scratch build.

---

## Part 1 — Firmware (`firmware/`) — the highest-value salvage

This is the single biggest asset in the old repo and the one whose loss would hurt most. It is **C, ESP-IDF, hardware-validated path, with 58 host-buildable unit tests and byte-exact wire-protocol test vectors.** It has near-zero coupling to the Python architecture — it talks a frozen wire protocol, and the wire protocol does not care what is on the other end. **Verdict for the whole tree: TAKE**, with one scoped, already-planned port.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `firmware/main/main.c` | 161 | TAKE | `app_main`, mode-select (protocol vs linenoise), protocol main loop. Pure firmware. |
| `firmware/main/crc16.{c,h}` | 34 | TAKE | CRC-16/CCITT-FALSE. Byte-exact against spec §7.1 vectors. Untouchable. |
| `firmware/main/cobs.{c,h}` | 101 | TAKE | COBS encode/decode. Byte-exact against §7.2. Untouchable. |
| `firmware/main/protocol.{c,h}` | 152 | TAKE | TLV frame state machine + serialiser. Byte-exact against §7.3. |
| `firmware/main/dispatch.{c,h}` | 279 | TAKE | CMD→handler routing. Pure firmware logic. |
| `firmware/main/servo.{c,h}` | 135 | TAKE | LEDC PWM driver + per-axis state. |
| `firmware/main/servo_math.c` | 50 | TAKE | Pulse↔angle linear interpolation. Pure, separately tested. |
| `firmware/main/cal_types.h` | 45 | TAKE | 12-byte calibration wire layout. |
| `firmware/main/calibration.{c,h}` | 163 | TAKE | NVS read/write + RAM cache. |
| `firmware/main/shell.{c,h}` | 251 | TAKE | linenoise REPL — the human emergency-debug path. Genuinely useful in the field. |
| `firmware/main/identity.h.in` | 7 | TAKE | git-SHA stamp at configure time. |
| `firmware/tests/*` (5 files + Unity) | — | TAKE | 58 C tests. The proof the firmware is correct. Move verbatim. |
| `firmware/CMakeLists.txt`, `partitions.csv`, `sdkconfig.defaults` | — | TAKE | Build config. |
| `firmware/README.md` | — | REFACTOR | Content is good; the §"On-target smoke test" section is rewritten — see Part 6, the smoke test is no longer a project-blocking gate. |

**The one scoped change — already decided in the old thread, not new work:** the ESP32-C3 → ESP32-S2 port. The C3 uses the USB-Serial-JTAG peripheral; the S2 has native USB-OTG (TinyUSB CDC), so `usb_serial_jtag_*` calls become TinyUSB CDC equivalents and the console driver migrates from `esp_console_dev_usb_serial_jtag_*` to the matching `esp_console_dev_usb_cdc_*` path. **The wire protocol is unchanged.** This is a bounded, well-trodden port — but it is **explicitly not a rename of the build target.** Thread 1 sizes it at ~2–3 h of real work touching the USB peripheral layer and the console driver; the ticket must scope it as such, not as a trivial retarget, or it will be mis-planned. It is a workstream-A ticket, and crucially it can happen on Maciej's bench in parallel; nothing in the software stack waits on it.

**Why this matters:** servo hardware bring-up was the thing that cost weeks last time. The firmware for it is *done and tested*. That is weeks of work inherited, not re-spent.

---

## Part 2 — Servo host driver (`rfmesh/scan/servo/`) — TAKE almost whole

The Python side of the servo link. Implemented in parallel against the same frozen `servo_uart_v1` spec, with a full test suite. It is already structured around a `Transport` **Protocol** (not an ABC) — which means it *already matches* the new architecture's structural-typing philosophy. Very little to do.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `scan/servo/crc.py` | 31 | TAKE | `crc16_ccitt_false`. Mirrors the firmware C; byte-exact. |
| `scan/servo/cobs.py` | 82 | TAKE | In-tree COBS (the PyPI `cobs` package is abandoned — the old `CLAUDE.md` flags this explicitly; keep the in-tree one). |
| `scan/servo/protocol.py` | 144 | TAKE | Frame codec, `Cmd`/`ErrorCode` IntEnums, wire constants. |
| `scan/servo/messages.py` | 323 | TAKE | Typed payload dataclasses with `pack`/`unpack`. Frozen, slotted dataclasses. |
| `scan/servo/transport.py` | 99 | TAKE | `Transport` Protocol + `SerialTransport` (lazy-imports pyserial). Already Protocol-based — no refactor. |
| `scan/servo/driver.py` | 348 | TAKE | `ServoDriver` synchronous client. Single-outstanding-command, master/slave. |
| `tools/servo_repl.py` | — | TAKE | Operator REPL. Lands in the new repo's `scripts/` or an `apps/` thin entrypoint. |

**Mechanical changes only:** import paths shift (`rfmesh.scan.servo` → `rfmesh_sdr.servo` or a dedicated `rfmesh-servo` package — placement decision is Part 7), and the servo-specific exceptions currently in the monolithic `rfmesh/exceptions.py` move with the code. **No logic changes.** The wire contract `docs/wire-protocols/servo_uart_v1.md` moves across verbatim and stays the frozen source of truth — it is exactly the kind of artefact the new governance model is built around.

One **new, small** piece of work this enables (not salvage, flagged here because it belongs near the servo code): the L1 bearing-scan orchestration — sweep pattern, dwell timing, RSSI integration per angle — was never written in the old repo (it was the pending `S2-T5b`). It is a workstream-B deliverable in the new plan, and it sits *on top of* this driver, which it now inherits ready-made.

---

## Part 3 — DSP (`rfmesh/dsp/`) — TAKE the lot, it is your L1 foundation

Pure functions over `complex64` arrays. 99–100% test coverage in the old repo. No I/O, no hardware coupling, no architecture coupling — the definition of portable. This *is* the L1 detection maths.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `dsp/rssi.py` | 265 | TAKE | `compute_rssi_dbfs`, `compute_rssi_in_band`, `compute_noise_floor_dbfs` (median with strongest-bin exclusion — exactly what L1 peak-find needs), `compute_snr_db`, `detect_clipping` + `ClippingReport`. |
| `dsp/spectrum.py` | 194 | TAKE | `compute_fft`, `compute_psd` (Welch), `compute_spectrogram` (STFT), `find_spectral_peak`. The STFT is also the front end the L3 classifier will want. |
| `dsp/constants.py` | 3 | TAKE | `EPSILON`. Trivial. |

**Placement:** these land in `rfmesh-dsp/` as the `l1_*` foundation. **Mechanical changes only** — import paths, and the logging call sites move from `loguru` to whatever the new repo standardises on (decision in Part 7; if it stays `loguru`, this is a zero-diff move).

**One thing to be explicit about:** `detect_clipping` / `ClippingReport` is the ADC-saturation guard. The very first research briefing in this project flagged ADC saturation as a top-three risk. The old repo *already solved and tested it*. It comes across intact. That risk is retired by inheritance.

---

## Part 4 — IQ I/O (`rfmesh/io/`) — split verdict

This package mixes three things: hardware abstraction, file I/O, and calibration. They get different verdicts because they have different coupling to the architecture.

### 4a. File I/O and format helpers — TAKE

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `io/iq_reader.py` | 142 | TAKE | `IQReader` + `_to_complex64`. uint8-interleaved → normalised `complex64`. Architecture-neutral. |
| `io/iq_recorder.py` | 154 | TAKE | `IQRecorder`. The Stage-1 writer. Useful for capturing replay/golden-file data in the new repo's test strategy. |
| `io/constants.py` | 12 | TAKE | `RTL_SDR_DC_OFFSET`, `BYTES_PER_SAMPLE`, etc. Move into `rfmesh-sdr` constants. |

`_to_complex64` is duplicated in both `io/iq_reader.py` and `io/devices/rtlsdr.py` in the old repo — the new repo de-duplicates it into one place in `rfmesh-sdr`. Minor cleanup, flag it in the ticket.

### 4b. `io/formats.py` — REFACTOR (split it)

`IQMetadata` (the sidecar format) is **TAKE** — it is a clean, useful Pydantic model for capture metadata, lands in `rfmesh-sdr`.

But `formats.py` also contains `RSSIMeasurement`, `NodeConfig`, and `AggregatorConfig` — and these are **superseded by `rfmesh-contracts`**. The new `NodeConfig`/`FusionConfig` are richer (capability lists, array config, bearer config, frozen `schema_version`) and bearing-centric. The old `RSSIMeasurement` is subsumed by `BearingReport`. **Verdict for those three classes: LEAVE** — do not port them, they are the old architecture. Only `IQMetadata` survives out of this file.

### 4c. `io/calibration.py` — REFACTOR

`DongleCalibration` / `CalibrationData` — the per-dongle RSSI/freq-offset model. The *logic* (offsets relative to a reference dongle, JSON persist/load, applied per capture) is sound and field-validated. But it is keyed by `"rtlsdr_<index>"` device-id strings and the old `architecture.md` §7 itself documents the `--device-serial` foot-gun this creates. **REFACTOR:** port the offset-application logic, but re-seat it against the new SDR abstraction — calibration data attaches to a `Receiver` by serial, not by a fragile index-keyed map. This is a genuine improvement the new structure makes room for, and the old repo's own docs already flagged it as future work.

### 4d. `io/device.py` + `io/devices/` — REFACTOR (the one real architectural seam)

This is the only place where the old and new architectures genuinely disagree, and it is worth being precise.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `io/device.py` | 155 | REFACTOR | `Device` **ABC** + `CaptureRequest`/`CaptureResult`/`DeviceCapabilities`. |
| `io/devices/rtlsdr.py` | 415 | REFACTOR | `RTLSDRDevice` — subprocess around `rtl_sdr`, plus a `stream()` generator. |
| `io/devices/bladerf.py` | 74 | LEAVE | Capabilities-only stub. The new `rfmesh-sdr` writes the real bladeRF `CoherentReceiver` against the new Protocol from scratch; a stub of the old shape is not a useful starting point. |

The old repo uses an **ABC** (`Device`); the new `rfmesh-contracts` deliberately uses **`typing.Protocol`** (`Receiver` / `CoherentReceiver`) — a decision from the architecture research, for structural typing and a clean dependency star with no inheritance coupling. So:

- The **`RTLSDRDevice` logic is gold and comes across** — the `rtl_sdr` subprocess pattern, the `stream()` generator reading `rtl_sdr -` stdout in chunks, the capability bounds (RTL-SDR V4 freq/rate limits), serial resolution via `rtl_eeprom`, the cleanup discipline. An agent ports all of that.
- What changes is the **declaration and the seams**: `class RTLSDRDevice(Device)` → a class that structurally satisfies the `Receiver` Protocol; `CaptureRequest`/`CaptureResult`/`DeviceCapabilities` → the contract's `SDRConfig` + `ReceiverCapabilities`; the lifecycle (`__enter__`/`capture`/`stream`) → the Protocol's `open`/`configure`/`read`/`capabilities`/`close`.
- `DeviceCapabilities` in the old repo already has a `coherent_channels` field — the old design was *reaching toward* the L1/L2 split. The new `ReceiverCapabilities` formalises it. The intent carries over; the shape is re-cut.

Effort: this is the one REFACTOR with real design content, but it is bounded — porting ~570 LoC of working, understood subprocess logic behind a new (and simpler) Protocol surface. Half a day of agent work with the old code open beside the new contract, not a redesign.

---

## Part 5 — Mesh (`rfmesh/mesh/`) — REFACTOR: keep the transport, re-cut the schemas

The old mesh is **working, tested infrastructure**: length-prefixed MsgPack over TCP, `MeshNode` (4-thread: capture/process/publish/heartbeat) with reconnect-backoff, `Aggregator` (asyncio, per-node ring buffer, stale detection, `subscribe_measurements()` fan-out). 1714 LoC across three files, well-documented concurrency model. None of this is throwaway. But it carries the old RSSI-centric message schema, and that is exactly what `rfmesh-contracts` replaces.

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `mesh/protocol.py` | 327 | REFACTOR | The **framing/codec is TAKE** — length-prefixed MsgPack, the sync+async IO shims (`encode_message`/`decode_message`/`read_message`/`write_message`), the `MAX_MESSAGE_BYTES` guard, the protocol-version check. The **schemas are LEAVE** — `NodeHello`/`NodeStatus`/`Measurement`/`MeasurementBatch` are superseded by the contract's `BearingReport`/`FixEvent`/`NodeStatus`. So: keep the envelope machinery, swap the payload types for the frozen contracts. |
| `mesh/node.py` | 784 | REFACTOR | The **threading skeleton and connection machinery are TAKE-grade** — the 4-thread model, the lock-serialised `_AggregatorConnection`, the reconnect loop, drop-oldest queues with counters. What changes: the *process* thread currently computes an RSSI-centric `Measurement`; in the new design it runs a `BearingEstimator` (from `rfmesh-dsp`) and emits a `BearingReport`. The runtime becomes the new `rfmesh-node` workstream, with this file as its proven starting skeleton rather than a blank page. |
| `mesh/aggregator.py` | 603 | REFACTOR | The **asyncio server, per-client tasking, stale detection, and `subscribe_*` fan-out are TAKE-grade**. What changes: it consumes `BearingReport`s and feeds the new `rfmesh-fusion` `Fuser` instead of just logging measurements; the subscriber stream now carries `FixEvent`s. Proven server scaffold, new payloads. |

**The framing decision worth a callout:** the new contracts already standardise messages as Pydantic models. The old repo's "length-prefixed MsgPack envelope with a `{type, payload}` dict" is a perfectly good wire format for them — `model_dump` on the way out, `model_validate` on the way in. The agent doing the `rfmesh-node`/transport workstream inherits a *working, tested* framing layer and only re-points it at the new types. That is a large de-risk on the networking, which is otherwise fiddly to get right under time pressure.

**Two named regression anchors carry over with this code.** Thread 1's review caught two real bugs in the old mesh implementation: a 25 dB SNR-invariant error in `S2-T3.1` (the proposed `in_band_snr >= peak_snr - 5` invariant was wrong by 25 dB for a CW signal) and a lazy subscriber-registration race in `S2-T4`. When `node.py` and `aggregator.py` are refactored, those two failures must come across as **explicit, named regression tests** — not be left as process anecdotes. They are the cheapest possible insurance that the refactor does not silently reintroduce a bug that was already paid for once.

---

## Part 6 — CLI, configs, docs, infrastructure

### CLI (`rfmesh/cli/`) — mostly LEAVE, selectively REFACTOR

| File | LoC | Verdict | Notes |
|---|---|---|---|
| `cli/record_iq.py` | 195 | REFACTOR-lite | Wraps `IQRecorder`. Useful as a capture tool for building golden/replay test data. Port as a thin `apps/` entrypoint once the I/O layer is settled. |
| `cli/calibrate_dongles.py` | 375 | REFACTOR-lite | Sequential multi-dongle calibration. Ports once `io/calibration.py` is re-seated (Part 4c). |
| `cli/servo_calibrate.py` | 296 | TAKE-lite | Servo calibration CLI. Follows the servo driver across (Part 2). |
| `cli/run_node.py` | 292 | LEAVE | Assembles the *old* `MeshNodeConfig`. The new `rfmesh-node` has its own config-from-`NodeConfig` entrypoint. Re-derive. |
| `cli/run_aggregator.py` | 176 | LEAVE | Same reasoning — re-derive against the new `FusionConfig`. |

The CLI layer is where the old architecture is most visible (old config shapes, old message types), so most of it is re-derived rather than ported. That is cheap — CLI entrypoints are thin by design — and it is the *right* place to absorb the change rather than fight it.

### Configs (`configs/`) — LEAVE (re-derive)

`node_example.json` / `dongle_calibration.example.json` are the old flat config shape. The new repo's `configs/*.yaml` are written fresh against `rfmesh-contracts` `NodeConfig`/`FusionConfig` — richer, validated, `extra="forbid"`. Re-derive; the old files are a reference for *what fields a node needs*, not a thing to port.

### Docs — split

| Item | Verdict | Notes |
|---|---|---|
| `docs/wire-protocols/servo_uart_v1.md` | TAKE | Frozen wire contract. Moves verbatim, stays the source of truth. |
| `docs/architecture.md` | LEAVE | Describes the old single-package architecture. The new repo's `ARCHITECTURE.md` is written fresh — but this is *valuable reference* while writing it. |
| `docs/PROJECT_PLAN.md`, `docs/tickets/**` | LEAVE (reference) | The old staged-ticket plan. Superseded by the new workstream plan, but the *ticket format* is a proven model the new `WORKSTREAMS.md` borrows from. |
| `docs/hardware_calibration.md` | TAKE | Empirical calibration sign-convention notes. Field-verified knowledge — keep it. |
| `docs/STAGE1_RETROSPECTIVE.md`, `docs/memory.md` | LEAVE (reference) | History. Read once for hard-won discoveries, do not port. |
| `notebooks/` | LEAVE | Stage-1 exploration. Not part of the deliverable. |

### Thread-1-produced documents (in `/mnt/user-data/outputs/`, not in the GitHub repo)

The audit above covers the GitHub repo. Thread 1 also produced several standalone documents that were never committed to the repo and so are easy to lose in the transition. Thread 1's closing digest named them explicitly:

| Document | Verdict | Notes |
|---|---|---|
| `tower_sanity_playbook.md` | **TAKE** | This *is* the Phase C smoke-test procedure — the `rtl_power` scan to identify a reference cellular carrier, then the manual sweep + RSSI-vs-angle check for a parabolic peak in the known direction. Still valid, **still unexecuted** — see the inherited-context document, where Phase C is tracked as the first hardware-validation gate. It moves into the new repo's `docs/` as the hardware-validation procedure. |
| `lora_beacon_spec.md` | **TAKE (as reference)** | Firmware spec for the 868 MHz LoRa beacon — the controlled test emitter for all pre-BoTH3 work in Poland. Building the beacon firmware is itself a workstream item; this spec is its input. |
| `field_test_plan.md` | **REFERENCE** | The old field-test plan assumed 2 nodes. The new architecture is N≥3, so the plan is partially superseded — but its geometry analysis (notably the observation that an N1–N2 baseline gives only ~10° bearing differential to a distant tower, collapsing the triangulation) is exactly the kind of GDOP reasoning the new fusion design needs, and carries over as input. |
| `mks_config_playbook.md` | **LEAVE (dead)** | MKS SERVO42D was dropped; MG996R is the servo. This document is dead. Recorded here only so nobody resurrects it. |
| `CONVERSATION_BOOTSTRAP_2.md` | **REFERENCE** | Thread 2's bootstrap document. Superseded by the new coordination package's bootstraps, but a useful format reference. |

### Infrastructure — REFACTOR (the patterns, not the files)

| Item | Verdict | Notes |
|---|---|---|
| `pyproject.toml` | REFACTOR | The old single-package layout becomes the workspace-root + per-package `pyproject.toml` set. But the *tool config* — `ruff` rules, `mypy --strict`, `pytest --cov` — is proven and carries straight over. |
| `.github/workflows/ci.yml` | TAKE-pattern | ruff + mypy + pytest + codecov. The exact shape the new repo wants; re-point paths at the workspace. |
| `.pre-commit-config.yaml` | TAKE-pattern | ruff + mypy + pytest-on-push. Carries over; add the new repo's contract-protection hook (fails if `rfmesh-contracts/src` is touched without bumping `version.py`). |
| `CLAUDE.md` | REFACTOR | Becomes `AGENTS.md` + `CLAUDE.md` under the new governance model. The old one's "hard rules" section is a direct ancestor of the new "Five Invariants". |
| `.claudeignore`, `.python-version`, `LICENSE` | TAKE | Mechanical. |

---

## Part 7 — Open placement decisions (need Maciej's call, or lead's, before tickets)

These are not "take/leave" — they are decisions the audit surfaces because they affect how the salvaged code is packaged. None blocks starting; all should be settled before the relevant workstream's first ticket.

1. **Where does the servo code live?** Options: (a) its own `rfmesh-servo` package, (b) a subpackage of `rfmesh-sdr`. It is logically a peripheral-control concern, not an SDR concern — leaning (a), a small dedicated package, keeps `rfmesh-sdr` focused. Lead recommendation: **(a)**.
2. **Logging: keep `loguru` or standardise on `structlog`?** The old repo is all-in on `loguru`. The architecture research suggested `structlog` for structured/CI-friendly logs. If we keep `loguru`, the DSP/IO salvage is a zero-diff move; if we switch, every salvaged file gets its log call sites touched. Lead recommendation: **keep `loguru`** — the salvage cost of switching is real and the benefit is marginal for a hackathon deliverable.
3. **`scipy-stubs` / `types-msgpack`** — the old repo pins these for `mypy --strict`. Carry the same pins into the workspace dev-dependencies. No decision really, just don't lose them.
4. **The `_to_complex64` duplication** — exists twice in the old repo. New repo: one home in `rfmesh-sdr`. Trivial, just don't replicate the duplication.

---

## Salvage figure — what this inherits

Rough LoC of *proven, tested* code that comes across rather than being written from scratch:

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

**The headline:** roughly **3200 LoC moves at TAKE quality** (firmware, servo, DSP, file I/O) — near-mechanical, logic untouched. Another **~2000 LoC moves at REFACTOR quality** — logic kept, seams re-cut against the contracts. And critically, the **two things that hurt most to get wrong** — the hardware-validated servo firmware, and the working mesh transport — are both inherited rather than re-risked.

What is genuinely *new* work, not salvage: `rfmesh-contracts` (done), the L1 bearing-scan orchestration, all of L2 (MUSIC, MVDR, the coherent `Receiver` impls), all of fusion (Stansfield/MLE/GDOP/ellipses), all of L3 (the classifier), the CoT/PyTAK output, and the simulator. That is the real scope of the multi-agent build — and it is meaningfully smaller because the foundation underneath it is inherited.

---

## What this audit does NOT decide

- It does not assign salvaged files to workstreams — that is `WORKSTREAMS.md`'s job, and this audit becomes its input (each workstream row will carry a "salvage" column pointing back here).
- It does not write the refactor tickets — it scopes them. The REFACTOR verdicts each describe *what changes*; the actual ticket is the owning Opus agent's job.
- It does not re-confirm test pass/coverage on the old code — the old repo's CI badge and the old thread's reports are taken at face value. The new repo re-establishes its own CI from line one regardless. One specific number to *not* trust blindly: Thread 1's digest reports an unreconciled discrepancy between "377 Python tests" and an agent-reported "398 collected" on 2026-05-13. The new repo's CI makes this moot — but if a salvage ticket cites an inherited test count as a target, use the count the new CI actually produces, not either old figure.

---

## Sign-off

Maciej: review the verdicts. The ones most worth a second look:

- **Part 4d** — the `Device` ABC → `Receiver` Protocol refactor. This is the one real architectural seam. Confirm you are happy that the *logic* of `RTLSDRDevice` is worth porting (it is ~415 LoC of subprocess-wrangling you would otherwise rewrite) rather than re-deriving from scratch.
- **Part 5** — keeping the mesh transport. Confirm you agree the framing/threading/asyncio machinery is worth inheriting, given its payload schemas are being swapped out. The alternative is writing the networking fresh; the audit's position is that the old one is tested and that is worth a lot under time pressure.
- **Part 7.1 and 7.2** — the two placement decisions (servo package location, logging library). Lead recommendations are stated; override if you disagree.

Once you sign off, this feeds straight into the coordination package: `ARCHITECTURE.md` references it, `WORKSTREAMS.md` gets its salvage column from it, and each workstream bootstrap points its Opus agent at the relevant parts.
