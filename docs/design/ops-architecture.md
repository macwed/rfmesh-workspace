# Ops architecture — `rfmesh-cot`, `rfmesh-node`, `rfmesh-ops`, `apps/demo-replay`

- **Status:** DRAFT, awaiting lead-Opus ratification. Once ratified, this
  document is binding for the next four ticket-builder agents.
- **Author:** Architect subagent (lead-Opus Council, role per
  `.claude/agents/architect.yaml`).
- **Date:** 2026-05-17.
- **Decision scope:** package shapes, public APIs, dependency graph, and
  rendering / transport / replay technology choices for the four
  remaining packages that turn the merged `rfmesh-contracts /
  rfmesh-sdr / rfmesh-dsp / rfmesh-fusion / rfmesh-ml` stack into a live
  ops demo for the BoTH3 jury.
- **Does not decide:** internal module layout inside each of those four
  packages (workstream freedom — `ARCHITECTURE.md` §8 "Not binding"),
  caption strings, slide design, hardware procurement, anything outside
  software.

---

## §0 Scope and binding posture

### What this document binds

1. The public API surface of each of the four packages
   (`rfmesh-cot`, `rfmesh-node`, `rfmesh-ops`, `apps/demo-replay`):
   class names, function signatures, package boundaries.
2. The **dependency graph** between them and the existing five
   packages, including the one place the current `import-linter`
   `independence` contract has to be relaxed (`rfmesh-node` is the
   composition root and must import from siblings).
3. The chosen runtime dependencies (`pytak`, `aiohttp`, `matplotlib`,
   `msgpack`, `pyyaml`) and the trade-offs that produced each choice.
4. The recorded-IQ replay file format and where it lives on disk.
5. The cross-cutting concerns that are **explicitly out of scope** for
   v1.0 (telemetry, auth, persistence, distributed tracing). A
   downstream ticket that drags one of these in is rejected by the
   Reviewer subagent on this paragraph.

### What this document does **not** bind

- The internal layout of any module inside the four packages. The
  workstream that builds it chooses (per `ARCHITECTURE.md` §8).
- The dashboard's exact panel pixel layout, caption strings, colour
  choices. Demo-narrative subagent territory.
- The CoT XML payload's stable-track-UID strategy (the contract has a
  `fix_id` per fix; whether a moving emitter consolidates into one ATAK
  track UID is a `rfmesh-cot` workstream call within the bounds of
  PyTAK's `CoT` element).
- The YAML schema for `Scenario` (the trench-demo loader). That is a
  separate ticket; this design only fixes *where it lives* and *what it
  feeds*.

### Reading order for builder agents

A builder agent who picks up one of the four packages reads, in order:

1. `docs/ADVANTAGES.md` (the eight advantages — each
   panel maps to one).
