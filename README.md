# rfmesh

A **€250 directional radio that points itself, survives jamming by
pointing away from it, and triangulates the jammer as a free
side-effect** (per ADR-021, 2026-05-23). Target event: **BoTH3
Counter-Jamming Challenge 2** (Belgian MoD, Commando Training Centre
Marche-les-Dames, 22–24 May 2026).

Each node carries an SDR and a directional Yagi on a 1-axis pan
servo. Two nodes that want to talk auto-acquire each other (GPS-prior
pointing + scan-and-stare per ADR-019) and lock a directional link
with side-lobe rejection of co-channel jammers. The geolocation
pipeline (L1 amplitude DF + Stansfield/MLE fusion + ATAK markers) is
preserved as a **side-effect feature** on the same hardware — peer
sweeps drop bearings on unknown emitters in the band, fusion draws an
honest confidence ellipse with 2+ nodes.

This is a `uv` workspace with member packages under `packages/`,
firmware under `firmware/` + `firmware-beacon/`, the FastAPI backend
+ Leaflet frontend under `deployment/`, and Pi field-deployment
scripts under `field-deploy/`.

## Getting started

```bash
# install uv first: https://docs.astral.sh/uv/getting-started/installation/
uv sync                # creates .venv, installs all workspace packages editable
just verify            # ruff + mypy + pytest (the gate AGENTS.md names)
```

For the operator's manual (setup → dev → demo → firmware flash) see
`docs/MANUAL.md`. For Pi field deployment see `field-deploy/README.md`.

## Documents — read in this order

For anyone (human or agent) joining cold:

1. **`ARCHITECTURE.md`** — the *why*. Binding invariants + Appendix A
   (universal contract conventions) + Appendix B (hardware quirks +
   regression anchors). Read cover-to-cover.
2. **`INTERFACES.md`** — the *what*. Semantic dictionary of every
   contract type.
3. **`AGENTS.md`** — the *how*. The Seven Binding Invariants, allowed
   commands, council protocol, ticket format. Binds every agent.
4. **`CLAUDE.md`** — Claude Code project quick reference (autonomy
   policy + council protocol). Loaded automatically at session start.
5. **`docs/DOC_INDEX.md`** — selective-read index. Names every binding
   section, ADR, and fact so an agent can jump to one without
   scanning the whole tree.

ADRs (`docs/adr/`) are the append-only architectural-decision record;
read the one named in any binding doc that references it.

`docs/deprecated/` carries the retired pre-pivot docs (`WORKSTREAMS.md`,
`SALVAGE_AUDIT.md`, full `INHERITED_CONTEXT.md`, sprint logs, retired
tickets). **Not binding** — historical reference only.

## Layout

```
rfmesh-workspace/
├── README.md                       ← this file
├── ARCHITECTURE.md                 ← binding architecture (+ appendices)
├── INTERFACES.md                   ← contract semantics
├── AGENTS.md                       ← rules for AI agents
├── CLAUDE.md                       ← Claude Code project quick reference
├── pyproject.toml                  ← uv workspace root, tool config
├── uv.lock
├── justfile                        ← verify, lint, type, test, fmt
│
├── docs/
│   ├── DOC_INDEX.md                ← selective-read pointer index
│   ├── adr/                        ← architecture-decision records (binding)
│   ├── ADVANTAGES.md               ← 8 pitch advantages (binding, B7)
│   ├── MANUAL.md                   ← operator manual
│   ├── operator-cot-guide.md       ← operator CoT messaging (per ADR-018)
│   ├── wire-protocols/             ← frozen wire specs (servo_uart_v1, ...)
│   ├── data-pipeline.md            ← node → backend data pipeline
│   ├── deprecated/                 ← retired pre-pivot docs (reference only)
│   ├── archive/                    ← historical workstream-to-lead status
│   └── demo/                       ← demo deck stub (rewrite pending)
│
├── packages/
│   ├── rfmesh-contracts/           ← frozen interface layer (B1, lead-only)
│   ├── rfmesh-sdr/                 ← SDR adapters + SyntheticReceiver
│   ├── rfmesh-dsp/                 ← L1 + L2 estimators (pure)
│   ├── rfmesh-fusion/              ← Stansfield + MLE + GDOP + ellipse (pure)
│   ├── rfmesh-cot/                 ← PyTAK CoT publisher
│   ├── rfmesh-node/                ← runtime composer (Node + L1Sweep +
│   │                                  Rendezvous + CommandChannel)
│   ├── rfmesh-ops/                 ← dev/diagnostic dashboard (matplotlib)
│   ├── rfmesh-servo/               ← host-side servo driver (UART)
│   └── rfmesh-ml/                  ← L3 classifier + threat library
│
├── apps/
│   ├── demo-replay/                ← in-process orchestrator for the demo
│   └── operator-console/           ← operator console scaffold
│
├── deployment/
│   ├── backend/                    ← FastAPI + WebSocket + Leaflet GeoJSON
│   ├── frontend/                   ← Leaflet UI: link.html / locate.html / ...
│   ├── node/                       ← node bring-up scripts (RUNBOOK.md)
│   ├── webmap/                     ← FreeTAKServer webmap container
│   └── docker-compose*.yml         ← dev + prod + webmap composition
│
├── field-deploy/                   ← Pi systemd field-deployment scripts
├── firmware/                       ← ESP32-C6 servo controller (ADR-015)
├── firmware-beacon/                ← LoRa reference beacon firmware
├── scenarios/                      ← YAML demo geometries
├── configs/                        ← YAML node + fusion configs
├── recordings/                     ← operator-personal IQ captures (gitignored)
├── scripts/                        ← bench + cal + flash + dashboard helpers
└── theory/                         ← separately-tracked study program
```

## Governance, short version

- **`packages/rfmesh-contracts/` is frozen.** Edits by the lead only,
  via an ADR, paired with a `SCHEMA_VERSION` bump. See `AGENTS.md`
  §1 (Invariant B1).
- **Every package depends only on `rfmesh-contracts`.** Star graph;
  enforced by `import-linter`.
- **`rfmesh-dsp` + `rfmesh-fusion` are pure.** No I/O, no subprocess,
  no SDR access (B5).
- **Honest sigma on every bearing.** B2 is the load-bearing technical
  invariant; gated by an empirical sigma-honesty test.
- **No silent fallbacks.** B3 everywhere.
- **Verify before declaring done.** `just verify` is the gate; paste
  the output when closing a ticket.

## Prior project

`github.com/macwed/rf-mesh` is the parts-donor repository. It is
**not** this repo's git ancestor — this is a fresh workspace that
selectively inherits per the (retired) `docs/deprecated/SALVAGE_AUDIT.md`.
The old repo stays as a stable historical reference; nothing in it is
altered.

## License

Apache-2.0 (carried over from the prior project; see `LICENSE`).
