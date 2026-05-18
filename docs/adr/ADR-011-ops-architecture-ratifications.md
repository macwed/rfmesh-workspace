# ADR-011 — Ratify four lead decisions from `docs/design/ops-architecture.md`

- **Status:** ACCEPTED (2026-05-17, lead-Opus + Maciej).
- **Author:** lead-Opus.
- **Decision scope:** four small, independent decisions surfaced by the
  architect subagent's ops-architecture design pass and by the WS-B-006
  ticket drafter. Each unblocks one or more builders in the next batch.
- **Amends:** `pyproject.toml` `[tool.importlinter.contracts]`; adds
  `pyyaml` to `rfmesh-ml` runtime deps; adds `types-PyYAML` to workspace
  dev deps. No contracts package edits; `SCHEMA_VERSION` unchanged.
- **Provenance:**
  - `docs/design/ops-architecture.md` §1.3, §1.4, §3.1, §4 (commit
    `f8d4d0d`).
  - `docs/tickets/WS-B-006-threat-library.md` §Stop conditions §A
    (commit `f8d4d0d`).
  - Lead recommendation pasted in `docs/SPRINT_LOG.md` §"Open lead
    decisions for ratification" (commit `90ab700`).

---

## Context — why these four, why together

The architect subagent's design pass (`docs/design/ops-architecture.md`)
specifies the public API + dependency graph for the four remaining
packages (`rfmesh-cot`, `rfmesh-node`, `rfmesh-ops`, `apps/demo-replay`).
Four decisions in that document need lead ratification before builder
subagents can be spawned. Bundling them into one ADR (rather than four
separate ADRs) is justified by:

- Each decision is small (one paragraph of rationale, one line of code
  change or one /uvadd-request).
- All four are gates on the **same next builder batch** (cot/node/ops/
  B-006). Splitting into four ADRs would serialise work that is already
  paid-for by the architect's design pass.
- None of them touch `rfmesh-contracts`. Invariant B1 untouched.
- None of them remove a docs/ADVANTAGES.md advantage. Council protocol §1 not
  triggered.

The four are catalogued as D1-D4 below. Each is binding once this ADR
is accepted.

---

## D1 — `import-linter` relaxation: `rfmesh-node` is the composition root

### Decision

Remove `rfmesh_node` from the `Star dependency` `independence` contract's
`modules` list. Add a `forbidden` contract naming `rfmesh_ops` as the
single sibling `rfmesh-node` must **not** import (nodes do not run
dashboards). Per `docs/design/ops-architecture.md` §1.3 and §4.1.

### Rationale

The `independence` contract's *intent* (`ARCHITECTURE.md` §3) is to
prevent two estimator / fuser / CoT packages from coupling their
internals. It is not intended to forbid a composition root from
assembling the pieces. `rfmesh-node` is exactly that composition root:
its asyncio service wires `Receiver` → `BearingEstimator` → `Bearer` →
`Fuser` → `CoT publisher` from concrete classes that satisfy the
abstract Protocols in `rfmesh-contracts`. Refusing the import means
either inverting control (each package would have to register itself
into the runtime, mutating runtime imports into framework-style
plumbing — strictly worse) or duplicating Protocol shims everywhere.

The new explicit `forbidden` clause naming `rfmesh_ops` makes the
single composition-root invariant visible (`node` does not pull
`ops`) without leaving a hole in the star.

### Applies to

`pyproject.toml`: the `Star dependency` contract is renamed and
`rfmesh_node` removed from `modules`. A new contract `Node runtime:
composition root may import these sibling packages` (positive name,
single `forbidden_modules = ["rfmesh_ops"]`) is added. No other
contract changes.

### Why not

- *Keep `rfmesh-node` in the `independence` list and ignore the
  resulting violations:* `ignore_imports` was considered for `rfmesh_node
  -> rfmesh_*` but that would require listing every sibling individually
  and updating the list with each new Protocol impl. The carve-out (a
  named composition root) is cleaner.
- *Move composition into a new top-level `apps/rfmesh-node` package:*
  inverts the package ownership table in `WORKSTREAMS.md`; bigger blast
  radius than the contract relaxation. Not pursued.

---

## D2 — `rfmesh-ops` carve-out for `rfmesh_dsp.l2_null_steering`

### Decision

Allow `rfmesh-ops` to import exactly one module from `rfmesh-dsp`:
`rfmesh_dsp.l2_null_steering` (per ADR-008 §D7 / ADR-010). Express the
carve-out as a `forbidden` contract listing every `rfmesh_dsp.*`
submodule **except** `l2_null_steering`. Per
`docs/design/ops-architecture.md` §1.4 and §4.1.

### Rationale

The Advantage #4 dashboard panel renders a receive-pattern polar plot
using `compute_receive_pattern(w, geometry)`. The math (steering-vector
sweep + `|w^H a(θ)|²`) is pure numpy; the inputs (`w`, array geometry)
are already on the dashboard side (the dashboard knows the array from
`NodeStatus.active_capabilities` + scenario; `w` ships as a debug
payload).

