# TICKET WS-A-006: Port servo host driver from `macwed/rf-mesh` salvage

## Goal (one sentence)

Re-home the salvaged servo host driver (`macwed/rf-mesh:rfmesh/scan/servo/` —
~1027 LoC, 6 modules, Protocol-based, full test suite) into
`packages/rfmesh-servo/` so the L1 bearing-sweep workflow has its
production-ready serial-protocol driver against the frozen
`servo_uart_v1` wire spec.

## Context (links only, not content)

- Contracts touched (read-only): the salvaged driver is **not** a
  workspace-contract consumer per se — it talks the `servo_uart_v1`
  wire protocol to ESP32-S2 firmware. The `rfmesh-contracts` package
  is not imported by `rfmesh-servo`. (Verify in
  `pyproject.toml`'s `[tool.importlinter.contracts]`: `rfmesh-servo`
  is in the `independence` modules list — sibling-import forbidden.)
- Architecture: `WORKSTREAMS.md` §1 row A — `packages/rfmesh-servo/`
  is in Workstream A's ownership. `SALVAGE_AUDIT.md` Part 2 names
  every module to take (TAKE-near-whole verdict on all 6).
- Inherited context: `INHERITED_CONTEXT.md` §1.2 — MG996R clones
  have unknown pulse ranges per axis; calibration is mandatory;
  `INHERITED_CONTEXT.md` §1.1 — the wire protocol `servo_uart_v1` is
  unchanged across the ESP32-C3 → ESP32-S2 port (WS-A-007); this
  host driver does not care which USB stack is on the other end.
