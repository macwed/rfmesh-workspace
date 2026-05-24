"""WebSocket routes — comms-mode control plane + UI push.

ADR-018 (PROPOSED 2026-05-23) introduces a bidirectional command plane
between the user's web UI and deployed nodes. This module hosts:

* ``GET /ws/node/{node_id}`` — long-lived node-initiated WebSocket. The
  node POSTs its bearings via the existing ``POST /bearings`` HTTP path
  (BartekDu's `HttpBearer`); this WS carries only the *backend → node*
  command channel: manual-steer commands, parked-mode toggles, future
  control payloads.
* ``GET /ws/ui`` — UI-initiated WebSocket for live push of bearing /
  link-state updates. Replaces 2.5 s polling for soldier-grade
  manual-steer latency. The existing `/bearings` polling path stays as
  the fallback for the locate.html / emit.html maps.

Per-node registry lives in ``app.state.node_ws_registry`` (initialised
in the FastAPI ``lifespan``). When the UI POSTs a manual-steer command
through ``POST /command/{node_id}`` (defined here, mounted on the same
FastAPI app), the route looks the node up in the registry and pushes
the command down the WS. Missing entries return HTTP 503 with a clear
"node not registered / offline" message — never a silent drop (B3).

Out of scope (deferred until ADR-018 is ACCEPTED + paired ticket):

* The state machine on the node side that consumes these commands
  (NodeController).
* The frontend ``link.html`` page that issues them.
* Contract-level enum bump for peer-acquisition bearings (ADR-019).

This module is the **transport surface** the rest of the stack will
land against; it must be in place before NodeController and link.html
land so they have a concrete WS contract to integrate with.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rfmesh_contracts import NodeStatus

if TYPE_CHECKING:
    from fastapi import FastAPI

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command envelope (server-internal; not in rfmesh-contracts)
# ---------------------------------------------------------------------------


class ManualSteerCommand(BaseModel):
    """Manual-steer command issued by the UI to a specific node.

    Lives in backend code (and is imported by ``rfmesh-node`` later);
    NOT in ``rfmesh-contracts`` per ADR-018. The contracts package is
    reserved for cross-workstream data products (BearingReport,
    FixEvent, NodeStatus); UI↔node coordination is server-vs-node
    infrastructure, not a contract.

    Attributes:
        kind: Literal "manual_steer" — tag for the WS-payload union
            once more command types land (park, all_stop, ...).
        axis: Servo axis ID (single-axis nodes use 0).
        target_angle_deg: Geographic-frame angle the node should move
            to (CW positive). The node-side handler validates against
            its cached calibration limits before issuing the move.
        timeout_s: After this idle interval the node returns to
            SWEEPING. Operator can re-issue the command to extend.
        requestor_id: Free-form tag for audit ("ui-soldier-1",
            "dev-bench"). Logged, not enforced.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="manual_steer", pattern="^manual_steer$")
    axis: int = Field(ge=0, le=255)
    target_angle_deg: float
    timeout_s: float = Field(default=10.0, ge=0.5, le=600.0)
    requestor_id: str = Field(default="unknown", max_length=64)


class AllStopCommand(BaseModel):
    """Mesh-wide ALL-STOP — every node halts servo motion and parks.

    Sent to *every* registered node WS in parallel; failures per-node
    are logged but the route returns 200 as long as the broadcast was
    attempted (mesh-wide STOP must not block on one slow node).
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="all_stop", pattern="^all_stop$")
    requestor_id: str = Field(default="unknown", max_length=64)


class ClearFaultCommand(BaseModel):
    """Operator ack of a sticky FAULT (ADR-024 §8).

    Sent in response to a controller-reported FAULT
    (mode_drain_timeout, uncalibrated servo, hardware refusal). The
    node-side controller transitions FAULT -> SWEEPING; if the
    underlying condition is still present, the next loop tick re-raises
    FAULT with the new ``status_detail`` -- the operator sees the loop
    visibly, not a silent re-FAULT.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="clear_fault", pattern="^clear_fault$")
    requestor_id: str = Field(default="unknown", max_length=64)