Option A (chosen): the pattern compute lives where the math lives
(`rfmesh-dsp`). One named carve-out, reviewer-auditable.

Option B (rejected): duplicate `compute_receive_pattern` into a tiny
`rfmesh_ops.patterns` module. Duplicates pure math; if
`compute_receive_pattern` evolves (e.g. UCA support per ADR-010 follow-up),
the two copies drift silently.

The carve-out is the smaller violation of the star principle.

### Applies to

`pyproject.toml`: a new contract `Ops imports only the null-steering
pattern from DSP` of type `forbidden`, `source_modules = ["rfmesh_ops"]`,
`forbidden_modules = ["rfmesh_dsp.l1", "rfmesh_dsp.l2_music",
"rfmesh_dsp.l2_mvdr", "rfmesh_dsp.steering", "rfmesh_dsp.spectrum",
"rfmesh_dsp.rssi"]`. The omitted submodule (`rfmesh_dsp.l2_null_steering`)
is the allowed import.

### Why not

- *Block the import and have the dashboard ship pre-rendered pattern
  PNGs:* defeats the live-demo advantage; the panel must update as `w`
  changes during the operator's "engage null" interaction.
- *Move `compute_receive_pattern` into `rfmesh-contracts`:* the
  function depends on numpy steering-vector primitives that already
  live in `rfmesh-dsp`; pulling them into contracts widens the frozen
  surface for no benefit.

---

## D3 — Dashboard rendering: matplotlib

### Decision

`rfmesh-ops` renders panels with **matplotlib** (`>= 3.8`) on an
interactive backend (`Qt5Agg`/`QtAgg` preferred, `TkAgg` fallback). Per
`docs/design/ops-architecture.md` §3.1.

### Rationale

The architect's trade-off table (§3.1) ranks matplotlib first on three
grounds simultaneously:

1. **Time-to-build**: 1-2 days vs 5-7 days for FastAPI+SPA. With ~3
   weeks remaining for everything not yet built (CoT, node, ops,
   demo-replay, hardware integration, rehearsal), the saved 3-5 days
   buys rehearsal time. ADR-009's percentage-display refinement and
   ADR-008 §D8's 20 dB null-depth cap are both rehearsal-driven; the
   time matters.
2. **Jury demographic**: Belgian Defence RF/EW judges read matplotlib
   plots in their day jobs. A signal-processing-flavour dashboard that
   looks like a competent researcher's working notebook lands better
   than a polished SPA.
3. **Replay parity**: `apps/demo-replay` reuses the same dashboard. In-
   process matplotlib + `DashboardClient.in_process` queue means
   replay can drive the dashboard without sockets.

The cost (no remote-view capability without VNC/screen-sharing) is
acceptable: the dashboard runs on the operator laptop on stage, the
jury sees the projected screen, ATAK on a separate tablet (CoT TCP)
is the remote-view path.

### Applies to

`rfmesh-ops`'s eventual `pyproject.toml` `[project] dependencies` will
list `matplotlib >= 3.8`. Backend choice is workstream freedom; the
builder picks `QtAgg` (modern) or `TkAgg` (no extra dep) and documents
the choice in the package README.

### Why not

- *FastAPI + SPA (React/Vue)*: longer build, higher polish, but risk
  of half-finished if we run out of time. The architect's §5 R1 names
  this risk explicitly; mitigation is to pick matplotlib first.
- *Bokeh / Dash / Plotly*: middle ground in build time; medium-high
  polish but adds web-stack risk without web-stack benefits (the
  dashboard runs on the operator laptop, not a kiosk).
- *PyQt + custom widgets*: highest polish, longest build; only worth
  it for a productised dashboard, not a demo.

### Open follow-up

The architect's §5 R1 names a Reviewer-subagent re-check at week 1 of
dashboard work: if the partial build looks visibly inadequate next to
the data, the choice is revisited then. **This ADR commits to
matplotlib as the v1.0 default; the re-check is the safety valve, not
a deferral.**

---

## D4 — `pyyaml` /uvadd-request approval (and `types-PyYAML` dev dep)

### Decision

`uv add --package rfmesh-ml "pyyaml>=6.0"` and `uv add --dev
"types-PyYAML"` at the workspace root, executed by the lead. Per
`docs/tickets/WS-B-006-threat-library.md` Stop conditions §A and
`AGENTS.md` §3 (lead-only `uv add`).

### Rationale

