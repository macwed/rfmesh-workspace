# rfmesh — Work Split + Coordination Protocol

**Last refreshed:** 2026-05-18 at `ec8740c`.

This document is the **rules of engagement** for the active project split. Owners + boundaries + git workflow + escalation. Read once. Reference when a coordination question arises.

---

## §1 Active owners

| Owner | Role | Primary tracks | Commit destination |
|---|---|---|---|
| **Maciej** (`@macwed`) | Lead architect + project owner | Hardware bench, Phase C, firmware flash, RF domain decisions, merge to `main` | Direct push allowed; **only person who merges PRs** |
| **Lead-Opus** (Claude Code) | Lead execution agent | Software polish, integration, simulator, firmware source, council orchestration | Direct push to `main` via `lead-Opus` session; lands work autonomously |
| **Friend** (handle TBD) | Code collaborator, broad coverage | WS-A-005 + WS-A-006 (RTLSDR + servo salvage) → then claim from `BACKLOG.md` | Branch + PR; Maciej merges |

The three-way split is **disjoint** by design — no two owners touch the same files in the same week. If a coordination point arises (cross-track decision), Maciej is the tiebreaker.

---

## §2 Track boundaries — what's mine vs yours

### Friend's track (until WS-A-006 merges)

**You may touch:**
- `packages/rfmesh-sdr/src/rfmesh_sdr/devices/rtlsdr.py` (create — WS-A-005)
- `packages/rfmesh-sdr/src/rfmesh_sdr/devices/__init__.py` (create or modify)
- `packages/rfmesh-sdr/src/rfmesh_sdr/_iq_helpers.py` (create — single-home `_to_complex64`)
- `packages/rfmesh-sdr/tests/devices/test_rtlsdr.py` (create)
- `packages/rfmesh-servo/src/rfmesh_servo/*.py` (create — WS-A-006, lifts the 6 modules from prior repo)
- `packages/rfmesh-servo/tests/*.py` (create)
- `pyproject.toml` — only if you need to add `pyserial` (write `/uvadd-request` first; see `AGENTS.md` §3)
- Your branch only. Never push to `main`.

**You may NOT touch:**
- `packages/rfmesh-contracts/src/` (Invariant 1 — contracts frozen, lead-only)
- Anything outside `packages/rfmesh-sdr/` or `packages/rfmesh-servo/` for your tickets
- `firmware/` (Maciej + lead-Opus track)
- `docs/demo/` (lead-Opus track)
- `apps/demo-replay/` (lead-Opus track)
- `packages/rfmesh-ops/` (lead-Opus track)

### Lead-Opus track

- Tier E (E1-E5): `docs/demo/`, `packages/rfmesh-ops/panels/`, `docs/adr/`
- Multi-node simulator: `apps/demo-replay/`, `scenarios/three_node_trench.yaml`
- G7 polar panel: `packages/rfmesh-ops/panels/`
- WS-A-007 firmware source: `firmware/main/*.c`, `firmware/main/*.h`, `firmware/sdkconfig.defaults`
- WS-A-007 LoRa beacon: `firmware/lora-beacon/` (new)
- Phase C follow-ups C3/C4/C5 after Maciej captures `.iqx`

### Maciej track

- `docs/hardware/` — bench checklists, tutorials, Phase C content
- `docs/phase-c-report/` — bench logs, polar plots, `.iqx` captures
- `firmware/` flashing + smoke testing (sources authored by lead-Opus)
- Merge gate on every PR
- Architecture decisions (ADR proposals)
- Site selection, hardware procurement, deployment ergonomics

---

## §3 Git workflow

### Branch naming

- `feature/<short-name>` — new feature or ticket
- `bugfix/<short-name>` — bug fix
- `docs/<short-name>` — docs-only change
- `refactor/<short-name>` — refactor without behaviour change
- `hardware/<short-name>` — firmware / bench tooling

### Per-PR checklist (mirrors `.github/PULL_REQUEST_TEMPLATE.md`)

Before opening:

- [ ] `just verify` is green locally
- [ ] No contract touched (or ADR proposal attached if a contract change is genuinely needed)
- [ ] No silent fallbacks introduced (Invariant 4)
- [ ] Test coverage matches the touched code (golden test for new DSP per Invariant 3)
- [ ] No `# noqa` / `# type: ignore` clusters without inline justification
- [ ] PR description follows the template

After opening:

- [ ] Lead-Opus invokes 4 council reviewers in parallel
- [ ] Address every BLOCK; push fixes to the same branch
- [ ] 4× APPROVE → ping `@macwed` for merge

### Commit message format

Conventional Commits style (per the existing log on `main`):

```
<type>(<scope>): <short subject ≤ 70 chars>

<body — wrap at 80 chars; explain WHY, the WHAT is in the diff>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>  # if Claude wrote the code
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`. Scope: package name or feature area.

### Force-push policy

**Never.** Not on `main`, not on shared branches. If you need to clean up local commits before opening a PR, `git rebase -i` is fine on your own branch *before* the first push. Once pushed, forward-only.

---

## §4 Council protocol — the four reviewers

Every non-trivial PR (anything beyond a typo fix) goes through **4-agent council review** before merge. The agents are defined at `.claude/agents/*.yaml` and invoked by Lead-Opus.

### The four lenses

1. **architect** — invariants B1-B7, contract integrity, ADR coverage, dep graph
2. **code-reviewer** — sigma honesty, no silent fallbacks, dead code, test gaps
3. **rf-dsp-specialist** — algorithmic correctness, sigma mechanism, jury edge cases (only on DSP-touching PRs)
4. **demo-integrity** — slide↔code parity, UI caps, contingencies (only on demo-touching PRs)

### Sequencing

