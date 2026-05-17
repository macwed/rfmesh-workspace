<!--
  rfmesh PR template. Mirror of the council protocol in docs/WORK-SPLIT.md §4.
  Keep this short — verbose templates get ignored.
-->

## Summary
<!-- 1-3 bullet points. Why this change. What it does NOT do. -->

-
-

## Ticket / context
<!-- Link the ticket file under docs/tickets/ or docs/tickets/skeletons/ if applicable. -->

- Ticket: `docs/tickets/<...>.md`
- Backlog ID: `<E1 | WS-A-005 | G7 | ...>`
- Related ADRs: `docs/adr/ADR-<NNN>.md` (if any)

## Invariant checklist
<!-- AGENTS.md §1 Five Invariants. Confirm each before merging. -->

- [ ] B1 — `packages/rfmesh-contracts/src/` untouched (or ADR cited)
- [ ] B2 — No cross-workstream imports (`lint-imports` 6 KEPT)
- [ ] B3 — No silent fallbacks; every `except` re-raises or logs+raises
- [ ] B4 — Golden test added for any new DSP function (`tests/golden/`)
- [ ] B5 — DSP/Fusion/ML purity preserved (no `socket`/`subprocess`/network)

## Verification

Paste the **literal output** of:

```
uv run pytest -m "not hardware"
uv run mypy packages
uv run ruff check
uv run lint-imports
```

<details>
<summary>pytest</summary>

```
<paste here>
```

</details>

<details>
<summary>mypy</summary>

```
<paste here>
```

</details>

<details>
<summary>ruff + lint-imports</summary>

```
<paste here>
```

</details>

## Council review

Tag the lead so review subagents can be invoked. The four reviewers run in parallel.

cc `@macwed` — please trigger council review.

Required for merge:
- [ ] architect APPROVE (or APPROVE+NOTE)
- [ ] code-reviewer APPROVE (or APPROVE+NOTE)
- [ ] rf-dsp-specialist APPROVE (only if touching `rfmesh-dsp` / `rfmesh-fusion`)
- [ ] demo-integrity APPROVE (only if touching `docs/demo/`, `rfmesh-ops`, `rfmesh-cot`)

Any `BLOCK` from a reviewer = fix in this branch and re-request review.

## Stop conditions hit during work?
<!-- Per docs/WORK-SPLIT.md §7. If yes, link the SCRATCHPAD entry. -->

- [ ] No stop condition hit
- [ ] Stop hit; SCRATCHPAD at `.claude/scratchpad/<...>.md`

## Out-of-scope deliberations
<!-- Per the ticket's "Out of scope" section. Anything you almost did but caught yourself? Worth noting here for the reviewer. -->

-
