# rfmesh-contracts

**The frozen interface layer of the rfmesh project.** Every other workstream
depends on this package; no workstream depends on any other. The dependency
graph is a star with `rfmesh-contracts` at the centre.

## What is in here

| Module | Contents |
|---|---|
| `version.py` | `SCHEMA_VERSION` — the frozen contract version. |
| `enums.py` | `Capability`, `EmitterClass`, `ConfidenceLevel`, `ArrayGeometry`, `BearerKind`. |
| `geospatial.py` | `GeodeticPosition`, `EllipseENU` — shared geometric value types. |
| `messages.py` | `BearingReport`, `FixEvent`, `NodeStatus` — the wire format between workstreams. |
| `config.py` | `SDRConfig`, `ArrayConfig`, `BearerConfig`, `NodeConfig`, `FusionConfig` — the YAML deployment schema. |
| `protocols.py` | `Receiver`, `CoherentReceiver`, `BearingEstimator`, `Fuser`, `CotPublisher`, `Bearer` — behavioural contracts. |

## The rule

This package is edited **only by the lead architect**, and only via an
accepted ADR (`docs/adr/`). Every change ends with a `SCHEMA_VERSION` bump.

A workstream agent who believes a contract must change writes a
CHANGE-REQUEST ADR and **stops** — it does not edit the contract. See
`AGENTS.md` → "The Five Invariants" and `INTERFACES.md` for the semantics of
every type.

## Why it is shaped this way

- **Pydantic models** for data contracts: validation happens at the boundary,
  malformed messages/configs fail loudly and locally.
- **`Literal[SCHEMA_VERSION]`-style version pinning** on every message: a
  workstream built against a stale contract is a *type error* under mypy,
  caught before integration, not during the live demo.
- **`typing.Protocol`** for behavioural contracts: structural typing means a
  workstream conforms by shape, with no inheritance coupling back to this
  package. The dependency star stays clean.
- **`extra="forbid"`** on every model: a typo'd YAML key or an unexpected
  field is a loud error, never a silently ignored setting.

## Install (within the workspace)

This is a `uv` workspace member. From the repo root:

```sh
uv sync
```

`rfmesh-contracts` is then importable by every workspace package and resolves
to this source tree (editable).
