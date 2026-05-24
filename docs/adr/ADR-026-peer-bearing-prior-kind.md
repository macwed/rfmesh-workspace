# ADR-026 — Peer-bearing prior axis + SCHEMA_VERSION 1.4.0

**Status:** PROPOSED (2026-05-24)
**Date:** 2026-05-24
**Author:** lead-Opus, baking in the original council BLOCK on item 4 of
the post-cold-start plan. The peer-bearing sigma-honesty work was BLOCKED
2026-05-23 by rf-dsp + code-reviewer on three design questions; this ADR
resolves all three before any code lands.
**SCHEMA_VERSION change:** 1.3.0 → 1.4.0 (MINOR-additive — three new
optional fields + one new enum). Backwards-compatible: every existing
contract producer/consumer continues to round-trip without edits.
**Scope:** `packages/rfmesh-contracts/`, `packages/rfmesh-fusion/`,
`packages/rfmesh-dsp/` (sigma-honesty test fixture), `packages/rfmesh-node/`
(rendezvous emits prior-tagged reports).

## Context

ADR-019 added directional rendezvous: two nodes auto-acquire each other by
GPS-prior pointing + scan-and-stare refinement. ADR-021 §"Peer-bearing
sigma honesty (B2)" identified that peer-acquired bearings have a strong
Bayesian prior (surveyed peer position + prior comms), and feeding them
into the existing `FixEvent` fusion at face-value sigma double-counts
information. ADR-021 deferred the fix to a paired contract bump.

The original council pass (2026-05-23) BLOCKED the naive plan on three
design questions. **All three resolutions are baked into this ADR before
implementation begins.**

### Council Q1 — `method` overload vs separate `prior_kind` axis

Naive plan: add `Capability.L1_PEER_RSSI` and key the fusion filter on
`BearingReport.method`. RF-DSP **BLOCKED**: this collapses two orthogonal
axes (estimator type vs. epistemic status):

> The DSP code path is the same `L1AmplitudeSweepEstimator` whether the
> target is a peer beacon or an unknown emitter — only the prior on
> `azimuth_deg` differs. Reusing `method` means a future L2 phase-coherent
> peer-acquisition needs `L2_MUSIC_PEER`, `L2_CAPON_PEER`,
> `L2_MVDR_NULL_PEER` — combinatorial explosion. And the fusion filter
> accidentally rejects any legitimate L1 emitter bearing if a future
> estimator retargets.

**Resolution adopted (RF-DSP recommendation):** keep `method = L1_RSSI`
(or whatever estimator type produced the report — faithful to
`INTERFACES.md` §3). Add a separate orthogonal `BearingReport.prior_kind:
BearingPriorKind | None` field. Fusion filters by `prior_kind`, not by
method. `Capability.L1_PEER_RSSI` is **explicitly rejected**.

### Council Q2 — Likelihood vs posterior sigma

Naive claim: "tighter sigma than the unknown-emitter case." RF-DSP +
EW both flagged this as a B2 violation if the tightening comes from
folding the prior into `azimuth_sigma_deg`:

> The Monte-Carlo honesty test measures across-realisation spread of the
> *estimator output*; if the estimator's residual variance at given SNR
> is unchanged, the empirical spread will be unchanged, and a smaller
> claimed sigma will fail the ±20% MC band — exactly as B2 demands.
> A jury asks "is your reported sigma the posterior std-dev, or the
> likelihood std-dev?" Those are different numbers.

**Resolution adopted (RF-DSP path (a) — "honest path"):**
`BearingReport.azimuth_sigma_deg` continues to report the **likelihood**
σ (raw peak-fit residual from the parabola, no prior folded in). The
prior travels on two new optional fields:

- `BearingReport.prior_mean_deg: float | None` — the prior mean for this
  bearing, in geographic degrees CW from north, when `prior_kind is
  BearingPriorKind.PEER_LINK`. Computed by the rendezvous loop from the
  surveyed peer position (`geodesic_initial_bearing_deg`).
