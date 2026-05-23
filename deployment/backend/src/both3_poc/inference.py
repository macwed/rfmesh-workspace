"""Emitter-investigation inference for the both3-poc layer.

A *pure* mapping from one observed emitter (center frequency, occupied
bandwidth, optional emitter class) to a **ranked list of candidate equipment**
plus an explicit **UNKNOWN** mass. No FastAPI, no network, no side effects
beyond reading the equipment catalog once at first use (cached). The main
thread wires the result into a `/fixes/{id}/investigate` endpoint and
JSON-serializes the dataclasses below.

Design honesty (mirrors the project's "no magic" stance, ARCHITECTURE.md §7
and the catalog's own caveats):

- **Never a single verdict.** The result is always a ranked list of candidates
  *plus* an UNKNOWN candidate that carries non-zero mass. Known candidates
  never sum to 1.0; the residual is UNKNOWN.
- **UNKNOWN grows when evidence is weak** — a multi-use band, a missing or
  uninformative bandwidth, no emitter-class hint, or many band-mates competing.
- **Two planes, kept honest.** The *band-gate* plane answers "what systems /
  targets are even plausible in this band". The *candidate* plane scores
  individual equipment entries. Both are returned so the UI can show the gate
  context next to the ranked IDs.
- **Measured vs inferred stays distinct.** The measured feature vector
  (center_freq, occupied_bw, emitter_class, derived band name) is returned
  separately from the inferred candidates so the UI never blurs the two.
- **Qualitative ERP only, no dBm.** Every result carries ``no_dbm = True`` and
  ``erp_class`` is the catalog's qualitative band — never an absolute power.

Scoring is deliberately simple and explainable (band match + bandwidth match +
waveform/class consistency, weighted by the catalog ``confidence_prior``).
It is a triage aid, not a classifier; the L3 ML classifier is a separate layer.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# Catalog lives at deployment/backend/data/equipment_catalog.json.
# This module is at deployment/backend/src/both3_poc/inference.py, so the data
# dir is three parents up (src/both3_poc -> src -> backend) then /data.
_DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "equipment_catalog.json"

# Floor and ceiling on the reserved UNKNOWN mass. Even with strong, unambiguous
# evidence we never let the known candidates claim the entire probability mass —
# the catalog is finite and the world is not.
_UNKNOWN_FLOOR = 0.08
_UNKNOWN_CEIL = 0.92

# Above this many competing band-mates, the band is crowded enough that we add
# extra UNKNOWN mass (less certainty that any single named ID is right).
_MANY_CANDIDATES = 4

# Maps an emitter_class hint (free-form, lower-cased) to the catalog waveform
# tokens and target tokens it is consistent with. Used only to *boost* matching
# candidates, never to exclude — absence of a match leaves the band/BW score
# intact (and feeds UNKNOWN), it does not zero a candidate out.
_CLASS_WAVEFORMS: dict[str, tuple[str, ...]] = {
    "gps_jammer": ("barrage_noise", "swept_cw", "spot_cw"),
    "gnss_jammer": ("barrage_noise", "swept_cw", "spot_cw"),
    "jammer": ("barrage_noise", "swept_cw", "spot_cw"),
    "spoofer": ("gnss_like_dsss",),
    "gnss_spoofer": ("gnss_like_dsss",),
    "elrs": ("lora_chirp", "flrc", "fhss", "fsk"),
    "crossfire": ("fhss", "lora_chirp"),
    "droneid": ("ofdm", "fhss"),
    "wifi": ("ofdm", "dsss"),
    "fpv_analog": ("wide_fm",),
    "fpv_digital": ("ofdm",),
    "gsm": ("gmsk", "ofdm", "tdma"),
    "cellular": ("gmsk", "ofdm", "tdma"),
}

_CLASS_TARGETS: dict[str, tuple[str, ...]] = {
    "gps_jammer": ("gnss_gps", "glonass"),
    "gnss_jammer": ("gnss_gps", "glonass"),
    "spoofer": ("gnss_gps", "glonass"),
    "gnss_spoofer": ("gnss_gps", "glonass"),
    "elrs": ("rc_control_link",),
    "crossfire": ("rc_control_link",),
    "droneid": ("droneid",),
    "wifi": ("wifi",),
    "fpv_analog": ("fpv_5g8",),
    "fpv_digital": ("fpv_5g8", "fpv_2g4"),
    "gsm": ("gsm_cellular",),
    "cellular": ("gsm_cellular",),
}


# --------------------------------------------------------------------------- #
# Result shapes (dataclasses → JSON-serializable via `as_dict` / `asdict`).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BandGateHit:
    """One band-gate entry whose frequency range contains the observation.

    The band-gate plane: "what systems / targets are even plausible here".
    """

    key: str
    plausible_systems: list[str]
    targets: list[str]
    multi_use: bool
    notes: str


@dataclass(frozen=True)
class Candidate:
    """One ranked candidate equipment entry, with its honesty payload.

    ``confidence`` is a normalized share of the (1 - UNKNOWN) known mass — it is
    a *relative* plausibility among catalog band-mates, not a calibrated
    probability of truth. Always read alongside the sibling UNKNOWN candidate.
    """

    id: str
    name: str
    confidence: float
    role: str
    targets: list[str]
    mobility: str
    antenna_height_class_m: dict[str, float]
    erp_class: str
    recommended_action: str
    time_critical: bool
    evidence: list[str]
    confounders: list[str]
    sources: list[dict[str, Any]]
    is_unknown: bool = False
    no_dbm: bool = True


@dataclass(frozen=True)
class MeasuredFeatures:
    """The measured feature vector — kept separate from inferred candidates.

    Only ``band_name`` is *derived* (the band-gate key the center frequency
    falls in); the rest are passed-through observations.
    """

    center_freq_hz: float | None
    occupied_bw_hz: float | None
    emitter_class: str | None
    band_name: str | None


@dataclass(frozen=True)
class InvestigationResult:
    """Full investigation output: measured features, gate hits, ranked candidates."""

    measured: MeasuredFeatures
    band_gate_hits: list[BandGateHit]
    candidates: list[Candidate]
    unknown_confidence: float
    notes: list[str]
    no_dbm: bool = True

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable dict for a FastAPI response."""
        return asdict(self)


