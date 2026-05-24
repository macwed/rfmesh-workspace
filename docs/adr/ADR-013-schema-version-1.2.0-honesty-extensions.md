# ADR-013 — SCHEMA_VERSION 1.2.0: G3 GDOP-uncomputable sentinel + G4 L1-refused-prominence enum

**Status:** EXECUTED (2026-05-24; deferral lifted to unblock ADR-025 DSSS comms work — see "Execution order" below)
**Date:** 2026-05-18
**Author:** lead-Opus (drafting per the 2026-05-18 project audit; ACCEPTED by Maciej on 2026-05-18; EXECUTED 2026-05-24 on `feature/directional-comms`).
**SCHEMA_VERSION change:** 1.1.0 → **1.2.0** (MINOR, additive, backward-compatible).

## Execution order

ACCEPTED 2026-05-18; deferral was contingent on WS-A-008 hardware smoke tests. On 2026-05-24 the lead lifted that gate (smoke tests will not run; project goes straight to full prototype on `feature/directional-comms` for ADR-025 DSSS comms), and the 8-step propagation below landed in one batch as the first commit of `feature/directional-comms`. The reason to land it here, not as a separate branch, is that ADR-025 also needs to bump `SCHEMA_VERSION` (1.2.0 → 1.3.0) and bundling the two `SchemaVersionT` propagations would tangle unrelated honesty fixes with the DSSS contract surface; G3/G4 are claimed by this ADR cleanly first, then DSSS gets the next number.

## Context

The 2026-05-18 project audit produced two findings that close real honesty gaps in the wire-format contract:

