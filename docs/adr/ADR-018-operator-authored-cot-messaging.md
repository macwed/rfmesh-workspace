# ADR-018: Operator-authored CoT messaging (templates, point/area, thread-safe store)

**Status:** ACCEPTED
**Date:** 2026-05-23
**Workstream:** C+D (owns `rfmesh-cot`, `rfmesh-ops`)
**Supersedes / relates:** extends the CoT surface described in WORKSTREAMS.md
§4 coordination point #4 ("CoT marker type strings and remarks formatting").

## Context

`rfmesh-cot` to date is **one-way and machine-driven**: the fusion server
turns a `FixEvent` / `NodeStatus` into a CoT marker and ships it to the TAK
endpoint. The system had no way for the **operator** to put information *onto*
the shared TAK map — drop a confirmed-hostile contact, draw a no-go area,
mark a waypoint, annotate a point with free text — even though that is half of
what a C2 picture is for and what the BoTH3 jury expects from a TAK
integration.

We want the operator to: pick a **template** (a tactical message type),
choose a **point or area** (on a map, or via CLI args), and **send** it so it
appears on every connected ATAK / WinTAK / iTAK client.

Two design questions had to be resolved against the binding invariants:

1. **Does this touch the frozen contracts (B1)?** The `CotPublisher` Protocol
   in `rfmesh-contracts` declares `publish(FixEvent)` / `publish_node_status`.
   Adding operator methods *to the Protocol* would be a contract change —
   ADR + `SCHEMA_VERSION` bump + lockstep across workstreams.
2. **Where does the shared mutable marker state live, and how is it made
   thread-safe?** The operator-input surface (CLI, a future map UI callback,
   a WebSocket handler) runs on a different thread from the publisher's
   asyncio TX loop.

## Decision

**1. Keep the contracts frozen. Add operator messaging as concrete API on
`PyTAKCotPublisher`, with new local types in `rfmesh-cot`.**

- `OperatorMarker` (frozen dataclass) and `MessageTemplate` + the `TEMPLATES`
  registry are **package-local** to `rfmesh-cot`. They never cross a
  workstream boundary — they flow ops → cot, both inside C+D — so they are
  *not* contract types and do not belong in `rfmesh-contracts` (ARCHITECTURE
  §3: contracts are only what crosses a boundary).
- `PyTAKCotPublisher` grows `publish_marker(OperatorMarker)` and
  `delete_marker(uid)`. These are **concrete methods, not Protocol methods.**
  The frozen `CotPublisher` Protocol is untouched; existing consumers
  (`run_fusion`'s `_build_cot_publisher`) are unaffected. **No B1 event, no
  `SCHEMA_VERSION` bump.**
- Pure encode (`operator_marker_to_cot_xml`, `operator_delete_to_cot_xml`)
  mirrors `markers.py`: testable with no network; only the publisher touches
  the wire.

**2. A `threading.Lock`-guarded `OperatorMarkerStore` holds live markers,
keyed by stable `uid`; cross-thread sends marshal onto the publisher's loop.**

- The store is the source of truth for "what markers should be live now",
  enabling re-broadcast before stale time and replay after a TAK reconnect.
- `all()` returns a snapshot tuple, so readers iterate their own copy while
  writers mutate — no "dict changed size during iteration".
- `PyTAKCotPublisher` captures its event loop at start; `publish_marker` /
  `delete_marker` use `loop.call_soon_threadsafe` when called off-loop and
  `put_nowait` when on-loop. `asyncio.Queue` is single-threaded; this is the
  safe hand-off. The existing `FixEvent` path is unchanged.

**3. Template vocabulary (v1, BoTH3 demo set).** Point: `hostile` (a-h-G),
`friendly` (a-f-G), `neutral` (a-n-G), `unknown` (a-u-G), `waypoint`
(b-m-p-w), `spi` (b-m-p-s-p-i), `casevac` (b-r-f-h-c). Area (TAK free-form
`u-d-f` polygon): `no_go`, `area_of_interest`, `search_area`. Adding a
template is a one-line registry change; consumers read the registry.

## Honesty / invariant alignment (B3 — fail loud)

- Unknown template key → `CotEncodingError` (not a silently-invented marker).
- Geometry/template mismatch (vertices on a point template, < 3 vertices on a
  polygon) → `CotEncodingError`.
- Operator markers carry `how="h-g-i-g-o"` (human input), distinct from the
  machine `how="m-r"` on a `FixEvent` — a consumer can always tell operator
  intent from a DF-derived fix. They do **not** carry a fabricated `ce`
  (circular error); operator markers are exactly where the operator pointed,
  so `ce` is the CoT "unknown" sentinel rather than a made-up accuracy.

## Consequences

- **CLI now, map UI next.** `rfmesh-cot-send` proves the operator→TAK round
  trip and is the backend a map UI calls into. The map picker (matplotlib
  panel in `rfmesh-ops`, or a web map) is a follow-up; it only has to produce
  `(template_key, point | vertices, callsign, remarks)` and call
  `publish_marker`. Input-surface choice is deliberately deferred.
- **Re-broadcast loop is not yet wired.** The store supports it; a periodic
  task that replays `store.all()` before stale time (and on reconnect) is a
  small follow-up in `rfmesh-ops` / `rfmesh-node`.
- **GeoChat is approximated by marker + `<remarks>`.** True TAK GeoChat
  (`__chat` detail + addressed recipients) is out of scope for v1; the
  free-text path is "drop a pin with a note", which is what the demo needs.
- Endpoint is configured at runtime (`--cot-url`, default the BoTH3 server);
  no credentials are needed to *push* CoT on port 8087 — the admin panel
  (port 5000) is only for creating system users.