- `BearingReport.prior_sigma_deg: float | None` — the prior 1-σ
  uncertainty in degrees. Folded from peer position uncertainty + LOS
  geometry by the producer.

Downstream consumers (a future tracker, an operator UI rendering the
peer-link confidence) compute the posterior `(mean, sigma)` from
likelihood + prior; the wire stays honest, the MC sigma-honesty test
keeps passing because it tests the likelihood σ exclusively.

### Council Q3 — Per-peak vs per-sweep fusion filter

EW NOTE on the naive plan:

> Filter dropping whole peer sweeps loses incidental unknown-emitter
> peaks. A peer-sweep that crosses an off-axis emitter generates
> legitimate unknown-emitter bearings (different RSSI peak, different θ
> from the peer prior). Drop only the peak at the GPS-prior bearing;
> keep secondary peaks as `prior_kind = FLAT`.

**Resolution adopted:** the producer (rendezvous loop) emits **per-peak**
reports. The peak at the GPS-prior bearing is tagged `prior_kind =
PEER_LINK`; secondary peaks the L1 estimator finds during the same refine
sweep are tagged `prior_kind = FLAT` and carry no prior fields. Fusion
filters per-peak by `prior_kind`, not per-sweep — preserving Advantage #1
(density-scaled GDOP).

This requires the L1 estimator to support a "multi-peak" mode. v1.4.0
scope: rendezvous loop produces ONE primary peak (the existing single-peak
behaviour) tagged `PEER_LINK`. Multi-peak harvest is a follow-up
(documented in §"Iteration plan" below). The contract surface is right
for the future; the producer's single-peak v1 is honest.

## Decision

### Change A — Bump `SCHEMA_VERSION` 1.3.0 → 1.4.0

MINOR-additive. Three new optional fields on existing models + one new
enum + one new value-object class. Every existing producer / consumer
round-trips unchanged. ADR-012 `Literal[SchemaVersionT]` tripwire fires
mypy on every workstream's pin until they update — same mechanic as
ADR-013 + ADR-025.

### Change B — New enum `BearingPriorKind`

`packages/rfmesh-contracts/src/rfmesh_contracts/enums.py`:

```python
class BearingPriorKind(StrEnum):
    """The epistemic status of a BearingReport's azimuth prior (ADR-026).

    Orthogonal to ``Capability`` (the estimator-type axis). A peer-
    acquired bearing produced by the SAME L1_RSSI estimator path that
    produces emitter bearings carries ``prior_kind = PEER_LINK``; an
    incidental secondary peak from the same sweep carries
    ``prior_kind = FLAT``.
    """

    #: No prior — the bearing is a measurement of an unknown emitter.
    FLAT = "flat"
    #: Bayesian prior from a known peer link (surveyed position +
    #: prior comms). Producer also populates ``prior_mean_deg`` +
    #: ``prior_sigma_deg``. Fusion filters these out of emitter
    #: FixEvent computation (Q3 per-peak filter, ADR-026).
    PEER_LINK = "peer_link"
```

### Change C — New value object `PeerLink`

`packages/rfmesh-contracts/src/rfmesh_contracts/messages.py`:

```python
class PeerLink(BaseModel):
    """Live peer-link state for one peer (ADR-026)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    peer_node_id: str = Field(min_length=1)
    last_lock_t_unix_ns: int | None = Field(default=None, gt=0)
    link_margin_db: float | None = Field(default=None)
    # Future: posterior bearing to peer, link state enum, etc.
```

### Change D — `BearingReport` gains three optional fields

