"""``rfmesh-beat-e-preload`` CLI — pre-render the Beat E A/B receive patterns.

Usage::

    rfmesh-beat-e-preload --out cache/beat_e.npz \\
        --array=ULA --n=2 --spacing=0.164 --freq=915e6 \\
        --signal-deg=306 --jammer-deg=126

Pre-stage helper for the BoTH3 jury demo: pre-computes the
baseline (Beat E.0, uniform weights, no null) and engaged-null
(Beat E.1, MVDR weights against a synthetic R with a jammer at
``--jammer-deg``) receive patterns and saves them as a single ``.npz``
cache. At stage time the dashboard's NullSteeringPanel loads the cache
in well under 200 ms (vs 2-3 s for the full DSP recompute), so the
operator's "Engage null" button registers as instant from the
audience's perspective. The cache uses ``allow_pickle=False`` for
faster cold-start load and safer reads against an externally-provided
file.

Demo-integrity council recommendation, 2026-05-20 slide-deck review:
*"preload the Beat E.0 baseline polar plot before stage time so the
t=0 render is instant rather than a 2-3 s computation pause."*

Exit codes:

* 0 — success, cache written.
* 2 — CLI argument error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rfmesh_contracts import ArrayGeometry

from ..beat_e_preload import precompute_and_save

_DEFAULT_FREQUENCY_HZ: float = 915e6
_DEFAULT_SIGNAL_SNR_DB: float = 30.0
_DEFAULT_JAMMER_TO_SIGNAL_DB: float = 20.0
_DEFAULT_SCAN_STEP_DEG: float = 0.5

_ARRAY_CHOICES = ("ULA", "UCA", "CUSTOM")

_LOG = logging.getLogger("rfmesh-beat-e-preload")


_TRENCH_DEMO_EXAMPLE = """\
Trench-demo defaults (the canonical pre-stage invocation):

    rfmesh-beat-e-preload \\
        --out cache/beat_e_trench.npz \\
        --array=ULA --n=2 --spacing=0.164 --freq=915e6 \\
        --signal-deg=306 --jammer-deg=126

Geometry per docs/demo/trench-demo-geometry.md (emitter at +N, jammer
180 deg from signal for a clean null demonstration). At 915 MHz
lambda/2 = 0.164 m (the canonical L2 partner-pool array spacing).
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rfmesh-beat-e-preload",
        description=(
            "Pre-render the Beat E.0 baseline + Beat E.1 engaged-null "
            "receive patterns for the dashboard NullSteeringPanel. Run "
            "once before stage time; the cache file the dashboard "
            "loads is a portable .npz that takes well under 200 ms to read."
        ),
        epilog=_TRENCH_DEMO_EXAMPLE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output .npz cache path (parent directories created if missing).",
    )
    p.add_argument(
        "--array",
        choices=_ARRAY_CHOICES,
        default="ULA",
        help="Array geometry (default: ULA).",
    )
    p.add_argument(
        "--n",
        type=int,
        required=True,
        help="Number of coherent RX channels.",
    )
    p.add_argument(
        "--spacing",
        type=float,
        required=True,
        help=(
            "ULA inter-element spacing in metres, or UCA radius in metres. "
            "For 915 MHz: lambda/2 ~= 0.164 m."
        ),
    )
    p.add_argument(
        "--freq",
        type=float,
        default=_DEFAULT_FREQUENCY_HZ,
        help=f"Carrier frequency in Hz (default {_DEFAULT_FREQUENCY_HZ:.3g}).",
    )
    p.add_argument(
        "--signal-deg",
        type=float,
        required=True,
        help="Signal (target) azimuth in degrees true.",
    )
    p.add_argument(
        "--jammer-deg",
        type=float,
        required=True,
        help="Jammer azimuth in degrees true (the null direction).",
    )
    p.add_argument(
        "--scan-step-deg",
        type=float,
        default=_DEFAULT_SCAN_STEP_DEG,
        help=(
            f"Angular resolution of the precomputed pattern in degrees "
            f"(default {_DEFAULT_SCAN_STEP_DEG}; matches raw_pseudospectrum)."
        ),
    )
    p.add_argument(
        "--signal-snr-db",
        type=float,
        default=_DEFAULT_SIGNAL_SNR_DB,
        help=(
            f"Synthetic-R signal SNR in dB (default {_DEFAULT_SIGNAL_SNR_DB}). "
            "Sets the variance of the signal outer-product in R."
        ),
    )
    p.add_argument(
        "--jammer-to-signal-db",
        type=float,
        default=_DEFAULT_JAMMER_TO_SIGNAL_DB,
        help=(
            f"Synthetic-R jammer-to-signal power ratio in dB "
            f"(default {_DEFAULT_JAMMER_TO_SIGNAL_DB}). The jammer is set "
            "louder than the signal to exercise the null path."
        ),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default INFO).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(message)s")
    geometry = ArrayGeometry(args.array.lower())
    if geometry is ArrayGeometry.CUSTOM:
        _LOG.error(
            "CUSTOM geometry not supported by this CLI; "
            "use the Python API directly with element_positions_m."
        )
        return 2
    cache = precompute_and_save(
        out_path=args.out,
        array_geometry=geometry,
        n_elements=args.n,
        element_spacing_m=args.spacing,
        signal_azimuth_deg=args.signal_deg,
        jammer_azimuth_deg=args.jammer_deg,
        frequency_hz=args.freq,
        scan_step_deg=args.scan_step_deg,
        signal_snr_db=args.signal_snr_db,
        jammer_to_signal_db=args.jammer_to_signal_db,
    )
    _LOG.info(
        "Beat E cache written: %s (azimuth bins %d, baseline depth %.2f dB, engaged depth %.2f dB)",
        args.out,
        cache.baseline.azimuths_deg.shape[0],
        cache.baseline.depth_db,
        cache.engaged.depth_db,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module-level entry point
    sys.exit(main())
