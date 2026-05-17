"""``rfmesh-fusion-server`` CLI entry point.

Loads a ``FusionConfig`` from YAML, builds the
``StansfieldMLEFuser``, optionally wires a CoT publisher (when
``rfmesh-cot`` is installed) and a dashboard pubsub channel, and
runs ``FusionService.run()`` until SIGINT.

The CoT publisher is dependency-injected: we import it lazily so
the fusion server can run headless (no CoT) without the
``rfmesh-cot`` package being importable -- useful for unit tests
and for bench runs that watch only the dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError
from rfmesh_contracts import FusionConfig

from ..dashboard_pubsub import DashboardPubSub
from ..fusion_service import FusionService

if TYPE_CHECKING:
    from rfmesh_contracts import CotPublisher

_LOG = logging.getLogger(__name__)


def _build_cot_publisher(config: FusionConfig) -> CotPublisher | None:
    """Construct a CoT publisher if ``cot_url`` is set + rfmesh-cot importable."""
    if config.cot_url is None:
        return None
    try:
        # Lazy import: rfmesh-cot is optional. Loading it at module
        # import time would force every fusion-server user to install
        # PyTAK and its transitive deps even for a headless bench run.
        from rfmesh_cot import PyTAKCotPublisher  # noqa: PLC0415
    except ImportError:
        _LOG.warning(
            "rfmesh-cot not installed; cot_url=%s will be ignored.",
            config.cot_url,
        )
        return None
    return PyTAKCotPublisher(endpoint_url=str(config.cot_url))


async def _run(config: FusionConfig) -> None:
    # Lazy import: keeps the import-time cost of the CLI module
    # small for unit tests that only need the argparse surface.
    from rfmesh_fusion import (  # type: ignore[import-untyped]  # noqa: PLC0415
        StansfieldMLEFuser,
    )

    fuser = StansfieldMLEFuser(config)
    cot_publisher = _build_cot_publisher(config)
    dashboard_pubsub = DashboardPubSub()
    service = FusionService(
        config,
        fuser=fuser,
        cot_publisher=cot_publisher,
        dashboard_pubsub=dashboard_pubsub,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(service.shutdown()))

    await service.run()


def run_fusion_main(argv: list[str] | None = None) -> int:
    """``rfmesh-fusion-server`` CLI -- returns a process exit code."""
    parser = argparse.ArgumentParser(prog="rfmesh-fusion-server")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to a FusionConfig YAML",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="logging level (DEBUG/INFO/WARNING/ERROR)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper())

    # Operator-facing error UX (demo-integrity council R5).
    try:
        with args.config.open("r", encoding="utf-8") as fp:
            payload = yaml.safe_load(fp)
        config = FusionConfig.model_validate(payload)
    except FileNotFoundError as exc:
        _LOG.error("rfmesh-fusion-server: config not found: %s", exc)
        return 2
    except yaml.YAMLError as exc:
        _LOG.error("rfmesh-fusion-server: malformed config YAML: %s", exc)
        return 2
    except ValidationError as exc:
        _LOG.error("rfmesh-fusion-server: config schema invalid:\n%s", exc)
        return 2

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_fusion_main())
