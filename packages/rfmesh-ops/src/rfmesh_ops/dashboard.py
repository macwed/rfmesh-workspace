"""Dashboard -- the live ops dashboard application.

Owns a ``matplotlib.figure.Figure`` populated from a
``DashboardLayout`` and an asyncio task that consumes contract
messages from a ``DashboardClient`` stream, dispatches each to its
panels, and triggers a non-blocking canvas redraw.

The dashboard is not a renderer of the message stream verbatim -- each
panel knows which message types it cares about (``handled_message_types``
on the Panel base class) and the dashboard skips redraws on panels
that did not receive their type. This keeps the redraw rate at the
fusion batch rate (~10 Hz) without paying for full-figure redraws on
every status / bearing.

BACKEND POLICY
--------------
Tests force matplotlib's ``Agg`` backend in ``conftest.py`` (no display
needed). On-stage default is ``TkAgg`` (no extra runtime dep). The
README documents the QtAgg + PyQt5 fallback for laptops where TkAgg
performs poorly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib.figure as mpl_figure
import matplotlib.pyplot as plt

if TYPE_CHECKING:
    from matplotlib.gridspec import GridSpec

from rfmesh_ops.client import DashboardClient, DashboardMessage
from rfmesh_ops.layouts import DashboardLayout
from rfmesh_ops.panels import Panel

_LOG = logging.getLogger(__name__)


class Dashboard:
    """The live dashboard application.

    Construct with a ``DashboardClient`` (the stream source) and a
    ``DashboardLayout`` (which panels go where). Call ``run()`` from
    an asyncio event loop; the dashboard consumes messages until the
    stream closes or the task is cancelled.
    """

    def __init__(self, client: DashboardClient, layout: DashboardLayout) -> None:
        self._client = client
        self._layout = layout
        self._figure: mpl_figure.Figure
        self._panels: list[Panel] = []
        self._build_figure()

    @property
    def figure(self) -> mpl_figure.Figure:
        """The matplotlib Figure owned by this dashboard.

        Exposed primarily for tests and for ``apps/demo-replay`` to
        ``savefig`` for slide deck artefacts.
        """
        return self._figure

    @property
    def panels(self) -> tuple[Panel, ...]:
        """Read-only view of the constructed panels."""
        return tuple(self._panels)

    def _build_figure(self) -> None:
        """Allocate the Figure and one Axes per PanelSpec.

        Uses a GridSpec from the layout's ``grid_shape``. Panels are
        instantiated against their carved Axes; subsequent message
        dispatch goes through ``Panel.update()``.
        """
        rows, cols = self._layout.grid_shape
        # Reasonable default figure size; the on-stage layout takes
        # the figure window dimensions from whatever the operator's
        # display delivers.
        figsize = (4.5 * cols, 3.5 * rows)
        self._figure = plt.figure(figsize=figsize)
        gridspec: GridSpec = self._figure.add_gridspec(rows, cols)
        for spec in self._layout.panels:
            kw = spec.subplot_kwargs
            row = int(kw.get("row", 0))
            col = int(kw.get("col", 0))
            rowspan = int(kw.get("rowspan", 1))
            colspan = int(kw.get("colspan", 1))
            projection = kw.get("projection")
            slot = gridspec[row : row + rowspan, col : col + colspan]
            ax_kwargs: dict[str, Any] = {}
            if projection is not None:
                ax_kwargs["projection"] = projection
            ax = self._figure.add_subplot(slot, **ax_kwargs)
            panel = spec.panel_cls(ax)
            self._panels.append(panel)
        # Some backends raise on tight_layout for unusual axes; the
        # dashboard still works without it.
        with contextlib.suppress(RuntimeError, ValueError):
            self._figure.tight_layout()

    async def run(self) -> None:
        """Consume the client's stream and dispatch each message.

        Stops when the stream closes, the task is cancelled, or the
        client signals end-of-stream. The figure is left as-is for
        the caller to ``savefig`` or display.
        """
        try:
            async for msg in self._client.stream():
                self._dispatch_one(msg)
                # Hint matplotlib that the figure is dirty without
                # blocking on a synchronous redraw. Non-interactive
                # backends (Agg) treat this as a no-op.
                try:
                    self._figure.canvas.draw_idle()
                except (RuntimeError, ValueError) as exc:
                    _LOG.debug("draw_idle suppressed: %s", exc)
        except asyncio.CancelledError:
            await self._client.close()
            raise
        finally:
            await self._client.close()

    def _dispatch_one(self, msg: DashboardMessage) -> None:
        """Route one message to every panel that handles its type."""
        for panel in self._panels:
            if panel.handles(msg):
                try:
                    panel.update(msg)
                except (ValueError, TypeError, RuntimeError) as exc:
                    # Defensive: a malformed payload should not bring
                    # the whole dashboard down (Invariant 4 surface --
                    # log loudly, keep running so the operator can
                    # see the failure on every other panel).
                    _LOG.error(
                        "panel %s raised on update: %s",
                        type(panel).__name__,
                        exc,
                    )

    def savefig(self, path: Path | str) -> None:
        """Save the current figure to ``path`` (PNG by extension).

        Convenience wrapper used by ``apps/demo-replay`` and by tests
        to produce a Phase-C bench-side artefact.
        """
        self._figure.savefig(str(path), bbox_inches="tight")
