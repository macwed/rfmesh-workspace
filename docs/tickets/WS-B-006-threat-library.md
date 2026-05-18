# TICKET WS-B-006: Threat-class enrichment + YAML profile library

## Goal (one sentence)

Implement `packages/rfmesh-ml/src/rfmesh_ml/threats/` — the open,
operator-extensible enrichment layer that maps WS-B-005's
modulation-class output (`{"cw", "fhss", "fsk", "lora", "unknown"}`) plus
RF context (`center_freq_hz`, `snr_db`) onto
`rfmesh_contracts.enums.EmitterClass` via a YAML profile library, with
honest `UNKNOWN` returns for stub-only emitters (POLE21, VOLNOREZ) and
for inputs that match no profile.

## Context (links only, not content)

- Contracts touched (read-only):
  `rfmesh_contracts.enums.EmitterClass` — the seven-member enum this
  module emits (`UNKNOWN`, `ELRS`, `CROSSFIRE`, `GSM_JAMMER`, `POLE21`,
  `VOLNOREZ`, `DRONEID`). See `INTERFACES.md` §1 for the **load-bearing
  semantics of `UNKNOWN`** (the classifier ran and is not confident
  enough — *not* a fabricated label) and for the **v1.0.0 "stub"
  designation on `POLE21` / `VOLNOREZ`** (profile placeholders pending
  real-IQ capture).
- Contracts touched (read-only, downstream sink):
  `rfmesh_contracts.messages.BearingReport.emitter_class` and
  `BearingReport.classification_confidence`. WS-B-006 does **not**
  populate these directly; the node runtime (WS-D, future) consumes
  this module's output to populate them. See `INTERFACES.md` §3 for the
  `None` vs `EmitterClass.UNKNOWN` distinction this module preserves:
  `None` = "classifier did not run"; `UNKNOWN` = "classifier ran,
  unsure" — WS-B-006 returns `UNKNOWN`, never `None`.
- Architecture: `docs/ADVANTAGES.md` §1 Advantage #5
  ("Open, extensible threat library as moat"). WS-B-005 ships the
  modulation-class layer; **WS-B-006 ships the wrapper that turns it
  into a credible Advantage #5 demo artefact** — an operator can open
  the YAML profile on a tablet next to the ATAK marker and explain
  what was matched, and can add a new profile by editing one file.
- WS-B-005 is the upstream dependency:
  `docs/tickets/WS-B-005-l3-classifier.md`. Read it in full — in
  particular the `ClassificationResult` shape
  (`modulation_class`, `confidence`, `feature_vector`) and the
  honesty rule on `"unknown"` at low SNR (Invariant B3).
- `WORKSTREAMS.md` §1 Workstream B row owns `packages/rfmesh-ml/` and
  `packages/rfmesh-ml/threats/` per the row's "Owns" entry.
  §2 Workstream B deliverable item 5 names "Threat library: one module
  per `EmitterClass` member in `packages/rfmesh-ml/threats/`; the moat
  is the *open structure*, not the v1.0.0 contents."
- AGENTS.md invariants:
  - **B1** — contracts frozen. WS-B-006 imports `EmitterClass` from
    `rfmesh_contracts.enums` and emits its members; never adds a
    member, never renames a value (a new member is an ADR + MINOR
    `SCHEMA_VERSION` bump, see `INTERFACES.md` §1).
  - **B3** — no silent fallbacks. **Stub-profile matches return
    `(EmitterClass.UNKNOWN, 0.0)`**, never a fabricated confidence on
    `POLE21` / `VOLNOREZ`. Out-of-band frequency on a non-stub profile
    returns `(EmitterClass.UNKNOWN, modulation_confidence)`, never a
    fabricated class. Malformed YAML at load raises loudly, never
    silently skips the broken profile.
  - **B5** — pure. No network at import or at runtime; the only file
    I/O is loading bundled YAML profiles (allowed by Invariant 5,
    which explicitly permits loading vendor data tables at import).
- Prior tickets this depends on: **WS-B-005** (this module consumes
  the `ClassificationResult` it emits — schema dependency, not import
  dependency in the strict sense; see acceptance criterion 4 on how
  the type is referenced). **Out-of-band dependency:**
  `pyyaml` is **not** in the workspace lock at the time of this
  draft — see "Stop conditions" §A for the `/uvadd-request` gate that
  precedes coding.
