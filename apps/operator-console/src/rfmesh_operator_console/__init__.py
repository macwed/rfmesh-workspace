"""rfmesh operator console -- click-the-map UI for operator-authored CoT.

A small aiohttp web app: open it in a browser, pick a message template,
click the map to drop a point (or draw an area), add an optional note, and
it ships the marker to the TAK server so it appears on every connected
ATAK / WinTAK / iTAK client. The backend holds one TAK connection open so
FreeTAKServer reliably relays the markers.

Composition root (apps/*): importing ``rfmesh_cot`` here is intentional.
"""

from __future__ import annotations

from rfmesh_operator_console.server import build_app

__all__ = ["build_app"]
