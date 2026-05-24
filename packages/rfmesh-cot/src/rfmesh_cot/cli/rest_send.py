"""``rfmesh-cot-rest`` -- push one point marker over the FreeTAKServer REST API.

The REST counterpart of ``rfmesh-cot-send``. Where ``rfmesh-cot-send`` opens
a CoT TCP socket, this makes one authenticated HTTP call to FreeTAKServer's
``ManageGeoObject`` API -- the path ported from the ``drupal/atak`` module.
No persistent connection, no self-SA keepalive: FTS owns the fan-out.

The token is a FreeTAKServer System-User token (FTS Web UI -> User -> give a
user token). Supply it with ``--token`` or the ``FTS_API_TOKEN`` env var.

EXAMPLES
--------
Check connectivity + auth (prints the API version)::

    rfmesh-cot-rest --base-url http://tak.example.com:19023 --token T --help-api

Drop a hostile contact::

    rfmesh-cot-rest --base-url http://tak.example.com:19023 --token T \\
        --template hostile --lat 50.066 --lon 4.866 --callsign "Jammer A"

Move it (same uid)::

    rfmesh-cot-rest --base-url ... --token T --template hostile \\
        --lat 50.07 --lon 4.87 --uid rfmesh.op.hostile.jammer-a

Stale it now (REST has no delete; this re-PUTs a 1 s timeout)::

    rfmesh-cot-rest --base-url ... --token T --template hostile \\
        --lat 50.07 --lon 4.87 --uid rfmesh.op.hostile.jammer-a --expire
"""

from __future__ import annotations

import argparse
import os
import sys

from ..exceptions import CotRestError
from ..operator import TEMPLATES, OperatorMarker, template
from ..rest import FreeTakServerRestClient


def _slug(text: str) -> str:
    """Make a uid-safe slug from a callsign / template key."""
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "marker"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rfmesh-cot-rest",
        description="Push an operator point marker to FreeTAKServer over its REST API.",
    )
    p.add_argument("--base-url", required=True, help="FTS REST base URL, e.g. http://host:19023")
    p.add_argument(
        "--token",
        default=os.environ.get("FTS_API_TOKEN", ""),
        help="FTS System-User token (or set FTS_API_TOKEN)",
    )
    p.add_argument("--timeout", type=float, default=10.0, help="HTTP request timeout (s)")
    p.add_argument(
        "--help-api",
        action="store_true",
        help="call getHelp and print the API version + endpoints, then exit",
    )
    # Point templates only -- the REST API models points, not polygons.
    point_templates = sorted(k for k, t in TEMPLATES.items() if t.geometry == "point")
    p.add_argument("--template", choices=point_templates, help="point message template")
    p.add_argument("--lat", type=float, help="latitude (decimal degrees)")
    p.add_argument("--lon", type=float, help="longitude (decimal degrees)")
    p.add_argument("--callsign", default="", help="label on the map (default: template label)")
    p.add_argument("--remarks", default="", help="free-text note")
    p.add_argument("--uid", default="", help="stable uid (re-send to move; default: derived)")
    p.add_argument("--stale", type=float, default=None, help="override timeout/stale in seconds")
    p.add_argument("--callsign-prefix", default="rfmesh", help="uid namespace prefix")
    p.add_argument(
        "--expire",
        action="store_true",
        help="stale the marker now instead of placing it (re-PUT with 1 s timeout)",
    )
    return p


def _make_marker(args: argparse.Namespace) -> OperatorMarker:
    tmpl = template(args.template)
    cs = args.callsign or tmpl.label
    uid = args.uid or f"{args.callsign_prefix}.op.{tmpl.key}.{_slug(cs)}"
    return OperatorMarker(
        template_key=tmpl.key,
        uid=uid,
        lat_deg=args.lat if args.lat is not None else 0.0,
        lon_deg=args.lon if args.lon is not None else 0.0,
        callsign=cs,
        remarks=args.remarks,
        stale_after_s=args.stale,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``rfmesh-cot-rest`` console script."""
    args = _build_parser().parse_args(argv)
    client = FreeTakServerRestClient(args.base_url, args.token, timeout_s=args.timeout)

    if args.help_api:
        try:
            info = client.get_help()
        except CotRestError as exc:
            print(f"getHelp failed: {exc}", file=sys.stderr)
            return 1
        print(f"APIVersion={info.get('APIVersion', '?')}")
        for ep in info.get("SupportedEndpoints", []):
            print(f"  {ep}")
        return 0

    if not args.template:
        print("error: --template is required (or use --help-api)", file=sys.stderr)
        return 2
    if args.lat is None or args.lon is None:
        print(f"error: template {args.template!r} needs --lat and --lon", file=sys.stderr)
        return 2

    marker = _make_marker(args)
    try:
        if args.expire:
            uid = client.expire_marker(marker)
            print(f"expired marker uid={uid} via {args.base_url}")
        else:
            uid = client.publish_marker(marker)
            print(f"sent template={marker.template_key} uid={uid} -> {args.base_url}")
    except CotRestError as exc:
        detail = f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
        print(f"rfmesh-cot-rest failed{detail}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