- Salvage: none. New code.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-ml -v` passes. New tests added to
   `packages/rfmesh-ml/tests/test_threat_enrichment.py` and named so a
   failure points at exactly one property:

   - `test_enrichment_result_shape` — `enrich_to_emitter_class(...)`
     returns a `tuple[EmitterClass, float]` (named-tuple or bare tuple
     — the builder chooses; if a structured result type is preferred,
     name it `EnrichmentResult` with fields `(emitter_class,
     confidence)` and document it). The `confidence` value is a Python
     `float` in `[0.0, 1.0]`.
   - `test_enrich_elrs_868mhz` — synthesise a `ClassificationResult`
     with `modulation_class="lora"`, `confidence=0.9`, and call
     `enrich_to_emitter_class(result, center_freq_hz=868.5e6,
     snr_db=15.0)`. Assert `(EmitterClass.ELRS, conf)` with
     `conf >= 0.5`.
   - `test_enrich_elrs_2_4ghz` — same upstream modulation, but
     `center_freq_hz=2.45e9`. Assert `EmitterClass.ELRS` again (ELRS
     operates on both bands; the YAML profile lists both rules).
   - `test_enrich_crossfire_868mhz` — Crossfire is also LoRa-band
     FHSS at 868 MHz. The discriminator between Crossfire and ELRS
     at 868 MHz is **not solved by frequency band alone** — see
     "Implementation notes — ELRS vs Crossfire discriminator" and
     consult `rf-dsp-specialist` if needed. v1.0.0 acceptance for
     this test: the builder picks a defensible
     discriminator (e.g. hop-rate band or a placeholder
     `discriminator: "elrs_default"` rule-priority annotation in the
     YAML) and the test asserts the *chosen* class. If the
     discriminator cannot be made honest at v1.0.0, both ELRS and
     Crossfire profiles match → `EmitterClass.UNKNOWN` is the
     honest answer and the test asserts that, with a documented
     limitation in the profile YAML's `notes` field.
   - `test_enrich_gsm_jammer_900mhz` — modulation `"fsk"` (placeholder
     for GSM-band continuous emission; the v1.0.0 GSM_JAMMER profile
     keys off frequency band primarily — see implementation notes),
     `center_freq_hz=935e6` (GSM-900 downlink centre). Assert
     `EmitterClass.GSM_JAMMER` with `conf >= 0.5`. Note: the GSM
     `match_rules` profile may be deliberately permissive at v1.0.0
     (real GSM-jammer discrimination needs context, not waveform —
     see `INTERFACES.md` §1 "distinctive in *context*"); the test
     pins the contract that the profile matches, not the claim that
     the system can distinguish a real GSM-band emitter from a
     legitimate handset.
   - `test_enrich_droneid_2_4ghz` — modulation `"fsk"` (placeholder
     for OcuSync downlink; the real DroneID waveform is OFDM-based
     and a v1.5+ classifier task, see WS-B-005 out-of-scope),
     `center_freq_hz=2.437e9`. Assert `EmitterClass.DRONEID` with
     `conf >= 0.5`. The profile rule includes a `notes` field
     documenting that v1.0.0 keys off the 2.4 GHz band + a generic
     "narrow-burst" modulation hint; the v1.5+ ticket sharpens this
     against real captures.
   - `test_enrich_pole21_returns_unknown` — synthesise *any* upstream
     modulation result with high confidence and a frequency that
     *would* match the POLE21 stub profile (e.g. an L1 GNSS band,
     `center_freq_hz=1.575e9`, modulation `"fhss"`). Assert
     `(EmitterClass.UNKNOWN, 0.0)`. The honesty rule: the stub
     profile is matched, but **the public API returns UNKNOWN with
     zero confidence** because shipping a fake `POLE21` label on the
     demo would be dishonest. A log message at `INFO` level
     containing the substrings `"matched stub"` and `"pole21"` is
     emitted; the test asserts the log via `caplog`.
   - `test_enrich_volnorez_returns_unknown` — same shape as POLE21,
     different profile. Assert `(EmitterClass.UNKNOWN, 0.0)` + log
     message naming `volnorez`.
   - `test_enrich_out_of_band_returns_unknown` — modulation
     `"lora"`, `confidence=0.95`, but `center_freq_hz=5.8e9`
     (well outside any rule's `freq_band_hz` in the v1.0.0 profile
     library). Assert `(EmitterClass.UNKNOWN, 0.95)` — the
     *upstream* confidence carries forward unchanged (the modulation
     classifier was confident; the threat library could not place
     it). No log spam.
   - `test_enrich_upstream_unknown_passes_through` — modulation
     `"unknown"`, `confidence=0.3`. Assert
     `(EmitterClass.UNKNOWN, 0.3)`. No rule is consulted: if the
     upstream layer is unsure, the wrapper inherits that uncertainty.
   - `test_enrich_below_rule_min_confidence_returns_unknown` —
     modulation `"lora"`, `confidence=0.5`, `center_freq_hz=868.5e6`,
     and the ELRS profile's matching rule declares
     `min_confidence: 0.6`. Assert `(EmitterClass.UNKNOWN, 0.5)` —
     the rule's `min_confidence` floor is honoured; the wrapper does
     not silently relax it.
   - `test_yaml_profile_dynamically_loaded` — **the load-bearing
     extensibility test.** Use `tmp_path` to write a temporary YAML
     profile for a *fake* additional rule (e.g. an ELRS variant
     matching a deliberately-narrow frequency window like
     `[866.0e6, 866.5e6]`). Call `enrich_to_emitter_class(...,
     profile_library_path=tmp_path)` with an upstream `lora`
     classification at `866.25e6`. Assert the temporary profile is
     picked up — the matched `EmitterClass` is the one declared in
     the temporary YAML (any existing
     `EmitterClass` member; the test does not invent enum members).
     This test proves an operator can drop a YAML file under
     `threats/profiles/` post-deployment and the enrichment layer
     consumes it without recompilation. **If this test fails the
     ticket fails — this is Advantage #5 in test form.**
   - `test_malformed_yaml_raises` — write a syntactically broken
     YAML under `tmp_path` (e.g. unclosed bracket, tab where spaces
     expected) and call `enrich_to_emitter_class(...,
     profile_library_path=tmp_path)`. Assert raises
     `ThreatProfileError` (or whatever the module's loader-error
     class is named — see implementation notes) with a message that
     names the offending file. **Does not silently skip the broken
     profile** (Invariant B3).
   - `test_schema_invalid_yaml_raises` — write a YAML that parses
     cleanly but violates the profile schema (e.g.
     `emitter_class: "not_a_real_class"`, or missing required
     `match_rules` field). Assert raises `ThreatProfileError` with
     a message that names the schema violation. Same Invariant B3
     gate as malformed YAML.
   - `test_stub_profile_requires_stub_reason` — write a YAML with
     `stub: true` but no (or empty) `stub_reason`. Assert raises
     `ThreatProfileError`. **The schema requires stubs to document
     why** — this is the operator-credibility gate (a profile
     labelled stub without a reason is indistinguishable from a
     forgotten work-in-progress).
   - `test_imports_are_clean` — assert the
     `rfmesh_ml.threats` module's `__all__` exports exactly
     `{"enrich_to_emitter_class", "ThreatProfileError"}` (plus the
     result-type name if the builder introduces one). Locks the
     public surface so a follow-up does not silently widen it.

   Total: ~14 tests. The numbers can drift up if the builder adds
   structural coverage (e.g. one test per shipped profile YAML's
   schema validity); they should not drift *down* — each behaviour
   enumerated above is load-bearing for Advantage #5.

2. The v1.0.0 profile library lives under
   `packages/rfmesh-ml/threats/profiles/` and contains **exactly six**
   YAML files at first commit — one per non-`UNKNOWN` member of
   `EmitterClass` (six members: ELRS, CROSSFIRE, GSM_JAMMER, POLE21,
   VOLNOREZ, DRONEID). `UNKNOWN` is *not* a profile (it is the
   no-match return; profiles cannot declare themselves UNKNOWN).
   Each YAML conforms to this schema:

   ```yaml
   emitter_class: elrs
   human_name: "ExpressLRS R/C control link"
   references:
     - "https://www.expresslrs.org/3.0/info/about/"
     - "Datasheet: Semtech SX1276 LoRa transceiver, 868/915 MHz"
   match_rules:
     - modulation_class: lora
       freq_band_hz: [863.0e6, 870.0e6]
       min_confidence: 0.6
       notes: "EU 868 MHz ISM band; ELRS default for region 1."
     - modulation_class: lora
       freq_band_hz: [2.4e9, 2.485e9]
       min_confidence: 0.6
       notes: "2.4 GHz ISM band; ELRS for region 2 and short-range."
   stub: false
   stub_reason: ""
   ```

   Field constraints (enforced by the profile loader, tested):
   - `emitter_class`: string, lowercase, must be a valid value of
     `EmitterClass` (i.e. one of `{"elrs", "crossfire", "gsm_jammer",
     "pole21", "volnorez", "droneid"}` — `"unknown"` is **rejected**
     at load time).
   - `human_name`: non-empty string.
   - `references`: list of strings, possibly empty (an empty list is
     allowed for stubs whose references are classified or pending
     real-capture; not allowed for non-stub profiles — see schema rule
     below).
   - `match_rules`: list of mappings, possibly empty (an empty list
     is allowed only for stubs; non-stub profiles with empty
     `match_rules` are rejected because they could never match
     anything).
     - `modulation_class`: string, must be a valid `ModulationLabel`
       value (`{"cw", "fhss", "fsk", "lora", "unknown"}`).
     - `freq_band_hz`: `[low, high]` two-element list, both positive
       finite floats, `low < high`. Units explicitly Hz.
     - `min_confidence`: float in `[0.0, 1.0]`.
     - `notes`: optional string, default empty.
   - `stub`: bool, default `false`.
   - `stub_reason`: string. **Required non-empty if `stub: true`**;
     ignored (must be empty string) if `stub: false`.

   The two stub profiles (POLE21, VOLNOREZ) ship with `stub: true`,
   a populated `stub_reason` (e.g. `"Awaiting real-IQ capture and
   classifier training; structural placeholder per INTERFACES.md §1
   stub-at-v1.0.0 designation."` for POLE21, and an analogous note
   for VOLNOREZ), and **may** carry indicative `match_rules` for
   demo/dashboard purposes — but the enrichment function returns
   `UNKNOWN` on a stub match regardless of how generous the rule
   looks. The rules on a stub profile are documentation, not
   classification.