class SendCommsMessageCommand(BaseModel):
    """Operator typed a DSSS comms message in the link panel (ADR-025 Iter 4).

    Backend forwards through the same ``/command/{node_id}`` plumbing
    as ``ManualSteerCommand``; node-side handler parses + queues onto
    the ``CommsLoop`` outbox. Schema mirrors
    ``rfmesh_node.commands.SendCommsMessageCommand`` (duplication is
    deliberate -- backend / node validate independently; both raise
    422 on schema-invalid payloads).
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="send_comms_message", pattern="^send_comms_message$")
    peer_node_id: str = Field(min_length=1, max_length=64)
    payload_text: str = Field(min_length=1, max_length=256)
    requestor_id: str = Field(default="unknown", max_length=64)


# ---------------------------------------------------------------------------
# Per-node WS registry
# ---------------------------------------------------------------------------


class NodeWsRegistry:
    """Registry of currently-connected node WebSockets + capability snapshots.

    A single registry instance lives on ``app.state.node_ws_registry``,
    populated by the ``/ws/node/{node_id}`` handler on connect and
    cleared on disconnect. ADR-022: the node sends a ``node_hello``
    frame once per connect carrying its capability + calibrated-arc
    snapshot; the registry caches it so ``GET /node/{id}/capabilities``
    can serve it (the soldier UI clamps the manual-steer slider to
    that arc, ADR-021 §"Manual-steer safety" layer 3).

    Thread-safety: FastAPI's event loop is single-threaded; the
    registry's mutation points (add / remove / snapshot) happen on the
    loop, so no lock is required.
    """

    def __init__(self) -> None:
        self._by_node: dict[str, WebSocket] = {}
        # ADR-022 capability snapshots, populated from inbound node_hello
        # frames. Kept across reconnect-disconnect transitions intentionally:
        # a brief network flap should not lose the slider clamp.
        self._hello_by_node: dict[str, dict[str, Any]] = {}

    def register(self, node_id: str, ws: WebSocket) -> None:
        """Register a connected node WS. Overwrites any prior entry.

        Overwriting on re-connect is the right policy: if a node
        reconnects (e.g. after a network flap), the new socket is the
        live one. The old WebSocket is left dangling (it will eventually
        time out); not closing it here avoids a race with the prior
        handler's own cleanup.
        """
        if node_id in self._by_node:
            _LOG.info(
                "NodeWsRegistry: %s reconnecting; replacing previous socket",
                node_id,
            )
        self._by_node[node_id] = ws

    def unregister(self, node_id: str, ws: WebSocket) -> None:
        """Remove the entry only if the recorded WS is this one.

        Guards against a race where a new connection has already
        overwritten the entry while the old handler is still cleaning
        up — without this check, the old handler's unregister would
        evict the live new socket.
        """
        current = self._by_node.get(node_id)
        if current is ws:
            del self._by_node[node_id]

    def get(self, node_id: str) -> WebSocket | None:
        return self._by_node.get(node_id)

    def node_ids(self) -> tuple[str, ...]:
        return tuple(self._by_node.keys())

    def __len__(self) -> int:
        return len(self._by_node)

    # ----- ADR-022 capability snapshots ------------------------------------

    def set_hello(self, node_id: str, payload: dict[str, Any]) -> None:
        """Cache a ``node_hello`` snapshot for this node (ADR-022)."""
        self._hello_by_node[node_id] = payload

    def get_hello(self, node_id: str) -> dict[str, Any] | None:
        """Return the cached snapshot, or ``None`` if no hello has been seen."""
        return self._hello_by_node.get(node_id)

    def online_node_ids(self) -> set[str]:
        """Currently-connected node_ids (subset of nodes with cached hellos)."""
        return set(self._by_node)

    def hello_snapshots(self) -> dict[str, dict[str, Any]]:
        """Snapshot of every cached node_hello payload (read-only copy)."""
        return dict(self._hello_by_node)


class UiWsRegistry:
    """Registry of connected UI WebSockets for push fan-out.

    The bearings / link-state push path subscribes UI sockets here and
    fans bearings out from the ``POST /bearings`` ingest handler. The
    fan-out is best-effort (mirrors ``DashboardPubSub`` semantics in
    ``rfmesh-node``): a slow / errored UI subscriber gets dropped, the
    publish call never blocks the bearings handler.
    """

    def __init__(self) -> None:
        self._subs: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        self._subs.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._subs.discard(ws)

    def subscribers(self) -> tuple[WebSocket, ...]:
        return tuple(self._subs)

    def __len__(self) -> int:
        return len(self._subs)


def init_registries(app: FastAPI) -> None:
    """Attach fresh ``NodeWsRegistry`` + ``UiWsRegistry`` to ``app.state``.

    Called from the FastAPI ``lifespan`` so the registries' lifetime
    matches the app's; one line of edit in ``app.py``.
    """
    app.state.node_ws_registry = NodeWsRegistry()
    app.state.ui_ws_registry = UiWsRegistry()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter()


@router.websocket("/ws/node/{node_id}")
async def node_socket(ws: WebSocket, node_id: str) -> None:
    """Accept a long-lived node-initiated WebSocket.

    Connection lifecycle:
      1. Node opens ``ws://backend/ws/node/{node_id}``.
      2. Backend ``accept()``s + registers in ``NodeWsRegistry``.
      3. Node sends ``{"kind": "node_hello", ...}`` once -- ADR-022
         capability handshake. Backend caches it on the registry; the
         UI fetches via ``GET /node/{node_id}/capabilities``.
      4. Backend pushes ``ManualSteerCommand`` / ``AllStopCommand``
         frames down the socket when the UI issues commands.
      5. Node optionally sends ``{"kind": "command_refused", ...}`` /
         future ack frames; backend fans them out to the UI via
         ``push_to_ui_subscribers`` so a red toast appears on
         ``link.html`` when a steer is refused (B3).
      6. On disconnect the handler unregisters and exits. The cached
         hello snapshot persists across the disconnect so a brief
         flap does not drop the slider clamp.
    """
    app = ws.app
    registry: NodeWsRegistry = app.state.node_ws_registry
    await ws.accept()
    registry.register(node_id, ws)
    _LOG.info(
        "ws/node: %s connected (total nodes registered=%d)",
        node_id,
        len(registry),
    )
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                _LOG.warning("ws/node: %s sent non-JSON frame; dropping", node_id)
                continue
            if not isinstance(payload, dict):
                continue
            kind = payload.get("kind")
            if kind == "node_hello":
                registry.set_hello(node_id, payload)
                _LOG.info(
                    "ws/node: %s hello received (caps=%s arc=%s)",
                    node_id,
                    payload.get("active_capabilities"),
                    payload.get("calibrated_geographic_arc_deg"),
                )
                # Fan a "node_hello" event to the UI so a freshly-opened
                # link.html that loaded before this node connected can
                # pick the snapshot up without polling.
                await push_to_ui_subscribers(
                    app, {"kind": "node_hello", "node_id": node_id, "data": payload}
                )
            elif kind == "command_refused":
                # Refusal from the node (ADR-022 stub handler) -- forward
                # to the UI so link.html can render the red toast (B3).
                await push_to_ui_subscribers(
                    app, {"kind": "command_refused", "node_id": node_id, "data": payload}
                )
            elif kind == "node_state":
                # ADR-024: NodeController emitted a state transition
                # (SWEEPING / ACQUIRED_PEER / MANUAL_HOLD / PARKED /
                # FAULT). Backend relays to UI so the badge + countdown
                # flip within ~200 ms of the firmware event (per the
                # demo-integrity event-driven-link-state rec).
                await push_to_ui_subscribers(
                    app, {"kind": "node_state", "node_id": node_id, "data": payload}
                )
            elif kind == "comms_rx":
                # ADR-025 Iter 4.6: decoded inbound DSSS frame from a
                # linked peer. The node already stripped padding +
                # UTF-8-decoded the payload; relay verbatim. link.js
                # appends to the message log newest-first.
                await push_to_ui_subscribers(app, payload)
            elif kind == "comms_status":
                # ADR-025 Iter 4.6: CommsLoopStats snapshot (link
                # up/down, frames sent/received/dropped, last tx/rx
                # timestamps). Relay verbatim; link.js latches per-node
                # and renders the comms panel state line.
                await push_to_ui_subscribers(app, payload)
            # Other frames (future ack / status / heartbeat) ignored for now.
    except WebSocketDisconnect:
        pass
    finally:
        registry.unregister(node_id, ws)
        _LOG.info(
            "ws/node: %s disconnected (total nodes registered=%d)",
            node_id,
            len(registry),
        )


@router.websocket("/ws/ui")
async def ui_socket(ws: WebSocket) -> None:
    """Accept a UI WebSocket for live bearing / link-state push.

    The UI subscribes once at page load; the backend pushes JSON frames
    on every ``POST /bearings`` ingest event and on per-node state
    changes. A future ``link.html`` is the primary consumer.
    """
    app = ws.app
    registry: UiWsRegistry = app.state.ui_ws_registry
    await ws.accept()
    registry.add(ws)
    _LOG.info("ws/ui: client connected (subscribers=%d)", len(registry))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            # UI does not send commands here; commands go through
            # POST /command/{node_id}. Receive loop just keeps the
            # connection alive and ignores frames.
    except WebSocketDisconnect:
        pass
    finally:
        registry.remove(ws)
        _LOG.info("ws/ui: client disconnected (subscribers=%d)", len(registry))


@router.post("/command/{node_id}")
async def post_command(
    node_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Forward a manual-steer command from UI HTTP to the node WS.

    The UI POSTs a JSON command here; the backend validates it, looks
    up the node in the registry, and sends the payload down the WS.
    Returns 200 on successful send, 503 if the node is not registered
    (offline), 422 on schema-invalid payload (B3: never silently
    accept-and-drop).
    """
    kind = payload.get("kind", "manual_steer")
    try:
        if kind == "manual_steer":
            command: ManualSteerCommand | SendCommsMessageCommand = (
                ManualSteerCommand.model_validate(payload)
            )
        elif kind == "send_comms_message":
            command = SendCommsMessageCommand.model_validate(payload)
        else:
            msg = (
                f"unknown command kind {kind!r}. Use POST /command/{{node_id}} "
                "for manual_steer / send_comms_message; "
                "POST /command_broadcast for all_stop."
            )
            raise HTTPException(status_code=422, detail=msg)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    registry: NodeWsRegistry = request.app.state.node_ws_registry
    ws = registry.get(node_id)
    if ws is None:
        msg = (
            f"node {node_id!r} is not connected to the backend. Check the "
            "node's command-channel WS reconnect log."
        )
        raise HTTPException(status_code=503, detail=msg)
    try:
        await ws.send_text(command.model_dump_json())
    except Exception as exc:
        _LOG.warning(
            "ws/node: failed to push command to %s; evicting and returning 503: %s",
            node_id,
            exc,
        )
        registry.unregister(node_id, ws)
        raise HTTPException(
            status_code=503,
            detail=f"node {node_id!r}: WS send failed ({exc!r})",
        ) from exc
    return {"delivered_to": node_id, "kind": command.kind}