```python
class BearingReport(BaseModel):
    # ... existing fields unchanged ...
    prior_kind: BearingPriorKind | None = Field(
        default=None,
        description=(
            "Epistemic axis (ADR-026): FLAT for unknown-emitter bearings, "
            "PEER_LINK for peer-acquired. None = legacy producer / pre-1.4.0 "
            "report; consumers treat as FLAT. Fusion uses this for the "
            "emitter-FixEvent filter (not the method)."
        ),
    )
    prior_mean_deg: float | None = Field(
        default=None,
        ge=0.0,
        lt=360.0,
        description=(
            "Prior mean azimuth in geographic degrees CW from north. "
            "Populated when prior_kind=PEER_LINK; None otherwise."
        ),
    )
    prior_sigma_deg: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Prior 1-σ uncertainty in degrees. Populated when "
            "prior_kind=PEER_LINK. The wire's ``azimuth_sigma_deg`` "
            "stays as the LIKELIHOOD σ (no prior folded in); downstream "
            "consumers combine likelihood + prior to derive posterior."
        ),
    )

    @model_validator(mode="after")
    def _prior_fields_coherent(self) -> BearingReport:
        # PEER_LINK requires both prior fields populated.
        if self.prior_kind is BearingPriorKind.PEER_LINK:
            if self.prior_mean_deg is None or self.prior_sigma_deg is None:
                msg = (
                    "BearingReport.prior_kind=PEER_LINK requires both "
                    "prior_mean_deg and prior_sigma_deg."
                )
                raise ValueError(msg)
        # FLAT / None must NOT carry prior data (would mislead consumers).
        elif self.prior_mean_deg is not None or self.prior_sigma_deg is not None:
            msg = (
                "BearingReport.prior_mean_deg / prior_sigma_deg are only "
                "valid when prior_kind=PEER_LINK."
            )
            raise ValueError(msg)
        return self
```

### Change E — `NodeStatus.peer_links` optional field

```python
class NodeStatus(BaseModel):
    # ... existing fields unchanged ...
    peer_links: tuple[PeerLink, ...] | None = Field(
        default=None,
        description=(
            "Live peer-link state, one entry per known peer (ADR-026). "
            "None on legacy nodes that predate 1.4.0. Empty tuple on a "
            "1.4.0+ node with no peers configured (different from None: "
            "explicit 'has no peers')."
        ),
    )
```

### Change F — `Fuser.fuse` per-peak filter

`packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py`:

```python
def fuse(self, bearings, config):
    # Existing filter for L1_REFUSED_PROMINENCE stays. Add:
    emitter_bearings = tuple(
        b for b in bearings
        if b.prior_kind is not BearingPriorKind.PEER_LINK
    )
    if not emitter_bearings:
        # B3: a batch composed entirely of peer-link bearings is not a
        # silent empty fix. Either we have nothing to fuse or the
        # caller's filter is wrong.
        msg = (
            "Fuser.fuse: batch contains only PEER_LINK-prior bearings; "
            "no emitter geolocation possible. (PEER_LINK reports are "
            "the peer-acquisition surface; they belong to the link-state "
            "consumer, not the emitter fusion.)"
        )
        raise ValueError(msg)
    # ... existing fuse pipeline on emitter_bearings ...
```

The B3 raise (instead of silent `return None`) is per RF-DSP NOTE 3.

### Change G — Sigma-honesty test fixture for the refine geometry

`packages/rfmesh-dsp/tests/test_sigma_honesty.py` extended with a new
fixture mirroring the rendezvous refine arc: known beacon at LOS, narrow
arc (±20°), `prior_kind = PEER_LINK` enabled. Asserts the LIKELIHOOD σ
(empirical MC spread of `azimuth_deg` across realisations) stays in the
±20% band at SNR {10, 20, 30} dB. **The test does NOT exercise the
posterior** — the prior fields are inert to MC sigma-honesty by design
(Q2 resolution).

### Change H — Rendezvous loop populates the new fields

`packages/rfmesh-node/src/rfmesh_node/rendezvous.py`: the
`L1AmplitudeSweepEstimator` already produces a `BearingReport`; the
rendezvous loop overrides `prior_kind = PEER_LINK`, `prior_mean_deg =
geodesic_initial_bearing_deg(self_pos, peer_pos)`, `prior_sigma_deg =
fold(peer_position.sigma_m, range_m)` before emit. The L1 sweep
(jammer-DF) leaves `prior_kind = FLAT` (or None for pre-1.4.0 producers).

Prior-sigma folding formula (lives in `rendezvous.py`):

