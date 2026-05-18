# /uvadd-request — pyserial for rfmesh-servo

**Date:** 2026-05-18
**Requesting workstream:** A (servo host driver port, WS-A-006).
**Lead disposition:** Pre-approved by Maciej in the 2026-05-18 hardware-first sprint plan.

## What

Add `pyserial>=3.5` as a **runtime** dependency of `packages/rfmesh-servo`.

## Why

`SerialTransport` in `packages/rfmesh-servo/src/rfmesh_servo/transport.py` is
the only `Transport` implementation that talks to real serial ports (the
ESP32-S2 servo controller enumerates as `/dev/ttyACM*`). Without `pyserial`
the host cannot drive the firmware that just landed in `firmware/` per
WS-A-007a. `Transport` is a `typing.Protocol`, so unit tests inject
fakes — `pyserial` is **lazy-imported** inside `SerialTransport.__init__`,
keeping the codec layer importable without it. This is what the salvage
did and the port preserves.

## Version range

`pyserial>=3.5` matches the salvage's pin (`macwed/rf-mesh`'s
`pyproject.toml`). Version 3.5 is the long-stable LTS line; the 3.x API
the driver uses (`Serial.read`, `Serial.write`, `timeout`,
`bytesize/parity/stopbits/xonxoff/rtscts/dsrdtr` kwargs) has been frozen
since ~2018.

## Invariant check (per AGENTS.md §3 / §5)

- **B1 (contracts frozen).** Not threatened — `rfmesh-servo` does not
  import `rfmesh_contracts.messages`, and adding `pyserial` does not
  change `SCHEMA_VERSION`.
- **B5 (DSP/Fusion purity).** Not threatened — `pyserial` lands in the
  `rfmesh-servo` package only. DSP/Fusion never import from it.
  `import-linter` star-independence contract continues to hold.
- **B6 (no hardware assumptions in software packages).** Not threatened —
  `pyserial` is a *transport*, not a hardware assumption: `Transport`
  Protocol is satisfied equally by `SerialTransport` and unit-test
  fakes. Hardware presence is detected/refused via the firmware's PONG
  handshake, not assumed in code.
- **WD-1 (star independence).** Not threatened — `pyserial` is an
  external PyPI dep, not a sibling rfmesh package.

## Companion change

The salvage's `loguru` dependency is **not** added; the port replaces
`from loguru import logger` with stdlib `logging` in `driver.py` and
`transport.py`. Two reasons:

1. Minimises the workspace dep footprint (one new dep instead of two).
2. `loguru` was used only for INFO/DEBUG narration; the stdlib `logging`
   API is the standard Python idiom and works identically for the
   driver's use case.

## Verification (post-uv-sync)

```bash
uv sync                              # picks up pyserial 3.5+
uv run python -c "import serial; print(serial.__version__)"  # >= 3.5
uv run pytest packages/rfmesh-servo  # 7 test files; codec tests need no hw
```

The `tests/test_integration.py` and `tests/hardware/*` paths that
actually exercise `pyserial` are mock-based (driver + fake transport)
or `@pytest.mark.hardware` gated; no real device required for the
non-hardware test pass.
