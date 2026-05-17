# apps/demo-replay

The BoTH3 jury-demo failure contingency. Plays back a scenario YAML
through the production rfmesh stack (Receiver -> BearingEstimator ->
FusionService -> CoT/Dashboard) so the operator can show the demo
even if live hardware is misbehaving on stage.

**Architectural home:** `docs/design/ops-architecture.md` §1.5 + §2.4.
**Status:** v1.0 -- in-process orchestration, simulator-driven.
**Out of scope (yet):** recorded-IQ playback via `SyntheticReceiver`
"replay" mode (WS-A-005 follow-up); multi-process spawning.

## Why this is a top-level app, not a package

`apps/demo-replay` is *not* a library -- nothing in the workspace
imports back from it -- and it is the one place where importing from
every workspace package is intentional. Its module name is
`rfmesh_demo_replay`, deliberately *not* `rfmesh_*`, so the
`import-linter` `independence` contracts do not apply by construction
(`docs/design/ops-architecture.md` §1.5).

## Quick start

```bash
# Replay a scenario through the in-process orchestrator
uv run rfmesh-demo-replay --scenario scenarios/trench_demo.yaml \
                         --pessimism 1.0 --no-cot --headless

# Capture per-node IQ for the same scenario (uses the simulator)
uv run rfmesh-demo-record --scenario scenarios/trench_demo.yaml \
                         --output recordings/dry_run_01/ \
                         --duration-s 10
```

## Scenario YAML

The loader (`ScenarioLoader.load(yaml_path) -> Scenario`) accepts the
shape documented in `scenarios/trench_demo.yaml`. The loader carries
small ergonomic bridges (e.g. `demo_beats` -> `beats`, `UNKNOWN` ->
`unknown`) so the design-intent YAML round-trips through; otherwise
the schema is strict (`extra="forbid"`, Pydantic validators on every
numeric range).

A minimal scenario lives at
`apps/demo-replay/tests/fixtures/tiny_scenario.yaml`; that is the one
the orchestrator unit tests drive against.

## Design decision: in-process orchestration for v1.0

The architect spec describes `ReplayOrchestrator` as "spawns N node
processes + 1 fusion + 1 dashboard". For BoTH3 (2-5 nodes on one
operator laptop), v1.0 ships an **in-process** orchestrator. Every
`Node` / `FusionService` / `DashboardPubSub` runs as asyncio tasks in
a single Python process.

Rationale:

* Lower bug surface (one event loop, one stop signal, no subprocess
  log multiplexing).
* Faster to debug and rehearse.
* The jury sees the dashboard, not the process tree -- the
  demonstration is identical.

Migration path: replace `_NodeContext` with one
`multiprocessing.Process`-per-node helper. The orchestrator's
seams (per-node context + shared `FusionService` + shared
`DashboardPubSub`) already match the multi-process shape.

## Design decision: SyntheticReceiver "replay" mode deferred

The architect spec (`docs/design/ops-architecture.md` §3.3) calls for
adding a `mode: Literal["synthesize", "replay"]` to
`SyntheticReceiver` so the orchestrator can replay recorded `.iqx`
files transparently. v1.0 of this app ships *without* that extension:

* The `ReplayRecorder` writes the `.iqx + .json` files today (so a
  Phase-C bench session can produce them).
* `ReplayMetadata` (the sidecar) is defined here so the on-disk
  format is locked in advance.
* The orchestrator drives `SyntheticReceiver` in synthesise mode
  (the only mode that exists in `rfmesh-sdr` today). When WS-A-005
  lands and adds replay mode, the orchestrator's `_NodeContext`
  builder picks it up via the `replay_iqx_path` field already on
  `NodeReplaySpec`.

This is a clean deferral: nothing in v1.0 is invalidated when the
replay-mode extension lands.

## File format: `.iqx + .json`

Per-node files in a session directory:

```
recordings/<session>/
    <node_id>.iqx     # interleaved complex64, little-endian, headerless
    <node_id>.json    # ReplayMetadata sidecar
```

* **Single-channel L1:** `.iqx` is `[s0, s1, s2, ...]`, complex64.
* **Multi-channel coherent:** `.iqx` is interleaved-by-sample:
  `[s0c0, s0c1, s1c0, s1c1, ...]`. The sidecar's `n_channels`
  carries the channel count; readers reshape with
  `np.fromfile(...).reshape(n_samples, n_channels)`.

The sidecar is `ReplayMetadata`, which extends `IQMetadata` with
scenario / node / beat / ground-truth / channel-model provenance.
Every replay-specific field defaults to `None` so a sidecar without
scenario context is structurally an `IQMetadata` on the JSON wire.

## CLI reference

### `rfmesh-demo-replay`

```
rfmesh-demo-replay --scenario PATH
                   [--pessimism FACTOR]   # 1.0 / 1.5 / 2.0
                   [--no-cot]
                   [--headless]
                   [--log-level LEVEL]
```

* `--pessimism` inflates the simulator noise floor by `10 * log10(F)`
  dB. Used for the Phase-C overlay demos per
  `INHERITED_CONTEXT.md` §3.1.1.
* `--no-cot` disables the CoT publisher (the v1.0 default is also
  off; the flag is forward-compatible).
* `--headless` disables the in-process dashboard subscriber.

### `rfmesh-demo-record`

```
rfmesh-demo-record --scenario PATH
                   --output DIR
                   --duration-s SECONDS
                   [--beat-id ID]
                   [--log-level LEVEL]
```

Builds one `SyntheticReceiver` per node from the scenario, opens /
configures / optionally calibrates each, captures `--duration-s`
seconds in parallel, writes `<output>/<node_id>.iqx + .json` per
node.

## Tests

```
uv run pytest apps/demo-replay -v
```

Twenty test cases cover the scenario loader, the recorder, the
sidecar metadata, and an in-process end-to-end orchestrator run.