@router.post("/status")
async def ingest_status(
    request: Request,
    payload: Any = Body(...),  # noqa: B008 -- FastAPI parameter dependency
) -> dict[str, Any]:
    """Ingest one or more ``NodeStatus`` heartbeats; fan to UI as ``node_status``.

    ADR-022 + demo-integrity rec on item 3: NodeStatus heartbeat (2s
    cadence, contract-bound) is one of two push payload kinds the UI
    listens for. The other -- ``node_state`` -- is event-driven from the
    NodeController on every mode transition (~200ms latency, ADR-024).
    Splitting the two decouples badge-flip latency from the heartbeat
    cadence.

    Accepts a single ``NodeStatus`` JSON or a list. 422 on schema-invalid
    payloads (B3, never silently accept-and-drop).
    """
    accepted = 0
    errors: list[str] = []
    pushed: list[dict[str, Any]] = []
    items = payload if isinstance(payload, list) else [payload]
    for i, rec in enumerate(items):
        try:
            status = NodeStatus.model_validate(rec)
            accepted += 1
            pushed.append(status.model_dump(mode="json"))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"[{i}] {exc}")
    for record in pushed:
        await push_to_ui_subscribers(
            request.app,
            {"kind": "node_status", "node_id": record.get("node_id"), "data": record},
        )
    if accepted == 0 and errors:
        raise HTTPException(status_code=422, detail=errors)
    return {"accepted": accepted, "errors": errors}