3. Public API in `packages/rfmesh-ml/src/rfmesh_ml/threats/__init__.py`:

   ```python
   from pathlib import Path

   from rfmesh_contracts.enums import EmitterClass
   from rfmesh_ml.modulation_classifier import ClassificationResult


   class ThreatProfileError(ValueError):
       """Raised when loading or validating a threat profile fails.

       Subclasses ``ValueError`` so existing ``except ValueError``
       call sites catch it. Invariant B3: malformed or schema-invalid
       YAML raises loudly, never silently skipped.
       """


   def enrich_to_emitter_class(
       modulation_result: ClassificationResult,
       *,
       center_freq_hz: float,
       snr_db: float | None = None,
       profile_library_path: Path | None = None,
   ) -> tuple[EmitterClass, float]:
       """Map a modulation-class result to an EmitterClass via the YAML
       threat-profile library.

       Parameters
       ----------
       modulation_result
           Output of ``ModulationClassifier.classify`` (WS-B-005). The
           wrapper consults ``modulation_class`` and ``confidence``;
           ``feature_vector`` is ignored.
       center_freq_hz
           Tuning centre when the IQ was captured, Hz. Used to
           match the rule's ``freq_band_hz`` window.
       snr_db
           Optional SNR estimate; reserved for v1.5+ rule refinements.
           v1.0.0 enrichment does not consume it but the signature
           carries it forward to avoid a future API break.
       profile_library_path
           Override for the bundled profile directory
           (``packages/rfmesh-ml/threats/profiles/``). When None, the
           bundled library is used. When supplied, ONLY the supplied
           directory's profiles are consulted (the bundled library is
           NOT merged in) — this keeps the dynamic-load test
           hermetic and makes operator overrides explicit.

       Returns
       -------
       (EmitterClass, float)
           The matched class and a combined confidence in [0, 1]. If
           no rule matches, or if the only matching profile is a
           stub, the return is ``(EmitterClass.UNKNOWN, ...)`` — see
           module docstring for the confidence-combination policy.

       Raises
       ------
       ThreatProfileError
           If the profile library cannot be loaded (malformed YAML,
           schema-invalid YAML, missing required fields, invalid
           ``emitter_class`` value, stub without ``stub_reason``).
       """
       ...
   ```

   - **Confidence-combination policy** (record it in the module
     docstring): when a non-stub profile rule matches with the
     rule's `min_confidence` threshold honoured, the returned
     confidence is `modulation_result.confidence` itself — the
     wrapper does NOT inflate the upstream confidence (a rule
     matching does not make the *modulation* classifier more
     certain than it already reported itself). A future v1.5
     ticket may combine `snr_db` and rule-specific evidence; v1.0.0
     keeps the policy simple and honest. The choice and rationale
     are documented in `enrich_to_emitter_class`'s docstring under
     a `## Confidence policy` heading.
   - **Matching priority**: if multiple profiles match (e.g.
     ELRS-868 and Crossfire-868), the wrapper returns
     `EmitterClass.UNKNOWN` with the upstream confidence — the
     honest answer when v1.0.0 cannot distinguish two candidate
     classes on the available evidence. The builder MAY introduce
     a `priority: int` field on rules (lower = higher priority) to
     resolve known ambiguities deterministically — if used, the
     priority is documented in the profile YAML's `notes` field
     and the test `test_enrich_crossfire_868mhz` asserts the
     priority-driven outcome. Otherwise, the multi-match case is
     UNKNOWN. (The simpler "UNKNOWN on multi-match" policy is the
     v1.0.0 default; priority is a permitted but optional
     enhancement.)
   - **Profile load lifecycle**: profiles are loaded **at first
     call** (lazy, cached in a module-level dict keyed by
     `profile_library_path`). Subsequent calls with the same
     `profile_library_path` reuse the cache. The cache is **not
     invalidated** during a process lifetime — operators must
     restart the node-runtime to pick up a YAML edit (this is
     consistent with the "no hot-reload" out-of-scope below).
     The dynamic-load test exercises the path-keyed cache: the
     bundled library and the `tmp_path` override do not collide.

