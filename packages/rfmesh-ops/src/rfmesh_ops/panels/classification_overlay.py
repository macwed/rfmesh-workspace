"""ClassificationOverlayPanel -- L3 emitter class + confidence.

Advantage #5 (open threat library) surface. Reads
``FixEvent.emitter_class`` and renders the label + a tooltip-style
annotation that **differentiates** between ``None`` (no L3 capability
ran) and ``EmitterClass.UNKNOWN`` (L3 ran and is unsure), per
``INTERFACES.md`` §3.

Display rule (BINDING per ADR-009 honesty narrative + INTERFACES §3):

* ``emitter_class is None`` -- main label reads ``UNKNOWN``;
  annotation reads ``no L3 capability``.
* ``emitter_class is EmitterClass.UNKNOWN`` -- main label reads
  ``UNKNOWN``; annotation reads ``classifier ran, unsure``.
* otherwise -- main label is the ``EmitterClass.value`` upper-cased;
  annotation includes ``classification_confidence`` if present.

Both ``None`` and ``UNKNOWN`` show **"UNKNOWN"** as the main label
text. The differentiation is in the secondary annotation.
"""

from __future__ import annotations

from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport, EmitterClass, FixEvent

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "L3 classification"
_LABEL_UNKNOWN = "UNKNOWN"
_ANNOTATION_NO_L3 = "no L3 capability"
_ANNOTATION_L3_UNSURE = "classifier ran, unsure"


class ClassificationOverlayPanel(Panel):
    """Displays the L3 emitter class label + tooltip annotation.

    Per-class classification confidence is sourced via a side-channel
    from the BearingReport stream (FixEvent does not carry it on the
    contract -- confidence is per-bearing not per-fix). The panel
    tracks the most recent confidence seen for each EmitterClass
    value across all node BearingReports; the FixEvent's
    emitter_class field is the trigger to render the corresponding
    confidence. demo-integrity council R6 (option a) — re-derive
    from BearingReport stream the same way ResidualsPanel re-derives
    is_outlier from azimuth_sigma_deg.
    """

    handled_message_types = (FixEvent, BearingReport)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._current_fix: FixEvent | None = None
        # Most-recent classification confidence per EmitterClass, seen
        # across all node BearingReports. None entries mean "the
        # classifier did not emit a confidence number" (e.g. a node
        # without L3 capability shipped a bearing with emitter_class=None).
        self._confidence_by_class: dict[EmitterClass, float] = {}
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.axis("off")
        self.ax.text(
            0.5,
            0.5,
            "awaiting fix",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        if isinstance(msg, BearingReport):
            # Track latest classification confidence per emitter class.
            if msg.emitter_class is not None and msg.classification_confidence is not None:
                self._confidence_by_class[msg.emitter_class] = float(msg.classification_confidence)
            return
        if not isinstance(msg, FixEvent):
            return
        self._current_fix = msg
        self._render()

    def _label_text(self, fix: FixEvent) -> tuple[str, str]:
        """Return (main_label, annotation_tooltip).

        Implements the binding None-vs-UNKNOWN distinction from
        INTERFACES.md §3 / ADR-009. The *main label text* is the
        identical string "UNKNOWN" in both cases; the *annotation*
        differentiates so the operator can read which it is.
        """
        cls = fix.emitter_class
        if cls is None:
            return _LABEL_UNKNOWN, _ANNOTATION_NO_L3
        if cls is EmitterClass.UNKNOWN:
            return _LABEL_UNKNOWN, _ANNOTATION_L3_UNSURE
        # A real classification.
        main = cls.value.upper()
        confidence = self._confidence_by_class.get(cls)
        if confidence is not None:
            return main, f"classifier ran, confidence {confidence:.2f}"
        return main, "classifier ran, confident"

    def _render(self) -> None:
        if self._current_fix is None:
            self._render_empty()
            return
        fix = self._current_fix
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.axis("off")
        main_label, annotation = self._label_text(fix)
        self.ax.text(
            0.5,
            0.65,
            main_label,
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            fontsize=20,
            fontweight="bold",
        )
        self.ax.text(
            0.5,
            0.35,
            annotation,
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            fontsize=10,
            color="gray",
        )