# --------------------------------------------------------------------------- #
# Catalog loading (cached once).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Catalog:
    """Parsed equipment catalog: the band-gate map and the equipment list."""

    band_gate: dict[str, dict[str, Any]]
    equipment: list[dict[str, Any]]


@lru_cache(maxsize=4)
def load_catalog(path: str | Path = _DEFAULT_CATALOG_PATH) -> Catalog:
    """Load and cache the equipment catalog from ``path``.

    Cached by path so repeated calls in a long-lived process touch the disk
    once. Raises loudly (no silent fallback) if the file is missing or invalid
    JSON — a broken catalog is a deployment error, not a runtime degradation.
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    band_gate = raw.get("band_gate", {})
    equipment = raw.get("equipment", [])
    if not isinstance(band_gate, dict) or not isinstance(equipment, list):
        msg = f"equipment catalog at {p} missing dict 'band_gate' and/or list 'equipment'"
        raise ValueError(msg)
    return Catalog(band_gate=band_gate, equipment=equipment)


# --------------------------------------------------------------------------- #
# Scoring helpers (pure functions).
# --------------------------------------------------------------------------- #


def _freq_in_ranges(freq_hz: float, ranges: list[list[float]]) -> bool:
    """True if ``freq_hz`` falls within any [lo, hi] range."""
    return any(lo <= freq_hz <= hi for lo, hi in ranges)


def _format_mhz(freq_hz: float) -> str:
    """Compact MHz label for an evidence chip, e.g. 1575MHz."""
    mhz = freq_hz / 1e6
    return f"{mhz:.0f}MHz" if mhz >= 1 else f"{freq_hz / 1e3:.0f}kHz"


def _bandwidth_score(occupied_bw_hz: float, entry: dict[str, Any]) -> tuple[float, str | None]:
    """Score how well an observed bandwidth fits an entry's occupied-BW range.

    Returns (score in [0, 1], evidence chip or None). 1.0 inside the range,
    decaying toward 0 outside it on a log scale (bandwidth spans decades). A
    zero/degenerate entry range (e.g. passive DF receivers) yields a neutral
    0.5 — neither evidence for nor against.
    """
    bw = entry.get("occupied_bw_hz", {})
    rng = bw.get("range") or [0, 0]
    lo, hi = float(rng[0]), float(rng[1])
    if hi <= 0:
        return 0.5, None  # entry has no meaningful BW (passive/recon) — neutral
    if lo <= occupied_bw_hz <= hi:
        return 1.0, f"BW {_format_mhz(occupied_bw_hz)}"
    # Outside the range: penalize by how many octaves off we are.
    edge = lo if occupied_bw_hz < lo else hi
    if edge <= 0 or occupied_bw_hz <= 0:
        return 0.25, None
    octaves = abs(math.log2(occupied_bw_hz / edge))
    score = max(0.1, 1.0 - 0.35 * octaves)
    return score, f"BW {_format_mhz(occupied_bw_hz)} (off-band)"


def _class_consistency(
    emitter_class: str | None, entry: dict[str, Any]
) -> tuple[float, str | None]:
    """Score waveform/target consistency with an emitter-class hint.

    Returns (multiplier >= 1.0 boost mapped to [0, 1] score, evidence or None).
    A matching class boosts the entry; a non-matching class is neutral (does not
    exclude — absence of evidence is not evidence of absence).
    """
    if not emitter_class:
        return 0.5, None  # no hint — neutral
    key = emitter_class.strip().lower()
    waveforms = set(entry.get("waveform", []))
    targets = set(entry.get("targets", []))
    want_wf = set(_CLASS_WAVEFORMS.get(key, ()))
    want_tg = set(_CLASS_TARGETS.get(key, ()))
    wf_hit = bool(waveforms & want_wf)
    tg_hit = bool(targets & want_tg)
    if wf_hit and tg_hit:
        return 1.0, f"class~{key}"
    if wf_hit or tg_hit:
        return 0.75, f"class~{key}"
    if want_wf or want_tg:
        return 0.3, None  # known class, but this entry does not fit it
    return 0.5, None  # class not in our map — neutral


def _hop_chip(entry: dict[str, Any]) -> str | None:
    """Evidence chip describing the entry's hop signature."""
    hop = entry.get("signatures", {}).get("hop", {})
    if hop.get("present"):
        rate = hop.get("rate_hz")
        return f"hop {rate:.0f}Hz" if rate else "hop"
    return "no-hop"


