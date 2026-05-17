"""Acceptance tests for the WS-B-006 threat-class enrichment layer.

The 14+ tests enumerated in ``docs/tickets/WS-B-006-threat-library.md``
acceptance criterion 1, plus a few structural-coverage extras. Each test
is named so a failure points at exactly one property: shape, per-class
positive match, stub honesty (POLE21, VOLNOREZ), multi-match honesty
(ELRS-vs-Crossfire at 868 MHz), out-of-band, upstream-unknown
pass-through, below-min-confidence floor, dynamic load, schema-gate
failures (malformed YAML, schema-invalid YAML, stub-without-reason), and
public-surface lock.

Determinism: WS-B-006 has no randomised inputs -- tests synthesise
``ClassificationResult`` values directly via the
``make_classification_result`` fixture and call
``enrich_to_emitter_class`` on them. No IQ generation, no rng.

Honesty discipline (Invariant B3) -- the tests that *encode* the
honesty rules, not just the happy paths:

* ``test_enrich_pole21_returns_unknown`` and
  ``test_enrich_volnorez_returns_unknown`` -- stub profiles must return
  ``(EmitterClass.UNKNOWN, 0.0)``, never the stub label itself.
* ``test_enrich_crossfire_868mhz_returns_unknown_multimatch`` -- ELRS
  and Crossfire both match 868 MHz LoRa; v1.0.0 cannot honestly
  distinguish them, so the wrapper returns UNKNOWN with the upstream
  confidence. See elrs.yaml / crossfire.yaml ``notes`` for the
  documented limitation.
* ``test_enrich_below_rule_min_confidence_returns_unknown`` -- the
  rule's ``min_confidence`` floor is honoured; the wrapper does not
  silently relax it.
* ``test_malformed_yaml_raises``, ``test_schema_invalid_yaml_raises``,
  ``test_stub_profile_requires_stub_reason`` -- the schema gate raises
  ``ThreatProfileError`` loudly. Naming the offending file is part of
  the operator-credibility surface.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from rfmesh_contracts.enums import EmitterClass
from rfmesh_ml import ClassificationResult, enrich_to_emitter_class
from rfmesh_ml import threats as threats_mod
from rfmesh_ml.rules import ModulationLabel
from rfmesh_ml.threats import ThreatProfileError
from rfmesh_ml.threats.profile import load_profile_library

_CONFIDENCE_FLOOR_FOR_POSITIVE: float = 0.5
"""Lower bound asserted on a positive (non-stub, single-match) call.
Mirrors the ticket's acceptance lines that read ``conf >= 0.5``."""

_TUPLE_LENGTH: int = 2
"""Expected length of the ``enrich_to_emitter_class`` return tuple
(emitter_class, confidence). Named to satisfy ruff's PLR2004 even
where per-file-ignores would otherwise relax it."""


# ---------------------------------------------------------------------------
# Shape / structural
# ---------------------------------------------------------------------------