- **RF-DSP F12** — `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py:238-243` synthesises a fabricated GDOP value (`gdop_value = max(active_config.gdop_warn_threshold * 10.0, 1.0)`) when the geometry is degenerate enough that `compute_gdop` raises `DegenerateGeometryError`. The contract has no field for "GDOP could not be computed" — the fuser must emit *some* float to satisfy `FixEvent.gdop: float`. The dashboard receives "GDOP=60" and renders it as a real measurement, which is a B3 (no-silent-fallback) edge case: technically not silent (it's a deliberately large number to force `confidence_level = LOW`), but the operator's mental model gets a fake GDOP.

- **RF-DSP F2** — `L1AmplitudeSweepEstimator` refuses to emit a `BearingReport` when the peak prominence is below the 6 dB gate. As of commit `bee8413` (E1 close-out) the estimator captures a `last_refusal_reason` attribute for the operator. But the **wire-level** surface is still nothing: no `BearingReport` is published, so the fusion server and dashboard cannot see the refusal as a structured event. Mast A in Phase C bench (`docs/phase-c-report/findings.md`) is the canonical example: 1.96 dB front-back, the estimator correctly refuses, but a remote dashboard subscriber has no way to render "L1 refused" — it just sees nothing arrive from that node for one batch window.

Both findings were parked in the project audit as **G3 / G4 — needs SCHEMA_VERSION 1.2.0 bump + lead sign-off** (per Invariant B1, contracts are lead-only). This ADR is the sign-off request.

## Decision

**Bump `SCHEMA_VERSION` from `"1.1.0"` to `"1.2.0"`** as a MINOR-additive release. Two changes land together to share one round of `SchemaVersionT` propagation across `messages.py` + `config.py` + every downstream consumer's `mypy` re-check.

### Change G3 — `FixEvent.gdop_uncomputable_reason: str | None`

Add a new optional field to `rfmesh_contracts.messages.FixEvent`:

```python
gdop_uncomputable_reason: str | None = Field(
    default=None,
    description=(
        "Free-form reason the GDOP value is not a measured geometric "
        "dilution but a sentinel placeholder. None on every healthy "
        "fix. Set by the fusion solver when compute_gdop() raises "
        "DegenerateGeometryError (e.g. 'bearing lines parallel within "
        "1e-12 rad — geometry near-collinear'). The dashboard renders "
        "'GDOP: uncomputable (<reason>)' instead of the literal "
        "`gdop` field, which in that case carries a sentinel float "
        "(> gdop_warn_threshold * 10) only to satisfy the existing "
        "FixEvent.gdop validator. Confidence band is forced LOW per "
        "ADR-005 D4 in this branch. None on a healthy fix; populated "
        "only on the fallback-centroid / degenerate-geometry path."
    ),
)
```

`FixEvent.gdop: float` stays as-is (no field removed, no validator changed). Producers on 1.1.0 omitting this field default to `None` — backward-compatible. Consumers on 1.1.0 reading a 1.2.0 producer's `FixEvent` see a new field they don't know about; their Pydantic model rejects via `extra="forbid"`. **This is the breaking-side caveat** (see "Consequences" below — the `extra="forbid"` semantics make MINOR consumer-side more abrupt than MINOR producer-side; we still call this MINOR because new consumers cannot misinterpret old producers).

### Change G4 — `Capability.L1_REFUSED_PROMINENCE` enum value

Add a new enum member to `rfmesh_contracts.enums.Capability`:

```python
L1_REFUSED_PROMINENCE = "l1_refused_prominence"
```

with semantics: this is a *capability state*, not a *bearing method*. A `BearingReport` carrying `method = Capability.L1_REFUSED_PROMINENCE` is the wire-level surface for L1 refusal. Producers on the L1 path who choose to publish refusal events (an optional wiring; the current `Node.run` does not yet drive estimators directly — see `apps/demo-replay/src/rfmesh_demo_replay/replay.py` for the orchestrator path) emit:

```python
BearingReport(
    schema_version="1.2.0",
    node_id=...,
    t_unix_ns=...,
    node_position=...,
    azimuth_deg=0.0,         # sentinel — not a real bearing
    azimuth_sigma_deg=180.0, # sentinel — infinite-uncertainty equivalent
    method=Capability.L1_REFUSED_PROMINENCE,
    snr_db=...,              # the SNR diagnostic, if known
)
```

Two follow-on contract implications:

1. **`Fuser.fuse()` filters out refusal events** — they do not contribute to a fix. Document in `INTERFACES.md` §5 `Fuser` protocol that input `BearingReport`s with `method = L1_REFUSED_PROMINENCE` are skipped, not weighted as `1/azimuth_sigma_deg^2`.
2. **`BearingsPanel` + `BearingScanPanel` render the refusal** instead of a sigma-wedge — the operator sees the refusal where the wedge would be, with the cause (`last_refusal_reason` becomes a free-form field on the `BearingReport`? — TBD; see "Open question" below).

### Open question — does G4 also need `BearingReport.refusal_reason: str | None`?

Without a free-form reason field, G4 carries only the *fact* of refusal, not the *cause*. The L1 estimator has six distinct refusal causes (prominence-gate, saddle, vertex-out-of-window, singular-cov, non-finite-variance, sweep-underpopulated). The operator-facing surface (E1) carries them as strings. The wire-facing surface (G4) would lose them unless we also add:

```python
refusal_reason: str | None = Field(
    default=None,
    description=(
        "Free-form diagnostic string when method is "
        "Capability.L1_REFUSED_PROMINENCE (or other L<n>-refused values "
        "added in future). None on healthy bearings."
    ),
)
```

**Recommendation:** add both. The marginal cost is one optional field; the marginal gain is the operator sees the refusal cause on a remote dashboard the same way E1 surfaces it locally.

## Why MINOR, not MAJOR

Per `packages/rfmesh-contracts/src/rfmesh_contracts/version.py:21-23` definition:

> MINOR — additive, backward-compatible change (a new *optional* field, a new enum member that existing consumers can treat as "unknown"). Producers on `N.M.x` can still be read by consumers on `N.(M-k).x`.

Both changes are MINOR-additive by this definition. Caveat: because every contract model has `extra="forbid"`, a *consumer* on 1.1.0 reading a 1.2.0 producer's message **will fail validation**. That asymmetry is inherent to the project's `extra="forbid"` discipline and is documented as part of the SCHEMA_VERSION semantics. The cleanup is: when ADR-013 is accepted and `SCHEMA_VERSION = "1.2.0"` lands, every workstream's `mypy` fails on the next CI run until each updates its dependency pin. **That is the tripwire firing as designed.**

## Consequences

**Operational:**

- Every package's pinned `SchemaVersionT` in `packages/rfmesh-contracts/src/rfmesh_contracts/version.py` updates: `type SchemaVersionT = Literal["1.2.0"]`. Mypy fires across the workspace until every reference is updated; this is exactly the type-system tripwire ADR-012 exists for.
- 521 tests need to be re-run; expect ~5 contract round-trip / extra-forbid tests to fail until updated (the `test_stale_schema_version_rejected_*` tests at `packages/rfmesh-contracts/tests/test_schema_version_tripwire.py` need `"1.0.0"` → `"1.1.0"` to keep the stale-rejection semantics).
- `tools/null_depth_mc_stats.md` + `docs/demo/*.md` numeric receipts are unaffected (no algorithm change).
- `apps/demo-replay/src/rfmesh_demo_replay/scenario.py` and downstream YAML loaders need a `"1.2.0"` schema_version reference where they currently hardcode `"1.1.0"`.

**Code changes paired with this ADR (post-acceptance):**

1. `packages/rfmesh-contracts/src/rfmesh_contracts/version.py` — bump SCHEMA_VERSION + SchemaVersionT
2. `packages/rfmesh-contracts/src/rfmesh_contracts/enums.py` — add `L1_REFUSED_PROMINENCE` to `Capability`
3. `packages/rfmesh-contracts/src/rfmesh_contracts/messages.py` — add `gdop_uncomputable_reason` + `refusal_reason` to `FixEvent` / `BearingReport`
4. `packages/rfmesh-contracts/tests/*.py` — update stale-version tests' baseline (1.0.0 → 1.1.0)
5. `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py` — populate `gdop_uncomputable_reason` on the degenerate-geometry path; skip `L1_REFUSED_PROMINENCE` reports in `fuse()`
6. `packages/rfmesh-ops/src/rfmesh_ops/panels/fix.py` — render `gdop_uncomputable_reason` instead of literal `gdop`
7. `packages/rfmesh-ops/src/rfmesh_ops/panels/bearings.py` — render refusal symbol instead of sigma-wedge for `L1_REFUSED_PROMINENCE` reports
8. `INTERFACES.md` — §1 `Capability` table adds `L1_REFUSED_PROMINENCE`; §3 `FixEvent.gdop_uncomputable_reason` + `BearingReport.refusal_reason` semantic dictionary entries; §0 SCHEMA_VERSION pin update.

Estimated implementation: **2-3 hours** post-acceptance, council-reviewable per commit.

## Tradeoffs considered

**Why bump now vs deferring further:**
- The current 1.1.0 system silently emits a fabricated GDOP — a real B3 hazard in front of an RF/EW jury. ADR-013 closes that.
- L1 refusal visibility was half-solved by E1 (operator can see `last_refusal_reason` locally on the estimator instance); G4 closes the remote-dashboard half.
- We are 25 days from BoTH3. A SCHEMA_VERSION bump this close to demo is risk; deferring to post-event is the alternative. **The risk is small (additive, mechanically propagated) and the jury-credibility win is real.**

**Why pair G3 + G4 in one bump vs two separate bumps:**
- One round of mypy propagation across 9 packages costs ~1 hour. Two rounds cost ~2 hours plus the awkward intermediate state.
- Both are honesty-payload extensions in the same architectural class. Pairing makes the SPRINT_LOG narrative clean.

**Why not just fix the fuser to never produce a degenerate-geometry path:**
- It already does the right algorithmic thing (`fallback_centroid`); G3 is about making the *reporting* honest. The fix is wire-level, not algorithmic.

**Why a string field for `gdop_uncomputable_reason` vs a typed enum:**
- The set of failure modes is small now (geometry-near-collinear, all-bearings-parallel) but may grow as the solver matures. Free-form string lets us iterate without further SCHEMA_VERSION bumps. The cost is the dashboard does a substring match for rendering; that's acceptable.

## References

- Project audit 2026-05-18 (architect / code-reviewer / rf-dsp-specialist / demo-integrity council).
- ADR-012 — the `Literal[SCHEMA_VERSION]` tripwire mechanism that this bump exercises.
- `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py:238-243` — the fabricated GDOP value this ADR replaces.
- `packages/rfmesh-dsp/src/rfmesh_dsp/l1.py` — `last_refusal_reason` property (E1, commit `bee8413`).
- `docs/phase-c-report/findings.md` Mast A — the canonical bench example of L1 refusal we now want to surface on the wire.
- `AGENTS.md` §1 Invariant B1 — contracts frozen; ADR is the sign-off mechanism.

## Sign-off

When Maciej reviews and accepts:

1. Change this file's `Status:` line from `PROPOSED` to `ACCEPTED`.
2. Add a line: `**Accepted by:** Maciej (`@macwed`), <date>.`
3. Lead-Opus executes the 8-step code-change list under "Consequences", one commit per logical group, council-reviewed.

If rejected:

1. Change `Status:` to `REJECTED`.
2. Add a line stating the reason.
3. The G3 fabricated-GDOP path stays as-is; the G4 wire surface stays operator-only via E1. Both findings remain on the BACKLOG as `parking-lot` until a future bump.
