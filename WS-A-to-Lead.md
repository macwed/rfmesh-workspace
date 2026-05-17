# WS-A status update for the lead

**From:** Opus-A
**Date:** 2026-05-16
**Scope:** Status after WS-A-001 through WS-A-004 landed.

---

## Status: WS-A core simulator complete

Four tickets merged. The simulator is now feature-complete for
everything WS-B and WS-CD need from us to ship a demo:

| Ticket | What it ships | Tests |
|---|---|---|
| WS-A-001 | L1-only `SyntheticReceiver`, `Receiver` Protocol | 13 |
| WS-A-002 | Coherent L2 mode, `CoherentReceiver`, `calibrate()` | +14 |
| WS-A-003 | Channel + receiver impairments (multipath, two-ray, shadowing, IQ imbalance, DC offset, ADC quantize) | +11 |
| WS-A-004 | IQ file I/O foundation (Reader, Recorder, Metadata) — salvage TAKE | +12 |
| | **Total** | **50** |

All gates green at ticket scope. mypy strict (38 source files),
ruff, pytest. Per-package `just verify` green; workspace-wide
`just verify` has the duplicate-conftest issue surfaced in
scratchpad ws-a-2026-05-16 (see "Items needing your call" below).

## Items needing your call

Three open items in scratchpads, in priority order:

1. **`py.typed` on `rfmesh-contracts`** (scratchpad ws-a-2026-05-15).
   Every workstream's mypy strict mode is currently sprinkled with
   `# type: ignore[import-untyped, unused-ignore]` on contract imports.
   PEP-561 marker file is a zero-byte addition that does not change
   any field, enum, Protocol, or `SCHEMA_VERSION`. My read: not a
   contract change. Your call under Invariant 1.

2. **`explicit_package_bases = true` in workspace mypy config**
   (scratchpad ws-a-2026-05-16). After ADR-006, each `tests/` is
   a bare directory with its own `conftest.py`; workspace-wide
   mypy sees two modules both named `conftest`. One-line fix in
   workspace `pyproject.toml`. Preserves ADR-006 (does not re-enable
   namespace packages).

3. **ADR-004 — array calibration file format** (PROPOSED).
   Needs Opus-B's sign-off (consumer side) and your sign-off
   before WS-B-002 wires real calibration loading. Not blocking
   any current work — calibration is in-memory for now per
   WS-A-002. Blocks WS-B's L2 hardware integration when it lands.

## Cross-workstream decisions I made unilaterally

Resolved during WS-A-002 design when WS-B/WS-CD had not yet
started; documented in the WS-A-002 ticket and in the handoffs.
Surface as ADR if either consumer pushes back.

- **Azimuth convention: array-local frame inside the simulator.**
  WS-B's MUSIC runs in array-local, converts to geographic once
  at `BearingReport` emit. Rationale: EW-deployed nodes with
  servo-controllable heading benefit from a single conversion
  point.
- **Calibration via simulated wideband noise injection** (not
  pilot tone). Rationale: self-contained handshake, no exploitable
  frequency signature, matches KrakenSDR production precedent.
- **`set_antenna_heading()` ownership: node-runtime, not L1Estimator.**
  Proposed to WS-CD in the handoff doc. If WS-CD pushes back and
  prefers adding `set_antenna_heading` to the `Receiver` Protocol,
  it becomes an ADR-level conversation that routes back through
  you.

## Cross-workstream handoffs written

Both consumer-facing, in `docs/handoffs/`:

- `WS-A-to-WS-B.md` — public API surface, σ-honesty test pattern
  using `reseed()`, steering vector closed forms WS-B's manifold
  module must invert.
- `WS-A-to-WS-CD.md` — narrower; lifecycle contract,
  capability-detection branching, antenna-heading proposal.

## What is next in WS-A

Sequenced for the next sprint, not started yet:

- **WS-A-005** — port salvaged `RTLSDRDevice` (subprocess wrapper
  around `rtl_sdr` binary) to the `Receiver` Protocol, plus
  re-seat `DongleCalibration` against `Receiver.capabilities().serial`.
  First WS-A ticket that introduces a hardware-side dependency
  (system-level `rtl_sdr` in PATH; no new pip dep). Salvage
  REFACTOR-grade.
- **WS-A-006** — port salvaged servo driver + `servo_uart_v1` wire
  contract. Salvage TAKE-grade for the wire protocol; light
  refactor for the runtime.
- **WS-A-007** — port salvaged firmware S2 + LoRa beacon
  firmware. Independent of the Python stack.

These are not on the critical path; B and CD are now unblocked
without them. I am idle on the Python side until either you
prioritise WS-A-005+ or something surfaces from B/CD review.

## One small process note

Agent on WS-A-003 caught a physics errata in my acceptance criterion
1.a (two-ray null location — I had it at `d_bp`, the canonical
formula puts the deepest null at `d_bp/2` with `d_bp` itself being
the last constructive peak). Agent implemented the strict physics
and surfaced the correction. From here on I cross-check any closed-form
physics reference before writing it into an acceptance criterion.
Mentioning because the agent's discipline is the only reason the
errata did not ship into the test suite as a wrong invariant.
