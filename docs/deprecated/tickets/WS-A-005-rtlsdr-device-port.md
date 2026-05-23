# TICKET WS-A-005: Port `RTLSDRDevice` to the `Receiver` Protocol

## Goal (one sentence)

Re-home the salvaged `rtl_sdr`-subprocess receiver from
`macwed/rf-mesh:rfmesh/io/devices/rtlsdr.py` (~415 LoC, proven prior
art) into `packages/rfmesh-sdr/src/rfmesh_sdr/devices/rtlsdr.py` behind
the `rfmesh_contracts.protocols.Receiver` Protocol so the production
node runtime can drive a real RTL-SDR V4 dongle without changing any
DSP / node code.

## Context (links only, not content)

- Contracts touched (read-only):
  `rfmesh_contracts.protocols.Receiver`, `rfmesh_contracts.config.SDRConfig`,
  `rfmesh_contracts.IQBlock` type alias.
- Architecture: `ARCHITECTURE.md` §2 Axis 1 (SDR hardware absorbed in
  `rfmesh-sdr` behind the Protocol) + `INTERFACES.md` §5 `Receiver`
  Protocol surface.
- Salvage: `SALVAGE_AUDIT.md` Part 4d — old `RTLSDRDevice` ABC-based
  subclass (`Device` ABC → `Receiver` Protocol structural-typing swap)
  + `rtl_eeprom` serial-resolution path; existing tests carry over.
- Inherited context: `INHERITED_CONTEXT.md` §1.3 (no SDR in scope is
  power-calibrated; `ReceiverCapabilities.is_power_calibrated = False`).
- Prior tickets this depends on: WS-A-001 / WS-A-002 / WS-A-003
  (simulator + impairments — already merged). WS-A-004 IO port
  (already merged). Phase C bench result (informational; gating only
  the live-hardware integration session, not the code port).

## Acceptance criteria

1. `uv run pytest packages/rfmesh-sdr -v -m "not hardware"` passes.
   New tests under `packages/rfmesh-sdr/tests/test_rtlsdr_receiver.py`,
   each named so the failure points at one property:

   - `test_implements_receiver_protocol` — `isinstance(RTLSDRDevice(...),
     Receiver)` is True at runtime (Receiver is `@runtime_checkable`).
   - `test_capabilities_shape` — `capabilities()` returns a
     `ReceiverCapabilities` with `n_channels=1`,
     `is_power_calibrated=False`, RTL-SDR V4 freq/rate bounds populated.
   - `test_actual_sample_rate_surfaced` — `configure()` requesting
     2.4 MS/s reports the actual achieved rate via
     `ReceiverCapabilities.actual_sample_rate_hz` (Invariant B3 — no
     silent shortfall).
   - `test_read_exact_n_or_raises` — `read(n)` returns exactly `n`
     samples or raises (no zero-padded short reads).
   - `test_serial_resolution_prefers_serial_over_index` — when the
     `SDRConfig.serial` is provided, the device resolves to that serial
     (mock the `rtl_eeprom` output).
   - `test_lifecycle_open_configure_read_close` — full lifecycle: open
     → configure → read N samples → close.
   - `test_close_idempotent` — `close()` on an already-closed device
     does not raise.

2. `@pytest.mark.hardware` smoke tests in
   `packages/rfmesh-sdr/tests/hardware/test_rtlsdr_live.py`:

   - `test_live_read_returns_complex64_block` — opens a real
     RTL-SDR V4 (gated by hardware presence; `pytest.skip` if not
     attached), reads 100k samples at 2.4 MS/s, asserts dtype is
     `complex64` + finite values.
   - `test_live_gain_setting_is_explicit` — `gain_db=30.0` sets the
     gain reproducibly; not `"auto"` (per `INHERITED_CONTEXT.md` §1.3
     "prefer explicit gain for DF work").

   Hardware tests are opt-in (`-m hardware`), never run in CI per
   `CLAUDE.md` working environment.

3. `uv run mypy packages/rfmesh-sdr` strict clean.
4. `uv run ruff check packages/rfmesh-sdr` clean.
5. `uv run lint-imports`: still 6 KEPT, 0 broken. The new file imports
   only from `rfmesh_contracts` + stdlib + `numpy` + `pyrtlsdr` (or
   whatever runtime dep is added).
6. The salvaged `_to_complex64` helper (currently duplicated in old
   repo) is **not** duplicated — uses the one already in
   `packages/rfmesh-sdr/src/rfmesh_sdr/iq.py` (consolidated under
   WS-A-004). If it does not yet live there, this ticket moves it
   there first.

## Out of scope (explicit non-goals)

