# rfmesh — Collaborator Onboarding

**Welcome.** This document is your 30-minute cold start. Read it once cover to cover, then dive into your first ticket. Bookmark `BACKLOG.md` and `WORK-SPLIT.md` as your two daily reference points after today.

**Last refreshed:** 2026-05-18 against commit `ec8740c`.
**Lead:** Maciej (`macwed`, logmaciej@gmail.com), Poznań.
**Active lead-agent:** Claude Code Opus 4.7 on `main` directly per `AGENTS.md` §1.

---

## §1 What rfmesh is, in three sentences

A cooperative bearing mesh for RF emitter geolocation, targeting the Belgian Defence **BoTH3 Counter-Jamming Challenge 2** (jury date ≈ 25 days out). Distributed RTL-SDR nodes with directional antennas compute *bearings* locally, ship them via frozen contracts, a fusion server cross-fixes them into emitter positions with honest uncertainty, surfaced in ATAK as hostile-emitter markers with confidence ellipses. The dual-use angle: the same phase-coherent covariance matrix that subspace-DF estimates also synthesizes a spatial null on a co-channel jammer — anti-desense, not ECM.

If the marketing pitch is unclear, read `ARCHITECTURE.md` §0 next. If it's clear, skip to §2.

---

## §2 Read order — first 30 minutes

In order, do **not** skip:

1. **`ARCHITECTURE.md`** — the *why*. Binding invariants, capability layers, operating envelope.
2. **`INTERFACES.md`** — the *what*. Semantic dictionary of every contract type (mirrors `packages/rfmesh-contracts/` at `SCHEMA_VERSION = "1.1.0"`).
3. **`INHERITED_CONTEXT.md`** — knowledge from the prior project at `github.com/macwed/rf-mesh`. Especially **§3.1.1** (Phase C failure modes) and **§5** (regression anchors).
4. **`AGENTS.md`** — rules for agents (you + me + future). The **Seven Binding Invariants** are absolute.
5. **`docs/phase-c-report/findings.md`** — the load-bearing empirical result. L1 baseline is no longer hypothesis.

After those five, read your workstream-specific files (see §5 below).

---

## §3 State snapshot — what's done, what's running, what's blocked

**Shipped at `ec8740c`:**
- 9 packages: `rfmesh-contracts` (frozen at 1.1.0) + `sdr` + `dsp` + `ml` + `fusion` + `cot` + `node` + `ops` + `servo` (stub — to be filled). Plus `apps/demo-replay`.
- **481 pytest pass.** mypy strict + ruff + lint-imports (6 KEPT) all clean.
- 12 ADRs (`docs/adr/ADR-004…ADR-012`). 004 was the first because 001/002/003 were referenced in `WORKSTREAMS.md` but never authored — Tier E E2 backfills them.
- L1 amplitude-sweep DF + L2 MUSIC + L2 Capon + L2 null-steering + L3 modulation classifier.
- Stansfield + Gauss-Newton MLE fusion, GDOP, 95 % confidence ellipse, residuals + outlier flag.
- PyTAK CoT publisher, byte-exact canonical XML, WGS-84 polygon approximation of ellipse.
- Multi-bearer transport (Wi-Fi, LoRa stub, BothBearer with LoRa-down surfacing).
- 9-panel ops dashboard, 3 layouts (`TRENCH` for jury, `DEBUG` for bench, `MINIMAL` for screenshots).
- Demo-replay orchestrator + recorder, headless mode works (fix in `ec8740c`).
- Simulator: free-space, two-ray ground, multipath FIR, log-normal shadowing, composite.

**Live on Maciej's bench:**
- Phase C verdict **PASS** (Mast C, 14.9 dB front-back, 9° azimuth error). Two honest FAILs (Mast A multipath dominance at 650 m, Mast B co-channel at 2.2 km). See `docs/phase-c-report/findings.md`.
- 3 RTL-SDR + 3 ATK-10 Yagi + 3 MG996R servos. Bench bring-up in progress.
- Maciej re-measuring Mast A + Mast C on 2026-05-19 to capture `.iqx` + sweep JSON.

