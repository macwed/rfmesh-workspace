# CLAUDE.md

Quick reference for Claude Code working on rfmesh. The full agent rules are in @AGENTS.md — read that file in full at session start.

## Commands

```bash
uv sync                          # create .venv and install all workspace packages editable
just verify                      # full gate: ruff + mypy + pytest (run before declaring any ticket done)
just lint                        # ruff check + format check
just fmt                         # ruff format + fix in place
just type                        # mypy workspace-wide (strict)
just test                        # pytest, hardware tests excluded
just test-cov                    # pytest with coverage report
just ticket-verify <pkg>         # scoped verify for a single package (e.g. rfmesh-dsp)
uv run pytest -k <pattern>       # run a single test or subset
uv run pytest -m hardware        # hardware-only tests (requires physical SDR)
```

Paste `just verify` output into the conversation when declaring a ticket done.

## Architecture (one-paragraph orientation)

rfmesh is a cooperative bearing mesh for RF emitter geolocation. Distributed nodes compute bearings locally, ship them via contracts, fusion server cross-fixes positions. Star dependency graph: every workstream imports only from `rfmesh-contracts`. See @ARCHITECTURE.md §1 for binding architectural invariants and @INTERFACES.md for the semantic dictionary of contracts.

## Binding rules

The Five Invariants are defined in @AGENTS.md §1. They are absolute. Read them before doing anything.

## When to stop

Escalation conditions and the SCRATCHPAD protocol live in @AGENTS.md §6. When in doubt: stop and write a scratchpad note.

## Working environment

- Python 3.12, uv workspaces, ruff line-length 100, mypy strict
- `extra="forbid"` on every Pydantic model
- `tests/hardware/` and `@pytest.mark.hardware` are opt-in (never CI)
- Each ticket runs in its own worktree under `worktree/ws-<x>/<ticket>/`

## Key documents (read on cold start, in order)

1. @ARCHITECTURE.md — the *why*
2. @AGENTS.md — agent behaviour (binding)
3. @INTERFACES.md — contract semantics
4. @INHERITED_CONTEXT.md — prior project lessons
5. @WORKSTREAMS.md — ownership
6. @SALVAGE_AUDIT.md — disposition of macwed/rf-mesh