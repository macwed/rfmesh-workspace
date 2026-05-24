# ADR-025 — DSSS directional mesh comms (SCHEMA_VERSION 1.3.0)

**Status:** ACCEPTED (2026-05-24)
**Date:** 2026-05-24
**Author:** lead-Opus (drafting per the DSSS implementation plan in `docs/dsss/potrzebujplanuimplementacjirozszerzenidempotentplum.md`).
**Accepted by:** Maciej (@macwed), 2026-05-24.
**SCHEMA_VERSION change:** 1.2.0 → **1.3.0** (MINOR, additive, backward-compatible).
**Depends on:** ADR-021 (comms-first reframing), ADR-019 (antenna rendezvous), ADR-013 (1.2.0 honesty extensions — must land first to free 1.3.0).

## Context

ADR-021 reframed rfmesh as a **€250 directional radio that points itself, survives jamming by pointing away from it, and triangulates the jammer as a free side-effect**. ADR-019 specified how two nodes auto-acquire each other (GPS-prior pointing + scan-and-stare). Both ADRs assume the existence of a data link between two pointed nodes, but the codebase today is **100% RX/DF**: there is no transmitter primitive anywhere, no modulation, no framing, no multi-hop routing. The "primary" product the post-pivot pitch competes on does not exist in software.

The hardware reality this ADR plans against:

- **One BladeRF 2.0 micro (coherent 2-RX, also TX-capable, owned)** and at least one borrowed/added TX-capable SDR (HackRF One, ADALM-Pluto+) per active comms node. RTL-SDR V4 stays RX-only and **cannot participate in comms** — declaring `COMMS_DSSS` on an RTL-SDR node is a fatal boot error (B3, no silent downgrade).
- The existing servo + Yagi rig is reused without firmware change (B6: firmware is hardware concern, untouched).
- The mesh routing, peer roster and TDD timing live entirely at the node-runtime layer (precedent: `RendezvousConfig` in `rfmesh-node`), so the contract surface stays minimal.

The intended physical-layer choice is **DSSS BPSK ~10 Mchip/s with a length-1023 m-sequence (processing gain ≈ 30 dB)** over the directional Yagi beams, with multi-hop routing through the mesh. The combination gives LPI/LPD (low signal density off-axis), graceful EW degradation, and geometric jammer-avoidance as a free property — all of which map straight to the BoTH3 Counter-Jamming brief.

The implementation framing this ADR adopts is the iteration plan in `docs/dsss/potrzebujplanuimplementacjirozszerzenidempotentplum.md`. That document is the working plan; this ADR is the sign-off for its contract-touching subset (Iter 0).

## Decision

Land DSSS directional-comms support as a new pure-Python package `rfmesh-dsss`, plus the minimum contract surface needed to plug it into the existing star topology, in a single bump to SCHEMA_VERSION 1.3.0. The full plan ships across seven iterations; this ADR's authority covers Iter 0 only (contract change + skeleton). Iter 1+ proceed under the normal verify/council gates without further ADRs unless they themselves touch contracts.

### Change A — Bump `SCHEMA_VERSION` 1.2.0 → 1.3.0

MINOR-additive bump. ADR-013 just landed 1.2.0 with the G3/G4 honesty fields; this ADR uses the next number cleanly. Mechanics are identical to ADR-013's bump: `SCHEMA_VERSION` and `SchemaVersionT` move in lockstep in `version.py`, every workstream's `Literal[SchemaVersionT]` pin fires mypy until updated. The `extra="forbid"` asymmetry documented in ADR-013 applies here too.

### Change B — `Capability.COMMS_DSSS`

Add a new enum member to `rfmesh_contracts.enums.Capability`:

```python
COMMS_DSSS = "comms_dsss"
```

with semantics: a node declaring this capability participates in the DSSS directional mesh (i.e. transmits and receives DSSS frames over the pointed Yagi link). Requires **both** an RX path and a TX path on the configured SDR. A node declaring `COMMS_DSSS` whose detected SDR cannot transmit (e.g. RTL-SDR V4) is a fatal startup error.

`COMMS_DSSS` is **mutually exclusive** with the DF capabilities (`L1_RSSI`, `L2_MUSIC`, `L2_CAPON`, `L2_MVDR_NULL`) in v1.3.0. A node operates in **either** DF mode **or** COMMS mode, selected by CLI flag / config. Concurrent DF + COMMS on the same node is deferred to a future ADR. `L3_CLASSIFY` may coexist with `COMMS_DSSS` (classification is SDR-agnostic and runs on tapped IQ regardless of operating mode).

