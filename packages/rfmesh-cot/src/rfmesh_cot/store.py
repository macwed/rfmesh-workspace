"""``OperatorMarkerStore`` -- the thread-safe registry of live operator markers.

The operator's markers are *shared mutable state*: the input surface
(a CLI thread, a map-UI callback thread, or a future WebSocket handler)
writes them, while the publisher's asyncio TX loop reads them to (re)send.
Two threads touching one dict is exactly the "located thread-safe places"
the design calls for, so every operation here is guarded by a single
``threading.Lock`` and read-out is a *snapshot copy* -- callers iterate
their own tuple, never the live dict.

WHY A STORE AT ALL (vs fire-and-forget send)
--------------------------------------------
A CoT marker is not fire-and-forget: ATAK fades it at its stale time. To
keep a no-go area on every client's map you must *re-send* it before it
goes stale. The store is the source of truth for "what markers should be
live right now", so a periodic re-broadcast task (or a reconnect after a
dropped TAK link) can replay them. It is also what a map UI binds to in
order to list / edit / delete what the operator has placed.

The store holds *intent*, keyed by the marker's stable ``uid``. Putting
the same uid again is an edit (move / re-label); ``remove`` drops it.
"""

from __future__ import annotations

import threading

from .operator import OperatorMarker


class OperatorMarkerStore:
    """A lock-guarded ``uid -> OperatorMarker`` registry.

    All mutating and reading operations take a single lock; reads return
    snapshot copies so iteration outside the lock is always safe. The
    store carries no I/O -- it does not know about the publisher; the ops
    layer wires "store changed" to "publisher.publish_marker(...)".
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._markers: dict[str, OperatorMarker] = {}

    def put(self, marker: OperatorMarker) -> OperatorMarker:
        """Add or replace ``marker`` (keyed by ``marker.uid``).

        Returns the stored marker (the same object) so callers can chain
        a publish on the result. Replacing an existing uid is an edit.
        """
        with self._lock:
            self._markers[marker.uid] = marker
        return marker

    def remove(self, uid: str) -> OperatorMarker | None:
        """Drop the marker with ``uid``; return it, or ``None`` if absent.

        Returning the removed marker (rather than raising) lets the
        caller decide whether to emit a delete CoT -- there is nothing to
        un-send if it was never there.
        """
        with self._lock:
            return self._markers.pop(uid, None)

    def get(self, uid: str) -> OperatorMarker | None:
        """Return the marker with ``uid`` or ``None``."""
        with self._lock:
            return self._markers.get(uid)

    def all(self) -> tuple[OperatorMarker, ...]:
        """Return an immutable snapshot of every live marker.

        The tuple is a copy taken under the lock; the caller may iterate
        it freely while other threads keep mutating the store.
        """
        with self._lock:
            return tuple(self._markers.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._markers)

    def __contains__(self, uid: object) -> bool:
        with self._lock:
            return uid in self._markers
