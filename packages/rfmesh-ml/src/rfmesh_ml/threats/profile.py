"""YAML threat-profile schema + loader for WS-B-006.

Pydantic-backed schema for the operator-extensible YAML profile library
under ``packages/rfmesh-ml/threats/profiles/``. The schema is the
contract: malformed YAML, schema-invalid YAML, or a stub-profile without
a ``stub_reason`` all raise ``ThreatProfileError`` (Invariant B3 -- no
silent fallbacks at the data-load layer).

``load_profile_library`` is the only public function. It iterates
``*.yaml`` files in the supplied directory, parses each with
``yaml.safe_load`` (never ``yaml.load`` -- Invariant B3 / security:
arbitrary-Python-object instantiation is blocked by SafeLoader, so a
malformed or hostile YAML file fails loudly instead of silently
constructing a Python object the loader was not expecting), and
returns a ``dict[EmitterClass, ThreatProfile]``. Duplicate
``emitter_class`` across files is a load-time error.

Pure (Invariant B5): file I/O is explicitly the documented
"load vendor data tables" exception in ``AGENTS.md`` §1 Invariant 5;
nothing here touches the network or a subprocess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rfmesh_contracts.enums import EmitterClass

# Mirrors ``rfmesh_ml.rules.ModulationLabel`` without importing from there.
# We deliberately do NOT import ``ModulationLabel`` from ``rules.py``
# because the YAML schema is the operator-facing surface and tying it to
# an internal alias would couple the operator's edits to a private name.
# The string-set is enforced by the ``Literal`` below; if WS-B-005 ever
# adds a new modulation label, this list grows in lockstep (a one-line
# change here + a SCHEMA-side test in WS-B-005 territory).
ModulationLabelStr = Literal["cw", "fhss", "fsk", "lora", "unknown"]


class MatchRule(BaseModel):
    """One rule inside a ``ThreatProfile.match_rules`` list.

    A rule matches when the upstream ``ClassificationResult`` carries
    a ``modulation_class`` equal to this rule's value AND the supplied
    ``center_freq_hz`` falls inside ``[freq_band_hz[0], freq_band_hz[1]]``
    AND the upstream ``confidence >= min_confidence``.

    ``notes`` is operator-facing documentation -- the "open the YAML on
    a tablet next to ATAK" narrative lives here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    modulation_class: ModulationLabelStr
    freq_band_hz: tuple[float, float]
    min_confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    notes: str = ""

    @model_validator(mode="after")
    def _validate_freq_band(self) -> MatchRule:
        low, high = self.freq_band_hz
        if not (low > 0.0 and high > 0.0):
            msg = (
                f"freq_band_hz must be strictly positive Hz (got [{low}, {high}]); "
                "units are Hz, not MHz or GHz."
            )
            raise ValueError(msg)
        if low >= high:
            msg = f"freq_band_hz must satisfy low < high (got [{low}, {high}])."
            raise ValueError(msg)
        return self


class ThreatProfile(BaseModel):
    """One YAML profile under ``threats/profiles/``.

    See ``docs/tickets/WS-B-006-threat-library.md`` §2 for the binding
    schema; this model is the canonical enforcement of it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    emitter_class: EmitterClass
    human_name: Annotated[str, Field(min_length=1)]
    references: tuple[str, ...] = ()
    match_rules: tuple[MatchRule, ...] = ()
    stub: bool = False
    stub_reason: str = ""

    @model_validator(mode="after")
    def _validate_class_and_stub(self) -> ThreatProfile:
        # UNKNOWN cannot be a profile -- it is the no-match return.
        # Rejecting it at load means the schema gate, not the matcher,
        # catches an operator who tries to declare an UNKNOWN profile.
        if self.emitter_class is EmitterClass.UNKNOWN:
            msg = (
                "emitter_class='unknown' is rejected: UNKNOWN is the no-match "
                "return, not a profile. Profiles must declare one of the "
                "named EmitterClass members (elrs, crossfire, gsm_jammer, "
                "pole21, volnorez, droneid)."
            )
            raise ValueError(msg)

        if self.stub:
            if not self.stub_reason.strip():
                msg = (
                    "stub: true requires a non-empty stub_reason "
                    "(operator-credibility gate: a profile labelled stub "
                    "without a reason is indistinguishable from a "
                    "forgotten work-in-progress)."
                )
                raise ValueError(msg)
        else:
            if self.stub_reason:
                msg = (
                    "stub_reason must be empty when stub: false "
                    "(stray stub_reason on a non-stub profile suggests a "
                    "copy-paste mistake)."
                )
                raise ValueError(msg)
            if not self.match_rules:
                msg = (
                    "non-stub profile has empty match_rules; it could "
                    "never match anything. Add at least one MatchRule "
                    "or set stub: true."
                )
                raise ValueError(msg)
            if not self.references:
                msg = (
                    "non-stub profile must carry at least one entry in "
                    "'references' (URL, datasheet citation, or capture "
                    "provenance). Stubs may omit references."
                )
                raise ValueError(msg)
        return self


class ThreatProfileError(ValueError):
    """Raised when loading or validating a threat profile fails.

    Subclasses ``ValueError`` so existing ``except ValueError`` call
    sites catch it. Invariant B3: malformed or schema-invalid YAML
    raises loudly, never silently skipped. Every message names the
    offending file (and where possible the offending field) so an
    operator can fix the YAML directly.
    """


def load_profile_library(path: Path) -> dict[EmitterClass, ThreatProfile]:
    """Load every ``*.yaml`` under ``path`` as a ``ThreatProfile``.

    Parameters
    ----------
    path
        Directory containing operator-edited YAML profiles. Must exist
        and be readable. Subdirectories are NOT recursed (flat layout).

    Returns
    -------
    dict[EmitterClass, ThreatProfile]
        One entry per loaded profile, keyed by the profile's declared
        ``emitter_class``. If two YAML files declare the same
        ``emitter_class``, the second is a ``ThreatProfileError``
        (silent shadowing would be a B3 violation).

    Raises
    ------
    ThreatProfileError
        If ``path`` is not a directory, if any YAML is malformed,
        if any profile violates the schema, or if duplicate
        ``emitter_class`` is detected.
    """
    if not path.is_dir():
        msg = f"profile library path is not a directory: {path}"
        raise ThreatProfileError(msg)

    library: dict[EmitterClass, ThreatProfile] = {}
    # Stable, deterministic load order -- duplicate detection becomes
    # operator-debuggable instead of filesystem-order-dependent.
    for yaml_path in sorted(path.glob("*.yaml")):
        try:
            with yaml_path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            msg = f"malformed YAML in {yaml_path}: {exc}"
            raise ThreatProfileError(msg) from exc

        if not isinstance(raw, dict):
            msg = (
                f"profile {yaml_path} did not parse to a mapping "
                f"(got {type(raw).__name__}); the schema expects a YAML mapping."
            )
            raise ThreatProfileError(msg)

        try:
            profile = ThreatProfile.model_validate(raw)
        except ValidationError as exc:
            msg = f"schema violation in {yaml_path}: {exc}"
            raise ThreatProfileError(msg) from exc

        if profile.emitter_class in library:
            msg = (
                f"duplicate emitter_class '{profile.emitter_class.value}' "
                f"declared in {yaml_path}; already loaded from a previous file. "
                "Each EmitterClass member must have exactly one profile."
            )
            raise ThreatProfileError(msg)
        library[profile.emitter_class] = profile

    return library
