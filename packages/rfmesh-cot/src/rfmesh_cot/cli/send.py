"""``rfmesh-cot-send`` -- ship one operator-authored marker to a TAK server.

The smallest possible operator surface: pick a template, give a point (or
an area file of ``lat,lon`` lines), and this opens a TCP connection to the
TAK endpoint and pushes the CoT event. Proves the operator -> TAK round
trip end to end and is the backend any future map UI calls into.

EXAMPLES
--------
Drop a hostile contact::

    rfmesh-cot-send --template hostile --lat 50.066 --lon 4.866 \\
        --callsign "Jammer A" --remarks "ELRS uplink, operator-confirmed"

Draw a no-go area from a vertex file (one ``lat,lon`` per line)::

    rfmesh-cot-send --template no_go --area area.txt --callsign "No-go N"

Un-send a marker you previously placed::

    rfmesh-cot-send --delete rfmesh.op.hostile.jammer-a

The default endpoint is the BoTH3 hackathon TAK server; override with
``--cot-url`` (PyTAK-style ``tcp://host:port`` / ``tls://...``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..operator import TEMPLATES, OperatorMarker, template
from ..publisher import PyTAKCotPublisher

#: Default TAK endpoint -- the BoTH3 hackathon FreeTAKServer (CoT TCP port).
DEFAULT_COT_URL = "tcp://35.206.145.140:8087"


def _slug(text: str) -> str:
    """Make a uid-safe slug from a callsign / template key."""
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "marker"


def _read_area(path: Path) -> tuple[tuple[float, float], ...]:
    """Parse an area file: one ``lat,lon`` per line, blank/'#' lines skipped."""
    verts: list[tuple[float, float]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lat_s, _, lon_s = line.partition(",")
        verts.append((float(lat_s), float(lon_s)))
    return tuple(verts)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rfmesh-cot-send",
        description="Send an operator-authored CoT marker to a TAK server.",
    )
    p.add_argument(
        "--cot-url", default=DEFAULT_COT_URL, help=f"TAK endpoint (default {DEFAULT_COT_URL})"
    )
    p.add_argument(
        "--template",
        choices=sorted(TEMPLATES),
        help="message template (point: hostile/friendly/neutral/unknown/waypoint/spi/casevac; "
        "area: no_go/area_of_interest/search_area)",
    )
    p.add_argument("--lat", type=float, help="latitude (decimal degrees) for a point marker")
    p.add_argument("--lon", type=float, help="longitude (decimal degrees) for a point marker")
    p.add_argument("--area", type=Path, help="area file of 'lat,lon' lines for a polygon template")
    p.add_argument(
        "--callsign", default="", help="label shown on the map (default: template label)"
    )
    p.add_argument("--remarks", default="", help="free-text note shown when the marker is tapped")
    p.add_argument("--uid", default="", help="stable uid (re-send to move, default: derived)")
    p.add_argument("--stale", type=float, default=None, help="override stale time in seconds")
    p.add_argument("--callsign-prefix", default="rfmesh", help="uid namespace prefix")
    p.add_argument("--delete", metavar="UID", help="delete a previously-sent marker by uid")
    return p


def _make_marker(args: argparse.Namespace) -> OperatorMarker:
    tmpl = template(args.template)
    cs = args.callsign or tmpl.label
    uid = args.uid or f"{args.callsign_prefix}.op.{tmpl.key}.{_slug(cs)}"
    vertices = _read_area(args.area) if args.area is not None else None
    return OperatorMarker(
        template_key=tmpl.key,
        uid=uid,
        lat_deg=args.lat or 0.0,
        lon_deg=args.lon or 0.0,
        callsign=cs,
        remarks=args.remarks,
        vertices=vertices,
        stale_after_s=args.stale,
    )


async def _run(args: argparse.Namespace) -> int:
    async with PyTAKCotPublisher(args.cot_url, node_callsign=args.callsign_prefix) as pub:
        if args.delete:
            pub.delete_marker(args.delete)
            # Give the TX loop a tick to drain before the context exits.
            await asyncio.sleep(0.2)
            print(f"deleted marker uid={args.delete} via {args.cot_url}")
            return 0
        marker = _make_marker(args)
        pub.publish_marker(marker)
        await asyncio.sleep(0.2)
    print(f"sent template={marker.template_key} uid={marker.uid} -> {args.cot_url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``rfmesh-cot-send`` console script."""
    args = _build_parser().parse_args(argv)
    if not args.delete:
        if not args.template:
            print("error: --template is required (or use --delete UID)", file=sys.stderr)
            return 2
        tmpl = TEMPLATES[args.template]
        if tmpl.geometry == "point" and (args.lat is None or args.lon is None):
            print(f"error: template {tmpl.key!r} needs --lat and --lon", file=sys.stderr)
            return 2
        if tmpl.geometry == "polygon" and args.area is None:
            print(f"error: template {tmpl.key!r} needs --area <file>", file=sys.stderr)
            return 2
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        print(f"rfmesh-cot-send failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
