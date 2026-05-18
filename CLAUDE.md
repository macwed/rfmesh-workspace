# CLAUDE.md

Quick reference for Claude Code working on rfmesh. The full agent rules are in @AGENTS.md — read that file in full at session start.

## Autonomy policy — read before anything else

### When to ask Maciej (escalate only these)
1. A proposed change would modify `packages/rfmesh-contracts/` 
   (Invariant 1 — contracts frozen, ADR required first)
2. A decision would remove one of the 8 architectural advantages 
   from docs/ADVANTAGES.md
3. A hardware assumption is ambiguous and cannot be resolved 
   from existing docs
4. Council review produces a split verdict with no clear resolution

Everything else: resolve internally. Do not ask for confirmation on:
- file reads, writes, test runs, ruff/mypy, git operations within 
  a workstream branch
- dependency of uv packages (add with rationale in commit message)
- spawning subagents for parallel work
- sequencing decisions within a workstream

### Council review protocol (sequential, before any PR merge to main)
When a workstream ticket is ready to merge, run sequentially:

1. **Architect** (`--agents architect`) 
   — checks: contracts untouched? invariants B1-B7 held? ADRs needed?
   — output: APPROVE / BLOCK-WITH-REASON / REQUEST-ADR
   — if BLOCK: fix and re-run from step 1

2. **Code Reviewer** (`--agents code-reviewer`)
   — checks: sigma honesty (±20% band), no silent fallbacks, 
     pure DSP (no side effects in rfmesh-dsp/rfmesh-fusion), 
     golden tests present
   — output: APPROVE / BLOCK-WITH-REASON
   — if BLOCK: fix and re-run from step 2

3. **RF-DSP Specialist** (`--agents rf-dsp-specialist`)
   — checks: algorithmic correctness of bearing estimators, 
     edge cases jury will probe, sigma mechanism matches 
     estimator type (across-realisation vs within-realisation)
   — output: APPROVE / NOTE (non-blocking) / BLOCK-WITH-REASON

4. **Demo Integrity** (`--agents demo-integrity`)
   — checks: CoT payload tactical sense, dashboard readability 
     under field conditions, confidence ellipse honest and 
     interpretable, GDOP communicated to operator
   — output: APPROVE / RECOMMENDATION (non-blocking)

Only after 4x APPROVE (or APPROVE+NOTE/RECOMMENDATION): merge to main.

### Parallel work policy
Workstream subagents (A, B, C+D) run in parallel wherever tickets 
are independent. Council review is sequential and gates merges — 
it does not block parallel development in-flight.

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

The Seven Binding Invariants are defined in @AGENTS.md §1. They are absolute. Read them before doing anything.

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
