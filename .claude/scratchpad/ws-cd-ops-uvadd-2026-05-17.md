# /uvadd-request — WS-CD-OPS (rfmesh-ops dashboard)

**Date:** 2026-05-17
**Workstream:** C+D (rfmesh-ops package)
**Requestor:** lead-Opus delegate (ops package builder)
**Decision authority:** lead-Opus (self-approved per HANDOFF §2 lead-Opus model)

## Request

Add two runtime dependencies to `packages/rfmesh-ops/pyproject.toml`:

1. **`matplotlib>=3.8`** — chosen by ADR-011 §D3 (D3) as the dashboard
   rendering technology. Decision rationale recorded in
   `docs/design/ops-architecture.md` §3.1: matplotlib over web frameworks
   for the BoTH3 RF/EW jury (signal-processing audience reads
   matplotlib plots natively; 1-2 day build effort vs 5-7 days for a
   FastAPI+SPA; replay parity with `apps/demo-replay`).

2. **`aiohttp>=3.9`** — chosen by ADR-011 §D2 implicitly (via the
   `DashboardClient.websocket(url)` factory in `docs/design/ops-architecture.md`
   §2.3). aiohttp is the asyncio-native WebSocket client that subscribes
   to `FusionService.DashboardPubSub`. Already in scope for the broader
   rfmesh-node runtime as the bearer fallback; ops imports it
   independently for the dashboard-side WS consumer.

## Runtime vs dev classification

Both are **runtime** dependencies (declared in `[project.dependencies]`,
not in the workspace dev-dependencies group). The dashboard is a runtime
artefact (the operator runs `rfmesh-ops` on stage); matplotlib + aiohttp
must be on the install path for end-users.

## Invariants check

- **Invariant 1 (contracts frozen):** unaffected — neither dependency
  imports into `rfmesh-contracts`.
- **Invariant 2 (inter-workstream via contracts):** unaffected — both
  are external PyPI deps, not workspace siblings.
- **Invariant 3 (DSP golden tests):** unaffected — neither touches
  `rfmesh-dsp`.
- **Invariant 4 (no silent fallbacks):** OK — matplotlib backend
  selection is explicit (TkAgg default per README; tests force Agg via
  `conftest.py`); aiohttp WS connect errors propagate.
- **Invariant 5 (DSP/fusion pure):** unaffected — these are
  `rfmesh-ops` deps, not `rfmesh-dsp` or `rfmesh-fusion` deps. The
  `Fusion is pure` import-linter contract forbids `aiohttp` in fusion
  (line 220 of root pyproject.toml); ops is not pure by design.

## Backend choice

Default backend: **TkAgg** (no extra dep; ships with Python stdlib via
tkinter). Documented in `packages/rfmesh-ops/README.md`. QtAgg + PyQt5
left as a documented fallback if interactive performance is poor on
Maciej's laptop.

Tests force the **Agg** non-interactive backend in `conftest.py`
(`matplotlib.use("Agg")` BEFORE any panel import) so the suite runs
headless under pytest without DISPLAY.

## Approval

Self-approved per HANDOFF §2 (lead-Opus delegate has full lead
authority for ws-cd-ops scope per task brief).

## Commands run

```
uv add --package rfmesh-ops "matplotlib>=3.8"
uv add --package rfmesh-ops "aiohttp>=3.9"
```
