# AGENTS.md — Rules for AI Agents Working on rfmesh

**Status:** binding for every AI agent (mid-level Opus, Claude Code, future
agents). Edits by lead only.
**Audience:** the Opus workstream owners (A, B, C+D) and the Claude Code
agents they direct. Maciej-as-operator reads this to know what to expect.
**Date:** 2026-05-14 (last amended 2026-05-18 — Seven Binding Invariants rewrite per the cleanup of an operator-local handoff doc).

This file is read at the start of every agent session and consulted when in
doubt. It is short on purpose. Anthropic's published guidance is that ~150
binding instructions is the practical ceiling for reliable adherence; this
file fits inside that budget.

If a rule here is ever in tension with another document, **this file wins**
for agent behaviour; the conflict is then itself a bug to raise with the
lead.

> **History note (2026-05-18).** Earlier revisions of this file deferred
> to an operator-local doc (`docs/ADVANTAGES.md` / `AGENTS.md` §1, gitignored)
> for the relaxed operational scaffolds and the seven binding invariants
> B1-B7. That file is being retired. Its load-bearing content lives in:
> §1 below (the Seven Binding Invariants — same B-numbering), §3 +
> §7 here (relaxed operational scaffolds), and `docs/ADVANTAGES.md`
> (the eight architectural pitch advantages). No cross-repo references
> to the operator-local doc remain in tracked files.

---

## §1 The Seven Binding Invariants (B1-B7)

These are absolute. No ticket may instruct an agent to violate them. An
agent that is asked to violate one refuses, says why, and stops. The
B-numbering is referenced verbatim across the codebase (commit messages,
council reviews, ADRs, ticket "files-you-may-NOT-touch" lists).

**B1 — Contracts are frozen.**
`packages/rfmesh-contracts/src/rfmesh_contracts/**` is editable only by the
lead, only via an accepted ADR, only paired with a `SCHEMA_VERSION` bump.
A workstream agent who believes a contract change is needed:

1. Writes a CHANGE-REQUEST ADR under `docs/adr/` with status `PROPOSED`.
2. Names the contract, the change, and the motivating ticket.
3. **Stops.** Does not begin work that assumes the change.
4. Lead reviews; if accepted, lead bumps `SCHEMA_VERSION` and broadcasts.

This is not bureaucracy — every cross-workstream interface depends on
these types, and an unannounced change cascades silently. ADR-012 (the
`Literal[SCHEMA_VERSION]` tripwire) is the mypy-side enforcement; B1 is
the human-side enforcement.

**B2 — Honest sigma on every `BearingReport`.**
`azimuth_sigma_deg` is the system's load-bearing weight: fusion uses it
as inverse-variance, the confidence ellipse derives from it, the
operator's trust in the dashboard rests on it. Sigma honesty is
**empirical**, not prescribable — every `BearingEstimator` is gated by
a sigma-honesty test (e.g.
`packages/rfmesh-dsp/tests/test_sigma_honesty.py`) that asserts the
median claimed sigma against Monte-Carlo ground truth at SNR ∈
{10, 20, 30} dB within a **±20 % band**. An estimator that ships an
over-optimistic sigma silently poisons every fix it touches. An RF/EW
jury catches this in 30 seconds. This is the single most load-bearing
technical invariant in the project.

**B3 — No silent fallbacks.**
Wherever the system could fail silently, it must fail loudly. Concrete
applications:

- A `Receiver.read(n)` that cannot deliver `n` samples raises; it does not
  pad with zeros, does not return a short buffer.
- A `CoherentReceiver` asked for `read_coherent` before a successful
  `calibrate()` either refuses or returns data with `is_calibrated=False`,
  and the consumer (L2 DSP) refuses to emit bearings from it.
- A `BearingEstimator` that cannot produce a bearing this block returns
  `None` (with a populated `last_refusal_reason`, per E1), never a
  fabricated guess.
- A `NodeConfig` declaring a capability the hardware cannot meet is a
  fatal startup error, never a silent downgrade.
- A YAML config with a typo'd key is rejected at parse time
  (`extra="forbid"` on every Pydantic model).
- A `BearingReport` with a dishonest `azimuth_sigma_deg` (suspiciously
  small for the SNR, or a magic constant) is caught in review.

