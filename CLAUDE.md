# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## Architecture

rfmesh is a cooperative bearing mesh for RF emitter geolocation. Distributed nodes compute bearings locally and ship them to a fusion server, which cross-fixes them into emitter positions surfaced in ATAK.

**Three capability layers** (what a node does with its samples, not hardware tiers):
- **L1** — RSSI sweep with a servo-rotated directional antenna → one `BearingReport`/sweep, 5–15° σ
- **L2** — phase-coherent MUSIC/MVDR on a 2+ element array → 1–3° σ; same covariance matrix R enables null-steering (dual-use)
- **L3** — edge ML emitter classification (CNN over STFT spectrograms) → label + confidence in [0,1]

**Dependency graph is a star.** Every workstream imports from `rfmesh-contracts`. No workstream imports from any other. Three variability axes are each absorbed in exactly one place:
- SDR hardware differences → absorbed in `rfmesh-sdr` behind the `Receiver`/`CoherentReceiver` Protocols
- Per-node capability → absorbed in `rfmesh-node` at startup
- Node count → absorbed in `rfmesh-fusion` (Stansfield weighted-LS, indifferent to N ≥ 2)

**`packages/rfmesh-contracts/`** is the single source of truth for every cross-workstream interface: Pydantic models (`BearingReport`, `FixEvent`, `NodeStatus`, configs), `typing.Protocol` classes, enums, `SCHEMA_VERSION`. It has no runtime logic, no I/O, and depends only on `pydantic` and `numpy`.

**Simulator-first development.** `SyntheticReceiver` implements `Receiver` Protocol with parameterised noise and multipath. All DSP, fusion, and node-runtime code runs end-to-end in pytest with no hardware. Hardware integration is a config-string swap, not a code change.

## The Five Invariants (absolute — no ticket may override)

1. **Contracts are frozen.** `packages/rfmesh-contracts/src/**` is editable only by the lead, only via an accepted ADR, only paired with a `SCHEMA_VERSION` bump. If a contract change appears necessary: write `docs/adr/ADR-NNN-<short>.md` (status: PROPOSED), then **stop**.

2. **Inter-workstream communication is via contracts only.** Never import from a sibling workstream's package. If you want to, stop and raise a SCRATCHPAD note.

3. **Every new function in `rfmesh-dsp` has a golden-file test in `tests/golden/`.** No exceptions.

4. **No silent fallbacks.** Failures raise; short buffers raise; uncalibrated coherent reads return `is_calibrated=False` and are rejected upstream; `BearingEstimator` that cannot produce a bearing returns `None`; YAML with a typo'd key is rejected at parse time (`extra="forbid"` on every Pydantic model).

5. **`rfmesh-dsp` and `rfmesh-fusion` are pure.** No network calls, no file I/O (beyond loading vendor data tables at import), no subprocess, no SDR access. Both must run fully in pytest on a CI runner with no hardware.

## Governance rules for agents

- **Scope boundary:** software agents work on algorithms, data structures, tests, APIs, config handling, CI, and documentation of code. Do not touch physical hardware decisions, RF physics decisions already made, deployment logistics, or Maciej's operational choices.
- **Adding a runtime dependency** (`uv add`) is forbidden without lead approval. Write a `/uvadd-request` in the workstream's scratchpad with name, version range, runtime vs dev, rationale, and which Invariants it does not threaten.
- **`git push`** only to the workstream's own branch, never to `main`. Only the lead merges PRs to `main`.
- **`git push --force`**, `--no-verify`, `git merge` to `main`, mass deletes: forbidden without explicit lead approval.
- **Stop conditions for tickets:** produce the diff, paste `just verify` output, do not auto-commit or push. If a contract change appears necessary, write the ADR and stop.

## Escalation — write a SCRATCHPAD and stop

Write `.claude/scratchpad/<workstream>-<date>.md` and stop when:
- A ticket appears to require a contract change (Invariant 1)
- A ticket requires cross-workstream code changes (Invariant 2)
- A physical/hardware assumption is missing or ambiguous
- A new runtime dependency seems necessary
- Acceptance criteria appear unreachable as stated
- A bug surfaces in salvaged code not flagged in `SALVAGE_AUDIT.md` or `INHERITED_CONTEXT.md §5`
- Any ticket instruction conflicts with `AGENTS.md`, `ARCHITECTURE.md`, or `INTERFACES.md`

## Key documents (read in this order when joining cold)

1. `ARCHITECTURE.md` — the *why*. Binding architectural invariants.
2. `INTERFACES.md` — the *what*. Semantic dictionary of every contract type.
3. `INHERITED_CONTEXT.md` — the *what we already learned* from the prior project.
4. `WORKSTREAMS.md` — the *who*. Ownership and dependencies.
5. `AGENTS.md` — the *how*. Full agent rules (this file summarises the most critical).
6. `SALVAGE_AUDIT.md` — disposition of `github.com/macwed/rf-mesh` into this workspace.
7. Per-workstream onboarding: `docs/bootstrap-A.md`, `docs/bootstrap-B.md`, `docs/bootstrap-CD.md`

## Git worktree workflow

Each active ticket runs in its own worktree under `worktree/ws-<name>/<ticket-short-name>/`. Claude Code instances open in their assigned worktree and never see each other's files. Rebase on `origin/main` once per working session.

## Tool configuration

- **Python 3.12**, strict mypy, ruff line-length 100, `target-version = "py312"`
- `tests/hardware/` and `@pytest.mark.hardware` tests are opt-in; never run in CI
- `extra="forbid"` on every Pydantic model — typo'd config keys are fatal at parse time
- `SCHEMA_VERSION` in `packages/rfmesh-contracts/src/rfmesh_contracts/version.py` is pinned as `Literal[SCHEMA_VERSION]` in every message type — mypy catches drift across workstreams automatically