4. Internal layout under `packages/rfmesh-ml/src/rfmesh_ml/threats/`:

   - `__init__.py` — re-exports `enrich_to_emitter_class`,
     `ThreatProfileError`, and (if introduced) the result type.
     Module docstring documents the confidence policy and the
     stub-handling rule.
   - `profile.py` — `ThreatProfile` Pydantic model (frozen,
     `extra="forbid"`, mirrors §2's schema), `MatchRule` Pydantic
     model, `load_profile_library(path: Path) -> dict[EmitterClass,
     ThreatProfile]` loader function. **Pydantic only — the schema
     enforcement piggy-backs on the validator pattern already used
     in `rfmesh-contracts`.** The loader iterates `*.yaml` files in
     the directory, parses with `yaml.safe_load` (never
     `yaml.load`), constructs `ThreatProfile`s via
     `model_validate`, and surfaces every validation failure as a
     `ThreatProfileError` with a message naming the offending file
     and the field. Multiple files declaring the same
     `emitter_class` is a `ThreatProfileError` (would otherwise
     silently shadow).

   The split (`__init__.py` for the public API,
   `profile.py` for the schema + loader) keeps the test surface
   small and the loader code isolated for unit testing.

5. Test fixtures live in `packages/rfmesh-ml/tests/conftest.py`.
   The WS-B-005 ticket already plans a `conftest.py`; if it has not
   yet landed (see "Stop conditions" §B on the WS-B-005 dependency
   handshake), this ticket may create it and add the fixtures it
   needs (a synthetic-IQ helper for the classifier is not required
   here — WS-B-006 tests construct `ClassificationResult` values
   directly with no IQ involvement). If the WS-B-005 conftest *has*
   landed, this ticket extends it with:

   - a `make_classification_result(modulation_class, confidence,
     feature_dim=16) -> ClassificationResult` helper, returning a
     `ClassificationResult` with a finite all-zeros feature vector
     of the declared length. The helper does NOT exercise any
     classifier code path; it is a pure constructor for testing
     the wrapper.
   - a `bundled_profile_library_path` fixture pointing at
     `packages/rfmesh-ml/threats/profiles/` so tests can opt in to
     loading the v1.0.0 library.

   Synthetic-IQ helpers from WS-B-005 conftest are **not consumed**
   by WS-B-006 — the wrapper layer is tested against synthesised
   `ClassificationResult`s, not against IQ. This keeps the test
   suite fast and the layer boundary visible.

6. `uv run mypy packages/rfmesh-ml` is clean (strict mode, workspace
   default).

7. `uv run ruff check packages/rfmesh-ml` is clean.

8. Imports across the new source files are limited to:
     - `numpy`, `numpy.typing` (only if the result type carries the
       upstream `feature_vector`, which it should NOT — see API §3;
       likely no numpy import is needed in `threats/`)
     - stdlib (`pathlib`, `dataclasses`, `typing`, `collections.abc`,
       `logging`, `functools` for `lru_cache` if the builder uses
       it instead of a hand-rolled dict cache)
     - `yaml` (`pyyaml`) — the YAML loader; **uses `yaml.safe_load`
       exclusively** (never `yaml.load` without an explicit safe
       Loader argument). Forbidden silently-unsafe constructs in
       YAML (e.g. arbitrary Python object instantiation) are
       blocked by SafeLoader.
     - `pydantic` — already a workspace dependency via
       `rfmesh-contracts`; used here for the profile schema models.
     - `rfmesh_contracts.enums.EmitterClass` (read-only).
     - `rfmesh_ml.modulation_classifier.ClassificationResult` (the
       upstream type — read-only; this is an intra-package import,
       not cross-workstream).
   **No imports from any sibling workstream package** (no
   `rfmesh_sdr.*`, no `rfmesh_dsp.*`, no `rfmesh_node.*`, no
   `rfmesh_fusion.*`). `lint-imports` clean — the boundary is the
   `rfmesh-contracts` + intra-`rfmesh-ml` star.

9. `rfmesh-ml/pyproject.toml` already declares `threats` in the
   hatch wheel target (`[tool.hatch.build.targets.wheel]
   packages = ["src/rfmesh_ml", "threats"]`). **Verify this line
   is unchanged at the end of the ticket.** WS-B-005's builder
   may or may not have touched it; the wheel must still ship the
   `threats/` directory (the `profiles/*.yaml` subdirectory under
   it is bundled with the wheel via the directory inclusion). If
   the hatch target needs adjustment to include
   `threats/profiles/*.yaml` explicitly (some hatch versions
   require an `include`/`force-include` directive for non-Python
   data files), add the minimal directive; document it in the
   ticket's final test output.

10. `pyyaml` is added to `rfmesh-ml`'s `[project] dependencies` as
    a versioned constraint (`pyyaml>=6.0`) — **only after** the
    `/uvadd-request` lead approval gate clears (Stop condition §A).
    If `types-PyYAML` is required to satisfy mypy strict (likely),
    it is added to the workspace's dev dependencies in the same
    `/uvadd-request`. Pinned version ranges follow the workspace
    convention.

## Out of scope (explicit non-goals)

- **The modulation-class classifier itself.** WS-B-005 owns
  `modulation_classifier.py`, `features.py`, `rules.py` — this
  ticket consumes the `ClassificationResult` type and the
  `ModulationLabel` value space, and does not modify either.
- **Real-IQ-trained POLE21 / VOLNOREZ profiles.** Those require
  actual recordings and a follow-up training+labelling ticket
  (post-BoTH3). v1.0.0 ships these as **stubs with `stub: true`
  and a populated `stub_reason`** — the contract shape is stable;
  the classifier output is honest `UNKNOWN`.
- **CNN-based enrichment or neural rule learning.** Rules-based
  YAML matching is v1.0.0. The CNN backend (if WS-B-005's optional
  path is exercised in v1.5+) emits the same
  `ClassificationResult` and consumes the same YAML library — no
  change to the threats layer.
- **The node-runtime that wires WS-B-005 + WS-B-006 →
  `BearingReport.emitter_class`.** That is WS-D (rfmesh-node);
  see `INTERFACES.md` §3 for the `None` vs `UNKNOWN` distinction
  the node runtime preserves on the wire. This ticket exposes the
  function the node will call; it does not call it.
- **Hot-reload of YAML profiles at runtime.** Profiles load at
  first call and cache. An operator who edits a YAML must restart
  the node-runtime process. (An ADR-NNN on hot-reload is welcome
  if the field demand surfaces; not v1.0.0.)
- **A web UI or CLI for editing profiles.** The operator edits a
  text file. The demo's "open the YAML on a tablet next to ATAK"
  story uses the OS's text viewer, not a bespoke editor.
- **Modifying `rfmesh-contracts`.** Adding an `EmitterClass`
  member (e.g. `MAVLINK`, `LTE_JAMMER`) is a CHANGE-REQUEST ADR +
  `SCHEMA_VERSION` MINOR bump; this ticket does not propose or
  apply such additions.
- **Combining `snr_db` into the confidence formula.** The
  signature carries `snr_db` to avoid a future API break, but
  v1.0.0 ignores it. Documenting *what an honest SNR-aware
  formula would look like* is welcome in the module docstring;
  implementing it is v1.5+.

## Files you may touch

- `packages/rfmesh-ml/src/rfmesh_ml/threats/__init__.py`
  (create — the public enrichment API + `ThreatProfileError`).
  The directory currently exists with an empty `__init__.py`; the
  builder replaces that empty file with the public-API module.
- `packages/rfmesh-ml/src/rfmesh_ml/threats/profile.py`
  (create — `ThreatProfile` + `MatchRule` Pydantic models +
  `load_profile_library` loader).
- `packages/rfmesh-ml/threats/profiles/elrs.yaml` (create).
- `packages/rfmesh-ml/threats/profiles/crossfire.yaml` (create).
- `packages/rfmesh-ml/threats/profiles/gsm_jammer.yaml` (create).
- `packages/rfmesh-ml/threats/profiles/pole21.yaml` (create —
  **stub**, `stub: true`, `stub_reason` populated).
- `packages/rfmesh-ml/threats/profiles/volnorez.yaml` (create —
  **stub**, same shape).
- `packages/rfmesh-ml/threats/profiles/droneid.yaml` (create).
- `packages/rfmesh-ml/src/rfmesh_ml/__init__.py` (modify — re-export
  `enrich_to_emitter_class` and `ThreatProfileError` so callers
  can write `from rfmesh_ml import enrich_to_emitter_class`).
- `packages/rfmesh-ml/tests/test_threat_enrichment.py` (create —
  the ~14 tests enumerated in acceptance criterion 1).
- `packages/rfmesh-ml/tests/conftest.py` (create OR extend — see
  acceptance criterion 5 on the WS-B-005 dependency handshake).
- `packages/rfmesh-ml/pyproject.toml` (modify — **only after**
  the `/uvadd-request` approval clears; add `pyyaml>=6.0` to
  `[project] dependencies`; verify the hatch wheel target still
  includes `threats`).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- `packages/rfmesh-ml/src/rfmesh_ml/modulation_classifier.py`
  (WS-B-005 territory — read-only consumer at most).
- `packages/rfmesh-ml/src/rfmesh_ml/features.py` (WS-B-005).
- `packages/rfmesh-ml/src/rfmesh_ml/rules.py` (WS-B-005).
- Any sibling workstream package (`rfmesh-sdr`, `rfmesh-dsp`,
  `rfmesh-fusion`, `rfmesh-cot`, `rfmesh-node`, `rfmesh-ops`).
- The workspace root `pyproject.toml` or `uv.lock` directly —
  `pyyaml` is added via `uv add --package rfmesh-ml pyyaml`
  *after* lead approval, which mutates the lock file
  transactionally. No hand-editing of `uv.lock`.

## Stop conditions

### §A — pyyaml dependency gate (precedes coding)

`pyyaml` is **not** in the workspace lock at the time of this
draft. Before writing any code, the builder writes a
`/uvadd-request` scratchpad note under
`.claude/scratchpad/ws-b-006-uvadd-pyyaml-<date>.md` with the
following content:

- **Package**: `pyyaml`, version range `>=6.0`.
- **Classification**: runtime dependency of `rfmesh-ml`.
- **Rationale**: WS-B-006 (the threat-class enrichment layer)
  loads operator-extensible YAML profiles under
  `packages/rfmesh-ml/threats/profiles/`. YAML is the operator-
  facing artefact (Advantage #5 — *"open the YAML on a tablet
  next to ATAK"*); JSON or TOML would not deliver the same
  human-edit ergonomics for nested rule lists. `pyyaml` is the
  de-facto standard YAML library on PyPI; the safe loader
  (`yaml.safe_load`) is used exclusively to avoid the arbitrary-
  Python-object class of vulnerabilities.
- **Invariants check**: B1 not threatened (contracts unchanged);
  B3 not threatened (loader raises loudly); B5 not threatened
  (YAML loading is the documented "load vendor data tables at
  import" exception in Invariant 5, applied at the *first call*
  level for cache discipline).
- **Companion**: `types-PyYAML` added to workspace dev-deps if
  mypy strict surfaces missing stubs (likely).

The builder then **stops** and waits for lead approval. The lead
runs `uv add --package rfmesh-ml pyyaml` (and the types stub if
needed) at the workspace root. **The builder does NOT run
`uv add` itself** (forbidden per AGENTS.md §3).

### §B — WS-B-005 dependency handshake

WS-B-005 is drafted but at the time of this draft was **not
yet implemented** (no `modulation_classifier.py` exists, no
`tests/` directory exists under `packages/rfmesh-ml/`). The
builder reads the WS-B-005 ticket and confirms one of:

1. **WS-B-005 has merged** — `modulation_classifier.py`,
   `features.py`, `rules.py`, `tests/conftest.py` are on
   `main`. The builder imports `ClassificationResult` from the
   merged module and proceeds.
2. **WS-B-005 has NOT merged** — the builder coordinates with
   the lead on one of two paths:
   - (a) Wait for WS-B-005 to merge before starting WS-B-006.
   - (b) Define a minimal local `ClassificationResult`-shaped
     Protocol or duck-typed reference in the test layer (the
     ticket's tests construct `ClassificationResult` directly;
     if the type is unavailable, the tests use a structural
     stand-in). The source code under
     `src/rfmesh_ml/threats/` references the type by *string*
     annotation (`"ClassificationResult"`) under
     `from __future__ import annotations` and defers the import
     to runtime — this allows WS-B-006 to land independently of
     WS-B-005's implementation, with the tests asserting the
     wiring fully on merge. **Path (a) is preferred**; path (b)
     is a lead-approved escape if WS-B-005 slips.

   In either case, **STOP and check with the lead** before
   coding. Do not silently invent a local
   `ClassificationResult` type that diverges from WS-B-005's
   shipped shape.

### §C — Honesty gates (do not relax)

- **Stub-profile honesty.** A test fails because the POLE21 or
  VOLNOREZ profile match path returns `(EmitterClass.POLE21,
  some_confidence)` instead of `(EmitterClass.UNKNOWN, 0.0)` →
  **STOP**. Do not "fix the test" by relaxing the stub-handling
  assertion. The honesty rule is the demo's load-bearing claim
  ("we don't ship fake classifications"); a builder who weakens
  it produces a fake demo. Scratchpad and stop.
- **Multi-match honesty.** If the `test_enrich_crossfire_868mhz`
  test ends up asserting `EmitterClass.UNKNOWN` because v1.0.0
  cannot distinguish ELRS from Crossfire at 868 MHz LoRa, that
  outcome is **acceptable and shipped** — document the limitation
  in the profile YAMLs' `notes` and in the module docstring.
  Choosing one over the other arbitrarily (e.g. always returning
  ELRS because alphabetically-first) is **not acceptable** — it
  silently corrupts the demo. Spawn `rf-dsp-specialist` if you
  believe an honest discriminator exists (hop-rate band, frame
  structure, dwell time); do not invent one.
- **Schema-strictness honesty.** A malformed YAML, schema-invalid
  YAML, or stub-without-reason that does **not** raise
  `ThreatProfileError` → **STOP**. The schema gate is the operator-
  credibility surface; relaxing it means an operator can ship a
  broken profile to production and the system silently swallows
  the error. Invariant B3 in the data-load layer.

### §D — Standard ticket stop pattern

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation the full output of:
  - `uv run pytest packages/rfmesh-ml -v`
  - `uv run mypy packages/rfmesh-ml`
  - `uv run ruff check packages/rfmesh-ml`
  - `uv run lint-imports` (if available in this workspace; if
    not, note that explicitly).
- If a contract change appears necessary (e.g. adding a new
  `EmitterClass` member to support a captured emitter), STOP and
  write `docs/adr/ADR-NNN-<short>.md` PROPOSED. Do not modify
  `rfmesh-contracts`.
- If you find that WS-B-005's `ClassificationResult` shape diverges
  from what this ticket assumes (e.g. WS-B-005 ships a different
  field name for `modulation_class`), STOP and write a SCRATCHPAD
  note. Do not modify WS-B-005's module.
- If the bundled profile library cannot be made to pass its own
  schema gate (e.g. the GSM_JAMMER rule the builder writes is
  rejected by `ThreatProfile.model_validate`), STOP. The schema is
  the contract; the data conforms to it, not the other way around.

## Implementation notes (non-binding, for guidance)

### ELRS vs Crossfire discriminator (the known hard case)

ELRS and Crossfire **both** operate on 868/915 MHz with LoRa-based
FHSS modulation, and both are likely to be classified as
`modulation_class="lora"` by WS-B-005. The naive v1.0.0 rule
"868 MHz + LoRa → ELRS" is dishonest because Crossfire is equally
valid.

Honest options for the builder, in order of preference:

1. **Hop-rate band**: ELRS hops at ~250 Hz (default 250 Hz packet
   rate, one hop per packet), Crossfire hops at ~150 Hz (50 Hz
   default packet rate with three-frame frequency rotation, or
   higher depending on mode). If WS-B-005's `feature_vector[5]`
   (hop-rate detector peak) is exposed as a stable feature, the
   YAML rule can encode `hop_rate_hz: [200, 300]` for ELRS and
   `hop_rate_hz: [100, 200]` for Crossfire. Requires extending the
   YAML `MatchRule` schema with an optional `hop_rate_hz` field
   AND extending `enrich_to_emitter_class` to consult
   `modulation_result.feature_vector[5]` against it. **Spawn
   `rf-dsp-specialist`** to confirm the canonical hop-rate values
   and that WS-B-005's feature index 5 is stable enough to key off.
2. **Multi-match UNKNOWN** (the v1.0.0 default): both profiles
   match → return `EmitterClass.UNKNOWN` with the upstream
   confidence. Documented as a known v1.0.0 limitation. The
   demo's narrative becomes: *"We honestly cannot distinguish
   ELRS from Crossfire on modulation + frequency alone; the
   threat library shows both candidates; the operator decides."*
   This is a more credible demo line to an RF/EW jury than a
   coin-flip.
3. **Priority annotation**: a `priority: int` field on rules,
   lower-wins. Permitted but not preferred at v1.0.0 because it
   buries a decision in YAML without operator-visible
   justification. If used, the priority is documented in the
   profile YAML's `notes` field with the empirical basis.

The builder picks one option, documents the choice in the
profile YAMLs' `notes` fields, and aligns the
`test_enrich_crossfire_868mhz` assertion with the choice. Either
"asserts ELRS" (option 1 or 3 with ELRS-preferred) or "asserts
UNKNOWN" (option 2) is acceptable; choosing arbitrarily without
documentation is not.

### GSM_JAMMER and DroneID at v1.0.0

`INTERFACES.md` §1 explicitly states that `GSM_JAMMER` is
*"distinctive in context (lone uplink burst from a static,
non-infrastructure location), not in waveform."* The v1.0.0
profile cannot honestly distinguish a GSM-band jammer from a
legitimate handset on modulation + frequency alone. The profile
therefore matches **any FSK-like modulation in the GSM bands**
(uplink 880–915 MHz, downlink 925–960 MHz; the builder picks
which band(s) to include and documents the choice in `notes`),
and the demo narrative documents that operator context (the
fusion server's `node_position` of the bearings, the absence of
nearby cell-tower infrastructure) is what turns a match into a
real GSM_JAMMER classification — not the threat library alone.

DroneID's v1.0.0 profile is similarly thin: the real DroneID /
OcuSync waveform is OFDM-based and WS-B-005 does not classify
OFDM as a modulation family. The v1.0.0 profile matches a generic
narrow-burst FSK-or-FHSS heuristic in the 2.4 GHz ISM band and
ships with `notes` documenting that real OFDM classification is a
v1.5+ ticket. The honesty rule on UNKNOWN-at-low-confidence
applies normally.

### Profile YAML examples (non-binding shapes)

ELRS:

```yaml
emitter_class: elrs
human_name: "ExpressLRS R/C control link (LoRa-FHSS)"
references:
  - "https://www.expresslrs.org/"
  - "Semtech SX1276 datasheet (LoRa modulation)"
match_rules:
  - modulation_class: lora
    freq_band_hz: [863.0e6, 870.0e6]
    min_confidence: 0.6
    notes: "EU 868 MHz ISM band."
  - modulation_class: lora
    freq_band_hz: [2.4e9, 2.485e9]
    min_confidence: 0.6
    notes: "2.4 GHz ISM band."
stub: false
stub_reason: ""
```

POLE21 (stub):

```yaml
emitter_class: pole21
human_name: "Pole-21 GNSS jamming complex"
references: []
match_rules:
  - modulation_class: fhss
    freq_band_hz: [1.56e9, 1.59e9]
    min_confidence: 0.5
    notes: "GPS L1 ±15 MHz; indicative only, real-IQ training pending."
stub: true
stub_reason: |
  Awaiting real-IQ capture and classifier training. v1.0.0 ships
  a structural placeholder per INTERFACES.md §1 stub-at-v1.0.0
  designation. The enrichment layer returns EmitterClass.UNKNOWN
  on any match against this profile (rfmesh_ml.threats honesty
  rule).
```

### Why this exists

The threat library is **Advantage #5** in the BoTH3 pitch (`docs/ADVANTAGES.md`
§0):