**B4 — Demo honesty payload.**
Every `FixEvent` carries covariance, 95% ellipse, GDOP, residuals, and
method. The ops dashboard renders all of them. The system
**self-diagnoses** on screen (high-residual nodes highlighted, GDOP
shown, refusals surfaced). This is the difference between
"engineering" and "magic" in front of the jury — and the difference is
what wins. A `FixEvent` that arrives without its full honesty payload
is a contract violation (caught by the Pydantic validators) and a
review-blocker.

**B5 — `rfmesh-dsp` and `rfmesh-fusion` are pure.**
No network calls, no file I/O (beyond loading vendor data tables at
import), no subprocess, no SDR access. Both must run end-to-end in pytest
on a CI runner with no hardware. The whole simulator-first development
model depends on this; an agent that adds a `requests.get(...)` to a DSP
function has broken the model. (Enforced statically by `import-linter`
contracts in `pyproject.toml`.)

**B6 — Software agents do software. Hardware is the operator's domain.**
Mast design, cable routing, antenna mounting, soldering, polarisation,
deployment ergonomics — out of scope. If a ticket implies a physical
assumption (e.g. *"antenna is vertical"*), treat it as given and do not
propose changes. The agent that designs a CAD bracket is overstepping.
Detailed scope boundary in §2 of this file.

**B7 — The eight architectural advantages are binding pitch content.**
`docs/ADVANTAGES.md` enumerates the eight architectural advantages this
project competes on. Any agent decision that *removes* one of them
needs a strong reason and must bubble up to the lead via an ADR proposal.
Tightening or extending an advantage is fine; weakening or eliminating
one is not.

### Workspace Disciplines (alongside the invariants)

The following are workspace conventions that operate at the same severity
as the invariants but are not numbered into the B-list because they are
mechanically enforced (CI / lint-imports / mypy) rather than reviewed by
humans:

- **WD-1 — Cross-package imports resolve to `rfmesh_contracts` or own package.** Every `import` from a sibling package is a B2 architectural-coupling violation. `import-linter` enforces six contracts (Star independence + node composition-root carve-out + ops null-steering carve-out + DSP/Fusion/ML purity). If you find yourself wanting to import from a sibling, stop and raise a SCRATCHPAD note (see §6).
- **WD-2 — Every new function in `rfmesh-dsp` has a golden-file test in `tests/golden/`.** DSP is the layer most prone to silent numeric regression. Golden files are small (~100 kB), checked in, generated by the workstream's own simulator fixture. No exceptions for "trivial" changes. Closes one of the B4 surfaces (the testing side of demo honesty).

---

## §2 The scope boundary — what agents do and do not touch

Restated from `WORKSTREAMS.md` §5 because it is the most easily forgotten
boundary:

**Software agents work on software.** Algorithms, data structures, tests,
APIs, configuration handling, CI, documentation of code.

**Software agents do not work on:**

- Physical hardware: mounts, cables, soldering, 3D-printing, antenna
  orientation, mast design, deployment ergonomics.
- RF physics decisions already made: polarization, antenna selection,
  band selection.
- The deployment site, the event logistics, hardware procurement.
- Maciej's operational choices.

An agent who finds a ticket that touches these areas refuses scope and
asks the lead to re-scope. An agent who finds physical-world assumptions
in a ticket (*"assume the antenna is vertical"*) treats them as facts and
does not second-guess.

The exception is when a physical fact has a software consequence that the
agent **must** respect — those facts live in `INHERITED_CONTEXT.md` §1 and
the agent reads them. Example: MG996R clones have unknown pulse range, so
DSP code never hard-codes pulse-to-angle conversion. That is in scope.
"What pulse range did Maciej actually find for axis 2?" is not.

---

## §3 Allowed and forbidden commands

Workstream agents who write tickets and Claude Code agents who execute
them operate within this allowlist. A command not on the allow list is
escalated.

### Always allowed

- `uv sync` — sync the workspace lockfile.
- `uv run pytest` — run tests, optionally with `-m "not hardware"`,
  `--cov`, `-k <pattern>`, package-scoped paths.
- `uv run ruff check`, `uv run ruff format`, `uv run mypy <package>`.
- `git status`, `git diff`, `git log`, `git branch`, `git checkout <branch>`,
  `git add <file>`, `git commit -m "..."`, `git rebase origin/main`.
- `python -c "..."` for one-off introspection inside the agent's
  workstream packages.

### Allowed with restrictions

- `git push` — lead-Opus pushes directly to `main`. Builder/council
  subagents push to whatever branch the runtime opened them on (may be
  an isolated worktree branch); lead merges back into `main`. See §7
  for the operative model.