def _gate_hit(key: str, gate: dict[str, Any]) -> BandGateHit:
    """Build a BandGateHit from a raw catalog band-gate entry."""
    return BandGateHit(
        key=key,
        plausible_systems=list(gate.get("plausible_systems", [])),
        targets=list(gate.get("targets", [])),
        multi_use=bool(gate.get("multi_use", False)),
        notes=str(gate.get("notes", "")),
    )


def _band_gate_hits(
    cat: Catalog, center_freq_hz: float | None, emitter_class: str | None
) -> list[BandGateHit]:
    """Find the plausible band-gate entries for an observation.

    By center frequency when available; otherwise fall back to the
    emitter-class target tokens.
    """
    if center_freq_hz is not None:
        return [
            _gate_hit(key, gate)
            for key, gate in cat.band_gate.items()
            if _freq_in_ranges(center_freq_hz, gate.get("hz", []))
        ]
    if emitter_class:
        want_tg = set(_CLASS_TARGETS.get(emitter_class.strip().lower(), ()))
        return [
            _gate_hit(key, gate)
            for key, gate in cat.band_gate.items()
            if want_tg & set(gate.get("targets", []))
        ]
    return []


def _score_entry(
    entry: dict[str, Any],
    center_freq_hz: float | None,
    occupied_bw_hz: float | None,
    emitter_class: str | None,
) -> tuple[float, list[str]] | None:
    """Score one equipment entry against the observation.

    Returns (raw_score, evidence_chips) if the entry is a candidate (its bands
    cover the frequency, or its targets match the class), else ``None``.
    """
    evidence: list[str] = []
    if center_freq_hz is not None:
        if not _freq_in_ranges(center_freq_hz, entry.get("bands_hz", [])):
            return None
        evidence.append(f"band {_format_mhz(center_freq_hz)}")
        band_score = 1.0
    elif emitter_class:
        want_tg = set(_CLASS_TARGETS.get(emitter_class.strip().lower(), ()))
        if not (want_tg & set(entry.get("targets", []))):
            return None
        band_score = 0.4  # class-only match: weak band evidence
    else:
        return None  # nothing to score against

    if occupied_bw_hz is not None:
        bw_score, bw_chip = _bandwidth_score(occupied_bw_hz, entry)
        if bw_chip:
            evidence.append(bw_chip)
    else:
        bw_score = 0.4  # no BW measured → weak, pushes mass to UNKNOWN

    class_score, class_chip = _class_consistency(emitter_class, entry)
    if class_chip:
        evidence.append(class_chip)

    hop = _hop_chip(entry)
    if hop:
        evidence.append(hop)

    prior = float(entry.get("confidence_prior", 0.5))
    raw = (0.45 * band_score + 0.35 * bw_score + 0.20 * class_score) * prior
    return raw, evidence


