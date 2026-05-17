# ADR-008 — L2 Capon vs MVDR null-steering: `Capability` enum split + `SCHEMA_VERSION` 1.1.0

> **AMENDED 2026-05-17 by ADR-010.** §D6's binding API shape for
> `compute_null_steering_weights` was insufficient: the slide-friendly
> `NullSteeringResult.null_depth_db` cannot be computed from R + w
> alone (it is a receive-pattern depth at the jammer azimuth, which
> requires the array manifold). The function signature gains geometry
> keyword-only parameters (`array_geometry`, `n_elements`,
> `element_spacing_m`, optional `element_positions_m`, `frequency_hz`);
> two tests were reframed; the consistency tolerance widened from 0.5
> dB to 10 dB (grid-resolution honest). See
> `ADR-010-null-steering-api-geometry-extension.md`. All other
> bindings of this ADR (look-direction not null-direction, 1e-6
> default loading, FB smoothing default ON, NullSteeringResult struct,
> ≤ 20 dB UI cap) stand.

- **Status:** ACCEPTED (2026-05-17, lead-Opus + Maciej, after council review by Architect / RF-DSP / Demo-Integrity subagents).
- **Author:** lead-Opus (Claude Code).
- **Reviewers:** Architect (REQUEST-ADR-FIRST → resolved here), RF-DSP (NOTE non-blocking, 4 corrections folded in), Demo-Integrity (CONCERN-WORTH-RAISING, narrative + null-depth budget folded in).
- **Decision scope:** the `Capability` enum in `rfmesh-contracts`; the
  `Capability.L2_MVDR_NULL` member's *semantics*; the
  `BearingReport.method` value WS-B-004's Capon estimator emits; a new
  pure-utility module in `rfmesh-dsp`; the pitch caption for Advantage #4.
- **Supersedes:** the v1.0.0 docstring caveat in `l2_mvdr.py` that
  acknowledges the misnomer.
- **Depends on:** WS-B-003 (MUSIC) and WS-B-004 (Capon) merged; ADR-007
  (fusion is method-agnostic — no Fuser dispatching on `method`).

---

## Context

`Capability.L2_MVDR_NULL` was frozen into the v1.0.0 contracts to label
**the actual null-steering capability** — the dual-use sibling of L2
MUSIC that synthesises a spatial null toward a jammer (the "one matrix,
two products" pitch in `HANDOFF_TO_CLAUDE_CODE_LEAD.md §0` Advantage #4).

WS-B-004 then shipped a **Capon (MVDR-spectrum) DoA estimator** under
the same enum value — because the enum was the only L2-non-MUSIC label
available pre-contract-change and Invariant B1 forbade workstream agents
from editing `rfmesh-contracts`. The result is a producer-side misnomer:
the estimator emits `BearingReport.method = L2_MVDR_NULL` but the
algorithm is the Capon spectrum, not null-steering. The module
docstring documents this honestly.

The misnomer is harmless inside the codebase (fusion is method-agnostic
per ARCHITECTURE.md §1; the dashboard does not yet exist; no consumer
branches on the enum value) — but it is **not** harmless for an RF/EW
jury. The pitch slide for Advantage #4 promises null-steering; the
contract advertises null-steering; the code emits the null-steering
enum value but does not synthesise a null. A jury reading the contracts
and the docstring caveat together loses 30 seconds of credibility we
cannot afford.

Two fixes were considered:

- **(a) Keep the misnomer, plan rename post-demo.** Cheapest. The
  previous lead recommended this. Lead-Opus disagrees — see above.
- **(b) Rename the enum + build the real null-steering deliverable.**
  Maciej greenlit option (b) on 2026-05-17.

This ADR records option (b) and the council-reviewed shape of the
build.

---

## Decision

### D1. Add `Capability.L2_CAPON`

A new enum member with string value `"l2_capon"`. The Capon
(MVDR-spectrum) DoA estimator in `packages/rfmesh-dsp/src/rfmesh_dsp/l2_mvdr.py`
emits `BearingReport.method = Capability.L2_CAPON` from this version on.
The module's file name does **not** change (the historical association
with MVDR-family algorithms is informative); the public API's `method`
property and `BearingReport.method` field both report `L2_CAPON`.

### D2. Reserve `Capability.L2_MVDR_NULL` for the genuine null-steering capability