- Coherent / multi-channel RTL-SDR support (only V4 single-channel here).
- Bias-tee toggling at runtime — `SDRConfig.bias_tee` is honored at
  open time; runtime toggles are a future ticket.
- `SoapyReceiver` (separate Receiver impl for HackRF / Pluto / bladeRF —
  see WS-A-005b parking-lot ticket).
- BladeRF / Pluto coherent receivers (WS-A-006-coherent in scope only
  after Pluto+ arrives).
- Firmware. (Servo and SDR are separate workstreams; firmware is
  WS-A-007.)
- Modifying `rfmesh-contracts`. (Invariant B1.)

## Files you may touch

- `packages/rfmesh-sdr/src/rfmesh_sdr/devices/__init__.py` (create or
  modify).
- `packages/rfmesh-sdr/src/rfmesh_sdr/devices/rtlsdr.py` (create — the
  port).
- `packages/rfmesh-sdr/src/rfmesh_sdr/iq.py` (modify if
  `_to_complex64` needs to consolidate here per acceptance criterion 6).
- `packages/rfmesh-sdr/tests/test_rtlsdr_receiver.py` (create).
- `packages/rfmesh-sdr/tests/hardware/test_rtlsdr_live.py` (create —
  `@pytest.mark.hardware` opt-in).
- `packages/rfmesh-sdr/pyproject.toml` (modify — add `pyrtlsdr` runtime
  dep after `/uvadd-request` approval; see §A below).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator.py` (WS-A-001 territory;
  read-only).
- Any sibling workstream package (`rfmesh-dsp`, `rfmesh-fusion`, etc.).
- The workspace root `pyproject.toml` directly — `uv add` mutates it
  transactionally.

## Stop conditions

### §A — /uvadd-request for `pyrtlsdr`

`pyrtlsdr` is not in the workspace lock. Builder writes
`.claude/scratchpad/ws-a-005-uvadd-pyrtlsdr-<date>.md` with:

- Package: `pyrtlsdr` (or whichever Python binding the salvage used —
  inspect old `RTLSDRDevice` first; it may use `subprocess` against
  `rtl_sdr` CLI rather than a Python binding, in which case no new
  runtime dep is needed and you skip §A entirely).
- Classification: runtime dependency of `rfmesh-sdr`.
- Rationale: Receiver impl for RTL-SDR V4 + V3 dongles, the proven
  pre-flight path per SALVAGE_AUDIT Part 4d.
- Invariants check: B1 untouched, B3 honored (read-exact-or-raise),
  B5 N/A (sdr is not pure by design — it does hardware I/O).

Lead approves; runs `uv add --package rfmesh-sdr pyrtlsdr`. Builder
does NOT run `uv add` itself.

**If the salvage uses subprocess against `rtl_sdr` CLI:** no
`/uvadd-request` needed — the binary is a system dep documented in the
package README; assume the bench environment has `rtl-sdr` installed.

### §B — Standard ticket stop pattern

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation the full output of:
  - `uv run pytest packages/rfmesh-sdr -m "not hardware" -v`
  - `uv run mypy packages/rfmesh-sdr`
  - `uv run ruff check packages/rfmesh-sdr`
  - `uv run lint-imports`
- If a contract change appears necessary (e.g.
  `ReceiverCapabilities` needs a new field), STOP and write
  `docs/adr/ADR-NNN-<short>.md` PROPOSED. Do not modify
  `rfmesh-contracts`.
- If the salvage's API diverges substantially from what this ticket
  assumes (e.g. the old `RTLSDRDevice.stream()` generator's shape is
  incompatible with `read(n)`-or-raise semantics), STOP and write a
  SCRATCHPAD note.

## Implementation notes (non-binding, guidance)

- The salvage's `RTLSDRDevice` is the proven prior art. Port the
  *logic* (the subprocess pattern, the `rtl_sdr -` stdout chunked
  read, the capability bounds, the `rtl_eeprom` serial path) — adapt
  the *seams*: `Device` ABC → `Receiver` Protocol; lifecycle methods
  rename to match `open / configure / read / capabilities / close`.
- `Receiver.read(n)` is sync from the caller's view; the old
  `stream()` generator becomes an internal queue/buffer the sync
  `read(n)` drains.
- Hard guarantee per `INTERFACES.md` §5: `read(n)` returns exactly
  `n` samples or raises. Silent short read would corrupt every
  downstream estimate. (B3.)
- `ReceiverCapabilities.is_power_calibrated = False` always (no SDR
  in scope is power-calibrated per `INHERITED_CONTEXT.md` §1.3).
- If Phase C surfaces multipath inflation requiring a different
  default `sweep_dwell_samples` for the L1 estimator, that is a
  WS-B follow-up not this ticket.
