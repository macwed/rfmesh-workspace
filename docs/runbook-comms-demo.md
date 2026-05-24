# Runbook: two-node DSSS comms demo (ADR-025 Iter 4.6)

End-to-end demo of the directional comms loop. No hardware required.
Two synthetic nodes exchange messages through an in-process loopback;
operator UX surfaces in `link.html`.

## What this proves

* DSSS PN spreading + BPSK modulation + framing + CRC round-trips
  (Iter 1-2).
* `SyntheticTransmitter` + `LoopbackChannel` + `LoopbackReceiver` close
  the simulator-first comms path (Iter 3).
* `CommsLoop` orchestrates pointing + TDD + TX/RX in async (Iter 4).
* `NodeController` routes `SendCommsMessageCommand` to the comms
  outbox; `Node` supervisor surfaces decoded inbound frames + link
  status to the operator via backend WS push (Iter 4.5).
* Backend `/ws/node/{id}` relays `comms_rx` + `comms_status` +
  `node_state` frames to all `/ws/ui` subscribers; `link.html` renders
  link state + message log + send box (Iter 4.6).

## Fast path: one-process loopback bench

Runs in ~1 second, prints exchanged messages to stdout. Use this to
prove the DSSS pipeline works:

```bash
uv run python scripts/bench_two_node_comms.py
```

Expected output:

```
[node-B] received from <hash>: 'node-A hello'
[node-A] received from <hash>: 'node-B hello'
[node-B] received from <hash>: 'A reply 1'
[node-A] received from <hash>: 'B reply 1'
...
bench complete: A frames {sent=4, received=4}, B frames {sent=4, received=4}
```

The `<hash>` is the 1-byte `_hash_node_id` mapping each `node_id`
string to a header byte (256-node mesh ceiling; v1.3.0 keeps the
collision risk negligible at O(10) nodes). Iter 5 multi-hop will
add an explicit node-id table.

## Full path: backend + frontend + two node processes

Use this to demo the soldier UX. Four terminals + a browser.

### Prerequisites

* `uv sync` has populated `.venv`.
* Backend deps installed: `cd deployment/backend && uv sync` (separate
  venv).
* Ports 8000 (backend) free.

### Step 1 — backend

```bash
cd deployment/backend
uv run uvicorn both3_poc.app:app --reload --port 8000
```

Wait for `Uvicorn running on http://127.0.0.1:8000`.

### Step 2 — node A

```bash
uv run rfmesh-node --config configs/node-comms-A.yaml
```

The YAML's `command_endpoint.backend_ws_url` is
`ws://10.0.0.1:8000`. **Edit the YAML to point at `localhost`
before running** if the bench host is not on the 10.0.0.1 network:

```yaml
command_endpoint:
  enabled: true
  backend_ws_url: "ws://127.0.0.1:8000"
```

Same for node B.

### Step 3 — node B

```bash
uv run rfmesh-node --config configs/node-comms-B.yaml
```

### Step 4 — soldier UX

Open in a browser:

```
http://127.0.0.1:8000/static/link.html
```

What you should see:

* Node list (left aside): `node-comms-A` + `node-comms-B` appear as
  the nodes phone home (~1 s after step 2 / step 3).
* Click a node → detail panel shows:
  * Link state (UP / DOWN with colour).
  * Frames sent / received / dropped counters.
  * Message log (newest-first, capped at 50).
  * Text input + Send button.
* Type a message into node-A's panel → press Enter → node-B's panel
  receives it on the next TDD slot (~200 ms).

### Caveat: simulator vs hardware

The `driver: sim` path in the YAMLs uses an in-process
`LoopbackChannel` per node. **Two separate node processes do not
share a loopback channel** -- the messages each node sends with
`driver=sim` go into its own buffer, not the peer's. So the
soldier-loop end-to-end via two processes is only realistic with
hardware drivers (Iter 6 -- BladeRF / HackRF / Pluto+).

For the v1.3.0 simulator demo, use the **one-process bench** above
(`scripts/bench_two_node_comms.py`) which constructs two CommsLoops
in one event loop sharing two unidirectional channels.

When Iter 6 lands and the BladeRF driver replaces `SyntheticTransmitter`
/ `LoopbackReceiver`, the two-process YAML flow becomes the field
deployment shape; the simulator path stays for CI / bench tests.

## Architecture cross-reference

* ADR-021 — comms-first product framing.
* ADR-025 — DSSS directional mesh comms (Iter 0 contract bump,
  Iter 1-4 DSP + orchestration, Iter 4.5 controller wiring,
  Iter 4.6 backend fan-out).
* `INTERFACES.md` §5 — `Transmitter` / `Receiver` Protocols the
  drivers + simulator implement.
* `packages/rfmesh-node/src/rfmesh_node/comms/` — node-layer
  orchestration (CommsLoop, SlotSchedule, CommsLinkConfig).
* `packages/rfmesh-dsss/` — pure-Python DSP (PN, BPSK, spread,
  framing, correlation, timing, link budget).
* `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/` --
  SyntheticTransmitter, LoopbackChannel, LoopbackReceiver
  (simulator-first comms peers).