2. `ARCHITECTURE.md` §7 (the demo, scene by scene).
3. `INTERFACES.md` §3 `FixEvent` / §5 `CotPublisher` / §5 `Bearer`.
4. This document, §1 (your package's row in the dependency graph) and
   §2 (your package's public API).
5. `docs/demo/trench-demo-geometry.md` §3 (the four beats your code
   must support).
6. `docs/adr/ADR-005`, `ADR-008 §D7`, `ADR-009` (the dashboard
   obligations the captions and panels carry).

---

## §1 Package map and dependency graph

### §1.1 The graph

```
                       rfmesh-contracts  (frozen, FROZEN, FROZEN)
                              ▲
        ┌──────┬──────┬───────┼────────┬────────┬───────────┐
        │      │      │       │        │        │           │
   rfmesh-sdr  rfmesh-dsp rfmesh-fusion rfmesh-ml rfmesh-cot rfmesh-ops
   (Receiver  (BearingEst,   (Fuser)    (L3)     (CoT pub)  (dashboard
    impls)     null-steer)                                   consumer)
        ▲      ▲       ▲         ▲       ▲          ▲
        │      │       │         │       │          │
        └──────┴───────┴─────────┴───────┴──────────┘
                              │
                         rfmesh-node
                    (asyncio composition root —
                     the ONE package that imports
                     from sibling packages)
                              ▲
                              │
                       apps/demo-replay
                  (top-level entry; may import
                   anything in the workspace)
```

### §1.2 The dependency rules, package by package

| Package | May import from | Notes |
|---|---|---|
| `rfmesh-contracts` | (nothing in workspace) | Star centre. Frozen per B1. |
| `rfmesh-sdr` | `rfmesh-contracts` only | Existing. Receiver impls, simulator. |
| `rfmesh-dsp` | `rfmesh-contracts` only | Existing. Pure (B5). |
| `rfmesh-fusion` | `rfmesh-contracts` only | Existing. Pure (B5). |
| `rfmesh-ml` | `rfmesh-contracts` only | Existing. L3 + threat library. |
| `rfmesh-servo` | `rfmesh-contracts` only | Existing. Servo wire driver. |
| **`rfmesh-cot`** | `rfmesh-contracts` only | **New.** Imports `FixEvent`, `EllipseENU`, `GeodeticPosition`, `EmitterClass`. PyTAK external dep. |
| **`rfmesh-ops`** | `rfmesh-contracts` + `rfmesh-dsp` (Advantage #4 panel only — see §1.4) | **New.** Pure consumer of contract types over a transport. The single `rfmesh-dsp` import is the one carved-out exception. |
| **`rfmesh-node`** | `rfmesh-contracts` + `rfmesh-sdr` + `rfmesh-dsp` + `rfmesh-fusion` + `rfmesh-cot` + `rfmesh-ml` | **New.** Composition root. Explicitly cross-package by design (the asyncio service wires Receiver → BearingEstimator → Bearer → Fuser → CoT). |
| **`apps/demo-replay`** | everything | Top-level entry. Not a library; nothing imports back. |

### §1.3 Why `rfmesh-node` is the only sibling-importer, and why that is fine

`ARCHITECTURE.md` §3 ("star dependency graph: every workstream imports
only from `rfmesh-contracts`") is the principle behind invariant B2
("Inter-workstream communication is via contracts only" in the old
AGENTS.md). The *intent* of that principle is **to prevent two
estimator/fuser/CoT packages from coupling their internals**, so that a
change in one workstream's implementation does not cascade through
another's. It is not intended to forbid a *composition root* from
assembling the pieces.

`rfmesh-node` is exactly that composition root. It is the asyncio
service whose job is *to assemble* a working node from concrete
classes that satisfy the abstract Protocols in `rfmesh-contracts`:

- A `Receiver` impl (`SyntheticReceiver` / `RTLSDRDevice` /
  `BladeRFCoherentReceiver`) from `rfmesh-sdr`.
- One or more `BearingEstimator` impls (`L1AmplitudeSweepEstimator`,
  `MUSICEstimator`, `CaponEstimator`) from `rfmesh-dsp`.
- Optionally an L3 classifier from `rfmesh-ml`.
- A `Fuser` impl (`StansfieldMLEFuser`) from `rfmesh-fusion` — only
  needed when this node runs the local fusion role (in the BoTH3
  deployment, one node is also the fusion server; see §2.3 below).
- A `CotPublisher` impl from `rfmesh-cot` — same caveat (only the
  fusion-server node publishes CoT).
- Its own `Bearer` impls (`WifiBearer`, `LoraBearer`, `BothBearer`)
  which are internal to `rfmesh-node`.

The right enforcement is therefore: **DSP / fusion / CoT / SDR /
servo / ml / ops are pairwise independent. `rfmesh-node` is the
single allowed importer of all of them.** §4 below specifies the
exact `import-linter` clause that encodes this.

### §1.4 The one carve-out: `rfmesh-ops` imports `rfmesh-dsp`

Per ADR-008 §D7, the Advantage #4 dual-use demo panel renders a
*receive-pattern polar plot* using
`rfmesh_dsp.l2_null_steering.compute_receive_pattern`. The plot's
input (the weight vector `w` and the array geometry) is small and
already on the dashboard side (the dashboard knows the array from the
node's `NodeStatus.active_capabilities` + the simulator scenario; `w`
is shipped as a debug payload when the operator clicks "Engage
null"). The compute itself is a pure numpy function on the same R the
Capon estimator already produces.

Two options were considered:

- **Option A (chosen).** `rfmesh-ops` imports
  `rfmesh_dsp.l2_null_steering`. The pattern compute lives where the
  math lives (with the rest of L2). Cost: one sibling-import
  exception, ops-side `lint-imports` carries an `ignore_imports`
  clause naming the specific module. Benefit: the math is in one
  place, and the same `compute_receive_pattern` is reused by the
  rf-dsp test suite, `apps/demo-replay`, and any future
  null-steering analysis subagent.
- **Option B.** Duplicate `compute_receive_pattern` into a tiny
  `rfmesh_ops.patterns` module so `rfmesh-ops` stays
  contracts-only. Cost: code duplication of pure-math; if
  `compute_receive_pattern` ever evolves (e.g. UCA geometry support
  per ADR-010), two copies drift. Benefit: cleaner dependency
  invariant.

Choice: **Option A.** The carve-out is small, named, and reviewer-
auditable. The alternative (a duplicated pure-math helper) violates
the spirit of the architecture more than the letter of it.

### §1.5 Where does `apps/demo-replay` live and what may it import?

`apps/demo-replay` is a top-level entry-point package (the existing
`apps/` directory is empty per the salvage audit). It is *not* a
library — nothing imports back from it. Its `pyproject.toml` may
depend on every workspace package. It is exempt from the
`import-linter` `independence` clause (the contract names siblings
explicitly; `apps/demo-replay`'s package name is **not**
`rfmesh_*`, so the contract does not apply by construction).

Concrete location: `apps/demo-replay/` (new directory at workspace
root). Its package name is `rfmesh_demo_replay` (under
`apps/demo-replay/src/rfmesh_demo_replay/`). Adding it to the
workspace requires one line in workspace `pyproject.toml` `[tool.uv.
workspace] members` (add `"apps/*"`) plus an entry in
`[tool.uv.sources]`. The lead does the workspace edit; the builder
agent only writes the package itself.

---

## §2 Each package: public API surface and main class names

### §2.1 `rfmesh-cot`

**Purpose:** turn a `FixEvent` (and friends) into a CoT XML event and
ship it to an ATAK / FreeTAKServer endpoint over PyTAK's TCP/UDP
transport.

**Runtime dependencies (new):** `pytak >= 6.0` (a few MB, MIT
license, async-friendly, used by Defence community — credible
choice for a Belgian Defence jury).

**Public API surface (everything below is exported from
`rfmesh_cot/__init__.py`):**

```python
# rfmesh_cot/publisher.py
class PyTAKCotPublisher:
    """Implements rfmesh_contracts.CotPublisher.

    publish(fix) is sync from the caller's view; transport queueing
    is internal (an asyncio.Queue backed by a PyTAK CLITool task).
    A transmit failure raises (no silent swallow — Invariant B3).
    """

    def __init__(
        self,
        endpoint_url: str,                # e.g. "tcp://10.0.0.2:8087"
        node_callsign: str = "rfmesh",    # CoT "uid" prefix
        pytak_config: dict[str, str] | None = None,  # PyTAK opts
    ) -> None: ...

    def publish(self, fix: FixEvent) -> None: ...
    def publish_node_status(self, status: NodeStatus) -> None: ...
    def close(self) -> None: ...
    async def __aenter__(self) -> "PyTAKCotPublisher": ...
    async def __aexit__(self, *args: object) -> None: ...

# rfmesh_cot/markers.py
def fix_event_to_cot_xml(
    fix: FixEvent,
    callsign_prefix: str = "rfmesh",
) -> bytes:
    """Render a FixEvent as a CoT XML <event> blob.

    - CoT type: "a-h-G-E-X-N" (atom-hostile-Ground-Equipment-Electronic-
      Nonspecific) for an unclassified emitter; refined when
      `fix.emitter_class` is known (see emitter_class_to_cot_type).
    - Position: fix.position (lat/lon/hae).
    - Ellipse: rendered as <shape><ellipse> with major/minor/angle
      from fix.confidence_ellipse_95. Also rendered as <link> of
      polygon vertices for ATAK clients that do not parse <ellipse>.
    - Remarks: "method=... GDOP=... range_m=... classification=..."
    - Stale time: fix.t_unix_ns + STALE_AFTER_S (default 30 s).
    """

def emitter_class_to_cot_type(cls: EmitterClass | None) -> str:
    """Map EmitterClass to CoT type designator. UNKNOWN/None →
    'a-h-G-E-X-N'. ELRS → 'a-h-G-E-X-N-RC'. POLE21 → 'a-h-G-E-X-N-J'
    (jammer). Closed mapping, never raises — unknown classes degrade
    to nonspecific electronic."""

# rfmesh_cot/ellipse.py
def ellipse_to_polygon_vertices(
    ellipse: EllipseENU,
    center_lat_deg: float,
    center_lon_deg: float,
    n_vertices: int = 72,
) -> tuple[tuple[float, float], ...]:
    """Project a 2-D ENU ellipse into a closed sequence of (lat, lon)
    polygon vertices (WGS-84). Uses the small-region equirectangular
    approximation; correct to ~cm at ranges <50 km from center.

    n_vertices=72 (one vertex per 5° of ellipse angle) is the default;
    smaller numbers render faster on ATAK but look blocky.
    """

# rfmesh_cot/exceptions.py
class CotError(Exception): ...
class CotTransportError(CotError): ...
class CotEncodingError(CotError): ...
```

**Layered design rationale:**

- `ellipse_to_polygon_vertices` is a pure function and is the only
  non-trivial math in the package (polar-to-cartesian projection in
  the rotated ENU frame, plus an equirectangular reprojection to
  geodetic). It lives in its own module so it is *testable in
  isolation* against closed-form cases (a circle at the equator
  centred on a known point, a rotated ellipse, an ellipse at high
  latitude where the equirectangular degrades — known limits).
- `fix_event_to_cot_xml` composes the polygon helper, the
  `emitter_class_to_cot_type` mapper, and the PyTAK CoT XML
  template. Pure function, no I/O, no asyncio.
- `PyTAKCotPublisher` is the **only** part of `rfmesh-cot` that
  touches the network. All of the encoding / mapping / projection is
  testable without a PyTAK server.

**File layout (workstream chooses, not binding):**

```
packages/rfmesh-cot/src/rfmesh_cot/
    __init__.py
    publisher.py       # PyTAKCotPublisher (async, network)
    markers.py         # fix_event_to_cot_xml, emitter_class_to_cot_type
    ellipse.py         # ellipse_to_polygon_vertices (pure math)
    exceptions.py      # CotError + transport/encoding
packages/rfmesh-cot/tests/
    test_polygon_vertices.py     # closed-form ellipse projection
    test_xml_encoding.py         # CoT shape, byte-exact against fixtures
    test_emitter_class_mapping.py
    test_publisher_async.py      # fake PyTAK server / loopback
```

### §2.2 `rfmesh-node`

**Purpose:** the asyncio service that runs on each node, wires
hardware to fusion, and (on the designated fusion-server node)
publishes CoT.

**Runtime dependencies (new):** `aiohttp >= 3.9` (for the
node-to-fusion HTTP/WebSocket bearer fallback and the ops dashboard
push channel — see §2.3.3), `msgpack >= 1.0` (the wire envelope, per
the salvage audit Part 5 transport). `pyyaml >= 6.0` for
`NodeConfig` loading. No new heavyweight deps; aiohttp is the
ubiquitous async-HTTP client/server.

**Public API surface (exported from `rfmesh_node/__init__.py`):**

```python
# rfmesh_node/node.py
class Node:
    """The asyncio service that runs one mesh node.

    Owns:
      - one Receiver (or CoherentReceiver for L2-capable nodes)
      - one or more BearingEstimator instances (per active capability)
      - one Bearer (or BothBearer for redundancy)
      - the heartbeat task
      - the local L3 classifier task (optional)

    Does NOT own:
      - the fusion server (that is FusionService; see §2.3)
      - the CoT publisher (FusionService owns the only one)
    """

    def __init__(self, config: NodeConfig, *, receiver: Receiver) -> None: ...
    async def run(self) -> None: ...        # main loop
    async def shutdown(self) -> None: ...

# rfmesh_node/capabilities.py
def detect_active_capabilities(
    declared: tuple[Capability, ...],
    receiver: Receiver,
    array: ArrayConfig | None,
) -> tuple[Capability, ...]:
    """Intersect declared capabilities with what the hardware can
    support. RAISES `CapabilityMismatchError` if any declared
    capability cannot be met — never silently downgrades (B3)."""

def build_estimators(
    active: tuple[Capability, ...],
    sdr_config: SDRConfig,
    array_config: ArrayConfig | None,
    heading_deg: float | None,
) -> tuple[BearingEstimator, ...]:
    """The capability → estimator dispatch table.

    L1_RSSI       → rfmesh_dsp.l1.L1AmplitudeSweepEstimator
    L2_MUSIC      → rfmesh_dsp.l2_music.MUSICEstimator
    L2_CAPON      → rfmesh_dsp.l2_mvdr.CaponEstimator
    L2_MVDR_NULL  → (not a BearingEstimator — utility module; node
                    instantiates a NullSteeringWorker instead)
    L3_CLASSIFY   → (not a BearingEstimator; an EmitterClassifier
                    that decorates BearingReports in the publish step)
    """

# rfmesh_node/runtime.py
class NodeRuntimeError(Exception): ...
class CapabilityMismatchError(NodeRuntimeError): ...
class ReceiverNotAvailableError(NodeRuntimeError): ...

# rfmesh_node/bearer/
class WifiBearer:       # implements rfmesh_contracts.Bearer
    def __init__(self, endpoint_url: str) -> None: ...
    async def send_bearing(self, report: BearingReport) -> None: ...
    async def send_status(self, status: NodeStatus) -> None: ...
    async def recv(self) -> tuple[BearingReport | NodeStatus, ...]: ...

class LoraBearer:
    def __init__(self, serial_port: str, baud: int = 9600) -> None: ...
    # same Protocol; drops raw_pseudospectrum to fit bandwidth

class BothBearer:
    def __init__(self, wifi: WifiBearer, lora: LoraBearer) -> None: ...
    # de-duplicates by (node_id, t_unix_ns)

# rfmesh_node/fusion_service.py
class FusionService:
    """The aggregator-side asyncio service.

    Listens for BearingReports on its Bearer, batches them per
    FusionConfig.batch_window_ms, calls StansfieldMLEFuser.fuse(),
    publishes the resulting FixEvent to:
      - the CoT publisher (one)
      - the ops dashboard push channel (zero or more subscribers)

    Owns the only CotPublisher instance in the mesh.
    """

    def __init__(
        self,
        config: FusionConfig,
        fuser: Fuser,                    # rfmesh_contracts.Fuser
        cot_publisher: CotPublisher | None = None,
        dashboard_pubsub: DashboardPubSub | None = None,  # see §2.3
    ) -> None: ...
    async def run(self) -> None: ...
    async def shutdown(self) -> None: ...

# rfmesh_node/dashboard_pubsub.py
class DashboardPubSub:
    """The publish channel the ops dashboard subscribes to.

    Asyncio in-process pub-sub if dashboard and fusion share a host
    (the BoTH3 deployment); WebSocket-backed (aiohttp) for remote
    dashboards. The dashboard side is rfmesh-ops, this side is here
    because FusionService owns the subscription list.
    """

    def add_subscriber(self, sub: "DashboardSubscriber") -> None: ...
    async def publish_fix(self, fix: FixEvent) -> None: ...
    async def publish_bearing(self, report: BearingReport) -> None: ...
    async def publish_node_status(self, status: NodeStatus) -> None: ...

# rfmesh_node/cli/
class run_node_main:
    """The `rfmesh-node` CLI entrypoint (per pyproject [project.scripts]).

    Usage: rfmesh-node --config configs/<node>.yaml
    """

class run_fusion_main:
    """The `rfmesh-fusion-server` CLI entrypoint.

    Usage: rfmesh-fusion-server --config configs/fusion.yaml
    """
```

**Capability dispatch is data, not code (per docs/ADVANTAGES.md §1 Advantage #3
"heterogeneous mesh"):**

The dispatch table in `build_estimators()` is a `dict[Capability,
type[BearingEstimator]]` (plus two specials for `L2_MVDR_NULL` and
`L3_CLASSIFY` which are not BearingEstimators). A node that declares
`[L1_RSSI]` instantiates one estimator; a node that declares `[L1_RSSI,
L2_MUSIC, L2_CAPON, L2_MVDR_NULL, L3_CLASSIFY]` instantiates four
estimators + one classifier + one null-steerer. Adding a new estimator
in the future is one line in the dispatch table — the runtime does
not change shape.

The asyncio task graph of a node is:

```
   Receiver.read(N) ──┬──► L1Estimator ──► bearing_queue
                      ├──► L2MUSICEstimator ──► bearing_queue
                      ├──► L2CaponEstimator ──► bearing_queue
                      └──► [tap for classifier IQ buffer]
                                                │
                                                ▼
                                       L3Classifier ──► classification
                                       (decorates the next BearingReport
                                        if its window straddles the IQ
                                        the bearing came from)
   bearing_queue ──► Bearer.send_bearing
   heartbeat_task ──► Bearer.send_status (every 2 s)
```

The classifier runs on its own task, asynchronously decorating
already-published bearings is *not* in scope for v1.0 — the
classifier label rides on the *current* bearing if the classifier has
a fresh-enough result; otherwise the bearing ships with
`emitter_class=None`. This is the simplest honest design and matches
the `None`-vs-`UNKNOWN` distinction in `INTERFACES.md` §3.

### §2.3 `rfmesh-ops`

**Purpose:** the live ops dashboard. The demo surface from
`ARCHITECTURE.md` §7. Renders every panel from the §0 Advantages list.

**Runtime dependencies (new):** `matplotlib >= 3.8` (the rendering
choice, see §3.1), `aiohttp >= 3.9` (the WebSocket / HTTP client
that subscribes to `FusionService.DashboardPubSub`).

**Public API surface:**

```python
# rfmesh_ops/dashboard.py
class Dashboard:
    """The live dashboard application.

    Owns a matplotlib Figure with the panels from ARCHITECTURE §7,
    and an asyncio task that consumes BearingReport / FixEvent /
    NodeStatus messages from a DashboardClient (see below) and
    updates the figure in-place.
    """

    def __init__(self, client: DashboardClient, layout: DashboardLayout) -> None: ...
    async def run(self) -> None: ...

# rfmesh_ops/client.py
class DashboardClient:
    """The transport-side consumer of FusionService.DashboardPubSub.

    Connects via WebSocket (remote fusion server) or in-process
    asyncio.Queue (co-located fusion + dashboard).
    """

    @classmethod
    def websocket(cls, url: str) -> "DashboardClient": ...
    @classmethod
    def in_process(cls, queue: asyncio.Queue[Any]) -> "DashboardClient": ...

    async def stream(self) -> AsyncIterator[BearingReport | FixEvent | NodeStatus]: ...

# rfmesh_ops/panels/
class NodeStatusPanel: ...        # per-node health, gnss_locked, active_caps
class BearingsPanel: ...          # 2-D ENU plot of each node's bearing line + σ-wedge
class FixPanel: ...               # 95% ellipse, GDOP annotation, percentage display (ADR-005 D5b)
class PseudospectrumPanel: ...    # P_MUSIC(θ) per L2 node (uses BearingReport.raw_pseudospectrum)
class ResidualsPanel: ...         # per-node residuals from FixEvent.residuals_deg
class L1vsL2Panel: ...            # A/B precision panel (Advantage #2)
class GdopHeatmapPanel: ...       # GDOP across candidate emitter region
class NullSteeringPanel: ...      # Advantage #4 (uses rfmesh_dsp.l2_null_steering)
class ClassificationOverlayPanel: ...  # EmitterClass label + confidence

# rfmesh_ops/layouts.py
@dataclass(frozen=True)
class DashboardLayout:
    """Which panels are visible and where (matplotlib subplot grid)."""

    panels: tuple[PanelSpec, ...]

DEMO_LAYOUT_TRENCH: DashboardLayout = ...     # the BoTH3 demo layout
DEMO_LAYOUT_DEBUG: DashboardLayout = ...      # all panels, big screen
DEMO_LAYOUT_MINIMAL: DashboardLayout = ...    # fix + ellipse only

# rfmesh_ops/cli/
class run_dashboard_main:
    """CLI: rfmesh-ops --connect ws://10.0.0.1:9001 --layout trench"""
```

**Panel-to-Advantage mapping (the demo-honesty audit checklist):**

| Panel | Advantage (docs/ADVANTAGES.md) | Data source |
|---|---|---|
| FixPanel + percentage display | #1 deployment density, #6 honesty | `FixEvent` |
| BearingsPanel σ-wedges | #2 sigma honesty | `BearingReport.azimuth_sigma_deg` |
| L1vsL2Panel A/B | #3 heterogeneous mesh | side-by-side `BearingReport`s |
| NullSteeringPanel | #4 dual-use | `rfmesh_dsp.l2_null_steering` + recorded R |
| ClassificationOverlayPanel | #5 open threat library | `FixEvent.emitter_class` |
| ResidualsPanel + outlier highlight | #6 self-diagnosis | `FixEvent.residuals_deg` |
| GdopHeatmapPanel | #1 + #6 (geometry honesty) | `FusionConfig`'s node positions |
| NodeStatusPanel gnss_locked | #2 GNSS-denied | `NodeStatus.gnss_locked` |
| FixPanel "ellipse shrinks as nodes join" | #1 deployment density | stream of `FixEvent` over beats A→D |

This table is binding-once-ratified: every panel exists *because of*
one or more pitch advantages. A future dashboard ticket that adds a
panel without a row in this table is rejected by the Reviewer.

**The percentage display (ADR-005 D5b + ADR-009 D5):** the FixPanel
shows `100 · semi_major_m / range_m` to one decimal place, alongside
the band name, with the BoTH3 spec band (≤ 1%) drawn as a shaded
reference region. This is *binding* and was named explicitly in
ADR-005 §D5(b). The matplotlib axis composition is workstream
freedom; the *presence* of the percentage is not.

### §2.4 `apps/demo-replay`

**Purpose:** the demo-failure contingency. Plays back recorded IQ
through the entire production pipeline (Receiver Protocol →
Estimators → Bearers → FusionService → CotPublisher → Dashboard) and
makes the ops dashboard look identical to a live run.

**Why it is a separate top-level app, not a `rfmesh-node` CLI subcommand:**

- It orchestrates **N** node processes plus one fusion-server
  process plus one dashboard process — a multi-process scenario, not a
  single-node action. Putting that in `rfmesh-node` would couple the
  node runtime to the scenario format. `apps/` is the architectural
  home for full-system entry points (per `WORKSTREAMS.md` row C+D:
  "apps/" is in C+D's ownership).
- It is the one place where it is *fine* to import from every
  workspace package. `rfmesh-node` is *not* fine that way — it must
  not import `rfmesh-ops` (the dashboard) because nodes do not run
  dashboards.

**Public API surface:**

```python
# rfmesh_demo_replay/scenario.py
class ScenarioLoader:
    """Loads a scenario YAML (trench_demo.yaml shape) into a
    fully-populated Scenario object.

    Returns:
      - one EnuOrigin
      - one EmitterSpec
      - one ChannelModel (composite or simple)
      - N NodeReplaySpec (each carries: NodeConfig + recorded IQ
        path OR live-synthetic config)
      - K DemoBeatSpec (sequenced subsets of active nodes)
      - FusionConfig
      - expected outcomes (for the test harness)
    """

    def load(self, yaml_path: Path) -> Scenario: ...

# rfmesh_demo_replay/replay.py
class ReplayOrchestrator:
    """Spawns the multi-process simulator.

    For each node in the scenario:
      - instantiates a SyntheticReceiver in 'replay' mode (see §3.3)
        OR a SyntheticReceiver in 'live-synthetic' mode
      - builds a rfmesh_node.Node with that receiver and the node's
        NodeConfig
      - sequences beats: at beat boundary, mark certain nodes
        offline (their bearers stop sending; fusion drops them via
        node_stale_after_s)

    Spawns one FusionService and one Dashboard. Wires them together.
    """

    def __init__(self, scenario: Scenario) -> None: ...
    async def run(self) -> None: ...

# rfmesh_demo_replay/recorder.py
class ReplayRecorder:
    """Captures IQ from a live SyntheticReceiver (or real RTLSDRDevice)
    and writes a per-node recording to disk.

    Used during Phase C bench validation to produce the recorded-IQ
    set that the demo replay falls back to.
    """

    def __init__(self, output_dir: Path) -> None: ...
    async def record(self, receivers: dict[str, Receiver], duration_s: float) -> None: ...

# rfmesh_demo_replay/cli/
class run_replay_main:
    """CLI: rfmesh-demo-replay --scenario scenarios/trench_demo.yaml
                               [--pessimism 1.0|1.5|2.0]
                               [--no-cot]
                               [--headless]
    """

class run_record_main:
    """CLI: rfmesh-demo-record --scenario scenarios/<name>.yaml
                               --output recordings/<session>/
                               --duration-s 60
    """
```

The Scenario / EmitterSpec / NodeReplaySpec / DemoBeatSpec pydantic
models live in `rfmesh_demo_replay.scenario`. They are **not**
contracts — they are app-internal, evolving, do not require a
SCHEMA_VERSION bump. The YAML format is what is documented in
`scenarios/trench_demo.yaml`.

---

## §3 Recommended tech-stack choices

### §3.1 Dashboard rendering: **matplotlib**, not web

**Choice: matplotlib + matplotlib's interactive backend (Qt5Agg or
TkAgg).**

Trade-off considered:

| Option | Time to build | Production polish | Demo-credible |
|---|---|---|---|
| matplotlib | 1-2 days | medium | yes |
| FastAPI + SPA (React/Vue) | 5-7 days | high | yes |
| Tkinter + custom canvas | 3 days | low | marginal |
| Bokeh / Dash | 3-4 days | medium-high | yes |
| PyQt + custom widgets | 5 days | high | yes |

**Reasoning for matplotlib:**

1. **Time budget.** ~3 weeks of project time left for *everything*
   not yet built (4 more fusion tickets, CoT, node runtime, ops,
   demo-replay, demo-narrative authoring, hardware integration). A
   2-day rendering effort vs a 5-7-day effort: that is 3-5 days of
   rehearsal time saved, which is *more* valuable than UX polish.
2. **Jury credibility.** Belgian Defence RF/EW jury reads matplotlib
   plots in their day jobs. A signal-processing-flavour dashboard
   that looks like a competent researcher's working notebook lands
   *better* than a polished SPA that looks like a consulting deck.
3. **The data is the message, not the chrome.** ADR-005 D5(b)
   binds the percentage display, the σ-wedge, the residuals
   highlight, the GDOP annotation — all of those are easy in
   matplotlib, and what matters is that they are *correct*, not
   that they live in a slick widget.
4. **Risk of rewrite.** §5 R1 — if we picked FastAPI+SPA and ran
   out of time, we would ship a half-finished web dashboard or
   pivot to matplotlib under pressure. Better to pick matplotlib
   first.
5. **Replay parity.** `apps/demo-replay` reuses the same dashboard;
   if the dashboard is in-process matplotlib, replay can drive it
   directly via the in-process `DashboardClient.in_process` queue,
   no socket required.

The cost (no remote-view capability without VNC / screen sharing)
is acceptable for the BoTH3 demo: the dashboard runs on the
operator laptop on stage, the jury sees the projected screen. ATAK
on a separate tablet (over CoT TCP) is the remote-view path.

**Backend:** matplotlib's `Qt5Agg` (or `QtAgg` on newer
matplotlib) gives the smoothest live-update story, but does pull
PyQt as a transitive runtime dep. If `pyqt` adds too much
deployment weight (RPi consideration — but the dashboard does not
run on the RPi nodes, only on the operator laptop), the fallback
is `TkAgg`, which ships with Python by default. Either is fine.

### §3.2 CoT library: **PyTAK**

`PyTAK >= 6` (MIT, ~3 MB). Reasoning:

- Used by the TAK community (FreeTAKServer, ATAK-CIV, MIL plugin
  pipelines). Real provenance.
- Async-native — fits the `rfmesh-node`'s asyncio runtime.
- Handles the CoT-XML, transport (TCP / UDP / TLS), and stale-time
  semantics out of the box. Re-implementing CoT framing for v1.0
  would be busywork and bug-prone.
- The competition: `takproto` (lower-level, no transport layer —
  too thin), `cot-utils` (pure-encoding utility — also too thin),
  rolling our own (silly).

The PyTAK dep is **only** in `rfmesh-cot`. The rest of the
workspace does not pull it transitively (Python wheel install
respects per-package deps).

### §3.3 Recorded-IQ replay file format: **per-node `.iqx` next to a `.json` sidecar**

**Choice:**

```
scenarios/<scenario_id>/recordings/<session>/
    node-l1-west.iqx           # interleaved complex64 IQ samples
    node-l1-west.json          # IQMetadata + replay metadata
    node-l1-east.iqx
    node-l1-east.json
    node-l1-south.iqx
    node-l1-south.json
    node-l2-overwatch.iqx      # 2-channel coherent IQ (interleaved by channel)
    node-l2-overwatch.json
    session.yaml               # session-level: timestamp, ground truth, channel config
```

**The `.iqx` format:**

- Pure binary, little-endian `complex64` interleaved (real, imag,
  real, imag, ...).
- For coherent (multi-channel) captures, channels are interleaved
  *within* a sample (sample_t0_ch0, sample_t0_ch1, sample_t1_ch0,
  sample_t1_ch1, ...). The `.json` sidecar declares `n_channels`.
- No header. The `.json` sidecar is the header. This is a
  deliberate decision so the `.iqx` is `mmap`-friendly and the
  Receiver does not need to seek past variable-length metadata.

**The `.json` sidecar** is the existing `IQMetadata` (salvaged
from the old repo via WS-A-004), extended with replay-specific
fields:

```python
class ReplayMetadata(IQMetadata):
    """IQMetadata + replay extras.

    IQMetadata already carries: sample_rate_hz, center_freq_hz,
    n_channels, total_samples, dtype, recorder_id, started_at_utc.

    Replay extras:
      ground_truth_emitter_enu_m: tuple[float, float] | None
      ground_truth_bearing_deg: float | None
      scenario_id: str
      node_id: str
      beat_id: str | None     # if recording covers one beat
      channel_model: dict[str, Any]  # what channel was simulated
    """
```

The extension is **on the recording side** (WS-A's IQ Recorder
already writes `.iqx` + `.json`); no contract change required.

**Why not a single concatenated `.iqx`?**

- A single file is easier to ship but harder to replay (the
  Receiver needs to know where each node's stream starts, which
  needs an index, which needs a header — exactly what we did not
  want).
- Per-node files map cleanly onto the per-node `SyntheticReceiver`
  in replay mode (each node opens its own file).
- Per-node files allow re-recording one node without re-recording
  the others (useful when Phase C surfaces a per-node issue).

**The `SyntheticReceiver` gains a "playback from file" mode, not a
separate class:**

ADR-004 + WS-A-001/002/003 made `SyntheticReceiver` the one
`Receiver` impl that can run from arbitrary inputs. Adding a
`mode: Literal["synthesize", "replay"]` parameter (and a
`replay_path: Path | None`) is a small addition to its constructor.
Why not a `RecordedIQReceiver` class?

- The two share ~all infrastructure (lifecycle, the `Receiver`
  Protocol surface, the noise/impairment hooks).
- The `SyntheticReceiver`'s impairment layer is *useful* on top of
  recorded IQ — e.g. inflate noise to simulate Phase-C pessimism on
  a clean recording. A separate `RecordedIQReceiver` would
  duplicate that layer.

This is a WS-A follow-up ticket (adding the mode), not a structural
change.

### §3.4 Bearer / transport: msgpack envelope over UDP (wifi) + LoRa

Salvaged from the old repo (Part 5 of `SALVAGE_AUDIT.md`):

- Length-prefixed `msgpack` envelope, `{type, payload}` dicts.
- Payload types are now `BearingReport.model_dump()`,
  `NodeStatus.model_dump()` (Pydantic models), and `FixEvent.
  model_dump()` for ops push.
- UDP for `WifiBearer` (the old code's choice — fits the
  small-message + best-effort semantics).
- LoRa drops `raw_pseudospectrum` to fit in the bearer's packet
  budget (~256 B), then `msgpack` and CRC over the remainder.

**The fusion-server → dashboard channel** is *not* the bearer. It is
a WebSocket (aiohttp), because the dashboard subscribes to a stream
(not a request/response pattern). Same `msgpack` envelope so the
wire format is uniform.

---

## §4 `lint-imports` clauses required

The existing workspace `pyproject.toml` `[tool.importlinter]`
section needs three changes. **The lead applies these as part of
ratifying this design.**

### §4.1 Relax the "Star dependency" contract — `rfmesh-node` is the composition root

The current contract:

```toml
[[tool.importlinter.contracts]]
name = "Star dependency: nothing imports from siblings"
type = "independence"
modules = [
    "rfmesh_sdr", "rfmesh_servo", "rfmesh_dsp", "rfmesh_ml",
    "rfmesh_fusion", "rfmesh_cot", "rfmesh_node", "rfmesh_ops",
]
```

forbids any sibling import among those eight packages. `rfmesh-node`
cannot satisfy this. Replace with:

```toml
[[tool.importlinter.contracts]]
name = "Star dependency: non-composition packages are independent"
type = "independence"
modules = [
    "rfmesh_sdr",
    "rfmesh_servo",
    "rfmesh_dsp",
    "rfmesh_ml",
    "rfmesh_fusion",
    "rfmesh_cot",
    "rfmesh_ops",
    # NOTE: rfmesh_node is the composition root and is intentionally
    # NOT in this list — it imports from every sibling above.
    # See docs/design/ops-architecture.md §1.3.
]

# rfmesh-ops's single sibling import (the Advantage #4 pattern compute,
# per ADR-008 D7) is named as an explicit exception, not a hole in the
# independence contract.

[[tool.importlinter.contracts]]
name = "Ops imports only rfmesh_dsp.l2_null_steering from siblings"
type = "forbidden"
source_modules = ["rfmesh_ops"]
forbidden_modules = [
    "rfmesh_sdr",
    "rfmesh_servo",
    "rfmesh_fusion",
    "rfmesh_cot",
    "rfmesh_node",
    "rfmesh_ml",
]
# rfmesh_dsp is NOT in this list — but only one module of it is allowed:

[[tool.importlinter.contracts]]
name = "Ops imports only the null-steering pattern from DSP"
type = "forbidden"
source_modules = ["rfmesh_ops"]
forbidden_modules = [
    "rfmesh_dsp.l1",
    "rfmesh_dsp.l2_music",
    "rfmesh_dsp.l2_mvdr",
    "rfmesh_dsp.steering",
    "rfmesh_dsp.spectrum",
    "rfmesh_dsp.rssi",
    # rfmesh_dsp.l2_null_steering is the only allowed import.
]
```

This pattern (an `independence` contract for *most* packages plus
named exceptions) is the import-linter idiom for composition roots.
It is verbose but it makes every exception *visible* in the
configuration — a Reviewer can audit it in 30 seconds.

### §4.2 Purity contracts for CoT, Node, Ops

`rfmesh-cot`, `rfmesh-node`, and `rfmesh-ops` are **not** required
to be pure (they do network / file I/O by definition — invariant B5
applies only to `rfmesh-dsp` and `rfmesh-fusion`). They get *no*
purity contract.

But `rfmesh-cot` and `rfmesh-ops` are required to be
*non-side-effecting on import* (importing them must not start a
server, open a socket, or read a file). This is the same as every
other Python package; no special contract needed — the existing
ruff/mypy gate catches the obvious cases.

### §4.3 Node runtime imports are explicit (positive contract)

```toml
[[tool.importlinter.contracts]]
name = "Node runtime: composition root may import these sibling packages"
type = "forbidden"
source_modules = ["rfmesh_node"]
forbidden_modules = ["rfmesh_ops"]   # node never imports the dashboard
```

This single-line contract is the **only** thing the composition root
is forbidden to do at the package boundary: import the dashboard.
Everything else is allowed (the star plus B5 still constrain the
*content* of what is imported).

### §4.4 Why we do not add a contract for `apps/demo-replay`

`apps/demo-replay` is *not* a `rfmesh_*` package (its module is
`rfmesh_demo_replay`, declared under `apps/` not `packages/`). It
sits outside the `import-linter` `root_packages` list deliberately
— it is the one place where the workspace's discipline is *not*
enforced (and that is intentional: it is the integration test
harness for the whole system).

---

## §5 Risk items, by "regret-if-wrong" cost

### R1. Dashboard rendering tech choice (HIGH regret)

Picking matplotlib and discovering at week 2 we wanted FastAPI+SPA
costs roughly the entire dashboard rebuild (5-7 days that we would
not have). Picking FastAPI+SPA and discovering we ran out of time
costs the same. Mitigation: the matplotlib choice is justified
above on three independent grounds (time, jury demographic, the
data-is-the-message principle); the Reviewer subagent's
demo-integrity sweep at week 1 of dashboard work re-checks this
decision against the actual look-and-feel of the partial build.

### R2. Recorded-IQ file format (HIGH regret)

Once recordings are made on Maciej's bench, changing the file
format invalidates them — and re-recording costs hardware time
that is not free. Mitigation: the `.iqx + .json` format is the
existing salvage from WS-A-004 (`IQRecorder`); we are reusing it,
not designing it new. The `ReplayMetadata` extension is additive
to `IQMetadata` (the recorder writes extra JSON fields; older
readers ignore them). The `mmap`-friendly headerless `.iqx` body
is also forward-compatible — any reader that knows
`sample_rate_hz` + `n_channels` from the sidecar can read it.

### R3. `rfmesh-cot` dependency direction (MEDIUM regret)

If `rfmesh-cot` ends up needing data only the dashboard has
(e.g. an operator-selected emitter classification override), the
clean star pattern breaks. Mitigation: the CoT publisher operates
purely on `FixEvent` from contracts. Any operator-side override is
applied at the **`FusionService`** (which sits in `rfmesh-node`,
the composition root, where cross-package state assembly is
allowed) before the `FixEvent` reaches the publisher. CoT stays
contracts-only.

### R4. PyTAK API drift (LOW regret)

PyTAK is at v6+, stable. Pinning to `pytak >= 6, < 7` in
`rfmesh-cot/pyproject.toml` is sufficient. If PyTAK breaks
backward compatibility in a minor release before BoTH3, we vendor
the subset we need (a few hundred lines).

### R5. matplotlib live-update jitter (LOW-MEDIUM regret)

`matplotlib` is not designed for high-frequency live updates;
naïve `figure.canvas.draw()` calls can stutter under > 5 Hz update
rate. Mitigation: the dashboard updates at the fusion batch rate
(every `FusionConfig.batch_window_ms = 100 ms` = 10 Hz), but each
panel only updates when its data changes (bearing arrives,
heartbeat ticks, fix is produced). The matplotlib `blitting`
mode (background cache + redraw of changed artists only) handles
this comfortably at 10 Hz. If it does not, the fallback is to
update at 1 Hz for non-fix panels and on-event for the fix panel.

### R6. WebSocket fan-out at fusion (LOW regret)

If multiple ops dashboards subscribe to one `FusionService`, the
WebSocket fan-out must be non-blocking. Mitigation: aiohttp's
`WebSocketResponse` is asyncio-native; `DashboardPubSub.publish_fix`
broadcasts with `await asyncio.gather(*[sub.send(...) for sub in
subs])` and a per-subscriber timeout. A slow subscriber is dropped
with a logged error, never blocks the fusion loop. Standard pattern.

### R7. Heterogeneous mesh node assembly bug (MEDIUM-HIGH regret)

The capability dispatch table in `build_estimators()` is **the
single point of failure** for Advantage #3 (heterogeneous mesh). A
bug that prevents an L1-only node from instantiating without an
ArrayConfig (or vice versa) loses the pitch. Mitigation: a
property-based test in `rfmesh-node/tests/test_capability_dispatch.py`
that enumerates every subset of `Capability` and asserts the
runtime either:
- builds the correct estimator set, or
- raises `CapabilityMismatchError` with a specific message.

No silent downgrades; the §4.1 lint-imports contract guards the
import surface; the property test guards the dispatch logic.

---

## §6 What this design does NOT decide

Parking lot for future tickets. These are real questions, just not
ours to settle today:

1. **The `Scenario` pydantic model.** The YAML schema in
   `scenarios/trench_demo.yaml` is design intent; the loader is a
   `apps/demo-replay` ticket. Field names will likely converge on
   the existing `NodeConfig` + `rfmesh_sdr.simulator.scenario`
   types.

2. **L3 classifier wiring details.** WS-B-005/006 is mid-flight;
   the classifier integrates as an asyncio task inside `rfmesh-node`,
   but the IQ-tap mechanism (does it read from a tee'd buffer, a
   ring buffer, etc.) is a `rfmesh-node` ticket choice.

3. **CoT stable-track-UID strategy.** A moving emitter ought to
   appear in ATAK as one *track* (one TAK UID) with many `FixEvent`
   updates. The mapping from `FixEvent.fix_id` (per-fix UUID) to a
   stable track UID is a `rfmesh-cot` ticket choice (probable
   strategy: hash `EmitterClass` + spatial cluster within a 100-m
   tolerance). v1.0 may ship the simple choice — one track UID per
   `fix.emitter_class` value — and refine later.

4. **Authentication / TLS on the bearer / WebSocket.** Not in scope
   for v1.0 demo (closed network on stage). PyTAK supports TLS;
   `rfmesh-cot` exposes the option but defaults to plain TCP.

5. **Persistence / replay-of-live-runs.** The dashboard does not
   record. `apps/demo-replay` only replays from `.iqx` files
   that the `ReplayRecorder` produces during a separate session.
   Live-run journaling for after-action review is a v1.1+ ticket.

6. **Telemetry / metrics / distributed tracing.** None in v1.0.
   The ops dashboard *is* the telemetry surface. If a v1.1+ ticket
   adds Prometheus / OpenTelemetry, it goes in `rfmesh-ops`
   (and only in `rfmesh-ops`) — never in `rfmesh-dsp` or
   `rfmesh-fusion` (those stay pure).

7. **CoT marker styling specifics.** The CoT `type=` strings, the
   stale-time, the icon URLs — `rfmesh-cot` workstream's authoring
   call in coordination with the demo-narrative subagent.

8. **Dashboard caption strings.** The exact wording of every panel
   caption (per ADR-009 D5 / the trench-demo geometry doc §3) is
   demo-narrative subagent territory, *not* a builder-agent
   decision. The builder agent ships placeholder strings the
   demo-narrative subagent edits.

9. **Pre-recorded vs live-on-stage decision.** Maciej's call,
   informed by Phase C results. The `apps/demo-replay` exists so
   the decision can be made on the day. The system is identical
   either way (same code path, replayed buffer vs live buffer).

10. **`Capability.L2_MVDR_NULL` in NodeConfig dispatch.** Per ADR-008
    D6, null-steering is *not* a `BearingEstimator` — it is a
    `NullSteeringWorker` (utility module wrapped in an asyncio task).
    `build_estimators()` in §2.2 above mentions this as a special;
    the exact `NullSteeringWorker` API is a `rfmesh-node` ticket
    choice within the bounds of the `compute_null_steering_weights`
    signature in ADR-008 D6 (as amended by ADR-010).

---

## §7 Cross-references

- `docs/ADVANTAGES.md` — the eight architectural
  advantages this design surfaces.
- `ARCHITECTURE.md` §3 (star dependency), §7 (the demo surface),
  §8 (binding vs not).
- `INTERFACES.md` §3 (`FixEvent`, `BearingReport`, `NodeStatus`),
  §5 (`CotPublisher`, `Bearer`, `Fuser`).
- `WORKSTREAMS.md` row C+D — the four packages this design covers.
- `docs/adr/ADR-005-fusion-confidence-policy.md` §D5(b) — dashboard
  percentage display obligation.
- `docs/adr/ADR-008-l2-capon-enum-and-null-steering-reservation.md`
  §D7 — Advantage #4 panel layout (the one carve-out from §1.4).
- `docs/adr/ADR-009-confidence-band-math-correction-and-demo-narrative.md`
  — the corrected demo narrative the dashboard captions follow.
- `docs/demo/trench-demo-geometry.md` §3 — the four demo beats this
  architecture must support.
- `SALVAGE_AUDIT.md` Part 5 — the msgpack framing layer the bearers
  reuse.
- `packages/rfmesh-fusion/src/rfmesh_fusion/__init__.py` — the
  public API the `FusionService` consumes.

---

## §8 Builder-agent kickoff checklist (the four downstream tickets)

When this design is ratified, the four ticket-builder agents pick up
in roughly this order. The order is dependency-driven: each later
package depends on at least one earlier one being defined.

1. **`rfmesh-cot`** (no sibling deps; can start immediately).
   Acceptance: `fix_event_to_cot_xml` golden-file tests; loopback
   `PyTAKCotPublisher` test; polygon-vertex closed-form tests.

2. **`rfmesh-ops`** (depends on contracts + the one
   `rfmesh-dsp.l2_null_steering` import that lands with WS-B-007).
   Acceptance: each panel renders against a golden `FixEvent` /
   `BearingReport` stream from a fixture; `DashboardClient`
   subscribes to an in-process queue and a WebSocket loopback.

3. **`rfmesh-node`** (depends on contracts + sdr + dsp + fusion +
   cot + ml — composition root). Acceptance: full simulator-driven
   end-to-end test with a 3-L1-node + 1-L2-node trench-demo
   scenario; capability-dispatch property test exhaustive over
   `Capability` subsets; bearer / fusion-service / cot integration
   smoke test.

4. **`apps/demo-replay`** (depends on all of the above plus
   `rfmesh-sdr`'s `SyntheticReceiver` gaining the replay mode).
   Acceptance: `rfmesh-demo-replay --scenario scenarios/trench_demo.yaml`
   runs end-to-end against the recorded fixtures from a Phase C
   bench session (or against simulator-only output if Phase C has
   not happened yet), produces the four-beat demo on the
   matplotlib dashboard, and emits CoT to a loopback FreeTAKServer.

Each ticket carries an explicit "this design's §X is binding"
reference so the builder knows what it cannot change without
re-opening this document.