def _compute_unknown_mass(
    center_freq_hz: float | None,
    occupied_bw_hz: float | None,
    emitter_class: str | None,
    *,
    multi_use_band: bool,
    n_candidates: int,
) -> float:
    """Reserved UNKNOWN probability mass, grown by every weakness in the evidence."""
    if n_candidates == 0:
        return 1.0
    unknown = _UNKNOWN_FLOOR
    if center_freq_hz is None:
        unknown += 0.40
    if occupied_bw_hz is None:
        unknown += 0.20
    if not emitter_class:
        unknown += 0.10
    if multi_use_band:
        unknown += 0.12
    if n_candidates >= _MANY_CANDIDATES:
        unknown += 0.08
    return max(_UNKNOWN_FLOOR, min(_UNKNOWN_CEIL, unknown))


def _make_candidate(entry: dict[str, Any], confidence: float, evidence: list[str]) -> Candidate:
    """Build a ranked Candidate from a catalog entry and its computed confidence."""
    return Candidate(
        id=str(entry["id"]),
        name=str(entry.get("name", entry["id"])),
        confidence=round(confidence, 4),
        role=str(entry.get("role", "")),
        targets=list(entry.get("targets", [])),
        mobility=str(entry.get("mobility", "")),
        antenna_height_class_m=dict(entry.get("antenna_height_class_m", {})),
        erp_class=str(entry.get("erp_class", "")),
        recommended_action=str(entry.get("recommended_action", "")),
        time_critical=bool(entry.get("time_critical", False)),
        evidence=evidence,
        confounders=list(entry.get("confounders", [])),
        sources=list(entry.get("sources", [])),
    )


def _make_unknown_candidate(
    confidence: float,
    *,
    center_freq_hz: float | None,
    occupied_bw_hz: float | None,
    multi_use_band: bool,
) -> Candidate:
    """Build the explicit UNKNOWN candidate carrying the reserved residual mass."""
    chips: list[str] = []
    if center_freq_hz is None:
        chips.append("no center freq")
    if occupied_bw_hz is None:
        chips.append("no bandwidth")
    if multi_use_band:
        chips.append("multi-use band")
    if not chips:
        chips.append("residual mass")
    return Candidate(
        id="UNKNOWN",
        name="UNKNOWN / unattributed emitter",
        confidence=round(confidence, 4),
        role="recon",
        targets=[],
        mobility="",
        antenna_height_class_m={},
        erp_class="",
        recommended_action="report",
        time_critical=False,
        evidence=chips,
        confounders=["all band-mates", "front-end overload", "harmonics/images"],
        sources=[],
        is_unknown=True,
    )


# --------------------------------------------------------------------------- #
# Public entry point.
# --------------------------------------------------------------------------- #


