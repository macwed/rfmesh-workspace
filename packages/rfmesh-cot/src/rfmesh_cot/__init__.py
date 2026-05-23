"""rfmesh-cot -- CoT/ATAK publisher for rfmesh ``FixEvent`` output.

Implements ``rfmesh_contracts.CotPublisher`` via PyTAK. Converts a
``FixEvent`` into a hostile-emitter CoT marker (with confidence-ellipse
polygon and ``EmitterClass`` overlay) and ships it to a configured TAK
endpoint (FreeTAKServer, ATAK end-user device, multicast group, etc.).

PUBLIC API
----------
* ``PyTAKCotPublisher``           -- the network-aware publisher class.
* ``fix_event_to_cot_xml``        -- pure encode of a FixEvent -> CoT bytes.
* ``node_status_to_cot_xml``      -- pure encode of a NodeStatus -> CoT bytes.
* ``emitter_class_to_cot_type``   -- closed EmitterClass -> CoT type mapping.
* ``ellipse_to_polygon_vertices`` -- pure ellipse -> (lat, lon) projection.
* ``STALE_AFTER_S``               -- default CoT stale-time (30 s).
* ``CotError``, ``CotTransportError``, ``CotEncodingError`` -- exceptions.

DESIGN NOTES
------------
* Pure-encode functions (``fix_event_to_cot_xml`` etc.) are testable
  without any network; only ``PyTAKCotPublisher`` touches the wire.
* CoT-type mapping is closed and never raises -- a future EmitterClass
  the table does not know about degrades gracefully to the
  "nonspecific electronic" type, never aborts the publisher mid-demo.
* Stale time is relative to ``fix.t_unix_ns`` (not the encoder's
  wall clock) so replays remain honest.
* No silent transport fallbacks: a PyTAK transmit failure becomes
  ``CotTransportError`` on the next public call. See ``AGENTS.md``
  §1 Invariant 4.
"""

from __future__ import annotations

from .ellipse import ellipse_to_polygon_vertices
from .exceptions import CotEncodingError, CotError, CotTransportError
from .markers import (
    STALE_AFTER_S,
    emitter_class_to_cot_type,
    fix_event_to_cot_xml,
    node_status_to_cot_xml,
)
from .operator import (
    TEMPLATES,
    MessageTemplate,
    OperatorMarker,
    build_self_sa_xml,
    operator_delete_to_cot_xml,
    operator_marker_to_cot_xml,
    template,
)
from .publisher import PyTAKCotPublisher
from .store import OperatorMarkerStore

__all__ = [
    "STALE_AFTER_S",
    "TEMPLATES",
    "CotEncodingError",
    "CotError",
    "CotTransportError",
    "MessageTemplate",
    "OperatorMarker",
    "OperatorMarkerStore",
    "PyTAKCotPublisher",
    "build_self_sa_xml",
    "ellipse_to_polygon_vertices",
    "emitter_class_to_cot_type",
    "fix_event_to_cot_xml",
    "node_status_to_cot_xml",
    "operator_delete_to_cot_xml",
    "operator_marker_to_cot_xml",
    "template",
]