**Blocked (waiting on external):**
- Pluto+ delivery — uncertain pre-event. L2 hardware path is partner-pool on-site (bladeRF) per `ARCHITECTURE.md` §5.
- Live multi-node integration — waits on Maciej's bench prep (~1-2 days).

**Open work — see `BACKLOG.md` for the prioritised list.**

---

## §4 Your first hour — environment setup

```bash
# Clone (if not done)
git clone git@github.com:macwed/rfmesh-workspace.git
cd rfmesh-workspace

# Install (uv handles the workspace)
uv sync

# Verify
just verify    # ruff + mypy + pytest (~4 minutes)

# Run a single test to confirm setup
uv run pytest packages/rfmesh-dsp/tests/test_sigma_honesty.py -v
```

If `just verify` fails: that is news. Open an issue or post in the channel before changing anything. **The repo's invariant is `verify` green on `main`**; a fresh clone failing it means an environment mismatch (Python version, uv version, missing system dep) that's worth fixing in the onboarding doc before you start.

System requirements (per `CLAUDE.md`):
- Python 3.12
- uv (latest)
- ruff line-length 100, mypy strict, `extra="forbid"` on every Pydantic model
- Linux preferred (Windows works but the firmware tooling assumes Linux paths)

---

## §5 Pick your workstream (after the read-order in §2)

Per `WORK-SPLIT.md`, the active split is:

| Owner | Workstream | Files of interest |
|---|---|---|
| **Friend** (you) | WS-A-005 RTLSDRDevice port + WS-A-006 servo host driver port | `packages/rfmesh-sdr/src/rfmesh_sdr/devices/`, `packages/rfmesh-servo/src/`, plus salvage from the prior repo per `SALVAGE_AUDIT.md` Part 2 + Part 4d |
| **Lead-Opus** | Tier E demo polish + multi-node simulator smoke test + G7 polar panel + WS-A-007 firmware source | `docs/demo/`, `packages/rfmesh-ops/`, `apps/demo-replay/`, `firmware/` |
| **Maciej** | Bench hardware + Phase C re-measurement + firmware flash | `docs/hardware/`, `docs/phase-c-report/` |

Your starter ticket: see `docs/tickets/skeletons/WS-A-005-rtlsdr-device-port.md`. The Acceptance Criteria are left blank — you fill them after reading the skeleton, the salvage source (`SALVAGE_AUDIT.md` Part 4d), and the `Receiver` Protocol (`INTERFACES.md` §5).

---

## §6 Council protocol for your PRs

The project does not push directly to `main` from human collaborators. **Lead (Maciej) is the only person who merges.** Your workflow:

1. Branch from `main`: `git checkout -b feature/<short-name>` (or `bugfix/<...>` / `docs/<...>`).
2. Work in your branch. Commit often. **Never force-push.** **Never `--no-verify`.**
3. Open a PR against `main` via `gh pr create` (template at `.github/PULL_REQUEST_TEMPLATE.md` auto-fills).
4. In the PR description, request council review by mentioning the four reviewers:
   - **architect** — checks contracts untouched, invariants B1-B7 held, ADRs needed
   - **code-reviewer** — sigma honesty, no silent fallbacks, pure DSP, golden tests
   - **rf-dsp-specialist** — algorithmic correctness, sigma mechanism, jury edge cases
   - **demo-integrity** — jury-facing claims, slide caps, UI text
5. Lead-Opus invokes the reviewer subagents via Claude Code, posts their one-line findings inline.
6. **4× APPROVE (or APPROVE+NOTE/RECOMMENDATION)** → Maciej merges. **BLOCK** → fix and re-run from the blocking reviewer.
7. After merge: rebase your next branch from updated `main`; delete the merged branch.

Per `CLAUDE.md` §"Council review protocol" the council is **sequential**: architect first, then code-reviewer, then rf-dsp-specialist, then demo-integrity. In practice we run them in parallel and treat any BLOCK as the gate. Lead-Opus handles the orchestration; you do not run subagents from your laptop.

---

## §7 Stop conditions — when to ask, not act

Pull the cord (post in escalation channel, do not push) if:

