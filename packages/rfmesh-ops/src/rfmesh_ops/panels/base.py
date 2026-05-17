"""Panel base class -- the visual element of a dashboard layout.

Every panel owns a single ``matplotlib.axes.Axes`` and implements one
method, ``update(msg)``, that re-renders the panel in-place when a new
``BearingReport`` / ``FixEvent`` / ``NodeStatus`` arrives over the
``DashboardClient`` stream. Panels do **not** own the figure, the layout,
the asyncio loop, or the transport -- those live on the ``Dashboard``.

Why a base class and not a Protocol?
------------------------------------
Two reasons:

1. **Shared message-routing logic.** ``update()`` is dispatched per
   panel by ``Dashboard.run()``; panels declare which message types
   they care about via ``handled_message_types`` so the Dashboard can
   skip irrelevant updates without paying matplotlib's redraw cost on
   every message. The base class hosts that mechanism.
2. **Test ergonomics.** Tests construct panels with a real
   ``matplotlib.figure.Figure().add_subplot()`` axes and call
   ``update()`` with canonical fixtures. Inheriting from a base class
   makes ``isinstance`` checks in ``test_layouts`` straightforward.

The matplotlib backend is selected by the caller -- the dashboard
``Dashboard.__init__`` reads ``MPLBACKEND`` env or defaults to TkAgg;
the test conftest sets ``Agg`` for headless runs (binding per
ADR-011 §D3 implementation note).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport, FixEvent, NodeStatus

# Type alias for any dashboard message a panel may receive.
DashboardMessage = BearingReport | FixEvent | NodeStatus


class Panel(ABC):
    """Abstract base for every dashboard panel.

    Subclasses implement ``update(msg)`` to redraw on each incoming
    contract message. The ``Dashboard`` owns the asyncio loop and the
    figure canvas; panels stay synchronous and pure-rendering.

    Attributes:
        ax: The matplotlib Axes this panel renders onto. Set by
            ``__init__`` and never reassigned (the layout owns the
            subplot lifecycle).
    """

    handled_message_types: tuple[type[DashboardMessage], ...] = (
        BearingReport,
        FixEvent,
        NodeStatus,
    )
    """Tuple of contract message types this panel responds to.

    Subclasses narrow this to skip irrelevant redraws. For instance,
    ``FixPanel.handled_message_types = (FixEvent,)`` -- so a stream of
    BearingReports does not force the FixPanel to redraw on every
    bearing.
    """

    def __init__(self, ax: Axes) -> None:
        """Initialise the panel against a matplotlib Axes.

        The Axes is owned by the figure / layout; the panel only renders
        into it. Subclasses MAY draw initial annotations (title, axis
        labels, empty placeholders) in ``__init__`` so the panel is
        visually present even before the first message arrives.

        Args:
            ax: Pre-allocated matplotlib Axes from ``Figure.add_subplot``.
        """
        self.ax = ax

    @abstractmethod
    def update(self, msg: DashboardMessage) -> None:
        """Update the panel from one incoming contract message.

        Pure rendering: no I/O, no network, no asyncio. The caller
        (``Dashboard.run``) batches multiple messages into a single
        frame redraw via ``figure.canvas.draw_idle()``.

        Args:
            msg: A ``BearingReport`` / ``FixEvent`` / ``NodeStatus``
                from the ``DashboardClient`` stream. Panels that do not
                care about the type are guarded by
                ``handled_message_types`` in ``Dashboard.run``; calls
                that reach ``update`` should be filtered already, but
                panels MAY no-op on unexpected types (defensive).
        """

    def handles(self, msg: DashboardMessage) -> bool:
        """Return True iff this panel responds to ``msg``'s type."""
        return isinstance(msg, self.handled_message_types)
