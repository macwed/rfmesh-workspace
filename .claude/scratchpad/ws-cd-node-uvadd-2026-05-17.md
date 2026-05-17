# /uvadd-request — `rfmesh-node` runtime dependencies

**Workstream:** C+D (rfmesh-node composition root).
**Author:** lead-Opus delegate (ticket-builder).
**Date:** 2026-05-17.
**Status:** approved by lead (per the ops-architecture design doc §2.2
which names these three deps as binding for the package).

## Packages requested

| Package    | Version pin   | Classification | Rationale |
|------------|--------------|----------------|-----------|
| `aiohttp`  | `>=3.9`      | runtime        | HTTP + WebSocket transport for the dashboard push channel (`DashboardPubSub` WebSocket subscriber) and the future HTTP-bearer fallback. Ubiquitous async-HTTP stack, MIT licence. Named binding in `docs/design/ops-architecture.md` §2.2 / §2.3. |
| `msgpack`  | `>=1.0`      | runtime        | Wire envelope encoder for the `Bearer` transports (UDP / LoRa). Salvaged pattern from the old `rfmesh/mesh/protocol.py` (length-prefixed MsgPack envelope), `SALVAGE_AUDIT.md` Part 5. Tiny C extension, well-maintained, Apache-2.0. |
| `pyyaml`   | `>=6.0`      | runtime        | YAML loading for `NodeConfig` / `FusionConfig`. Already in workspace dev-deps as `types-pyyaml`; we add the runtime side here. MIT licence. |

## Invariant compatibility

* **B1 (contracts frozen).** None of the three adds anything to
  `rfmesh-contracts/`. They live inside `rfmesh-node/` only.
* **B2 (inter-workstream comms via contracts).** They are transport /
  serialisation glue inside the composition root; they do not couple
  workstreams.
* **B3 (no silent fallbacks).** `aiohttp` raises on transport failure
  (we propagate); `msgpack` raises on malformed payloads; `pyyaml`
  raises on parse errors. All three are loud-fail by default.
* **B5 (rfmesh-dsp / rfmesh-fusion pure).** Not in DSP or fusion. The
  three "X is pure" import-linter contracts already forbid
  `aiohttp` / `socket` / `subprocess` in DSP / fusion / ML — this
  keeps that invariant honest.

## Execution

The lead runs the following at workspace root immediately after this
note lands:

```bash
uv add --package rfmesh-node "aiohttp>=3.9"
uv add --package rfmesh-node "msgpack>=1.0"
uv add --package rfmesh-node "pyyaml>=6.0"
```

If `mypy --strict` complains about untyped `msgpack`, the lead also
adds `types-msgpack` to the workspace `[dependency-groups] dev` list.