- Prior tickets this depends on: none. Salvage is independent of all
  other workstreams. Phase C bench result is **informational** (the
  servo's mechanical bring-up is what Phase C will surface); the
  code port lands independently.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-servo -v` passes. The full salvage
   test suite from `macwed/rf-mesh:rfmesh/scan/servo/tests/` comes
   across verbatim — these are the byte-exact wire-protocol tests
   against the `servo_uart_v1` spec and they must not regress.
2. New `tests/test_protocol_byte_exact_vectors.py` if not already in
   the salvage — the 7-test vector set documented in
   `docs/wire-protocols/servo_uart_v1.md` §7.1 / §7.2 / §7.3 (CRC,
   COBS, TLV) lands as automated tests.
3. `tests/test_driver_lifecycle.py` covers:
   - `ServoDriver.open()` + `close()` are idempotent.
   - Master/slave single-outstanding-command discipline: sending a
     second command before the first replies raises
     `ServoBusyError`.
   - Frame timeout: send a command, simulate no reply, assert
     `ServoTimeoutError` after the configured timeout.
4. `tests/test_calibration_workflow.py` covers the per-axis
   calibration NVS round-trip (the firmware-side persistence is
   tested by the firmware C suite; this side asserts the
   *host-driver* CLI workflow per
   `docs/wire-protocols/servo_uart_v1.md` §3.7):
   - `cal_status` reads the firmware-stored calibration.
   - `cal_clear` + `cal_store` round-trip with a synthetic 12-byte
     calibration record.
5. `uv run mypy packages/rfmesh-servo` strict clean.
6. `uv run ruff check packages/rfmesh-servo` clean.
7. `uv run lint-imports` still 6 KEPT.
8. `rfmesh-servo-calibrate` CLI registered under `[project.scripts]`
   in `packages/rfmesh-servo/pyproject.toml` and runs `--help`
   without error (smoke test: `uv run rfmesh-servo-calibrate --help`).
9. The salvaged in-tree `cobs.py` stays in-tree (PyPI `cobs` package
   is abandoned per old `CLAUDE.md` warning; do **not** replace with
   the abandoned package).
10. `tests/hardware/test_servo_live.py` (`@pytest.mark.hardware`) for
    a single live round-trip if a flashed ESP32-S2 is attached.
    Opt-in only; never in CI.

## Out of scope (explicit non-goals)

- ESP32-S2 firmware port — that is WS-A-007.
- Servo bracket / mount design — Maciej's physical domain
  (AGENTS.md §2).
- Multi-axis calibration database / GUI — v1.0 is per-axis
  command-line; a future UI ticket is parking-lot.
- Real-time servo trajectory planning — v1.0 ships
  step-and-dwell from the L1 estimator's perspective.
- Modifying `rfmesh-contracts`. (Invariant B1.)

## Files you may touch

- `packages/rfmesh-servo/src/rfmesh_servo/__init__.py` (modify or
  create).
- `packages/rfmesh-servo/src/rfmesh_servo/crc.py`
  (port — `crc16_ccitt_false`, byte-exact).
- `packages/rfmesh-servo/src/rfmesh_servo/cobs.py` (port — in-tree
  COBS encode/decode; do not replace with PyPI `cobs`).
- `packages/rfmesh-servo/src/rfmesh_servo/protocol.py` (port — frame
  codec + Cmd / ErrorCode IntEnums + wire constants).
- `packages/rfmesh-servo/src/rfmesh_servo/messages.py` (port — typed
  payload dataclasses with `pack`/`unpack`).
- `packages/rfmesh-servo/src/rfmesh_servo/transport.py` (port —
  `Transport` Protocol + `SerialTransport` lazy-imports `pyserial`).
- `packages/rfmesh-servo/src/rfmesh_servo/driver.py` (port —
  `ServoDriver` sync client; master/slave + reconnect-backoff +
  single-outstanding-command discipline).
- `packages/rfmesh-servo/src/rfmesh_servo/exceptions.py` (port —
  servo-specific exception hierarchy out of the monolithic old
  `rfmesh/exceptions.py`).
- `packages/rfmesh-servo/src/rfmesh_servo/py.typed` (create — PEP-561
  marker).
- `packages/rfmesh-servo/src/rfmesh_servo/cli/servo_calibrate.py`
  (port from `macwed/rf-mesh:rfmesh/cli/servo_calibrate.py`).
- `packages/rfmesh-servo/tests/test_crc.py` + `test_cobs.py` +
  `test_protocol.py` + `test_driver.py` + `test_messages.py` +
  `test_transport.py` + `test_calibration_workflow.py` (port full
  suite from old repo; rename to drop `__init__.py` per
  `AGENTS.md` §3.5).
- `packages/rfmesh-servo/tests/hardware/test_servo_live.py` (create
  — `@pytest.mark.hardware`).
- `packages/rfmesh-servo/pyproject.toml` (modify — add `pyserial`
  runtime dep after `/uvadd-request` approval; `[project.scripts]`
  for `rfmesh-servo-calibrate`).
- `docs/wire-protocols/servo_uart_v1.md` (TAKE verbatim from old
  repo if not yet present).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- `firmware/**` (WS-A-007 territory).
- Any sibling workstream package.
- The workspace root `pyproject.toml` (use `uv add` only).

## Stop conditions

### §A — /uvadd-request for `pyserial`

Builder writes
`.claude/scratchpad/ws-a-006-uvadd-pyserial-<date>.md`:

- Package: `pyserial>=3.5`.
- Classification: runtime dependency of `rfmesh-servo`.
- Rationale: serial transport for the `servo_uart_v1` wire
  protocol; lazy-imported in `transport.py` (the Protocol is
  satisfied by any duck-typed transport, so unit tests run
  without pyserial; the live `SerialTransport` impl imports it
  only when actually used).
- Invariants check: B1 untouched; B3 honored (frame timeouts +
  busy-error raise); B5 N/A.

Lead approves; runs `uv add --package rfmesh-servo "pyserial>=3.5"`.
Builder does NOT run `uv add` itself.

### §B — `servo_uart_v1` is frozen wire contract

The wire protocol document at
`docs/wire-protocols/servo_uart_v1.md` (TAKE per `SALVAGE_AUDIT.md`
Part 2) is the contract between host driver + firmware. **Do not
modify it.** A contract change requires an ADR + a firmware change
in lockstep — out of WS-A-006's scope.

### §C — Standard ticket stop pattern

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation:
  - `uv run pytest packages/rfmesh-servo -m "not hardware" -v`
  - `uv run mypy packages/rfmesh-servo`
  - `uv run ruff check packages/rfmesh-servo`
  - `uv run lint-imports`
  - `uv run rfmesh-servo-calibrate --help` (smoke).
- If the salvage's API has internal drift from the spec (e.g.
  `crc16_ccitt_false` table does not match §7.1 vectors),
  STOP and write SCRATCHPAD — the byte-exactness is the entire
  point of the salvage value.

## Implementation notes (non-binding, guidance)

- The salvage's `Transport` is *already a Protocol*, not an ABC —
  this matches the new repo's structural-typing philosophy
  (`SALVAGE_AUDIT.md` Part 2 calls this out as the cleanest
  salvage in the workspace).
- Mechanical changes only: import paths shift
  (`rfmesh.scan.servo` → `rfmesh_servo`). Logic untouched.
- Operator REPL `tools/servo_repl.py` lands as a thin `scripts/`
  entrypoint or `apps/servo-repl/` if a more polished home is
  wanted — workstream freedom.
- The `_to_complex64` consolidation noted in WS-A-005 is a sibling
  cleanup; not in WS-A-006 scope (servo does not handle IQ).
- Per `INHERITED_CONTEXT.md` §1.2: the host driver consumes
  calibrated angle values, never raw PWM pulses, for any
  bearing-construction. The firmware applies the calibration; the
  host driver carries calibration commands to the firmware. **No
  pulse-to-angle math anywhere in the Python driver.**
