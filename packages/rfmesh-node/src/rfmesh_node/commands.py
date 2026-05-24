"""Node-side command schemas + dispatch.

ADR-018 (PROPOSED 2026-05-23) introduces a manual-steer control plane
from the user's web UI to deployed nodes via a long-lived WebSocket
backend → node. The backend forwards UI commands as JSON frames; this
module owns:

* Pydantic models for each command kind (mirror the backend's
  ``deployment/backend/src/both3_poc/ws.py`` schemas — kept duplicated
  intentionally rather than dragged into ``rfmesh-contracts`` because
  the command envelope is server-vs-node coordination, not a
  cross-workstream data product. B1 keeps contracts frozen.)
* A discriminated-union ``parse_command(frame: str) -> Command`` helper
  that the node-side WS client uses to turn an incoming text frame
  into a typed object.

When the contract package grows a peer-acquisition enum (ADR-019), this
module will NOT change — it stays the UI↔node coordination layer.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class ManualSteerCommand(BaseModel):
    """Operator commanded the node to swing the antenna to a specific angle.

    Identical shape to the backend's model (see
    ``deployment/backend/src/both3_poc/ws.py``). Duplication is
    deliberate; both sides validate independently.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["manual_steer"] = "manual_steer"
    axis: int = Field(ge=0, le=255)
    target_angle_deg: float
    timeout_s: float = Field(default=10.0, ge=0.5, le=600.0)
    requestor_id: str = Field(default="unknown", max_length=64)


class AllStopCommand(BaseModel):
    """Mesh-wide ALL-STOP — node halts servo motion + parks."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["all_stop"] = "all_stop"
    requestor_id: str = Field(default="unknown", max_length=64)


class ClearFaultCommand(BaseModel):
    """Operator acknowledges a sticky FAULT (ADR-024 §6, §8).

    Sent in response to a controller-reported FAULT (mode_drain_timeout,
    uncalibrated servo, hardware refusal). Transitions FAULT -> SWEEPING
    if the underlying condition is fixed; if not, the controller raises
    FAULT again with the new ``status_detail``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["clear_fault"] = "clear_fault"
    requestor_id: str = Field(default="unknown", max_length=64)


class SendCommsMessageCommand(BaseModel):
    """Operator typed a message into the comms panel; queue it on the TX outbox (ADR-025 Iter 4).

    The frame size is bounded by ``CommsConfig.frame_payload_max_bytes``
    enforced by the comms loop at queue-time. The backend forwards
    this command verbatim through the existing ``/command/{node_id}``
    plumbing (same path as ``ManualSteerCommand`` -- no new endpoint
    needed). Node-side handler calls
    ``CommsLoop.queue_outbound(payload_bytes())``.

    Soldier surface (link.html): a text input + send button. Bytes
    in ``payload_text`` are encoded as UTF-8 on the node side. A
    future "binary upload" surface (file attachments) would add a
    sibling command kind rather than overloading this one.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["send_comms_message"] = "send_comms_message"
    peer_node_id: str = Field(min_length=1, max_length=64)
    payload_text: str = Field(min_length=1, max_length=256)
    requestor_id: str = Field(default="unknown", max_length=64)

    def payload_bytes(self) -> bytes:
        """UTF-8 encoding of ``payload_text``; what goes on the wire."""
        return self.payload_text.encode("utf-8")


Command = Annotated[
    ManualSteerCommand | AllStopCommand | ClearFaultCommand | SendCommsMessageCommand,
    Field(discriminator="kind"),
]


_COMMAND_ADAPTER: TypeAdapter[
    ManualSteerCommand | AllStopCommand | ClearFaultCommand | SendCommsMessageCommand
] = TypeAdapter(Command)


def parse_command(
    frame: str,
) -> ManualSteerCommand | AllStopCommand | ClearFaultCommand | SendCommsMessageCommand:
    """Decode a JSON text frame into a typed Command.

    Raises:
        ValueError: frame is not JSON or fails schema validation. The
            caller should log the offending frame and drop it (B3: never
            silently misinterpret a malformed command).
    """
    try:
        payload = json.loads(frame)
    except json.JSONDecodeError as exc:
        msg = f"command frame is not JSON: {exc}"
        raise ValueError(msg) from exc
    try:
        return _COMMAND_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        msg = f"command frame failed schema validation: {exc}"
        raise ValueError(msg) from exc


__all__ = [
    "AllStopCommand",
    "ClearFaultCommand",
    "Command",
    "ManualSteerCommand",
    "SendCommsMessageCommand",
    "parse_command",
]