1. **Contracts touched.** `packages/rfmesh-contracts/src/` is editable only by the lead, only via an accepted ADR. (Invariant B1.) If your ticket appears to require a contract change, stop and write a `CHANGE-REQUEST ADR` under `docs/adr/` with status `PROPOSED`, then ping Maciej. Do **not** start work that assumes the change.
2. **Cross-workstream import needed.** Every cross-package import must resolve to `rfmesh_contracts` or to your own package. If you find yourself wanting to import from a sibling workstream's package, stop. (Import-discipline rule WD-1.)
3. **Silent failure tempted.** Anywhere code could fail silently, it must fail loudly. (Invariant B3.) If you can't see how to fail loudly without breaking other things, stop and ask.
4. **Physical-world question.** Hardware mounts, antennas, polarisation, mast design, deployment ergonomics — Maciej's domain. Do not second-guess physical assumptions; treat them as facts.
5. **Council split verdict.** If reviewers disagree and there is no obvious resolution, stop and ask Maciej.

The cost of pausing is low. The cost of pushing through is unrecoverable hours later.

---

## §8 What this project does **not** want

Repeating the explicit non-goals (`WORKSTREAMS.md` §5 + `ARCHITECTURE.md` §8) so you can refuse polite suggestions to drift:

- **TDOA multilateration.** Out of scope. Architectural decision in `INHERITED_CONTEXT.md` §2.1.
- **Vehicular / mobile DF.** Static deployments only.
- **Audio / acoustic detection.** Separate project (FiberSense), separate event.
- **Hardened enclosures.** Demo hardware, not field-deployable kit.
- **Full multi-week ML training pipeline.** v1.0 ships open *structure*, not a trained library.
- **Anything mechanical or RF-deployment.** Maciej's domain entirely. Treat physical-world assumptions as facts.

If a ticket appears to drag one of these in, refuse scope and ask Maciej to re-cut.

---

## §9 Communication & escalation

- **Day-to-day:** PR comments. Tag Maciej (`@macwed`) for merge or for any escalation.
- **Async question on architecture:** post in the agreed channel (TBD between you and Maciej — Slack / Discord / Signal). If unclear, write a `SCRATCHPAD` entry at `.claude/scratchpad/<workstream>-<date>.md` per `AGENTS.md` §6.
- **Council disagreement / blocker:** Maciej is the tiebreaker. Lead-Opus drafts the proposed resolution; Maciej decides.
- **Emergency (broken `main`, force-push needed, etc.):** Maciej only. Do not force-push under any circumstance.

---

## §10 What to ignore

This document mentions, but you do **not** need to read on day 1:

- `SALVAGE_AUDIT.md` — Part 2 + Part 4d are needed when you start WS-A-005/006; the rest is historical.
- The 12 ADRs — read the one your ticket cites; the others can wait.
- `docs/design/ops-architecture.md` — relevant if you touch the dashboard; otherwise skim only.
- `firmware/` — Maciej's domain; you don't flash, only port source if WS-A-007 lands on you.
- The Polish docs (`docs/hardware/phase-c-tutorial-pl.md`) — Maciej-side translations.

---

## §11 Your first ticket pointer

`docs/tickets/skeletons/WS-A-005-rtlsdr-device-port.md` — a partially-filled skeleton ticket (goal, context, files-may-touch, files-may-NOT-touch, out-of-scope, stop conditions). You fill the **Acceptance Criteria** section after reading the salvage source. Expected effort: **½-1 day**.

Once you push that PR through council, you take WS-A-006 (servo host driver port, similar shape, ~1 day).

After WS-A-006: pick from `BACKLOG.md` based on what's not yet claimed. Coordinate via PR or Slack.

---

## §12 One final thing

**Honesty is the project's load-bearing value.** Read `INTERFACES.md` §0 on the universal conventions if you only have time for one section. The system's promise to the BoTH3 jury is that it refuses to lie when physics says it can't see. Every line of code you write either preserves that or breaks it. Sigma honesty, no silent fallbacks, no fabricated GDOP, no smoothed-over UNKNOWN classifications. When in doubt, fail loudly.

Welcome aboard.