`L2_MVDR_NULL` remains in the enum with the same string value
(`"l2_mvdr_null"`) but is **reserved**: from v1.1.0 it advertises the
real null-steering deliverable built in WS-B-007 (see §D6 below). No
`BearingReport.method` value of `L2_MVDR_NULL` appears on the wire in
v1.1.0 because null-steering does not emit `BearingReport`s — see §D6
on the wire surface.

The enum's docstring updates to make this distinction explicit (Capon =
DoA estimator; MVDR null-steering = receive-weight synthesiser; same R,
different products).

### D3. `SCHEMA_VERSION` 1.0.0 → 1.1.0 (MINOR, additive)

Per `INTERFACES.md` §0 versioning rules: MINOR = "additive,
backward-compatible: a new optional field, a new enum member that
existing consumers can treat as 'unknown'." Adding `L2_CAPON` is
purely additive at the contract surface. WS-B-004's switching from
emitting `L2_MVDR_NULL` to `L2_CAPON` is a producer-side change that
each consumer must handle gracefully (fusion is method-agnostic; no
existing consumer branches on the value).

The bump is the technical tripwire for any future-built consumer that
pins `Literal["1.0.0"]` — they will fail mypy in CI and be forced to
explicitly update their version pin, having read this ADR.

Files updated by this bump:

- `packages/rfmesh-contracts/src/rfmesh_contracts/version.py`:
  `SCHEMA_VERSION = "1.1.0"`.
- `packages/rfmesh-contracts/pyproject.toml`: `version = "1.1.0"`
  (paired with `SCHEMA_VERSION` per `version.py` comment).

### D4. `L2_CAPON` joins the L2-capable set in `NodeConfig` validation

`packages/rfmesh-contracts/src/rfmesh_contracts/config.py` validator
`_l2_caps` set updates from `{L2_MUSIC, L2_MVDR_NULL}` to
`{L2_MUSIC, L2_CAPON, L2_MVDR_NULL}`. A node declaring any of the three
requires an `ArrayConfig` block. Operationally: L2_CAPON requires
phase-coherent IQ just like L2_MUSIC, and L2_MVDR_NULL (when shipped)
also requires it — there is no L2 capability that runs on a
single-channel SDR.

### D5. Pitch caption + framing edits (Advantage #4)

Per Demo-Integrity review:

- HANDOFF §0 Advantage #4 caption: *"One matrix, two products: target
  geolocation for kinetic effect, null-steering for own-comms
  protection — back-to-back from one R per snapshot."* The "back-to-
  back from one R" replaces "simultaneously" — same R, sequential
  computations within ~tens of ms per batch is the honest framing.
- Operational positioning: **anti-desense, not ECM**. Null-steering
  protects the project's own L2 coherent DF channel from desensitisation
  by a co-channel jammer while the system continues to produce bearings
  on the jammer. It is not electronic countermeasures (which would
  require dedicated transmit power and adaptive cancellation across MHz
  of bandwidth). This framing is the answer to the jury question
  *"What operational value is a receive null if you are not also
  transmitting through this array?"*

These edits land in HANDOFF (historical, no edit needed — this ADR
supersedes), in the eventual pitch deck, and in the ops-dashboard
panel text.

### D6. WS-B-007 — build the real null-steering deliverable

New module `packages/rfmesh-dsp/src/rfmesh_dsp/l2_null_steering.py`,
pure numpy, no I/O, no contract surface beyond reading `Capability` and
returning a frozen `NullSteeringResult` dataclass. **Does not satisfy
the `BearingEstimator` Protocol** — it is a utility, not a bearing
producer, and the module asserts this in review.

API shape (binding):

```python
@dataclass(frozen=True)
class NullSteeringResult:
    weights: NDArray[np.complex64]      # (N,), the w vector
    null_depth_db: float                 # 10·log10(|w^H R w| /
                                         #          (|w^H w|^2 · trace(R)/N))
    look_gain_db: float                  # 10·log10(|w^H a_look|^2)
    condition_number: float              # cond(R + load·I)
    jammer_dominance_db: float           # 10·log10(λ_max(R) / λ_2(R))

def compute_null_steering_weights(
    R: NDArray[np.complex64],            # (N, N) sample covariance
    look_steering_vector: NDArray[np.complex64],  # a(θ_look) — the
                                                  # signal direction
                                                  # to preserve unit
                                                  # response toward;
                                                  # jammer is in R
    *,
    diagonal_loading_factor: float = 1e-6,
    use_forward_backward: bool = True,
) -> NullSteeringResult: ...

def apply_null(
    coherent_iq: NDArray[np.complex64],  # (N, T) coherent IQ block
    w: NDArray[np.complex64],            # (N,) weight vector
) -> NDArray[np.complex64]: ...          # (T,) nulled scalar stream

def compute_receive_pattern(
    w: NDArray[np.complex64],
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,            # ignored for CUSTOM
    element_positions_m: NDArray[np.float64] | None = None,  # CUSTOM only
    frequency_hz: float = 915e6,
    scan_step_deg: float = 0.5,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return (azimuth_deg_grid_0_to_360, gain_db) for the receive
    pattern of weights w. The demo's A/B polar plot reads from here."""
```

