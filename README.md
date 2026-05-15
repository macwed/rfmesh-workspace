# rfmesh

Cooperative bearing mesh for RF emitter geolocation. Target event:
**BoTH3 Counter-Jamming Challenge 2** (Belgian MoD, Commando Training
Centre Marche-les-Dames, 22–24 May 2026).

This is a `uv` workspace with multiple member packages under `packages/`,
plus inherited firmware under `firmware/`, deployment scenarios in
`scenarios/`, configs in `configs/`, and the demo apps in `apps/`.

## Getting started

```bash
# install uv first: https://docs.astral.sh/uv/getting-started/installation/
uv sync                # creates .venv, installs all workspace packages editable
just verify            # ruff + mypy + pytest (the gate AGENTS.md names)
```

## Documents — read in this order

For anyone (human or agent) joining cold:

1. **`ARCHITECTURE.md`** — the *why*. Binding architectural invariants.
2. **`INTERFACES.md`** — the *what*. Semantic dictionary of every contract.
3. **`INHERITED_CONTEXT.md`** — the *what we already learned*. Knowledge
   from the prior project that cannot be derived from code alone.
4. **`WORKSTREAMS.md`** — the *who*. Workstream ownership and dependencies.
5. **`AGENTS.md`** — the *how*. Rules for AI agents working on this repo.
6. **`SALVAGE_AUDIT.md`** — file-by-file disposition of the prior repo
   (`github.com/macwed/rf-mesh`) into this workspace.

Per-workstream onboarding (one page each):

- `docs/bootstrap-A.md` — Workstream A (SDR + simulator + firmware salvage)
- `docs/bootstrap-B.md` — Workstream B (DSP + ML)
- `docs/bootstrap-CD.md` — Workstream C+D (fusion + CoT + node runtime + ops)

## Layout

```
rfmesh-workspace/
├── pyproject.toml              ← workspace root, tool config
├── uv.lock                     ← committed for reproducibility
├── justfile                    ← verify, lint, type, test, fmt
├── README.md                   ← this file
├── ARCHITECTURE.md             ← binding architecture (the why)
├── INTERFACES.md               ← contract semantics (the what)
├── INHERITED_CONTEXT.md        ← hard-won facts from the prior project
├── WORKSTREAMS.md              ← ownership map and dependencies
├── AGENTS.md                   ← rules for AI agents
├── SALVAGE_AUDIT.md            ← what is inherited from rf-mesh
├── docs/
│   ├── adr/                    ← architecture decision records
│   ├── bootstrap-A.md          ← Workstream A onboarding
│   ├── bootstrap-B.md          ← Workstream B onboarding
│   └── bootstrap-CD.md         ← Workstream C+D onboarding
├── packages/
│   └── rfmesh-contracts/       ← frozen interface layer (lead-only)
│       ├── pyproject.toml
│       └── src/rfmesh_contracts/
│           ├── __init__.py
│           ├── version.py      ← SCHEMA_VERSION
│           ├── enums.py
│           ├── geospatial.py
│           ├── messages.py     ← BearingReport, FixEvent, NodeStatus
│           ├── config.py       ← Node/Fusion/SDR/Array/Bearer configs
│           └── protocols.py    ← Receiver, Fuser, CotPublisher, Bearer, ...
│   (workstream packages — rfmesh-sdr, rfmesh-dsp, ... — land here as
│    workstream owners deliver them)
├── apps/                       ← thin entrypoints (filled by Workstream C+D)
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── golden/                 ← golden-file DSP regression data
│   ├── property/               ← hypothesis-driven invariants
│   └── hardware/               ← @pytest.mark.hardware, opt-in only
├── scenarios/                  ← YAML demo geometries
├── configs/                    ← YAML node and fusion configs
└── firmware/                   ← ESP32 firmware (inherited from rf-mesh)
```

## Governance, short version

- **`packages/rfmesh-contracts/` is frozen.** Edits by the lead only, via
  an ADR, paired with a `SCHEMA_VERSION` bump. See `AGENTS.md` §1
  Invariant 1.
- **Every workstream depends only on `rfmesh-contracts`.** No package
  imports from another workstream. The dependency graph is a star.
- **Software agents do software.** Physical hardware, RF physics already
  decided, deployment ergonomics are out of scope for agents — see
  `AGENTS.md` §2.
- **Verify before declaring done.** `just verify` is the gate.

## Prior project

`github.com/macwed/rf-mesh` is the parts-donor repository. It is **not**
this repo's git ancestor — this is a fresh workspace that selectively
inherits per `SALVAGE_AUDIT.md`. The old repo stays as a stable
historical reference; nothing in it is altered.

## License

Apache-2.0 (carried over from the prior project; see `LICENSE`).