- File create / edit / delete — within the ticket's `Files-you-may-touch`
  list. Lead is unconstrained by `Files-you-may-touch`; subagents are.

### Forbidden without explicit lead approval

- `uv add <pkg>` — adding a runtime dependency. Subagent writes a
  `/uvadd-request` (see §5); lead approves and runs `uv add` at the
  workspace root.
- `git push --force`, `git push --no-verify`, `git commit --no-verify`,
  anything that bypasses pre-commit / CI. **Still binding for the
  lead.** Force-push to `main` is forbidden even for the lead — it
  rewrites published history and breaks any agent that pulled the
  prior tip.
- `rm -rf`, mass deletes, anything against the inherited firmware or
  servo trees without a salvage-amendment ticket.
- Modifying `packages/rfmesh-contracts/src/**` — see Invariant B1.
  Modifications by lead require an accepted ADR + `SCHEMA_VERSION`
  bump; subagents never touch this tree.
- Modifying `version.py` outside the contracts package or anywhere else
  that pretends to bump `SCHEMA_VERSION`.

> **Note (2026-05-17):** the pre-handoff rule "*`git merge` to `main` —
> only the lead merges to main*" is removed because the lead **is** the
> active conversation now (per `AGENTS.md` §1).
> The merge gate is operative automatically. The hook
> `.claude/hooks/block-forbidden-commands.sh` was relaxed to match.

---

## §3.5 Repo conventions

Workspace-wide conventions that apply across every package. These exist
because uniformity prevents subtle cross-workstream conflicts (mypy
duplicate-module errors, pytest collection clashes) that are expensive to
debug after the fact. Decided by the lead via ADR.

- **No `__init__.py` under any `tests/` directory.** Test directories are
  *not* importable packages — pytest discovers tests by file name, not by
  module import. Adding `__init__.py` makes `tests/` a package, which
  collides across workstreams whenever two packages have a file with the
  same name (e.g. `packages/rfmesh-sdr/tests/test_foo.py` and
  `packages/rfmesh-dsp/tests/test_foo.py`). Standard pytest practice, and
  the convention the prior repo `macwed/rf-mesh` followed. See ADR-006.
- Workstreams **may** create `tests/conftest.py` for shared fixtures
  within their own test tree; that file is not a package marker.

---

## §4 Ticket format

Tickets are the unit of work the developer pastes into Claude Code. Opus
workstream owners produce them; the developer (Maciej) executes them and
returns diffs for review. The format below is non-negotiable because the
"Files you may NOT touch" and "Out of scope" fields are the strongest
drift mitigation available.

```markdown
# TICKET <WS>-<NNN>: <one-line title>

## Goal (one sentence)
What the ticket accomplishes, in human English. No noun-phrase titles —
verbs and outcomes.

## Context (links only, not content)
- Contracts touched (read-only): rfmesh_contracts.<module>.<Type>
- Architecture references: ARCHITECTURE.md §<n>, INTERFACES.md §<n>
- Inherited context: INHERITED_CONTEXT.md §<n> if applicable
- Salvage: SALVAGE_AUDIT.md row for the file being ported, if applicable
- Prior tickets this depends on: <WS>-<NNN>

## Acceptance criteria
Concrete, runnable. Each line is something `just verify` or a specific
command will check.

1. `uv run pytest <path> -v` passes (list the tests added).
2. Golden test <name> in `tests/golden/` passes within <tolerance>.
3. Property test in `tests/property/<name>.py` passes with default
   hypothesis budget.
4. `uv run mypy packages/<pkg>` clean.
5. `uv run ruff check packages/<pkg>` clean.
6. The new code is pure / hardware-free / contracts-only (per the
   applicable Invariants).

## Out of scope (explicit non-goals)
- Do NOT add fields to <contract type>.
- Do NOT modify <sibling file> — that is ticket <WS>-<NNN>'s scope.
- Do NOT introduce new runtime dependencies — see §3.

## Files you may touch
- packages/<pkg>/src/<pkg>/<file>.py        (create | modify)
- packages/<pkg>/tests/test_<thing>.py      (create)

## Files you may NOT touch
- packages/rfmesh-contracts/**              (FROZEN — Invariant B1)
- Anything outside packages/<pkg>/
- <sibling file>                            (other ticket's scope)

## Stop conditions
- Stop after producing the diff. Do not auto-commit or push.
- Paste the test output and `mypy`/`ruff` output into the conversation.
- If a contract change appears necessary, stop and write
  docs/adr/ADR-NNN-<short>.md instead (status: PROPOSED) — do NOT proceed.
- If a physical-hardware assumption is needed, treat it as given and note
  it in the diff; do not propose changes to it.
```

