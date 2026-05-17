"""Threat-class enrichment layer (WS-B-006).

Maps the upstream modulation-class result from WS-B-005
(``ClassificationResult.modulation_class`` in
``{"cw", "fhss", "fsk", "lora", "unknown"}``) plus RF context
(``center_freq_hz``, optionally ``snr_db``) onto
``rfmesh_contracts.enums.EmitterClass`` via an operator-editable YAML
profile library bundled at ``packages/rfmesh-ml/threats/profiles/``.

This module is **Advantage #5** in the BoTH3 pitch: the open,
extensible threat library that contrasts with closed commercial gear
(RfPatrol Mk2 at EUR 5k+) and classified military libraries
(Bukovel-AD). An operator on a tablet can open the matched YAML next to
the ATAK marker and explain what was matched; an operator in the field
can add a new emitter by dropping a YAML file under
``threats/profiles/`` -- no firmware update, no vendor sign-off.

Honesty rules (Invariant B3 -- no silent fallbacks)
---------------------------------------------------

The four load-bearing honesty gates this module enforces:

1. **Stub profiles return UNKNOWN.** ``POLE21`` and ``VOLNOREZ`` ship
   as ``stub: true`` placeholders pending real-IQ capture. When a stub
   profile's rule matches the inputs, ``enrich_to_emitter_class``
   returns ``(EmitterClass.UNKNOWN, 0.0)`` -- never the stub label
   itself. An INFO-level log records that the stub was matched so an
   operator can trace why a candidate emitter was downgraded to
   UNKNOWN. The demo line "we don't ship fake classifications" depends
   on this.
2. **Multi-match returns UNKNOWN.** ELRS and Crossfire both operate
   on 868 MHz with LoRa-based FHSS modulation; v1.0.0 cannot honestly
   distinguish them on modulation + frequency alone. When two or more
   non-stub profiles match the same inputs, the wrapper returns
   ``(EmitterClass.UNKNOWN, upstream_confidence)``. The demo narrative
   becomes "we honestly cannot distinguish ELRS from Crossfire on
   modulation + frequency alone; the threat library shows both
   candidates; the operator decides." Choosing one arbitrarily would
   be dishonest.
3. **Out-of-band returns UNKNOWN.** A modulation-classified input
   whose ``center_freq_hz`` matches no rule's ``freq_band_hz`` window
   returns ``(EmitterClass.UNKNOWN, upstream_confidence)``. The
   upstream confidence is *preserved* because the modulation
   classifier was confident -- only the threat-library placement
   failed.
4. **Upstream "unknown" passes through.** If the modulation classifier
   itself said ``"unknown"``, no rule is consulted -- the wrapper
   inherits the upstream uncertainty and returns
   ``(EmitterClass.UNKNOWN, upstream_confidence)``.

Confidence policy
-----------------

When a single non-stub profile matches and the rule's
``min_confidence`` floor is honoured, the returned confidence is
``modulation_result.confidence`` itself -- the wrapper does NOT
inflate the upstream confidence (a rule matching does not make the
modulation classifier more certain than it already reported). A
future v1.5+ ticket may fold ``snr_db`` and rule-specific evidence
into a combined score; the signature carries ``snr_db`` forward to
avoid a future API break, but v1.0.0 ignores it.

Profile load lifecycle
----------------------

Profiles load at first call and cache in a module-level dict keyed by
the resolved ``profile_library_path``. The bundled library and any
operator-supplied override (e.g. ``tmp_path`` in tests) coexist in the
cache without collision. The cache is **not** invalidated during a
process lifetime -- an operator who edits a YAML must restart the
node-runtime. (Hot-reload is explicitly out-of-scope for v1.0.0; an
ADR is welcome if field demand surfaces.)

Public surface
--------------

``enrich_to_emitter_class`` and ``ThreatProfileError`` are the only
re-exports. ``__all__`` locks the surface so a follow-up cannot
silently widen it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rfmesh_contracts.enums import EmitterClass

from rfmesh_ml.modulation_classifier import ClassificationResult
from rfmesh_ml.threats.profile import (
    MatchRule,
    ThreatProfile,
    ThreatProfileError,
    load_profile_library,
)

__all__ = ["ThreatProfileError", "enrich_to_emitter_class"]

_log = logging.getLogger(__name__)

# Path-keyed cache. Each distinct ``profile_library_path`` resolves to
# its own snapshot of the on-disk library; the bundled directory and a
# test's ``tmp_path`` override do not collide. ``Path.resolve()`` is
# used as the key so symlinked aliases of the same directory share a
# cache entry.
_LIBRARY_CACHE: dict[Path, dict[EmitterClass, ThreatProfile]] = {}

# Bundled profile library: package-relative directory containing the
# v1.0.0 six YAML files (ELRS, CROSSFIRE, GSM_JAMMER, POLE21, VOLNOREZ,
# DRONEID). The path is computed at import time from this module's
# location -- works for both editable installs and wheels, because the
# ``threats`` directory is shipped as a hatch wheel target (see
# ``rfmesh-ml/pyproject.toml`` ``[tool.hatch.build.targets.wheel]``).
_BUNDLED_PROFILES_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent.parent / "threats" / "profiles"
)


def _get_library(path: Path | None) -> dict[EmitterClass, ThreatProfile]:
    """Return the cached profile library for ``path`` (or the bundled one)."""
    target = (path if path is not None else _BUNDLED_PROFILES_DIR).resolve()
    cached = _LIBRARY_CACHE.get(target)
    if cached is not None:
        return cached
    library = load_profile_library(target)
    _LIBRARY_CACHE[target] = library
    return library


def _rule_matches(
    rule: MatchRule, modulation_class: str, center_freq_hz: float, confidence: float
) -> bool:
    """Check if a single rule matches the inputs."""
    if rule.modulation_class != modulation_class:
        return False
    low, high = rule.freq_band_hz
    if not (low <= center_freq_hz <= high):
        return False
    return confidence >= rule.min_confidence


def _profile_has_freq_overlap(
    profile: ThreatProfile, modulation_class: str, center_freq_hz: float
) -> bool:
    """Check if any rule's modulation + freq window covers the inputs.

    Used only for stub-profile detection -- a stub profile that
    overlaps on band/modulation triggers the UNKNOWN + INFO-log path
    even if the upstream confidence falls below the stub's
    ``min_confidence``. We deliberately do not gate stub-detection on
    confidence because the honesty rule is "we have a placeholder for
    this band; we are not classifying it". The stub log fires only on
    stub profiles, so non-stub profiles are unaffected.
    """
    for rule in profile.match_rules:
        if rule.modulation_class != modulation_class:
            continue
        low, high = rule.freq_band_hz
        if low <= center_freq_hz <= high:
            return True
    return False


def enrich_to_emitter_class(
    modulation_result: ClassificationResult,
    *,
    center_freq_hz: float,
    snr_db: float | None = None,
    profile_library_path: Path | None = None,
) -> tuple[EmitterClass, float]:
    """Map a modulation-class result to an ``EmitterClass`` via the YAML library.

    Parameters
    ----------
    modulation_result
        Output of ``ModulationClassifier.classify`` (WS-B-005). The
        wrapper consults ``modulation_class`` and ``confidence``;
        ``feature_vector`` is ignored.
    center_freq_hz
        Tuning centre when the IQ was captured, Hz. Used to match the
        rule's ``freq_band_hz`` window.
    snr_db
        Optional SNR estimate; reserved for v1.5+ rule refinements.
        v1.0.0 enrichment does not consume it but the signature
        carries it forward to avoid a future API break.
    profile_library_path
        Override for the bundled profile directory
        (``packages/rfmesh-ml/threats/profiles/``). When ``None``, the
        bundled library is used. When supplied, ONLY the supplied
        directory's profiles are consulted (the bundled library is NOT
        merged in) -- this keeps the dynamic-load test hermetic and
        makes operator overrides explicit.

    Returns
    -------
    tuple[EmitterClass, float]
        The matched class and a combined confidence in ``[0.0, 1.0]``.
        On stub-match: ``(EmitterClass.UNKNOWN, 0.0)``. On multi-match,
        out-of-band, upstream-unknown, or below-min-confidence:
        ``(EmitterClass.UNKNOWN, modulation_result.confidence)``.

    Raises
    ------
    ThreatProfileError
        If the profile library cannot be loaded (malformed YAML,
        schema-invalid YAML, missing required fields, invalid
        ``emitter_class`` value, stub without ``stub_reason``).
    """
    library = _get_library(profile_library_path)
    upstream_class = modulation_result.modulation_class
    upstream_conf = modulation_result.confidence

    # Honesty rule 4: upstream "unknown" passes through unchanged.
    # No rule is consulted -- if the modulation layer is unsure, the
    # wrapper inherits that uncertainty.
    if upstream_class == "unknown":
        return (EmitterClass.UNKNOWN, upstream_conf)

    # First pass: scan for stub-profile band overlap. A stub match
    # short-circuits to UNKNOWN + INFO log regardless of any non-stub
    # matches that follow -- the v1.0.0 placeholder must never be
    # presented as a classification, even alongside a real match. If
    # in some future schema a stub overlaps a real profile's band,
    # the stub still wins the honesty fight.
    for profile in library.values():
        if profile.stub and _profile_has_freq_overlap(profile, upstream_class, center_freq_hz):
            _log.info(
                "matched stub profile for emitter_class=%s "
                "(returning UNKNOWN per WS-B-006 stub-honesty rule); "
                "stub_reason: %s",
                profile.emitter_class.value,
                profile.stub_reason.replace("\n", " ").strip(),
            )
            return (EmitterClass.UNKNOWN, 0.0)

    # Second pass: collect non-stub matches. A rule matches only when
    # modulation_class, freq window, AND min_confidence floor are all
    # satisfied. The below-min-confidence case is treated as "no match"
    # so the UNKNOWN return preserves the upstream confidence
    # (Invariant B3: we don't silently relax a rule's min_confidence
    # floor).
    matches: list[EmitterClass] = []
    for profile in library.values():
        if profile.stub:
            continue
        for rule in profile.match_rules:
            if _rule_matches(rule, upstream_class, center_freq_hz, upstream_conf):
                matches.append(profile.emitter_class)
                break  # one rule per profile is enough to count it

    if len(matches) == 1:
        # Single non-stub match: return the class with the upstream
        # confidence (confidence-combination policy: no inflation).
        return (matches[0], upstream_conf)

    # Multi-match or no-match: UNKNOWN, upstream confidence preserved.
    # The two cases are deliberately collapsed -- both are honest
    # "the threat library could not place this" outcomes; the operator
    # dashboard can disambiguate by reading the WS-B-005 modulation
    # label and the candidate-set log if needed.
    return (EmitterClass.UNKNOWN, upstream_conf)
