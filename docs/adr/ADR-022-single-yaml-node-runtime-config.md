# ADR-022 — Single-YAML node runtime config + web-UI capability handshake

**Status:** PROPOSED (2026-05-23)
**Date:** 2026-05-23
**Author:** lead-Opus, after the 5-member council review of the post-cold-start
plan from 2026-05-23 (architect APPROVE, code-reviewer APPROVE, demo-integrity
APPROVE, rf-dsp + ew OUT-OF-LANE).
**SCHEMA_VERSION change:** **NONE.** This ADR composes the already-frozen
`NodeConfig` (`rfmesh_contracts.config.NodeConfig`); it does not edit
`packages/rfmesh-contracts/**` (B1 satisfied).
**Scope:** `packages/rfmesh-node/` (new `runtime_config.py`, rewritten
`cli/run_node.py`), `deployment/backend/src/both3_poc/ws.py`
(node→backend `node_hello` frame + GET `/node/{node_id}/capabilities`),
`deployment/frontend/link.{html,js}` (slider clamp from the new endpoint),
`field-deploy/` (single-YAML field-node script).

## Context

`cli/run_node.py` currently accepts 16 individual CLI flags (`--config`,
`--log-level`, `--servo-port`, the six `--sweep-*` knobs, the six
`--peer-*`/`--rendezvous-*` knobs). The flag set was acceptable for the
sweep-only bench path but no longer scales:

1. **Rendezvous (ADR-019) and the comms-mode command channel (ADR-021 §"Manual
   steering control plane")** each pulled a structured config object
   (`RendezvousConfig`, the planned `CommandEndpointConfig`,
   `MotionConfig`); CLI flag soup forces every field through `argparse` and
   then back into a dataclass with no schema validation.
2. **Field deploys are soldier-facing**, not RF-engineer-facing. ADR-021 §"UI
   mandate" makes the browser UI the canonical operator surface and demotes
   the matplotlib dashboard to `/dev`. A 16-flag CLI is not a soldier
   surface; it is a sysadmin/debug surface.
3. **The web UI needs to know each node's per-axis calibrated arc** to clamp
   the manual-steer slider (ADR-021 §"Manual-steer safety", layer 3). Today
   that arc lives only in firmware NVS; no backend endpoint exposes it.
4. **Backwards-compat hazards**: BartekDu's `field-deploy/rfmesh-node.sh`
   invokes `rfmesh-node --config X --servo-port Y` (line 65); link.html's
   placeholder hint quotes `rfmesh-node --config <cfg> --servo-port <port>
   --command-endpoint ws://<backend>:8000` (line 70-71). A flag rename must
   honour the existing script invocations for at least one release.

## Decision

### 1. `NodeRuntimeConfig` wrapper

A new module `packages/rfmesh-node/src/rfmesh_node/runtime_config.py`
introduces a Pydantic `NodeRuntimeConfig` that composes — without
modifying — the frozen `NodeConfig`:

```python
class NodeRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node: NodeConfig                              # frozen contract; verbatim
    servo_port: str | None = None                 # /dev/ttyACM0; None disables servo
    sweep: SweepOverrideConfig                    # L1SweepConfig defaults override
    rendezvous: RendezvousOverrideConfig | None   # composes ADR-019 RendezvousConfig
    command_endpoint: CommandEndpointConfig | None # backend WS URL (ADR-021)
    motion: MotionOverrideConfig | None           # servo_motion.MotionConfig override
```

Each sub-model is Pydantic with `extra="forbid"` (B3 — typo'd YAML key fails
loudly at load time) and exposes a `.to_<dataclass>()` helper that
materialises the existing frozen dataclasses already consumed by
`L1SweepLoop`, `RendezvousLoop`, and `move_smooth`. The wrapper is
node-layer only — `rfmesh-contracts` is **not** touched (B1).

### 2. CLI shrinks to two flags

`cli/run_node.py` shrinks to `--config` (required path to the single YAML)
and `--log-level` (default `INFO`). Every prior flag (`--servo-port`,
`--sweep-*`, `--peer-*`, `--rendezvous-*`) moves into the YAML. The CLI's
job is now (a) load YAML, (b) configure logging, (c) hand `NodeRuntimeConfig`
to a builder.

### 3. Backwards-compat window (one release)

The CLI parser keeps the 14 legacy flags for one release with two binding
behaviours:

- If **any** legacy flag is passed, the parser refuses (`B3` — never silently
  merge a partial set with the YAML). The error names the YAML key the flag
  maps to: `"--peer-id is deprecated; move it to YAML key rendezvous.peer_node_id"`.
- The legacy-flag refusal is gated by an env var `RFMESH_NODE_ALLOW_LEGACY_FLAGS=1`
  which inverts the refusal into a `DeprecationWarning` for one release, after
  which the flags are removed outright. This gives `field-deploy/rfmesh-node.sh`
  one release of headroom to migrate.

The next release after that removes the legacy flags entirely.

### 4. Web-UI capability handshake — `node_hello` + `/node/{id}/capabilities`

A new node→backend frame, sent once on `CommandChannel` connect:

```json
{"kind": "node_hello",
 "node_id": "node-rtl-01",
 "active_capabilities": ["l1_rssi"],
 "heading_deg": 142.0,
 "calibrated_geographic_arc_deg": {"min": 52.0, "max": 232.0},
 "peer": {"node_id": "node-rtl-02", "lat_deg": ..., "lon_deg": ...},
 "cal_provenance": "2026-05-22T15:42:10Z"}
```

The backend `/ws/node/{node_id}` handler parses this frame, caches it on
`NodeWsRegistry`, and exposes a new HTTP route
`GET /node/{node_id}/capabilities` returning the cached payload (404 if
the node has not yet sent its hello, or has disconnected). The frontend
`link.js` calls this endpoint when a node is selected, and clamps the
manual-steer slider's `min`/`max` to the returned arc. Until the call
resolves, the slider stays disabled (B3 — never let the operator command an
angle the system cannot verify).

This is the data half of ADR-021 §"Manual-steer safety" layer 3 (client-side
range hint) that was deferred at the time of ADR-018 because the firmware
NVS arc wasn't yet plumbed.

### 5. `CommandChannel` wired into `Node` (handler stub, not yet NodeController)

`CommandChannel` exists (`packages/rfmesh-node/src/rfmesh_node/command_channel.py`)
but is dead code — no caller wires it in. This ADR wires it in as a
side-task on `Node.run`, alongside the heartbeat task, with two minimal
behaviours:

1. **On connect**, send the `node_hello` frame above. Backend caches it.
2. **On `ManualSteerCommand`**, the handler logs the command and emits an
   error response over WS (`{"kind": "command_refused", "reason":
   "NodeController not yet wired"}`). It **does not** issue
   `servo.move()` — manual-steer servo action is gated on the
   NodeController state machine (item 2 of the council-reviewed plan,
   code-reviewer BLOCK on race conditions). The stub satisfies B3: never
   silently accept-and-drop, never fabricate a successful steer.

The full servo-action handler lands together with NodeController in a
follow-up ADR. This ADR only delivers the **transport surface + capability
handshake** the UI needs to be functional today.

### 6. Soldier-vs-admin CLI policy

The single-YAML CLI is for **bench/admin/debug** use. The canonical
soldier-facing operation flow:

1. Node boots into systemd (`field-deploy/rfmesh-node.service`).
2. Node phones home to the backend via `CommandChannel` (URL from
   `command_endpoint.backend_ws_url` in YAML).
3. Backend caches the `node_hello` snapshot.
4. Operator opens `link.html` in a browser; the page enumerates connected
   nodes from `/ws/ui`, fetches per-node arc from `/node/{id}/capabilities`,
   and renders the soldier UX (slider clamped to the calibrated arc, link
   margin in dB, ALL-STOP two-tap).

No soldier ever invokes `rfmesh-node` from a shell. Sysadmins do, for
calibration / bringup / debug. Documenting this split in
`field-deploy/README.md` is part of this ADR's deliverable.

## Consequences

### Positive

- One source of truth per node: `configs/node-<serial>.yaml`. SD-card swap
  zero-config preserved (field-deploy/README.md §"How config selection works").
- Web UI can clamp the manual-steer slider honestly — no more default ±90°
  guess.
- Soldier UX path becomes well-defined: phone-home + browser. CLI is
  documented as sysadmin-only.
- `CommandChannel` is no longer dead code; the transport surface is alive
  even before NodeController lands.
- B1 (contracts frozen), B3 (extra="forbid", loud-refusal handler stub) and
  B6 (no hardware assumptions added to software) all hold.
- Demo-integrity council's "10-item soldier-grade checklist" gets two of its
  items satisfied: the hard-clamped slider and the calibration-provenance
  label.

### Negative

- One release of legacy-flag handling code (kept under
  `RFMESH_NODE_ALLOW_LEGACY_FLAGS` for backwards-compat). Removed in the
  next release.
- The stub command handler explicitly refuses servo actions — a soldier
  testing the UI today sees a red toast saying "NodeController not yet
  wired". This is honest (B3) but is a visible TODO until item 2 of the
  plan lands.
- `link.html` line 70-71 placeholder hint references `--command-endpoint`
  which never existed as a CLI flag; the hint will be updated to point at
  YAML.

### Risks

- An older `rfmesh-node.sh` (BartekDu's commit) invoking
  `rfmesh-node --config X --servo-port Y` will break immediately if the
  env-var gate is missing. Mitigation: the install script
  (`field-deploy/install-service.sh`) is updated in lockstep so the
  default install carries the new YAML key; ops who upgrade without
  re-running the installer get the deprecation warning, not a hard fail.
- A node behind cellular NAT may never reach the backend. This is a
  documented operational reality (ADR-021 §"Transport"); the
  `CommandChannel` already implements exponential backoff with jitter. No
  new risk introduced by this ADR.

## Alternatives considered

- **Keep the CLI flags, add YAML as an alternative.** Rejected: doubles the
  surface area and invites silent merge bugs (which one wins on a partial
  set?). The single-YAML constraint is what gives the soldier-vs-admin split
  its honesty.
- **Move the wrapper into `rfmesh-contracts`.** Rejected: violates B1 (these
  are node-runtime helpers, not cross-workstream data products); also
  forces a `SCHEMA_VERSION` bump on every node-runtime field tweak.
- **Embed `node_hello` payload in the contracts package.** Same rejection
  as above — UI↔node coordination is server-vs-node infrastructure (per
  ADR-021 §"Command envelope"); the contracts package stays reserved for
  cross-workstream data products.

## References

- ADR-018 (operator-authored CoT messaging) — established the command-plane
  surface this ADR's `node_hello` lives on.
- ADR-019 (antenna rendezvous) — `RendezvousConfig` whose fields move into
  the new YAML wrapper.
- ADR-021 (directional comms primary) — §"Manual-steer safety" layer 3
  (client-side range hint), §"UI mandate" (link.html is canonical),
  §"Transport" (WS, exponential backoff).
- `AGENTS.md` §1 — B1 (contracts frozen), B3 (no silent fallbacks), B6
  (hardware = operator domain).
- Council review 2026-05-23 (post cold-start): architect APPROVE (this
  ADR is the B1 path), code-reviewer APPROVE (recommended `extra="forbid"`
  on every sub-model — adopted), demo-integrity APPROVE (slider clamp
  satisfies UX checklist items).
- BartekDu's `field-deploy/rfmesh-node.sh` (commit `6ca3c43`) — the
  invocation pattern this ADR preserves under the deprecation window.