def test_enrichment_result_shape(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """``enrich_to_emitter_class`` returns ``(EmitterClass, float)``.

    The tuple shape is the contract. The float must be in ``[0.0, 1.0]``.
    """
    result = make_classification_result("lora", 0.9)
    out = enrich_to_emitter_class(result, center_freq_hz=868.5e6, snr_db=15.0)

    assert isinstance(out, tuple)
    assert len(out) == _TUPLE_LENGTH
    emitter_class, confidence = out
    assert isinstance(emitter_class, EmitterClass)
    assert isinstance(confidence, float)
    assert 0.0 <= confidence <= 1.0


def test_imports_are_clean() -> None:
    """``rfmesh_ml.threats.__all__`` locks the public surface.

    Exactly ``{"enrich_to_emitter_class", "ThreatProfileError"}`` --
    a follow-up cannot silently widen the surface.
    """
    assert set(threats_mod.__all__) == {"enrich_to_emitter_class", "ThreatProfileError"}


# ---------------------------------------------------------------------------
# Positive single-match: ELRS, GSM_JAMMER, DRONEID
# ---------------------------------------------------------------------------


def test_enrich_elrs_2_4ghz(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """LoRa @ 2.45 GHz -> ELRS unambiguously (Crossfire does not target 2.4 GHz)."""
    result = make_classification_result("lora", 0.9)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=2.45e9, snr_db=15.0)

    assert emitter_class is EmitterClass.ELRS
    assert confidence >= _CONFIDENCE_FLOOR_FOR_POSITIVE


def test_enrich_gsm_jammer_900mhz(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """FSK @ 935 MHz (GSM-900 downlink) -> GSM_JAMMER.

    Pins the contract that the profile matches, NOT the claim that the
    system distinguishes a real GSM jammer from a legitimate handset --
    that needs operator context per INTERFACES.md §1.
    """
    result = make_classification_result("fsk", 0.8)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=935.0e6, snr_db=12.0)

    assert emitter_class is EmitterClass.GSM_JAMMER
    assert confidence >= _CONFIDENCE_FLOOR_FOR_POSITIVE


def test_enrich_droneid_2_4ghz(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """FSK @ 2.437 GHz -> DRONEID (v1.0.0 thin proxy; real OFDM is v1.5+)."""
    result = make_classification_result("fsk", 0.85)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=2.437e9, snr_db=10.0)

    assert emitter_class is EmitterClass.DRONEID
    assert confidence >= _CONFIDENCE_FLOOR_FOR_POSITIVE


# ---------------------------------------------------------------------------
# Multi-match honesty: ELRS vs Crossfire at 868 MHz returns UNKNOWN.
# This is the v1.0.0 documented limitation (see elrs.yaml / crossfire.yaml
# notes). Choosing one arbitrarily would silently corrupt the demo.
# ---------------------------------------------------------------------------


def test_enrich_elrs_868mhz_returns_unknown_multimatch(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """LoRa @ 868 MHz -- ELRS and Crossfire both match -> UNKNOWN.

    The honest answer when v1.0.0 cannot distinguish two LoRa-FHSS
    candidates on band+modulation alone. Upstream confidence flows
    through; the threat-library placement is what failed.
    """
    upstream_confidence = 0.9
    result = make_classification_result("lora", upstream_confidence)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=868.5e6, snr_db=15.0)

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == pytest.approx(upstream_confidence)


def test_enrich_crossfire_868mhz_returns_unknown_multimatch(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """Same input as the ELRS-868 test; documents that Crossfire-868
    also yields UNKNOWN by the multi-match honesty policy.

    Two tests for the *same* input are deliberate -- one is named after
    each candidate so a future regression that drives the wrapper to
    return ELRS (or Crossfire) is caught from either reading direction.
    """
    upstream_confidence = 0.9
    result = make_classification_result("lora", upstream_confidence)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=868.5e6, snr_db=15.0)

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == pytest.approx(upstream_confidence)


# ---------------------------------------------------------------------------
# Stub honesty: POLE21 and VOLNOREZ return (UNKNOWN, 0.0).
# This is the load-bearing demo claim: "we don't ship fake classifications."
# ---------------------------------------------------------------------------


def test_enrich_pole21_returns_unknown(
    make_classification_result: Callable[..., ClassificationResult],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FHSS @ GPS L1 -- POLE21 stub matches band, returns (UNKNOWN, 0.0).

    Zero confidence (not upstream confidence) because the stub is a
    placeholder, not a classification. A demo line "POLE21 detected at
    0.0 confidence" is honest; "POLE21 detected at 0.9 confidence" is
    a lie.
    """
    result = make_classification_result("fhss", 0.9)
    with caplog.at_level(logging.INFO, logger="rfmesh_ml.threats"):
        emitter_class, confidence = enrich_to_emitter_class(
            result, center_freq_hz=1.575e9, snr_db=15.0
        )

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == 0.0
    # Operator trace: an INFO log must record the stub match by name.
    log_text = caplog.text.lower()
    assert "matched stub" in log_text
    assert "pole21" in log_text


def test_enrich_volnorez_returns_unknown(
    make_classification_result: Callable[..., ClassificationResult],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FHSS @ 2.4 GHz -- VOLNOREZ stub matches band, returns (UNKNOWN, 0.0).

    Same shape as the POLE21 test, different stub. The 2.4 GHz FHSS path
    also overlaps DroneID's FHSS rule but the stub-first scan short-
    circuits before any non-stub match is considered (stub-honesty wins
    over any real classification, by design).
    """
    result = make_classification_result("fhss", 0.85)
    with caplog.at_level(logging.INFO, logger="rfmesh_ml.threats"):
        emitter_class, confidence = enrich_to_emitter_class(
            result, center_freq_hz=2.45e9, snr_db=15.0
        )

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == 0.0
    log_text = caplog.text.lower()
    assert "matched stub" in log_text
    assert "volnorez" in log_text


# ---------------------------------------------------------------------------
# Out-of-band: upstream confidence is preserved, no class is invented.
# ---------------------------------------------------------------------------


def test_enrich_out_of_band_returns_unknown(
    make_classification_result: Callable[..., ClassificationResult],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LoRa @ 5.8 GHz -- no rule covers this band -> UNKNOWN, upstream conf.

    Upstream confidence (0.95) is *preserved* because the modulation
    classifier was confident; only the threat-library placement failed.
    No log spam expected (the stub-overlap log only fires on a stub
    match, not on no-match).
    """
    upstream_confidence = 0.95
    result = make_classification_result("lora", upstream_confidence)
    with caplog.at_level(logging.INFO, logger="rfmesh_ml.threats"):
        emitter_class, confidence = enrich_to_emitter_class(
            result, center_freq_hz=5.8e9, snr_db=20.0
        )

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == pytest.approx(upstream_confidence)
    # No "matched stub" log on a pure no-match path.
    assert "matched stub" not in caplog.text.lower()


# ---------------------------------------------------------------------------
# Upstream-unknown pass-through: no rule is consulted.
# ---------------------------------------------------------------------------


def test_enrich_upstream_unknown_passes_through(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """Upstream ``modulation_class="unknown"`` -> UNKNOWN, upstream conf.

    The wrapper does not consult any rule when the upstream layer was
    itself unsure. The wrapper inherits the upstream uncertainty
    verbatim.
    """
    upstream_confidence = 0.3
    result = make_classification_result("unknown", upstream_confidence)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=868.5e6, snr_db=5.0)

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == pytest.approx(upstream_confidence)


# ---------------------------------------------------------------------------
# Min-confidence floor honesty: rule's threshold is not silently relaxed.
# ---------------------------------------------------------------------------


def test_enrich_below_rule_min_confidence_returns_unknown(
    make_classification_result: Callable[..., ClassificationResult],
) -> None:
    """LoRa @ 2.45 GHz with conf=0.5 < ELRS rule's min_confidence=0.6.

    No match -> UNKNOWN with upstream confidence preserved. The wrapper
    does NOT relax the rule's min_confidence threshold (Invariant B3).
    The 2.4 GHz band is used here (not 868 MHz) so the multi-match
    UNKNOWN does not mask the below-floor UNKNOWN -- this test must
    fail loudly if the floor is ever removed.
    """
    upstream_confidence = 0.5
    result = make_classification_result("lora", upstream_confidence)
    emitter_class, confidence = enrich_to_emitter_class(result, center_freq_hz=2.45e9, snr_db=8.0)

    assert emitter_class is EmitterClass.UNKNOWN
    assert confidence == pytest.approx(upstream_confidence)


# ---------------------------------------------------------------------------
# Dynamic load: the load-bearing extensibility test.
# An operator can drop a YAML file under threats/profiles/ and the
# enrichment layer consumes it without recompilation. Advantage #5 in
# test form. If this test fails the ticket fails.
# ---------------------------------------------------------------------------


def test_yaml_profile_dynamically_loaded(
    make_classification_result: Callable[..., ClassificationResult],
    tmp_path: Path,
) -> None:
    """Write a narrow ELRS-variant profile to ``tmp_path``; assert it is loaded.

    The temporary YAML declares ``emitter_class: elrs`` and a narrow
    LoRa rule at 866-866.5 MHz. Calling
    ``enrich_to_emitter_class(..., profile_library_path=tmp_path)`` with
    upstream LoRa @ 866.25 MHz must return ``EmitterClass.ELRS`` -- the
    enum member declared in the YAML. The bundled library is NOT merged
    in (explicit override semantics), so there is no Crossfire to
    multi-match against.
    """
    profile_yaml = """
emitter_class: elrs
human_name: "Test-only narrow ELRS variant"
references:
  - "WS-B-006 test_yaml_profile_dynamically_loaded synthetic profile"
match_rules:
  - modulation_class: lora
    freq_band_hz: [866.0e6, 866.5e6]
    min_confidence: 0.5
    notes: "Synthetic narrow window for the dynamic-load extensibility test."
stub: false
stub_reason: ""
"""
    (tmp_path / "narrow_elrs.yaml").write_text(profile_yaml, encoding="utf-8")

    result = make_classification_result("lora", 0.85)
    emitter_class, confidence = enrich_to_emitter_class(
        result,
        center_freq_hz=866.25e6,
        snr_db=15.0,
        profile_library_path=tmp_path,
    )

    assert emitter_class is EmitterClass.ELRS
    assert confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Schema-gate failures: malformed YAML, schema-invalid YAML, stub-no-reason.
# Each must raise ThreatProfileError with a message naming the offending
# file. Invariant B3: never silently skip a broken profile.
# ---------------------------------------------------------------------------


def test_malformed_yaml_raises(
    make_classification_result: Callable[..., ClassificationResult],
    tmp_path: Path,
) -> None:
    """A syntactically broken YAML raises ``ThreatProfileError`` naming the file."""
    broken_path = tmp_path / "broken.yaml"
    # Unclosed bracket -- yaml.safe_load raises yaml.YAMLError.
    broken_path.write_text("emitter_class: elrs\nmatch_rules: [\n", encoding="utf-8")

    result = make_classification_result("lora", 0.9)
    with pytest.raises(ThreatProfileError) as excinfo:
        enrich_to_emitter_class(
            result,
            center_freq_hz=868.5e6,
            profile_library_path=tmp_path,
        )
    # The error message names the offending file (operator-credibility surface).
    assert "broken.yaml" in str(excinfo.value)


def test_schema_invalid_yaml_raises(
    make_classification_result: Callable[..., ClassificationResult],
    tmp_path: Path,
) -> None:
    """YAML that parses cleanly but violates the schema raises ThreatProfileError.

    Here: ``emitter_class: not_a_real_class`` -- the Pydantic model
    rejects an EmitterClass value outside the enum's value-space.
    """
    bad_schema_path = tmp_path / "bad_schema.yaml"
    bad_schema_path.write_text(
        "emitter_class: not_a_real_class\n"
        'human_name: "x"\n'
        "match_rules: []\n"
        "stub: false\n"
        'stub_reason: ""\n',
        encoding="utf-8",
    )

    result = make_classification_result("lora", 0.9)
    with pytest.raises(ThreatProfileError) as excinfo:
        enrich_to_emitter_class(
            result,
            center_freq_hz=868.5e6,
            profile_library_path=tmp_path,
        )
    assert "bad_schema.yaml" in str(excinfo.value)


def test_stub_profile_requires_stub_reason(
    make_classification_result: Callable[..., ClassificationResult],
    tmp_path: Path,
) -> None:
    """A ``stub: true`` profile without ``stub_reason`` raises ThreatProfileError.

    Operator-credibility gate: an unexplained stub is indistinguishable
    from a forgotten work-in-progress.
    """
    stub_no_reason_path = tmp_path / "stub_no_reason.yaml"
    stub_no_reason_path.write_text(
        "emitter_class: pole21\n"
        'human_name: "Pole-21 (test stub, no reason)"\n'
        "references: []\n"
        "match_rules: []\n"
        "stub: true\n"
        'stub_reason: ""\n',
        encoding="utf-8",
    )

    result = make_classification_result("fhss", 0.9)
    with pytest.raises(ThreatProfileError) as excinfo:
        enrich_to_emitter_class(
            result,
            center_freq_hz=1.575e9,
            profile_library_path=tmp_path,
        )
    assert "stub_no_reason.yaml" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Structural coverage of the bundled v1.0.0 library: all six YAMLs load
# cleanly with no schema gate firing. This is the "the library we ship
# actually conforms to its own schema" anchor test.
# ---------------------------------------------------------------------------


def test_bundled_library_loads_all_six_profiles(
    bundled_profile_library_path: Path,
) -> None:
    """The six bundled YAMLs (elrs, crossfire, gsm_jammer, droneid, pole21,
    volnorez) load and produce exactly the six expected ``EmitterClass``
    members. Acceptance criterion 2 in shape form."""
    library = load_profile_library(bundled_profile_library_path)
    expected = {
        EmitterClass.ELRS,
        EmitterClass.CROSSFIRE,
        EmitterClass.GSM_JAMMER,
        EmitterClass.POLE21,
        EmitterClass.VOLNOREZ,
        EmitterClass.DRONEID,
    }
    assert set(library.keys()) == expected
    # Stub flags reflect the v1.0.0 designation (INTERFACES.md §1).
    assert library[EmitterClass.POLE21].stub is True
    assert library[EmitterClass.VOLNOREZ].stub is True
    assert library[EmitterClass.ELRS].stub is False
    assert library[EmitterClass.CROSSFIRE].stub is False
    assert library[EmitterClass.GSM_JAMMER].stub is False
    assert library[EmitterClass.DRONEID].stub is False


def test_duplicate_emitter_class_across_files_raises(
    make_classification_result: Callable[..., ClassificationResult],
    tmp_path: Path,
) -> None:
    """Two YAML files declaring the same emitter_class raises ThreatProfileError.

    Silent shadowing would be a B3 violation. The loader detects the
    collision deterministically (sorted file order) and surfaces it.
    """
    profile_text = (
        "emitter_class: elrs\n"
        'human_name: "ELRS duplicate test"\n'
        "references:\n"
        '  - "duplicate-detector test"\n'
        "match_rules:\n"
        "  - modulation_class: lora\n"
        "    freq_band_hz: [863.0e6, 870.0e6]\n"
        "    min_confidence: 0.6\n"
        '    notes: ""\n'
        "stub: false\n"
        'stub_reason: ""\n'
    )
    (tmp_path / "a_elrs.yaml").write_text(profile_text, encoding="utf-8")
    (tmp_path / "b_elrs.yaml").write_text(profile_text, encoding="utf-8")

    result = make_classification_result("lora", 0.9)
    with pytest.raises(ThreatProfileError) as excinfo:
        enrich_to_emitter_class(
            result,
            center_freq_hz=868.5e6,
            profile_library_path=tmp_path,
        )
    # The second-loaded file is named in the error (sorted-order: b_elrs.yaml).
    assert "b_elrs.yaml" in str(excinfo.value)


@pytest.mark.parametrize(
    "label",
    ["cw", "fhss", "fsk", "lora", "unknown"],
)
def test_enrich_accepts_every_modulation_label(
    make_classification_result: Callable[..., ClassificationResult],
    label: ModulationLabel,
) -> None:
    """Every value in ``ModulationLabel`` is a valid wrapper input.

    Structural coverage: a future WS-B-005 change that renames a label
    will break this test as a fail-loud signal (instead of producing
    silently-no-match enrichment outputs).
    """
    result = make_classification_result(label, 0.7)
    # Use a band that does NOT match any non-stub bundled rule for any
    # of these labels (5.0 GHz is outside ELRS / Crossfire / GSM /
    # DroneID windows), so each call exercises the no-match path
    # without entangling the assertion with label-specific positives.
    emitter_class, _ = enrich_to_emitter_class(result, center_freq_hz=5.0e9, snr_db=10.0)
    # Every call returns a valid EmitterClass member; the specific
    # member depends on whether stubs overlap, but the call must not
    # raise and must not return a non-EmitterClass.
    assert isinstance(emitter_class, EmitterClass)