def investigate(
    center_freq_hz: float | None,
    occupied_bw_hz: float | None,
    emitter_class: str | None = None,
    observed: dict[str, Any] | None = None,
    catalog: Catalog | None = None,
) -> InvestigationResult:
    """Map an observed emitter to ranked candidate equipment + UNKNOWN mass.

    Parameters
    ----------
    center_freq_hz:
        Observed center frequency in Hz. If ``None``, the band-gate falls back
        to ``emitter_class`` and candidate scoring is band-agnostic (UNKNOWN
        dominates).
    occupied_bw_hz:
        Observed occupied bandwidth in Hz, or ``None`` if not measured /
        dwell insufficient (this *raises* UNKNOWN mass).
    emitter_class:
        Optional free-form class hint (e.g. ``"gps_jammer"``, ``"elrs"``). Used
        to boost consistent candidates and, when ``center_freq_hz`` is missing,
        to drive the band-gate fallback.
    observed:
        Optional extra observations (reserved for future signatures such as
        measured hop rate / duty cycle). Currently advisory only.
    catalog:
        Parsed catalog; defaults to the cached on-disk catalog.

    Returns
    -------
    InvestigationResult
        Measured feature vector, band-gate hits, ranked candidates, and the
        reserved UNKNOWN confidence — all JSON-serializable via ``as_dict()``.
    """
    cat = catalog if catalog is not None else load_catalog()
    notes: list[str] = ["erp is qualitative only; no absolute power (dBm) is claimed"]
    if observed:
        notes.append("extra observations supplied (advisory only in v1)")

    # ---- Band-gate plane: "what is even plausible in this band" ----------- #
    band_gate_hits = _band_gate_hits(cat, center_freq_hz, emitter_class)
    band_name = band_gate_hits[0].key if (band_gate_hits and center_freq_hz is not None) else None
    multi_use_band = any(h.multi_use for h in band_gate_hits)
    if center_freq_hz is None and emitter_class:
        notes.append("no center frequency: band-gate fell back to emitter_class")

    measured = MeasuredFeatures(
        center_freq_hz=center_freq_hz,
        occupied_bw_hz=occupied_bw_hz,
        emitter_class=emitter_class,
        band_name=band_name,
    )

    # ---- Candidate plane: score each band-mate entry ---------------------- #
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for entry in cat.equipment:
        if entry.get("id") == "unknown_candidate_by_band":
            continue  # the catalog's own UNKNOWN sentinel; we synthesize our own
        result = _score_entry(entry, center_freq_hz, occupied_bw_hz, emitter_class)
        if result is not None:
            scored.append((result[0], entry, result[1]))
    if occupied_bw_hz is None and scored:
        notes.append("no occupied bandwidth measured")

    # ---- Normalize with reserved UNKNOWN mass ----------------------------- #
    unknown = _compute_unknown_mass(
        center_freq_hz,
        occupied_bw_hz,
        emitter_class,
        multi_use_band=multi_use_band,
        n_candidates=len(scored),
    )
    total_raw = sum(s for s, _, _ in scored)
    known_mass = 1.0 - unknown

    candidates: list[Candidate] = []
    if total_raw > 0 and known_mass > 0:
        candidates = [
            _make_candidate(entry, known_mass * (raw / total_raw), evidence)
            for raw, entry, evidence in scored
        ]
        candidates.sort(key=lambda c: c.confidence, reverse=True)

    # Always append the explicit UNKNOWN candidate so the UI shows the residual.
    candidates.append(
        _make_unknown_candidate(
            unknown,
            center_freq_hz=center_freq_hz,
            occupied_bw_hz=occupied_bw_hz,
            multi_use_band=multi_use_band,
        )
    )

    return InvestigationResult(
        measured=measured,
        band_gate_hits=band_gate_hits,
        candidates=candidates,
        unknown_confidence=round(unknown, 4),
        notes=notes,
    )


def observer_height_m_for(candidate: Candidate) -> float:
    """Typical emitter antenna height (m) for a candidate, for posterior re-weight.

    Reads ``antenna_height_class_m.typ``. The main thread uses this to re-weight
    candidates against an observed/estimated emitter height (a tall mast system
    is less plausible if the fix puts the emitter at ground level, and vice
    versa). Returns 0.0 when the candidate has no height class (e.g. UNKNOWN).
    """
    return float(candidate.antenna_height_class_m.get("typ", 0.0))


# --------------------------------------------------------------------------- #
# Smoke test — run this file directly to self-verify against the real catalog.
# --------------------------------------------------------------------------- #


def _smoke() -> None:
    cat = load_catalog()
    print(f"catalog: {len(cat.band_gate)} band_gate entries, {len(cat.equipment)} equipment\n")

    cases: list[tuple[str, float | None, float | None, str | None]] = [
        ("GNSS L1 jammer", 1.5754e9, 20e6, "gps_jammer"),
        ("2.4 GHz control/FPV", 2.45e9, 1e6, "elrs"),
        ("5.8 GHz FPV video", 5.8e9, 20e6, "fpv_analog"),
        ("Starlink Ku downlink", 11.7e9, 240e6, None),
        ("2.4 GHz, no class/BW", 2.45e9, None, None),
    ]
    for label, freq, bw, klass in cases:
        res = investigate(freq, bw, klass, catalog=cat)
        bn = res.measured.band_name
        gate_keys = [h.key for h in res.band_gate_hits]
        multi = any(h.multi_use for h in res.band_gate_hits)
        print(f"=== {label} ===")
        print(f"  measured: f={freq} bw={bw} class={klass} band={bn}")
        print(f"  gate hits: {gate_keys} (multi_use={multi})")
        print(f"  UNKNOWN mass: {res.unknown_confidence}")
        for c in res.candidates[:4]:
            tag = " [UNKNOWN]" if c.is_unknown else ""
            print(
                f"    {c.confidence:>6.3f}  {c.id:<28} erp={c.erp_class or '-':<9} "
                f"act={c.recommended_action:<10} h_typ={observer_height_m_for(c)}m{tag}"
            )
        total = sum(c.confidence for c in res.candidates)
        print(f"  (sum of all confidences incl UNKNOWN = {total:.4f})\n")


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    _smoke()