WS-B-006 (threat-class enrichment layer) loads operator-extensible YAML
profiles under `packages/rfmesh-ml/threats/profiles/`. YAML is the
operator-facing artefact (Advantage #5 — "open the YAML on a tablet
next to ATAK"); JSON or TOML would not deliver the same human-edit
ergonomics for nested rule lists.

- `pyyaml` is the de-facto standard YAML library on PyPI; ~few MB,
  MIT, mature, widely deployed.
- The loader uses `yaml.safe_load` exclusively (never `yaml.load`); the
  arbitrary-Python-object class of vulnerabilities is blocked by
  `SafeLoader`. WS-B-006 acceptance criterion 8 binds this.
- `types-PyYAML` is needed for mypy strict; standard companion.

### Invariants check

- **B1 (contracts frozen)**: untouched. `pyyaml` consumed only by
  `rfmesh-ml`; contracts unchanged.
- **B3 (no silent fallbacks)**: WS-B-006's `ThreatProfileError` raises
  loudly on malformed/schema-invalid YAML; honesty gate preserved.
- **B5 (DSP/Fusion pure)**: untouched. `pyyaml` is `rfmesh-ml`'s dep,
  not `rfmesh-dsp`/`rfmesh-fusion`. Loading "vendor data tables at
  import" is the documented exception in Invariant B5.

### Applies to

- Workspace root: `uv add --package rfmesh-ml "pyyaml>=6.0"` (mutates
  `packages/rfmesh-ml/pyproject.toml` and `uv.lock` transactionally).
- Workspace root: `uv add --dev "types-PyYAML"` (mutates root
  `pyproject.toml` `[dependency-groups] dev` and `uv.lock`).

WS-B-006 builder uses `import yaml` (the standard import path
`pyyaml` exposes) and `yaml.safe_load` exclusively.

### Why not

- *Convert profiles to JSON or TOML*: human-edit ergonomics worse;
  nested rule lists in TOML are awkward and in JSON have no comment
  support. The "operator opens the YAML next to ATAK" demo line is the
  Advantage #5 hook; switching format weakens it.
- *Vendor a tiny YAML parser*: `pyyaml`'s SafeLoader is correct + fast
  + audited; reinventing it is unjustified.

---

## Consequences

### Immediate

- Four builders in the next batch are unblocked:
  - **WS-B-006** (threat library): D4 unblocks.
  - **WS-CD-node** builder: D1 unblocks the composition-root imports.
  - **WS-CD-ops** builder: D2 unblocks the null-steering import; D3
    fixes the rendering stack.
  - **WS-CD-cot** builder: unblocked structurally (no D1-D4 gate).
- Builders run **in parallel** with the architect doc (`docs/design/ops-
  architecture.md`) as their primary spec. WS-B-006 has its own
  ticket; cot/node/ops builders work directly from the design doc
  per the council pattern (architect's doc is the binding
  package-shape source).

### Downstream

- D1 makes `rfmesh-node` the only sibling-importing package in the
  workspace. The Reviewer subagent's PR sweep audits this: any *other*
  package found importing from siblings is a contract violation.
- D2 establishes the pattern for future single-module carve-outs (if
  any). The convention is: declare the carve-out by listing every
  forbidden sibling submodule **except** the allowed one, so the
  exception is visible.
- D3 commits the workspace to matplotlib as the live-render path. If
  D3 is reversed at the architect's §5 R1 re-check (week 1 of
  dashboard work), the corresponding ADR-NNN records the reversal and
  reads `docs/design/ops-architecture.md` §3.1 alternatives.
- D4 adds the workspace's first new runtime dep since the lead-Opus
  handoff. Future /uvadd-requests follow the same pattern: builder
  writes scratchpad, lead reads, lead runs `uv add`, lead commits.

### Tests / verification

- `uv run lint-imports` is green after the contract changes (D1 + D2).
  If it fails, the lead writes the failure into the ADR's
  Consequences and either revises the contracts or rejects the
  builder diff.
- `uv run pytest -m "not hardware"` is green (no test code touches
  `pyyaml` until WS-B-006 lands). D4's effect on the test suite is
  zero until the next commit.
- `mypy --strict` is green (D4's `types-PyYAML` resolves stub
  resolution for the future WS-B-006 imports).

---

## Out of scope

- The `Bearer` transport's `aiohttp` runtime dep, `msgpack` runtime
  dep, and `pytak` runtime dep are **not** ratified here. They are
  per-package adds (the cot/node builders will write their own
  /uvadd-requests when they start). Lead ratifies inline at that
  point.
- The dashboard's backend (`QtAgg` vs `TkAgg`) is workstream freedom.
- The `Scenario` YAML schema for `apps/demo-replay` is a separate
  ticket; ADR-011 only ratifies the architect doc's package APIs and
  the four gates above.
- Any contract change (e.g. adding `EmitterClass` members, adding
  `BearingReport` fields) remains a separate ADR + `SCHEMA_VERSION`
  bump per AGENTS.md §1 Invariant B1.

---

## Sign-off

Council review skipped per AGENTS.md §1 — all four decisions are scoped
small, do not touch contracts, do not remove a docs/ADVANTAGES.md advantage.
Lead-Opus accepts; Maciej accepts ("I accept your Recommendation,
ratify all four", 2026-05-17). ADR is therefore ACCEPTED on first
read.