### Change C — `Transmitter` / `CoherentTransmitter` Protocols + `TransmitterCapabilities`

Add behavioural contracts symmetric to `Receiver` / `CoherentReceiver`:

```python
class TransmitterCapabilities(Protocol):
    @property
    def driver(self) -> str: ...
    @property
    def n_tx_channels(self) -> int: ...
    @property
    def actual_sample_rate_hz(self) -> float: ...
    @property
    def max_tx_power_normalized(self) -> float: ...
    # NB: no `is_power_calibrated` / no `max_tx_power_dbm`. Same B.2
    # honesty rule as the RX side -- none of the SDRs in scope are
    # absolute-power-calibrated on transmit either. Normalized power
    # in [0.0, 1.0] is the honest unit the driver exposes.

@runtime_checkable
class Transmitter(Protocol):
    def open(self) -> None: ...
    def configure(self, config: NodeConfig) -> None: ...
    def write(self, iq: IQBlock) -> int: ...
    def capabilities(self) -> TransmitterCapabilities: ...
    def close(self) -> None: ...

@runtime_checkable
class CoherentTransmitter(Transmitter, Protocol):
    def write_coherent(self, iq: CoherentIQBlock) -> int: ...
```

Semantic guarantees:

- `write(iq)` returns the number of samples actually transmitted **or raises**. There is no short-write silent fallback (B3, mirror of `Receiver.read`'s hard guarantee). A producer that needs all `len(iq)` samples on-air must check the return value and raise on shortfall.
- `IQBlock` and `CoherentIQBlock` are the same numpy aliases the RX side uses; TX consumes the same shapes/dtypes the simulator and DSP produce.
- `CoherentTransmitter` is reserved for arrays — single-channel TX uses plain `Transmitter`. Most v1.3.0 hardware (HackRF, Pluto+, BladeRF in single-chain mode) is `Transmitter` only.

### Change D — `CommsConfig`

Add a new Pydantic config to `rfmesh_contracts.config`:

```python
class CommsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    carrier_freq_hz: float = Field(gt=0.0, ...)
    chip_rate_hz: float = Field(gt=0.0, ...)
    spreading_factor: int = Field(default=1023, ...)
    lfsr_taps: tuple[int, ...] = Field(...)
    lfsr_seed: int = Field(gt=0, ...)
    tdd_slot_ms: float = Field(gt=0.0, ...)
    tdd_guard_ms: float = Field(ge=0.0, ...)
    frame_payload_max_bytes: int = Field(gt=0, ...)
```

with cross-field validators: `spreading_factor` must equal `2**n - 1` for the LFSR-tap polynomial that produces an m-sequence; `chip_rate_hz` must not exceed the configured SDR `sample_rate_hz` (relationship checked at startup, where both are visible together).

This config carries only **cross-workstream** parameters (DSP + SDR + node all depend on `chip_rate_hz`, `carrier_freq_hz`, `spreading_factor`). Node-layer concerns (peer roster, routing table, per-link policy) stay in `rfmesh-node/comms/comms_config.py` as a `RendezvousConfig`-style helper — they do not touch the frozen contracts.

`NodeConfig` is **not** modified by this ADR.

### Change E — New package `rfmesh-dsss`

Greenfield, pure Python (numpy + scipy), mirror of `rfmesh-dsp` structure (B5: no network / file I/O / subprocess / SDR). Module list per the implementation plan:

- `pn_sequence.py` — m-sequence LFSR (length-10 = 1023 chips by default; properties: balance, two-level autocorrelation).
- `modulation.py` — BPSK map/demap; optional pulse-shape.
- `spreading.py` — spread (bit → 1023 chips), despread (correlate with PN).
- `correlation.py` — matched filter, FFT-correlation, preamble acquisition, code-phase search.
- `timing.py` — chip/symbol timing recovery (early-late), CFO/phase correction.
- `framing.py` — preamble + sync word + header (src/dst/seq/len) + payload + CRC encode/decode. Uses an internal CRC-16 (no sibling import — WD-1).
- `link_budget.py` — processing gain, theoretical BER(Eb/N0), honest link metrics.
- Plus `constants.py`, `exceptions.py`, `py.typed`.

Tests follow the `rfmesh-dsp` golden + Monte-Carlo pattern (`tests/golden/*.npz` from `golden_generator.py`; `test_ber_honesty.py` is the sigma-honesty analogue for the link layer — measured BER must lie within a ±20% band of the prediction from the documented processing gain).

`rfmesh-dsss` is added to:

- `pyproject.toml` workspace dependencies + `[tool.uv.sources]`.
- `[tool.importlinter]` `root_packages`.
- The **star-independence** contract (forbids cross-sibling imports).
- A **new purity contract** mirroring `rfmesh-dsp`'s, forbidding `requests`, `httpx`, `aiohttp`, `socket`, `subprocess` in `rfmesh_dsss`.

### Open question — Iter 0 contracts surface only, or stub modules now?

Iter 0 lands the contract bump + the package skeleton (pyproject, src/rfmesh_dsss/* module headers with docstrings only, tests/ scaffold). Module bodies are empty (`raise NotImplementedError` or `pass` placeholders) so the package imports cleanly under mypy and import-linter without claiming any functionality. Iter 1 fills the PN/modulation/spreading bodies; Iter 2 fills correlation/timing/framing; Iter 3 fills the synthetic transmitter + loopback channel in `rfmesh-sdr`. **Recommendation: ship Iter 0 as skeleton; do not block on module implementations.** That matches ADR-008's reservation-then-implementation pattern for `L2_CAPON` / `L2_MVDR_NULL`.

## Why MINOR, not MAJOR

Per `version.py:21-23` definition (unchanged): MINOR is additive, backward-compatible. Both new Protocols are *additions* — no existing class loses a method. `Capability.COMMS_DSSS` is a new enum member that existing consumers can treat as "unknown" (their match statements default to refusal, which is B3-compatible). `CommsConfig` is a new model; nothing references it from outside `rfmesh-dsss` and the node-layer comms wiring (Iter 4+). The `extra="forbid"` asymmetry described in ADR-013 still applies: a consumer on 1.2.0 reading a 1.3.0 producer's `BearingReport` will fail validation if the producer happens to have set a new field. No new fields ship on existing messages in this ADR, so the asymmetry only bites once Iter 1+ extend wire payloads — at which point we are operating under 1.3.0 and the tripwire is already cleared.

## Consequences

**Operational:**

- Every package's pinned `SchemaVersionT` updates: `type SchemaVersionT = Literal["1.3.0"]`. Mypy fires across the workspace until every consumer updates. This is exactly the type-system tripwire ADR-012 / ADR-013 exercise.
- The `test_schema_version_tripwire.py` stale-rejection baseline must move `"1.1.0"` → `"1.2.0"` to keep the rejection semantics (mirror of ADR-013 §Consequences).
- `apps/demo-replay/src/rfmesh_demo_replay/scenario.py`, configs, field-deploy examples, `INTERFACES.md` / `ARCHITECTURE.md` / `docs/data-pipeline.md` headers all migrate `"1.2.0"` → `"1.3.0"`.
- `import-linter` gains a new purity contract for `rfmesh_dsss`; new package is auto-discovered by `uv` workspace.

**Code changes paired with this ADR (post-acceptance, Iter 0):**

1. `packages/rfmesh-contracts/src/rfmesh_contracts/version.py` — bump SCHEMA_VERSION + SchemaVersionT.
2. `packages/rfmesh-contracts/src/rfmesh_contracts/enums.py` — add `COMMS_DSSS` to `Capability`.
3. `packages/rfmesh-contracts/src/rfmesh_contracts/protocols.py` — add `TransmitterCapabilities`, `Transmitter`, `CoherentTransmitter`.
4. `packages/rfmesh-contracts/src/rfmesh_contracts/config.py` — add `CommsConfig` with cross-field validators.
5. `packages/rfmesh-contracts/tests/*.py` — update stale-version tests' baseline `"1.1.0"` → `"1.2.0"`.
6. `packages/rfmesh-contracts/pyproject.toml` — pkg version 1.2.0 → 1.3.0.
7. `packages/rfmesh-dsss/**` — new skeleton (pyproject, src/, tests/, README).
8. Root `pyproject.toml` — add dependency, `[tool.uv.sources]`, `[tool.importlinter]` `root_packages` + star + new purity contract.
9. `INTERFACES.md` — §1 `Capability` table adds `COMMS_DSSS`; §4 adds `CommsConfig`; §5 adds `Transmitter` / `CoherentTransmitter` Protocols.
10. `ARCHITECTURE.md` / `docs/data-pipeline.md` headers — pin update.
11. Hardcoded `"1.2.0"` references in node tests + configs.

**Iter 1-7 (post-Iter-0, no further ADR required):**

| Iter | Scope |
|---|---|
| 1 | DSSS DSP core (pn_sequence, modulation, spreading) |
| 2 | Acquisition, sync, framing |
| 3 | Synthetic TX + loopback channel (`rfmesh-sdr` extension) |
| 4 | COMMS mode in node runtime (single link, `CommsLoop`, TDD) |
| 5 | Multi-hop static routing + relay (A-B-C scenario) |
| 6 | BladeRF / HackRF / Pluto TX drivers (hardware, opt-in) |
| 7 | (optional) Link visualisation in ops dashboard (link ribbons, RSSI, BER) |

Each iteration is independently testable and mergeable to the branch; the council protocol runs only at merge-to-main time, not per iteration.

## Tradeoffs considered

**Why land contracts + skeleton in one bump rather than reserving the enum / Protocols ADR-008-style and filling later:**

- ADR-008's reservation pattern was used for `L2_CAPON` / `L2_MVDR_NULL` because the implementation was already underway in `rfmesh-dsp` against the old enum. Here `rfmesh-dsss` does not exist yet, so reservation buys nothing — the package itself needs to land for any work to begin. One ADR + one bump is cleaner than two.

**Why `Transmitter` to contracts now rather than node-local:**

- Symmetry with `Receiver` / `CoherentReceiver`. Multiple workstreams (sdr drivers, simulator, node runtime, dsss DSP) will all reference the TX behavioural shape; a Protocol in `rfmesh-contracts` is the only place that does not violate WD-1 (cross-package imports resolve to contracts or own package).

**Why pure Python (numpy/scipy) and not GNU Radio:**

Carried over from the plan §4. Five reasons:

1. Purity (B5) + import-linter. GNU Radio is a flowgraph runtime (C++ scheduler, gr-blocks, custom threads) — sprung from a paradigm that conflicts with our pure-functions-and-golden-tests model.
2. Simulator-first (ARCHITECTURE §4). The whole dev model is "DSP on synthetic IQ with no code change". Pure functions test deterministically; flowgraphs do not.
3. Dependency weight + `uv`. GNU Radio is a heavy system package (apt/conda), does not install cleanly via pip/uv; would burden every Pi image and be a painful `/uvadd-request`.
4. Repo convention. Everything else is numpy/scipy + golden tests; matching that is a stated requirement.
5. Throughput. The heavy paths (correlation, despread) vectorise via `scipy.signal.fftconvolve` / FFT and serve block-processing fine. If realtime 10 Msps becomes a hot-loop wall, the escalation is `numba` (lightweight, uv-friendly), not GNU Radio. POC net rate is ~10 kbit/s — modest.

**Why mutual-exclusivity DF vs COMMS in v1.3.0 (not concurrent on one node):**

- The same SDR + same Yagi cannot be in two pipelines at once without an arbitration layer that does not exist. Designing it adds scope; the BoTH3 demo can run with N nodes split into a DF subset and a COMMS subset (or all-COMMS with triangulation as a side-effect of acquisition sweeps per ADR-021's framing).

**Why peer roster / routing / TDD timing stay node-layer (not in contracts):**

- Mirror of `RendezvousConfig`. These are deployment / orchestration concerns; baking them into frozen contracts would lock in design decisions before the multi-hop scenario is field-validated. Any future serialisation requirement (e.g. fusion server needing to inspect the routing table) can be added by ADR.

## References

- DSSS implementation plan: `docs/dsss/potrzebujplanuimplementacjirozszerzenidempotentplum.md`.
- ADR-021 — comms-first reframing (the *why* this ADR exists).
- ADR-019 — antenna rendezvous (the pointing primitive `CommsLoop` reuses).
- ADR-013 — 1.2.0 honesty extensions (must land first; SCHEMA_VERSION precedent).
- ADR-012 — `Literal[SchemaVersionT]` tripwire.
- ADR-008 — L2_CAPON enum + null-steering reservation (reservation-pattern precedent).
- `AGENTS.md` §1 — Seven Binding Invariants (B1 the gate this ADR opens).

## Sign-off

When Maciej reviews and accepts:

1. Change this file's `Status:` line from `PROPOSED` to `ACCEPTED`.
2. Add a line: `**Accepted by:** Maciej (@macwed), <date>.`
3. Lead-Opus executes Iter 0 (contracts bump + skeleton + import-linter), one commit per logical group, scoped-verify between steps.
4. Iter 1+ proceed under standard ticket / verify / council gates; no further ADR required unless an iteration touches contracts.

If rejected:

1. Change `Status:` to `REJECTED`.
2. Add a line stating the reason.
3. DSSS comms work is parked until a future bump.