```python
def peer_prior_sigma_deg(peer_position_sigma_m, range_m):
    """Approx fold: σ_θ ≈ (σ_position / range) rad → degrees. Cap at 90°
    (the servo arc) to avoid pathological values when range is small."""
    rad = peer_position_sigma_m / max(range_m, 1.0)
    return min(math.degrees(rad), 90.0)
```

### Change I — Posterior combiner helper + `link.html` render (v1.4.0 binding)

Demo-integrity REC 1 promoted into v1.4.0 scope: without an operator-
visible posterior render, the soldier UI shows the wider likelihood σ
while the wire-honest prior fields ride alongside unrendered — a jury
asking "what's your peer bearing uncertainty?" gets the wrong number.

**`combine_bearing_prior` helper** in
`packages/rfmesh-fusion/src/rfmesh_fusion/posterior.py`:

```python
def combine_bearing_prior(
    likelihood_mean_deg: float, likelihood_sigma_deg: float,
    prior_mean_deg: float, prior_sigma_deg: float,
) -> tuple[float, float]:
    """Inverse-variance combine for a Gaussian likelihood + prior on a
    circular azimuth axis. Returns (posterior_mean_deg, posterior_sigma_deg).

    The mean combine respects 0°/360° wrap by converting both inputs to
    unit vectors, weighting by 1/sigma², averaging in Cartesian, and
    back to a wrapped angle. The sigma combine is the standard
    inverse-variance formula `1/sigma_post² = 1/sigma_L² + 1/sigma_P²`.

    Lifts the RF-DSP NOTE 2 risk on ADR-026: two consumers reimplementing
    the combine inconsistently is a real hazard; one might forget the
    circular wrap. Single helper, every consumer keys off it.
    """
```

Plus a `tests/golden/posterior_combine_*.npz` round-trip set (WD-2 —
this IS in `rfmesh-fusion`, golden test required).

**`link.html` peer-link posterior render**: the detail drawer adds a
"Peer bearing" line that shows `posterior_mean ± posterior_sigma` when
the selected node has a PEER_LINK report; falls back to "—" otherwise.
Uses the helper via a small server-side endpoint (`GET /node/{id}/peer_bearing`)
that wraps the combine for the UI; alternative is to do the combine in
JS, but a server-side endpoint avoids reimplementing the wrap in
JavaScript (the same RF-DSP NOTE 2 concern, in a different language).

**`PeerLink` UI render convention (demo-integrity REC 2):**
`link.html` SHALL render `last_lock_t_unix_ns` as relative age
(`"linked Ns ago"`) and `link_margin_db` as signed dB with a ⚠ glyph
when `< +10 dB`. The threshold lives in `link.js` as a constant
`PEER_LINK_LOW_MARGIN_DB = 10.0`; documented in `link.js`'s top comment.

### Iteration plan (post-v1.4.0)

- **Iter 1 (deferred):** L1 multi-peak harvest in
  `L1AmplitudeSweepEstimator`. Today the estimator picks one peak per
  sweep; rendezvous-mode benefits from emitting *additional* secondary
  peaks with `prior_kind = FLAT` so an off-axis emitter caught during a
  peer-refine sweep contributes to emitter geolocation. Contract surface
  lands in v1.4.0; producer enhancement is a follow-up node-runtime
  ticket. **v1.4.0 ships single primary peak tagged PEER_LINK only**;
  secondary-peak FLAT path is contract-ready but unexercised. The demo
  deck must surface this as "designed, follow-up" rather than imply
  incidental-emitter harvest is live today (RF-DSP NOTE 3).
- **Deck rev (demo-integrity REC 3):** add one bullet under Advantage
  #1 (density-scaled GDOP): *"Peer bearings carry an honest Bayesian
  prior; jammer bearings don't. Same estimator path, two epistemic
  statuses, no double-counting."* Lands in the next pitch_deck.md rev,
  not this ADR.

## Consequences

### Positive

- All three council blocks resolved upstream in the ADR; implementation
  starts on a stable design.
- B2 sigma-honesty preserved: the MC test exercises the likelihood σ,
  which is unchanged from current estimator output, so the existing
  ±20% MC band still holds without re-tuning.