`CLAUDE.md` §"Council review protocol" describes them as **sequential**. In practice we run them in parallel and let any BLOCK gate the merge. Result is the same; throughput is 4× higher.

### Verdicts

- `APPROVE` — proceed
- `APPROVE + NOTE` — proceed; the NOTE is documentation, not a fix-required
- `APPROVE + RECOMMENDATION` — same as NOTE, demo-integrity-flavoured
- `BLOCK-WITH-REASON` — must fix and re-run from the blocking reviewer
- `REQUEST-ADR` — only architect can issue; pause work, write ADR, get sign-off, resume

### Who invokes the council

Lead-Opus orchestrates. Friend or Maciej can request a council pass on their own PR by tagging `@macwed` (who hands it off to Lead-Opus). Friend does **not** invoke subagents themselves — the lead account is the single orchestration point.

### Council output protocol

Reviewers post one-line findings inline on the PR. Format:

```
path:line: <emoji> <severity>: <problem>. <fix>.
```

Severity: 🔴 invariant violation / 🟡 risk / 🟢 informational. Lead-Opus aggregates the four verdicts into a single comment.

---

## §5 ADR protocol

Architecture Decision Records live at `docs/adr/ADR-<NNN>-<short-title>.md`. They are the **only** way to change contracts or override a binding invariant.

### When to write an ADR

- Any change to `packages/rfmesh-contracts/src/`
- Any SCHEMA_VERSION bump
- Removing or weakening any of the Five Invariants (`AGENTS.md` §1)
- A foundational decision that future agents will need to know the rationale of

### ADR lifecycle

1. **Friend** drafts `docs/adr/ADR-NNN-<short>.md` with `Status: PROPOSED`
2. **Council reviews** — architect first, others as relevant
3. **Maciej decides** — `Status: ACCEPTED` or `Status: REJECTED`
4. If ACCEPTED + contract change: **Lead-Opus** updates the contract + bumps `SCHEMA_VERSION` (via `version.py`) + propagates the bump to all `Literal[...]` type aliases
5. **Friend** rebases their feature work on the updated `main`

### Active ADRs

See `docs/adr/`. Currently ADR-004 through ADR-012 are accepted. ADR-001/002/003 are referenced in `WORKSTREAMS.md` but **not yet authored** — backfill is Tier E E2.

---

## §6 Communication channels

| Channel | Purpose | Latency expectation |
|---|---|---|
| **PR comments** | Day-to-day code discussion, council reviews, merge requests | <24 h |
| **GitHub issues** | Bug reports, feature proposals before they become tickets | <48 h |
| **Slack / Discord / Signal** (TBD between Friend + Maciej) | Real-time questions, blocker escalation, "is anyone there?" | Best-effort |
| **`.claude/scratchpad/`** | Agent-side notes when blocked mid-task; Maciej reviews periodically | Async; Maciej reviews at his pace |
| **`docs/SPRINT_LOG.md`** | Append-only history of what shipped + rationale | Updated per merge; never edited retroactively |

If Friend hits a question Maciej hasn't answered in 4 hours of working time, default to writing a `SCRATCHPAD` entry and either (a) continuing on a different ticket from `BACKLOG.md`, or (b) pausing entirely. Do not improvise on architectural ambiguity.

---

## §7 Conflict resolution

If two owners want the same file in the same week:
1. The one with the open PR closer to merge wins.
2. The other rebases or picks a different ticket.
3. If neither has a PR open: Maciej decides ownership for the week.

If Friend's work blocks Lead-Opus's work (e.g., Lead needs WS-A-005's `Receiver` shape to start an integration test): Friend's work goes first; Lead-Opus parallelises onto something independent until WS-A-005 lands.

If Lead-Opus's work blocks Friend (e.g., Friend needs an ADR Lead-Opus is drafting): Lead-Opus prioritises the ADR; Friend parallelises onto a different ticket.

**The escalation channel for stuck conflicts is Maciej.** He has final say.

---

## §8 What "done" means

A ticket is done when:

1. PR is open, council-reviewed (4× APPROVE / APPROVE+NOTE).
2. Maciej has merged.
3. `BACKLOG.md` row moved to "Closed" section with the merge commit ID.
4. `SPRINT_LOG.md` has an append entry noting the change, the rationale, and any non-obvious decisions.

A ticket is **not done** if:

- Tests are failing or skipped without a `@pytest.mark.skip` justification
- A reviewer's BLOCK has been ignored or papered over
- The implementation differs materially from the merged PR (drift between agreed plan and shipped code)
- A new contract was touched without an accepted ADR

---

## §9 Glossary

- **Lead-Opus** — the active Claude Code agent on `main` (currently Opus 4.7). Single agent, single session, operates on `main` directly per `HANDOFF_TO_CLAUDE_CODE_LEAD.md` §2.
- **Council subagents** — four reviewer agents at `.claude/agents/*.yaml`. Lead-Opus invokes them; humans don't.
- **Worktree** — `.claude/worktrees/` isolated git checkouts that Lead-Opus subagents may auto-create. Friend does not need to worry about these; they're orchestration internals.
- **Five Invariants** — `AGENTS.md` §1. Absolute. No ticket overrides them.
- **SCRATCHPAD** — `.claude/scratchpad/<workstream>-<date>.md`. Agent-side note when stuck. Surfaces to lead.
- **BoTH3** — Belgian Defence Counter-Jamming Challenge 2 demo target. Roughly 2026-06-12 (25 days from this doc's refresh date).

---

## §10 Re-reading this doc

When to re-read:
- You hit a coordination question and forgot the protocol
- A new owner joins (re-read with them)
- The split changes (Maciej revises the table in §1)

When **not** to re-read:
- Every day. It does not change every day.
