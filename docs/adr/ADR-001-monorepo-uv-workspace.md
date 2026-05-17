# ADR-001 — Monorepo uv-workspace layout

**Status:** ACCEPTED (backfilled 2026-05-18)
**Date:** 2026-05-18 — decision originally taken pre-WS-CD-001 (April 2026); backfilled per architect council finding F6 (project audit 2026-05-18).
**Author:** lead-Opus (backfill).
**SCHEMA_VERSION change:** **none** — backfilled documentation of an existing decision.

## Context

rfmesh consists of nine logically distinct packages (`rfmesh-contracts` + 8 implementers) plus one app (`apps/demo-replay`). Two layout options were on the table at project start:

1. **Polyrepo.** One git repo per package. Each `rfmesh-*` is an independent PyPI-installable. The app pulls them as version-pinned deps.
2. **Monorepo with uv workspaces.** One git repo, `[tool.uv.workspace]` in the root `pyproject.toml`, one `uv.lock`, per-package `pyproject.toml` files. All packages installed editable in the same venv.

The choice was made in favour of (2). `WORKSTREAMS.md` §2 references this ADR by number, but the ADR itself was never authored. This file backfills it.

## Decision

**Monorepo with uv workspaces.** Layout:

```
rfmesh-workspace/
├── pyproject.toml          # [tool.uv.workspace] members = ["packages/*", "apps/*"]
├── uv.lock                 # single lockfile, all transitive deps resolved together
├── packages/
│   ├── rfmesh-contracts/
│   ├── rfmesh-sdr/
│   ├── rfmesh-servo/
│   ├── rfmesh-dsp/
│   ├── rfmesh-ml/
│   ├── rfmesh-fusion/
│   ├── rfmesh-cot/
│   ├── rfmesh-node/
│   └── rfmesh-ops/
└── apps/
    └── demo-replay/
```

Each package has its own `pyproject.toml` declaring its runtime deps and its dependencies on sibling packages. The root `pyproject.toml` aggregates dev tooling (ruff, mypy, pytest, lint-imports, hypothesis) and workspace membership.

`uv sync` resolves the entire dependency graph once and installs every package editable into a shared `.venv/`. `uv add --package <pkg> <dep>` adds a runtime dep to a specific package; `uv add --dev <dep>` adds to the workspace dev deps.

## Consequences

**What this enables:**

1. **Single `uv.lock` for the whole workspace.** Cross-package transitive deps resolve together; no risk of `rfmesh-contracts` and `rfmesh-fusion` accidentally pinning different versions of `pydantic` and breaking the wire format on serialisation.
2. **Workspace-wide `mypy` invocation** (`uv run mypy packages/`) crosses package boundaries cleanly because everything is in the same Python environment. A `SCHEMA_VERSION` bump in `rfmesh-contracts/src/rfmesh_contracts/version.py` causes every dependent workstream's `Literal[SCHEMA_VERSION]` field to fail type-check **in the same `mypy` run** — the type-system tripwire ADR-012 documents (per the original design intent from `version.py:5`).
3. **Workspace-wide `pytest`** runs every package's tests in one invocation, exercising real cross-package imports rather than mocked stubs. `lint-imports` reads the entire `import` graph for contract violations.
4. **`import-linter` enforces the dependency-star architecture** at CI time. The 6 KEPT contracts in `pyproject.toml` are workspace-wide; in polyrepo this would require coordinating CI across 9 separate repositories.
5. **Code-review surface is one PR.** A change that spans `rfmesh-dsp` and `rfmesh-fusion` (e.g. a sigma-honesty refactor that touches both estimator and fuser) is reviewed atomically; the council reviewers see the cross-package impact in one diff.

**What this costs:**

1. **Lockstep dependency bumps.** A `numpy` major bump affects every package simultaneously; we cannot stage the upgrade across packages.
2. **CI runtime grows with workspace size.** `just verify` is currently ~4 minutes for 481 tests across 9 packages. A polyrepo could run them in parallel across 9 GitHub Actions jobs.
3. **PyPI publishing requires `uv build --package <name>`** per release — not insurmountable, but more friction than `cd packages/foo && uv publish`.
4. **External consumers cannot install one package without pulling the workspace.** Mitigation: each package is independently PyPI-buildable; the workspace structure is internal-only.

## Tradeoffs considered

**Why monorepo wins for rfmesh specifically:**

- The architecture is a **frozen-contract star** (`ARCHITECTURE.md` §3): every workstream depends only on `rfmesh-contracts`, and contracts are the source of truth. The single most important workflow is "lead bumps `SCHEMA_VERSION`; every dependent workstream gets a `mypy` failure immediately". Polyrepo defeats this — the contracts package would publish to PyPI, dependents pin a version, and you'd discover the mismatch on the next install, not the next `mypy` run. **The type-system tripwire is the architecture's load-bearing mechanism**; layout must protect it.
- The project is **single-team and hackathon-paced** (BoTH3 demo target ≈ 25 days). Cross-package refactors happen weekly. Polyrepo's per-repo PR / per-repo CI overhead would be a tax we can't afford.
- The code is **mostly Python** (the firmware C tree under `firmware/` is git-tracked but not in `[tool.uv.workspace]` because it has its own ESP-IDF build system). One language, one build tool, one lockfile.

**When polyrepo would have been right:**

- Multiple teams, each owning one package, with weekly release cadence per package.
- An open-source library subset (`rfmesh-dsp` as a standalone DSP library, for instance) where external consumers care about install-without-workspace.
- A long-lived production system where the lockstep-dep-bump cost dominates over the type-system-tripwire benefit.

None of those apply to rfmesh as scoped.

## Why this is documented and not just done

`WORKSTREAMS.md` §2 cites this as `ADR-001-monorepo-uv-workspace` because future agents (including future Claude Code sessions, including external contributors) will see the workspace structure and reasonably ask **"why not split into per-package repos?"**. Without this ADR, they would either re-litigate the decision or, worse, propose a polyrepo refactor at a time when the type-system tripwire is the only thing catching contract drift across 9 packages. The cost of writing this ADR is < 1 hour; the cost of NOT writing it is a future agent burning a day re-arguing it.

## References

- `WORKSTREAMS.md` §2 — cites ADR-001 by number.
- `ARCHITECTURE.md` §3 — the frozen-contract star that monorepo protects.
- `packages/rfmesh-contracts/src/rfmesh_contracts/version.py:5` — original docstring promising the type-system tripwire.
- ADR-012 — the realisation of that tripwire as `SchemaVersionT` literal alias; depends on workspace-wide `mypy` (this ADR's load-bearing benefit).
- `AGENTS.md` §1 Invariant 1 — contracts-frozen rule that monorepo makes enforceable.
