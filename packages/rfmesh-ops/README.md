# rfmesh-ops

Live ops dashboard for rfmesh. Owned by Workstream C+D.

The dashboard is a matplotlib figure populated with one or more
`Panel` subclasses, fed by a `DashboardClient` that streams contract
messages (`BearingReport` / `FixEvent` / `NodeStatus`) from a
fusion-server WebSocket or an in-process asyncio queue.

## Quick start

```bash
rfmesh-ops --connect ws://10.0.0.1:9001 --layout trench
```

`--layout` is one of `trench` (BoTH3 demo), `debug` (all 9 panels), or
`minimal` (FixPanel only).

## matplotlib backend

The dashboard renders through matplotlib. The choice of backend is
**not pinned** by the package itself -- whatever matplotlib's
`MPLBACKEND` env var / matplotlibrc resolves to is what runs.

* **Default on-stage backend: `TkAgg`.** Ships with Python's stdlib
  (no extra runtime dependency), good enough for the BoTH3 demo's
  ~10 Hz update rate (the fusion `batch_window_ms = 100 ms` cadence
  per `INTERFACES.md` §4 `FusionConfig`).
* **If `TkAgg` is laggy on the operator laptop:** switch to `QtAgg`.
  This requires adding `PyQt5` (or `PyQt6`) to the deployment
  environment. The dashboard code itself is backend-agnostic; only the
  environment variable changes:

  ```bash
  pip install PyQt5
  MPLBACKEND=QtAgg rfmesh-ops --connect ws://... --layout trench
  ```

* **Tests force `Agg`** (the non-interactive backend) via
  `conftest.py` so the suite is headless. Do not import `pyplot` at
  module level before `matplotlib.use("Agg")` runs.

Background on the choice (matplotlib over web frameworks):
`docs/design/ops-architecture.md` §3.1.

## Public API

```python
from rfmesh_ops import (
    Dashboard,
    DashboardClient,
    DashboardLayout,
    DEMO_LAYOUT_TRENCH,
    DEMO_LAYOUT_DEBUG,
    DEMO_LAYOUT_MINIMAL,
    PanelSpec,
)

import asyncio
queue: asyncio.Queue = asyncio.Queue()
client = DashboardClient.in_process(queue)
dashboard = Dashboard(client, DEMO_LAYOUT_TRENCH)
asyncio.run(dashboard.run())
```

## Panel-to-Advantage mapping

Binding per `docs/design/ops-architecture.md` §2.3:

| Panel                        | BoTH3 Advantage | Data source                              |
|------------------------------|------------------|-------------------------------------------|
| `FixPanel` + percentage      | #1, #6           | `FixEvent`                                |
| `BearingsPanel` sigma wedges | #2               | `BearingReport.azimuth_sigma_deg`         |
| `L1vsL2Panel`                | #3               | side-by-side `BearingReport`s             |
| `NullSteeringPanel`          | #4               | `rfmesh_dsp.l2_null_steering` + recorded R|
| `ClassificationOverlayPanel` | #5               | `FixEvent.emitter_class`                  |
| `ResidualsPanel`             | #6               | `FixEvent.residuals_deg`                  |
| `GdopHeatmapPanel`           | #1 + #6          | node positions + sigmas                   |
| `NodeStatusPanel`            | #2               | `NodeStatus.gnss_locked`                  |
| `PseudospectrumPanel`        | (L2 evidence)    | `BearingReport.raw_pseudospectrum`        |

## Honesty cuts (binding)

* `FixPanel`: percentage rendered to **one** decimal place per
  ADR-005 §D5(b).
* `NullSteeringPanel`: claimed null depth capped at **20 dB** per
  ADR-008 §D8 (`>=20 dB` text for values above).
* `ClassificationOverlayPanel`: `None` and `EmitterClass.UNKNOWN`
  both display the **same main label `"UNKNOWN"`**; the secondary
  annotation differentiates ("no L3 capability" vs "classifier ran,
  unsure") per `INTERFACES.md` §3.
* `ResidualsPanel`: outlier flag is re-derived per ADR-005 §D3 rule
  `|r_i| / sigma_i > 3` from the most recent `BearingReport`'s sigma
  per node, because the contract `FixEvent.residuals_deg` does not
  carry an `is_outlier` flag (the flag is internal to
  `rfmesh_fusion.residuals.ResidualsResult`).
