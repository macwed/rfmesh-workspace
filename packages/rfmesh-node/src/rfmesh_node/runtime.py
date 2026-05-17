"""Node-runtime exception hierarchy.

A small, named hierarchy so callers (the CLI, the test harness, the
council reviewer) can distinguish capability-mismatch from receiver-
unavailable from any other runtime problem. Per ``AGENTS.md`` §3 the
``rfmesh-contracts`` package carries *no* exception hierarchy of its
own (contracts stay pure data + Protocol); each workstream owns its
exceptions. This is ours.

Invariant B3 (no silent fallbacks) lives here as code: every
``raise CapabilityMismatchError(...)`` in this package is the
explicit refusal to downgrade. The dispatch table in
``capabilities.build_estimators`` likewise raises rather than
silently dropping an unknown ``Capability``.
"""

from __future__ import annotations


class NodeRuntimeError(Exception):
    """Base class for every runtime error raised by ``rfmesh-node``."""


class CapabilityMismatchError(NodeRuntimeError):
    """A declared ``Capability`` cannot be met by the detected hardware.

    Raised by ``detect_active_capabilities`` when the operator's
    ``NodeConfig.capabilities`` includes a capability the live
    ``Receiver`` cannot support (L2 declared but the receiver
    only has one coherent channel; L2 declared but the coherent
    receiver is not yet calibrated; etc.). The node refuses to
    boot rather than silently downgrade -- a silent downgrade
    would be the silent-fallback failure mode Invariant B3
    forbids.
    """


class ReceiverNotAvailableError(NodeRuntimeError):
    """The receiver could not be opened / configured.

    Distinct from ``CapabilityMismatchError`` (where the receiver is
    fine but the operator asked for something it cannot do): here
    the receiver itself is missing or refusing to start. The node
    surfaces the underlying message and stops.
    """