**Folded-in council corrections (binding):**

1. **Argument is `look_steering_vector`, not `null_steering_vector`.**
   RF-DSP review: the MVDR distortionless-response formula
   `w = R⁻¹ a / (a^H R⁻¹ a)` operates on the **signal** direction. The
   jammer is implicit in R; MVDR nulls it because nulling jammer
   power minimises total output power. A mis-wording calling the
   argument `null_steering_vector` is what the jury catches in 10
   seconds.

2. **Default loading is 1e-6, not 1e-3.** Diagonal loading inflates the
   smallest eigenvalue of R = the jammer-subspace direction of R⁻¹ =
   **fills in the null**. Capon's 1e-3 default exists for peak
   stability of the DoA spectrum and does not apply here. Trade-off is
   documented: 1e-6 caps achievable null depth at ~60 dB (more than
   enough); 1e-3 would cap it at ~30 dB and waste the dual-use story.

3. **Forward-backward smoothing default ON.** Opposite of WS-B-004's
   default-OFF. Real-world multipath produces correlated arrivals that
   MVDR cancels poorly without FB; the null-steering use case is
   coherent-multipath-prone (a jammer with ground reflection produces
   exactly the failure mode FB smoothing addresses).

4. **Return a verifiable result struct, not bare weights.** Every demo
   number Maciej quotes on stage must be derivable from `NullSteeringResult`
   fields. Demo-Integrity will not approve a slide quoting `null_depth_db`
   that is not a returned attribute.

**Acceptance criteria (from WS-B-007 ticket, summarised here):**

- Honest 2-emitter scenario: signal at θ_s, jammer at θ_j, N=4 UCA,
  λ/4 radius, SNR signal/jammer/noise = 10/20/0 dB, T=4096 snapshots.
- Achievable null depth (ideal calibration): ≥ 25 dB at θ_j; look gain
  within 3 dB of unsteered. Reported in `NullSteeringResult.null_depth_db`.