- B3 explicit: filter raises on all-peer batches; `prior_fields_coherent`
  validator refuses mismatched FLAT-with-prior or PEER_LINK-without-prior.
- Backwards-compatible: every legacy producer / consumer round-trips
  through 1.4.0 unchanged (all new fields default to None).
- Per-peak filter (Q3) means rendezvous sweeps that catch incidental
  emitters STILL contribute to FixEvent — Advantage #1 density preserved.
- The `prior_kind` axis is orthogonal to `method` — future L2
  peer-acquisition does NOT need new enum members.

### Negative

- Three new optional fields on `BearingReport` + one on `NodeStatus`.
  Wire footprint grows by ~30 bytes per report (negligible on Wi-Fi;
  on LoRa the bearer's compression layer can drop the optional fields
  for legacy reports).
- The rendezvous loop must compute and populate two extra fields per
  emit. Trivial CPU.
- Posterior combination is consumer responsibility — no posterior on the
  wire in v1.4.0. The first consumer to need it (`link.html` peer-link
  bearing rendering, Iter 2) implements the combine.

### Risks

- A producer that tags `prior_kind = PEER_LINK` but lies about
  `prior_mean_deg` / `prior_sigma_deg` corrupts the downstream
  posterior. Mitigation: the validator catches the missing-fields case;
  the "lying values" case is unrecoverable in software (same posture as
  azimuth_sigma_deg dishonesty — operator review during integration).
- A future caller that uses `method` to discriminate peer vs. emitter
  (instead of `prior_kind`) gets stale behaviour. Mitigation: the
  `BearingReport` docstring + `Fuser.fuse` docstring both point at
  `prior_kind` as the authoritative axis.

## Alternatives considered

- **`Capability.L1_PEER_RSSI` as the discriminator** (the naive item-4
  plan). **REJECTED per RF-DSP Q1:** method-overload, combinatorial
  explosion across estimator types, accidentally filters legitimate L1
  emitter bearings.
- **Posterior σ on the wire** (RF-DSP Q2 path (b)). **REJECTED** because
  the MC sigma-honesty test would become incoherent (it tests
  across-realisation spread, not posterior contraction) and the
  ±20% band would have to be re-derived from scratch.
- **Per-sweep fusion filter** (the naive item-4 plan). **REJECTED per EW
  Q3:** drops incidental emitter peaks caught during peer-refine sweeps,
  weakens Advantage #1 density.
- **Bundle this with ADR-013 / ADR-025**. **REJECTED:** ADR-013 was
  already MINOR-bumped to 1.2.0 and ADR-025 to 1.3.0 before this council
  resolution landed. Bundling would have meant either (a) blocking those
  two on this design or (b) re-doing the bump. Sequential MINOR bumps is
  the clean path.

## References

- `AGENTS.md` §1 — B1 (contracts), B2 (sigma honesty), B3 (no silent
  fallbacks).
- ADR-019 — antenna rendezvous (the rendezvous loop this ADR's prior_kind
  travels on).
- ADR-021 §"Peer-bearing sigma honesty (B2)" — the deferred fix this ADR
  resolves.
- ADR-008 — `L2_CAPON` precedent for MINOR-additive enum + field on
  `BearingReport`.
- ADR-013, ADR-025 — recent SCHEMA_VERSION bump precedents (1.2.0, 1.3.0).
- Council BLOCK 2026-05-23 on item 4 of the post-cold-start plan:
  - rf-dsp-specialist: BLOCK Q1 (method overload), BLOCK Q2 (sigma
    semantics ambiguous), NOTE Q3 (B3 loudness).
  - ew-specialist: APPROVE-WITH-FIXES — CAVEATED-OK per-peak filter
    (Q3 same), SHAKY framing on tightness, recommend likelihood σ on
    wire (Q2 path (a)).
  - code-reviewer: BLOCK — sigma-honesty fixture under-specified;
    PeerLink None-vs-empty distinction.
- `INTERFACES.md` §3 — `BearingReport` semantic dictionary (will need
  one update paragraph when this ADR is ACCEPTED).