The two fields that carry the most weight are **Out of scope** and **Files
you may NOT touch.** Multiple practitioners running parallel coding agents
converge on the same finding: negative constraints prevent more drift than
positive specifications.

---

## §5 Verification protocol

Before declaring a ticket done, the executing agent runs:

```bash
just verify
```

which is defined at the workspace root as:

```
verify: lint type test

lint:
    uv run ruff check .
    uv run ruff format --check .

type:
    uv run mypy packages/

test:
    uv run pytest -m "not hardware"

ticket-verify package:
    uv run pytest packages/{{package}}
    uv run mypy packages/{{package}}
    uv run ruff check packages/{{package}}
```

The agent **pastes the output** into the conversation when declaring done.
The reviewing Opus then checks the four gates: tests pass, types pass,
ruff passes, no contract violation in the diff. Only after the workstream
review passes does the work go to the lead.

For a new runtime dependency: write a `/uvadd-request` in the workstream's
scratchpad with name, version range, runtime vs dev classification,
rationale, and which Invariants it does not threaten. Lead approves or
denies; on approve, lead runs `uv add` at the workspace root.

---

## §6 Escalation — when to stop and surface

An agent stops and writes a SCRATCHPAD entry under
`.claude/scratchpad/<workstream>-<date>.md` when any of the following
applies. The lead reviews scratchpads.

- The ticket appears to require a contract change. (Invariant B1.)
- The ticket appears to require cross-workstream code changes.
  (Import-discipline rule WD-1.)
- A physical / hardware assumption is missing or ambiguous. (See §2 —
  agents do not resolve these themselves.)
- A new runtime dependency seems necessary. (See §5.)
- The acceptance criteria appear unreachable as stated (e.g. golden file
  does not exist, simulator behaviour seems wrong, expected tolerance
  cannot be met without breaking something else).
- An apparent bug surfaces in salvaged code that was not flagged in
  `SALVAGE_AUDIT.md` or `INHERITED_CONTEXT.md` §5.
- Any instruction in the ticket appears to conflict with this file
  (`AGENTS.md`), `ARCHITECTURE.md`, or `INTERFACES.md`.

The scratchpad entry names: what was requested, what blocked it, what the
agent recommends, and what (if anything) is provisionally produced. The
agent does **not** push through a blocker silently or with a workaround.

Pushing back on a misleading instruction *with evidence* is a quality
gate, not insubordination — this is inherited from the prior project's
two real bug catches (see `INHERITED_CONTEXT.md` §5) and is preserved
deliberately.

---

## §7 Git workflow

> **Updated 2026-05-17** to reflect the lead-Opus model per
> `AGENTS.md` §1. The pre-handoff
> three-worktree-per-workstream model (ws-a / ws-b / ws-cd) and the
> "morning sync ritual" are obsolete and have been removed. The
> simplified workflow:

- Lead-Opus operates on `main` directly. Commits and pushes land on
  `origin/main` as the lead executes — no PR ceremony.
- Builder/council subagents are spawned by the lead and, depending on
  the agent runtime, may operate either in the lead's `main` checkout
  or in an automatically-created isolated worktree (under
  `.claude/worktrees/`). When a subagent operates in an isolated
  worktree it pushes to its own branch; the lead fetches and
  `git merge --no-ff` into `main`. The hook
  `.claude/hooks/block-forbidden-commands.sh` permits this; force-push
  and `--no-verify` remain blocked (see §3).
- `.claude/worktrees/` stays in `.gitignore` — worktree contents do not
  appear as untracked files in the main checkout.

---

## §8 What this file does not cover

This file deliberately does not cover:

- Algorithms, design patterns, or *how* to write code for a given task.
  Those are workstream concerns and ticket details.
- The contents of any specific contract — see `INTERFACES.md`.
- The contents of any specific workstream's plan — see `WORKSTREAMS.md`
  and the workstream's bootstrap.
- Physical hardware. See §2.

This file covers **agent behaviour**. The Seven Binding Invariants, the scope
boundary, the command allowlist, the ticket format, the verification
protocol, the escalation rule, the git workflow. That is the whole job
of `AGENTS.md`.
