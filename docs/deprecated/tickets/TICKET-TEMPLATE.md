# TICKET-TEMPLATE — `<WS>-<NNN>: <one-line title>`

**This file is the template.** Copy it to `docs/tickets/WS-X-NNN-<short>.md` when authoring a new ticket. Fill every section. The non-obvious sections are commented inline.

---

# TICKET <WS>-<NNN>: <one-line title>

## Goal (one sentence)
What the ticket accomplishes, in human English. **Verbs and outcomes**, not noun phrases.

Good: *"Port the RTL-SDR subprocess wrangler to the `Receiver` Protocol."*
Bad: *"RTLSDRDevice port"* (not a sentence; no verb; no outcome).

## Context (links only, not content)
- Contracts touched (**read-only** for executors): `rfmesh_contracts.<module>.<Type>`
- Architecture references: `ARCHITECTURE.md` §<n>, `INTERFACES.md` §<n>
- Inherited context: `INHERITED_CONTEXT.md` §<n> if applicable
- Salvage: `SALVAGE_AUDIT.md` row for the file being ported, if applicable
- Prior tickets this depends on: `<WS>-<NNN>`
- ADR references: `docs/adr/ADR-<NNN>.md` if a decision shaped this

The Context section is **links and one-line annotations**, not paragraphs of explanation. The reviewer reads the linked source; the ticket lists the destination.

## Acceptance criteria
Concrete, runnable. Each line is something `just verify` or a specific command will check. Number every criterion.

1. `uv run pytest <path> -v` passes (list the tests added).
2. Golden test `<name>` in `tests/golden/` passes within `<tolerance>`.
3. Property test in `tests/property/<name>.py` passes with default hypothesis budget.
4. `uv run mypy packages/<pkg>` clean.
5. `uv run ruff check packages/<pkg>` clean.
6. `uv run lint-imports` — no broken contracts; ignore_imports list unchanged unless ADR justifies.
7. The new code is pure / hardware-free / contracts-only (per the applicable Invariants).

If the Acceptance Criteria cannot be expressed as runnable checks, the ticket scope is **too vague** — pause and tighten it before opening the PR.

## Out of scope (explicit non-goals)

This is the **most load-bearing section** of the ticket per `AGENTS.md` §4. Multiple practitioners running parallel coding agents converge on the same finding: negative constraints prevent more drift than positive specifications.

- Do NOT add fields to `<contract type>`.
- Do NOT modify `<sibling file>` — that is ticket `<WS>-<NNN>`'s scope.
- Do NOT introduce new runtime dependencies — write `/uvadd-request` first.
- Do NOT modify `<package outside this ticket>`.
- Do NOT refactor unrelated code "while you're in there".

## Files you may touch
- `packages/<pkg>/src/<pkg>/<file>.py`        (create | modify)
- `packages/<pkg>/tests/test_<thing>.py`      (create)

Be explicit. If a file is not on this list and you find yourself wanting to edit it, **stop and ask** (per `AGENTS.md` §6).

## Files you may NOT touch
- `packages/rfmesh-contracts/**`              (**FROZEN** — Invariant 1)
- Anything outside `packages/<pkg>/`
- `<sibling file>`                            (other ticket's scope)
- `firmware/**`                               (Maciej domain unless this ticket is firmware)
- `docs/adr/ADR-<existing>.md`                (ADRs are append-only after acceptance)

The MAY-NOT-TOUCH list is **stronger than the MAY-TOUCH list**. Even if you have a defensible reason to edit a MAY-NOT-TOUCH file, refuse scope and surface the question.

## Stop conditions
- Stop after producing the diff. Do not auto-commit or push.
- Paste the test output and `mypy` / `ruff` / `lint-imports` output into the PR description.
- If a contract change appears necessary, stop and write `docs/adr/ADR-NNN-<short>.md` (Status: PROPOSED) instead — do **not** proceed with the contract change.
- If a physical-hardware assumption is needed, treat it as given and note it in the diff; do not propose changes to physical-world assumptions.
- If a runtime dep is needed, write `/uvadd-request` in your branch's scratchpad and ping Maciej.

## Council gates
Which reviewers must APPROVE before this ticket can merge:

- [ ] architect (always)
- [ ] code-reviewer (always)
- [ ] rf-dsp-specialist (only if touching `rfmesh-dsp` or `rfmesh-fusion`)
- [ ] demo-integrity (only if touching `docs/demo/`, `rfmesh-ops`, or `rfmesh-cot`)

---

## Filled-out example: `WS-A-005-rtlsdr-device-port.md`

See `docs/tickets/skeletons/WS-A-005-rtlsdr-device-port.md` for a real skeleton currently open for the friend collaborator. The Goal, Context, Out-of-scope, Files-may-touch, Files-may-NOT-touch, and Stop-conditions are all pre-filled. The executor fills the Acceptance Criteria.

## Honesty checklist (before submitting the PR)

Run through this list with a clear head before requesting council review:

- [ ] **B1 — Contracts frozen.** No file under `packages/rfmesh-contracts/src/` modified without an accepted ADR. If you touched the contracts package, the PR description names the ADR and quotes the SCHEMA_VERSION bump.
- [ ] **B2 — Imports star-shaped.** No `from rfmesh_<other_workstream>.<...>` imports outside your own package or `rfmesh_contracts`. `uv run lint-imports` confirms.
- [ ] **B3 — No silent fallbacks.** Every `except`/`try` block either re-raises, logs + raises, or has a docstring justifying the silent path (teardown only).
- [ ] **B4 — Golden tests for new DSP.** Every new function in `rfmesh-dsp/src/` has at least one golden-file regression in `tests/golden/`.
- [ ] **B5 — DSP/Fusion/ML pure.** No `socket`/`requests`/`httpx`/`aiohttp`/`subprocess` in those packages. The lint-imports `forbidden` contracts enforce.
- [ ] **Sigma honesty.** `BearingReport.azimuth_sigma_deg` (if your code emits one) is derived from actual measurement, not a default. Cite the line.
- [ ] **`extra="forbid"` on every Pydantic model**, `frozen=True` where applicable.
- [ ] **`schema_version: SchemaVersionT`** (not `str`) on any new wire-format message (per ADR-012).
- [ ] **No `# noqa` / `# type: ignore` clusters** without inline comment explaining why.

---

## Ticket-prompt skeleton for Claude Code execution

If you (the executor) are going to hand the ticket to Claude Code:

```
TICKET <WS>-<NNN>: <title>

Read these first (in order):
1. <ARCHITECTURE.md §n>
2. <INTERFACES.md §n>
3. <prior ticket if any>
4. <salvage source if any>

Goal: <one-sentence goal>

Constraints (absolute):
- Files you may touch: <list>
- Files you may NOT touch: <list>
- Out of scope: <list>
- Stop conditions: <list>

Acceptance criteria — verify each before declaring done:
1. <runnable check>
2. <runnable check>
...

Paste test output + mypy + ruff + lint-imports into your reply.
Do NOT commit. Do NOT push. Hand the diff back for council review.
```

Use this prompt verbatim. The constraints are the project's drift mitigation. Padding them out with niceties weakens them.
