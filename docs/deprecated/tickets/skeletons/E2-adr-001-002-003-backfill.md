# TICKET E2: Backfill ADR-001, ADR-002, ADR-003

## Goal (one sentence)
Author the three foundational Architecture Decision Records that `WORKSTREAMS.md` §2 cites by number but which have never been written, so future agents and collaborators can read the *why* behind decisions that look pre-decided.

## Context (links only, not content)

- Architect council finding F6 (project audit 2026-05-18): three decisions referenced in `WORKSTREAMS.md` are missing as ADR files.
- The three decisions are:
  - **ADR-001 — Monorepo uv-workspace layout.** Why one `uv.lock` across 9 packages + 1 app instead of separate repos. Trade-offs: lockstep dependency bumps vs cross-package import simplicity.
  - **ADR-002 — Contracts as `typing.Protocol`.** Why structural typing (`@runtime_checkable` Protocol) instead of `abc.ABC` for `Receiver`, `CoherentReceiver`, `BearingEstimator`, `Fuser`, `CotPublisher`, `Bearer`. Trade-offs vs ABC: no inheritance coupling, mypy-friendly, runtime `isinstance` cheap.
  - **ADR-003 — No GNSS / no TDOA / no magnetometer dependency on the critical path.** Why the architecture deliberately rejects these three commonly-suggested upgrades. Cite `INHERITED_CONTEXT.md` §2.1 (TDOA), §2.2 (magnetometer), §2.3 (GNSS).
- Existing ADRs as format reference: `docs/adr/ADR-004` through `docs/adr/ADR-012`. ADR-012 is the most recent and most concisely structured.
- Reading order: `docs/adr/ADR-012-schema-version-literal-type.md` (newest, cleanest format) + `ARCHITECTURE.md` §6 (the no-GNSS-no-TDOA-no-magnetometer reasoning) + `INHERITED_CONTEXT.md` §2.

## Acceptance criteria

(Executor fills these. Suggested shape:)

1. Three files exist:
   - `docs/adr/ADR-001-monorepo-uv-workspace.md`
   - `docs/adr/ADR-002-contracts-as-protocol.md`
   - `docs/adr/ADR-003-no-gnss-no-tdoa-no-magnetometer.md`
2. Each ADR follows the ADR-012 format: Status, Date, Author, "SCHEMA_VERSION change (none)", Context, Decision, Consequences, Tradeoffs considered, References.
3. Each ADR's Status is `ACCEPTED` (these are not proposing new decisions — they are documenting decisions already taken). Date the file 2026-05-18 with an explicit "backfilled" annotation.
4. Each ADR cites at least 3 specific lines / sections of binding documents (ARCHITECTURE.md, INTERFACES.md, INHERITED_CONTEXT.md, WORKSTREAMS.md).
5. `uv run ruff check docs/` (markdown-aware) clean.
6. No code changes; no contract change.
7. PR description quotes the ADR titles + acceptance status.

## Out of scope (explicit non-goals)

- Do NOT propose new architectural decisions in these ADRs. They are **historical documentation**, not new policy.
- Do NOT modify `WORKSTREAMS.md`, `ARCHITECTURE.md`, or any binding doc. The ADRs reference these; they do not amend them.
- Do NOT modify any code. This is a pure docs ticket.
- Do NOT skip the "Tradeoffs considered" section because "we already decided" — that section is the load-bearing one for future agents who will be tempted to re-litigate.

## Files you may touch

- `docs/adr/ADR-001-monorepo-uv-workspace.md` (create)
- `docs/adr/ADR-002-contracts-as-protocol.md` (create)
- `docs/adr/ADR-003-no-gnss-no-tdoa-no-magnetometer.md` (create)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1)
- `ARCHITECTURE.md`, `INTERFACES.md`, `INHERITED_CONTEXT.md`, `WORKSTREAMS.md`, `AGENTS.md`, `CLAUDE.md` (binding meta-docs)
- Any source file (this is a docs ticket)
- Any other ADR (ADRs are append-only after acceptance)

## Stop conditions

- Stop after producing the three ADR files. Open PR for council review.
- If a decision turns out to be ambiguous (i.e. you cannot identify a single defensible reason from the cited binding docs), STOP and write a SCRATCHPAD entry. Do not invent a rationale.
- If you find yourself wanting to amend `ARCHITECTURE.md` or `INTERFACES.md` while writing these ADRs, STOP. The ADRs reference; they do not amend.

## Council gates

- [ ] **architect** (always — these are foundational ADRs)
- [ ] **code-reviewer** (always)
- [ ] rf-dsp-specialist (not relevant — pure docs)
- [ ] demo-integrity (not relevant — pure docs)

## Suggested owner

Friend, as PM-skill warm-up while environment is being set up. ~2 hours of focused docs work. Good first contribution because it forces a careful read of the binding meta-docs (which is the right onboarding move anyway).

## Implementation notes (non-binding)

- ADR-001 trade-off table: monorepo vs polyrepo. The decision per `WORKSTREAMS.md` §2 was monorepo + workspaces for **single lockfile** + **cross-package mypy** + **type-system tripwire on `SCHEMA_VERSION`**. Polyrepo loses all three.
- ADR-002 trade-off table: `typing.Protocol` vs `abc.ABC`. The decision per `ARCHITECTURE.md` §3 was Protocol because (a) structural typing keeps the dependency star clean (no `Receiver(ABC)` import from `rfmesh-contracts` into every implementer's class hierarchy), (b) `@runtime_checkable` `isinstance` is cheap, (c) test mocks don't need to inherit from a base class. ABC trade-off: explicit "this class implements `Receiver`" inheritance vs the Protocol's "fits by shape" structural check.
- ADR-003 references three sub-decisions that `ARCHITECTURE.md` §6 already binds. The ADR's job is to make the rationale findable from `docs/adr/` rather than buried in ARCHITECTURE §6 prose.

## Claude Code execution prompt skeleton

```
TICKET E2: Backfill ADR-001, ADR-002, ADR-003

Read first (in order):
1. docs/adr/ADR-012-schema-version-literal-type.md (format reference)
2. ARCHITECTURE.md §3, §6
3. INHERITED_CONTEXT.md §2.1, §2.2, §2.3
4. WORKSTREAMS.md §2

Goal: Author 3 ADRs documenting decisions already taken but never written.
Status: ACCEPTED (backfilled), Date 2026-05-18.

Files to create: docs/adr/ADR-{001,002,003}-*.md (titles in the ticket).
Files NOT to touch: every binding meta-doc + every code file.

Acceptance:
1. Three ADR files exist, ADR-012 format
2. Each cites ≥3 binding-doc line/section refs
3. Each has a Tradeoffs Considered section
4. ruff check docs/ clean

Paste files content + final ruff output. Do NOT commit.
```