@router.post("/node/{node_id}/clear_fault")
async def post_clear_fault(
    node_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Forward an operator FAULT-clear ack from UI HTTP to the node WS.

    ADR-024 §8: FAULT is sticky and requires operator ack. The UI POSTs
    here when the soldier presses "Clear FAULT" on a node whose
    ``status_detail`` they have read. Backend validates + forwards the
    JSON down the same WS used by manual_steer. Same B3 posture: 503
    if the node is not registered, 422 on schema-invalid payload.
    """
    try:
        command = ClearFaultCommand.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    registry: NodeWsRegistry = request.app.state.node_ws_registry
    ws = registry.get(node_id)
    if ws is None:
        msg = (
            f"node {node_id!r} is not connected. Cannot clear FAULT remotely; "
            "the operator must restart the node service out-of-band."
        )
        raise HTTPException(status_code=503, detail=msg)
    try:
        await ws.send_text(command.model_dump_json())
    except Exception as exc:
        _LOG.warning(
            "ws/node: failed to push clear_fault to %s; evicting and returning 503: %s",
            node_id,
            exc,
        )
        registry.unregister(node_id, ws)
        raise HTTPException(
            status_code=503,
            detail=f"node {node_id!r}: WS send failed ({exc!r})",
        ) from exc
    return {"delivered_to": node_id, "kind": command.kind}


@router.get("/node/{node_id}/capabilities")
async def get_node_capabilities(node_id: str, request: Request) -> dict[str, Any]:
    """Return the cached ``node_hello`` snapshot for one node (ADR-022).

    The soldier UI reads this when a node is selected so the
    manual-steer slider can clamp to the node's calibrated arc. 404 if
    the node has never sent a hello (e.g. not yet connected, or running
    an older build without the comms-mode wiring).
    """
    registry: NodeWsRegistry = request.app.state.node_ws_registry
    hello = registry.get_hello(node_id)
    if hello is None:
        msg = (
            f"node {node_id!r}: no capability snapshot cached. The node has "
            "either never connected or is running a build that predates "
            "ADR-022. Bring it up with command_endpoint.enabled=true."
        )
        raise HTTPException(status_code=404, detail=msg)
    online = node_id in registry.online_node_ids()
    return {"node_id": node_id, "online": online, "hello": hello}


@router.get("/nodes/capabilities")
async def list_node_capabilities(request: Request) -> dict[str, Any]:
    """List every cached node snapshot (ADR-022). Used by link.html for the
    initial node list when the page loads before any /bearings push arrives."""
    registry: NodeWsRegistry = request.app.state.node_ws_registry
    online = registry.online_node_ids()
    nodes = [
        {"node_id": node_id, "online": node_id in online, "hello": hello}
        for node_id, hello in registry.hello_snapshots().items()
    ]
    return {"nodes": nodes}


@router.post("/command_broadcast")
async def post_command_broadcast(
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Broadcast a command (today: only ``all_stop``) to every connected node.

    Soldier-grade panic button: mesh-wide ALL-STOP must not be blocked
    by one slow / disconnected node. The broadcast is best-effort —
    individual sends that fail are logged, but the route returns 200
    as long as the broadcast was attempted at all.
    """
    kind = payload.get("kind", "all_stop")
    try:
        if kind == "all_stop":
            command = AllStopCommand.model_validate(payload)
        else:
            msg = f"command_broadcast only accepts kind=all_stop, got {kind!r}."
            raise HTTPException(status_code=422, detail=msg)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    registry: NodeWsRegistry = request.app.state.node_ws_registry
    delivered: list[str] = []
    failed: list[str] = []
    for node_id in registry.node_ids():
        ws = registry.get(node_id)
        if ws is None:
            continue
        try:
            await ws.send_text(command.model_dump_json())
            delivered.append(node_id)
        except Exception as exc:
            _LOG.warning("ws/node: all_stop to %s failed: %s", node_id, exc)
            failed.append(node_id)
            registry.unregister(node_id, ws)
    return {
        "delivered_to": delivered,
        "failed": failed,
        "kind": command.kind,
    }


# ---------------------------------------------------------------------------
# UI push helper — called from app.py POST /bearings handler
# ---------------------------------------------------------------------------


async def push_to_ui_subscribers(app: FastAPI, payload: dict[str, Any]) -> None:
    """Fan a JSON payload out to every connected UI WebSocket.

    Best-effort: slow / errored subscribers are dropped with a logged
    warning; the publish never raises into the caller (the
    ``POST /bearings`` handler must not fail because the UI is slow).
    Mirrors the drop-slow-subscriber discipline in
    ``rfmesh_node.dashboard_pubsub.DashboardPubSub._fanout``.

    Called from ``POST /bearings`` in app.py — one line of edit there.
    """
    registry: UiWsRegistry | None = getattr(app.state, "ui_ws_registry", None)
    if registry is None or len(registry) == 0:
        return
    text = json.dumps(payload, separators=(",", ":"))
    dead: list[WebSocket] = []
    for ws in registry.subscribers():
        try:
            await asyncio.wait_for(ws.send_text(text), timeout=1.0)
        except Exception as exc:
            _LOG.warning("ws/ui: dropping slow / errored subscriber: %s", exc)
            dead.append(ws)
    for ws in dead:
        registry.remove(ws)


__all__ = [
    "AllStopCommand",
    "ClearFaultCommand",
    "ManualSteerCommand",
    "NodeWsRegistry",
    "UiWsRegistry",
    "init_registries",
    "push_to_ui_subscribers",
    "router",
]
