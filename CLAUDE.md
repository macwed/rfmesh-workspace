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
- file reads, writes, test runs, ruff/mypy, git operations on `main`
- dependency of uv packages (add with rationale in commit message)
- spawning subagents for parallel work
- sequencing decisions within a feature

### Council review protocol (sequential, before any merge to main)
When a substantive change is ready to merge, run sequentially:

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
Spawn builder / investigator subagents in parallel wherever the work
is independent. Council review (above) is sequential and gates merges
— it does not block parallel development in-flight.

EW-specialist (`--agents ew-specialist`) is available for pitch /
tactical / tradecraft calls; consult when the question is about how
the product reads to an RF/EW jury rather than about code.

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

rfmesh is a **€250 directional radio that points itself, survives
jamming by pointing away from it, and triangulates the jammer as a
free side-effect** (ADR-021 reframing, 2026-05-23). Each node carries
an SDR + directional Yagi on a 1-axis pan servo; two nodes
auto-acquire each other (GPS-prior pointing + scan-and-stare per
ADR-019) for high-gain directional comms. The geolocation pipeline
(L1 amplitude DF + Stansfield/MLE fusion + ATAK markers) is preserved
as a side-effect feature. Star dependency graph: every package imports
only from `rfmesh-contracts`. See @ARCHITECTURE.md §1 for binding
architectural invariants, @INTERFACES.md for contract semantics,
@docs/DOC_INDEX.md for selective deep-dives.

## Binding rules

The Seven Binding Invariants are defined in @AGENTS.md §1. They are absolute. Read them before doing anything.

## When to stop

Escalation conditions and the SCRATCHPAD protocol live in @AGENTS.md §6. When in doubt: stop and write a scratchpad note.

## Working environment

- Python 3.12, uv workspaces, ruff line-length 100, mypy strict
- `extra="forbid"` on every Pydantic model
- `tests/hardware/` and `@pytest.mark.hardware` are opt-in (never CI)
- Lead-Opus operates on `main` directly; subagents may run in
  isolated worktrees the runtime auto-creates under `.claude/worktrees/`.

## Key documents (read on cold start, in order)

1. @ARCHITECTURE.md — the *why* + Appendix A (interface conventions) +
   Appendix B (hardware quirks + regression anchors)
2. @AGENTS.md — agent behaviour (binding); Seven Binding Invariants
3. @INTERFACES.md — contract semantics (full per-field dictionary)
4. @docs/DOC_INDEX.md — selective-read pointer index (jump to a
   binding section without scanning the whole tree)
5. @docs/adr/ — append-only architectural decisions; binding ones are
   ADR-021 (comms-first reframing), ADR-019 (rendezvous), ADR-018
   (operator-authored CoT), ADR-015 (firmware target C6), ADR-014
   (Mast C empirical anchor), ADR-008 (L2 enum split)

Retired but reference-readable under `docs/deprecated/`:
`WORKSTREAMS.md`, `SALVAGE_AUDIT.md`, full `INHERITED_CONTEXT.md`,
sprint logs, ticket history. Their binding content (Seven Invariants,
hardware quirks, regression anchors) is already folded into
`AGENTS.md` §1 and `ARCHITECTURE.md` Appendix B.