- Robustness sweep (Maciej's slide-claim budget): ≥ 15 dB at θ_j under
  ±2° steering-vector mismatch + ±10° per-channel phase calibration
  error. **The pitch quotes "15-20 dB typical, up to 25 dB with fresh
  calibration"** — those are the numbers Maciej rehearses.
- N=2 ULA edge case handled: 1 degree of freedom, broadside-jammer
  pathology rejected with a `quality < threshold` flag in the result
  struct.
- 1000-trial Monte Carlo at the nominal scenario: median null depth
  reported, p5 and p95 reported. Tests assert the p5 ≥ 15 dB.
- `cond(R_loaded) > 1e8` rejects with a documented exception — the
  same gate WS-B-004 uses.
- mypy strict + ruff + lint-imports (dsp is pure) all green.

The full ticket: `docs/tickets/WS-B-007-l2-null-steering.md`.

### D7. Demo panel design (informational — WS-CD-ops authors)

The Advantage-#4 demo panel lives in the ops dashboard, **not** on the
ATAK marker. Layout (from Demo-Integrity review):

- Left half: receive-pattern polar plot (use
  `compute_receive_pattern`). Before/after toggle.
- Right half: MUSIC pseudospectrum with annotated jammer peak (from
  WS-B-003's existing `raw_pseudospectrum` debug payload).
- Caption: *"Same array. Same R. Two products."*
- Operator click sequence: t=0 "Engage null" → re-render pattern with
  visible notch → bar chart "jammer power before / after: -X dB"
  (single number, computed live from the recorded IQ buffer).

**Built around recorded IQ from day 1.** Maciej announces on stage:
*"This is a 30-second capture from our bench in Poznań last week —
same code path, replayed for jury-friendly timing."* No live-vs-recorded
decision on the day. Demo-replay infrastructure already in the WS-CD plan
(`apps/demo-replay`); the null-steering panel reads from the same
buffer.

### D8. The cap on claimed null depth

All UI text quoting null depth caps at **20 dB**. The Monte Carlo
results may show higher in clean simulator runs; the *reported* number
in dashboard captions, slide annotations, and Maciej's rehearsed
answers stays in the 15-20 dB band, with "up to ~25 dB with fresh
calibration" as the optimistic anchor. This is the band a Belgian
Defence RF specialist nods at; anything ≥30 dB invites a calibration-
residuals question the project cannot win.

---

## Rationale

The pitch wins or loses on Advantage #4 if a jury member knows MVDR.
The current state — enum advertises null-steering, code emits Capon
spectrum, docstring confesses the mismatch — is a credibility own-goal
that the council unanimously flagged. The fix is bounded:

- One additive enum member (technically MINOR, cleanly justified).
- One new module (~200 LoC, pure numpy, reuses the same R the existing
  Capon estimator already loads).
- One pre-merge update to WS-B-004's `method` emission.
- One pitch caption edit + Maciej's three rehearsed Q&A answers.

Cost: half a day of focused work + a Monte Carlo run. Benefit: the
dual-use slide is real, demonstrable, and survives a hostile RF-expert
question. Worth doing.

---

## Consequences

### Positive

- The pitch slide on Advantage #4 is backed by code that does what it
  claims.
- The contract enum is honest: producer's `method` value matches what
  the producer actually does.
- Dual-use story has a verifiable demo panel.
- Anti-desense framing (per D5) gives Maciej a defensible operational
  answer to the "why a receive null" question.
- Future contract refinements have a clean MINOR-bump precedent.

### Negative

- One more enum member, one more L2 module, one more test suite —
  modest scope creep on the WS-B side.
- WS-B-004 was reviewed and ready to merge before this ADR; the
  `method`-emission swap is a follow-on change rather than a clean
  in-ticket update. Logged in the WS-B-007 ticket as a precondition.
- Schema bump 1.0.0 → 1.1.0 triggers a `Literal[SCHEMA_VERSION]` mypy
  cascade in any consumer pinned to "1.0.0"; v1.0.0 has no external
  consumers so this is paper-only, but the bump's *purpose* is to
  surface future drift, not to silence it.

### Neutral

- Module file name `l2_mvdr.py` stays (does not become `l2_capon.py`).
  Renaming the file touches every importer in tests + `__init__.py`;
  the public API gets the new label via `method` and the module
  docstring. A cosmetic rename can land in a future cleanup ticket if
  anyone cares.

---

## Validation gates

- `uv run pytest packages/rfmesh-contracts -v` green after `SCHEMA_VERSION`
  bump + enum addition + config validator extension.
- `uv run pytest packages/rfmesh-dsp -v` green after WS-B-004's `method`
  emission swap (existing assertions in `tests/test_l2_mvdr.py` update
  from `L2_MVDR_NULL` to `L2_CAPON`).
- `uv run pytest packages/rfmesh-dsp/tests/test_l2_null_steering.py -v`
  green after WS-B-007 implementation (the acceptance criteria in §D6).
- `uv run mypy packages/` workspace-wide green (the `Literal` tripwire
  resolves consistently after the bump).
- `uv run ruff check .` green.
- `lint-imports` green (`rfmesh-dsp` stays pure).

---

## Out of scope (parking lot)

- A separate `NullEvent` wire-format message carrying the synthesised
  weights to fusion / dashboard. v1.1.0 ships null-steering as a
  node-local operator action; the weights stay in the DSP module and
  the receive-pattern plot is rendered ops-side from `apply_null`'s
  buffered IQ. If a future deployment wants centralised null-coordination
  across nodes, that is an ADR-009 conversation.
- A `Capability.L2_MUSIC_NULL` (the same null-steering done from the
  MUSIC noise-subspace projector rather than R⁻¹) — same mathematics,
  marginal additional value, not in the demo.
- Rename of `l2_mvdr.py` to `l2_capon.py`. Cosmetic, future cleanup.
- Pitch-deck slides themselves. The slides are Maciej's voice; this
  ADR provides the captions and the rehearsable Q&A, not the visual
  design.
