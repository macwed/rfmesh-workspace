"""``rfmesh-operator-console`` -- run the click-the-map operator console.

    rfmesh-operator-console            # serves http://127.0.0.1:8088
    rfmesh-operator-console --port 9000 --cot-url tcp://host:8087

Open the printed URL in a browser, pick a template, click the map, send.
"""

from __future__ import annotations

import argparse
import logging

from aiohttp import web

from rfmesh_operator_console.server import DEFAULT_COT_URL, build_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rfmesh-operator-console",
        description="Web map console for operator-authored TAK markers.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8088, help="bind port (default 8088)")
    parser.add_argument(
        "--cot-url", default=DEFAULT_COT_URL, help=f"TAK endpoint (default {DEFAULT_COT_URL})"
    )
    parser.add_argument(
        "--callsign",
        default="13-counter-jamming-console",
        help="self-SA callsign the console presents to the TAK server",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper())
    app = build_app(args.cot_url, self_callsign=args.callsign)
    print(f"operator console: http://{args.host}:{args.port}  ->  TAK {args.cot_url}")
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