> *"Open, extensible threat library as moat. The L3 classifier in
> `rfmesh-ml/threats/` is one Python module per emitter class
> (ELRS, Crossfire, GSM jammer, Pole-21, Volnorez, DroneID,
> UNKNOWN). Adding a new threat is a YAML profile + a Python
> module — no firmware update, no vendor sign-off. RfPatrol Mk2
> ships a closed library at €5k+; Bukovel-AD keeps its library
> classified. We ship the structure as v1.0 and let operators
> extend it. This is the moat — not the v1.0 library contents
> (stubs for the classified threats are honest about what is
> unverified), but the open extension model."*

v1.0.0 ships:

- **4 real profiles** (ELRS, CROSSFIRE, GSM_JAMMER, DRONEID) with
  honest scope limitations documented in each profile's `notes`.
- **2 documented stubs** (POLE21, VOLNOREZ) that match the demo
  but return UNKNOWN — the credibility-preserving placeholder.
- **The structural extensibility** (YAML schema + dynamic loader
  + `test_yaml_profile_dynamically_loaded`) that lets an
  operator add a new emitter post-deployment by writing one
  YAML file. **That test is the moat in test form.**

The demo line: *"Here is the threat profile for this emitter,
open on the tablet. We can add new ones in the field by editing
this file — no firmware update, no vendor sign-off."* The
contrast with RfPatrol Mk2's €5k+ closed library and
Bukovel-AD's classified library is the pitch.
