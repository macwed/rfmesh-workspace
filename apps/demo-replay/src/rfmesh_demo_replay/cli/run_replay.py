"""``rfmesh-demo-replay`` CLI -- run the full demo orchestration.

Usage::

    rfmesh-demo-replay --scenario scenarios/trench_demo.yaml \
                       [--pessimism 1.0|1.5|2.0] \
                       [--no-cot] [--headless] \
                       [--channel-override free_space|two_ray_ground|...] \
                       [--log-level INFO]

Loads the scenario YAML, constructs a ``ReplayOrchestrator`` in
in-process mode, and walks through the beats. The CLI prints a small
summary line per beat (beat id + active node count + fix-or-not).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from rfmesh_contracts import FixEvent

from ..replay import ReplayOrchestrator
from ..scenario import ChannelModelSpec, ScenarioLoader

_CHANNEL_OVERRIDE_CHOICES = (
    "free_space",
    "two_ray_ground",
    "multipath_fir",
    "log_normal_shadowing",
    "composite",
)

_LOG = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rfmesh-demo-replay")
    parser.add_argument(
        "--scenario",
        type=Path,
        required=True,
        help="Path to a scenario YAML file (e.g. scenarios/trench_demo.yaml).",
    )
    parser.add_argument(
        "--pessimism",
        type=float,
        default=1.0,
        help=(
            "Sigma-inflation factor for the Phase-C overlay (1.0 = honest, "
            "1.5 / 2.0 = pessimism levels). Default 1.0."
        ),
    )
    parser.add_argument(
        "--no-cot",
        action="store_true",
        help="Disable the CoT publisher (the default is also off in v1.0).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Disable the in-process dashboard subscriber.",
    )
    parser.add_argument(
        "--channel-override",
        type=str,
        default=None,
        choices=_CHANNEL_OVERRIDE_CHOICES,
        help=(
            "Override the scenario's channel model at load time. Useful for "
            "artifact-capture runs that need a free-space channel even when "
            "the scenario specifies a multipath stack (closes A3 NEXT-2). "
            "Composite is not directly overridable -- pick one atomic kind."
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="logging level (DEBUG/INFO/WARNING/ERROR).",
    )
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    scenario = ScenarioLoader().load(args.scenario)
    if args.channel_override is not None:
        # Replace the scenario's channel_model with an atomic kind. The
        # scenario model is frozen=True, so we rebuild it via model_copy
        # with the override. Documented in A3_NOTES.md NEXT-2.
        original_kind = scenario.channel_model.kind
        override = ChannelModelSpec(kind=args.channel_override)
        scenario = scenario.model_copy(update={"channel_model": override})
        _LOG.info(
            "rfmesh-demo-replay: channel overridden to %r (was %r)",
            args.channel_override,
            original_kind,
        )
    fix_sink: asyncio.Queue[FixEvent] = asyncio.Queue()
    orchestrator = ReplayOrchestrator(
        scenario,
        pessimism_factor=args.pessimism,
        enable_cot=not args.no_cot,
        enable_dashboard=not args.headless,
        fix_sink=fix_sink,
    )
    await orchestrator.run()

    n_fixes = fix_sink.qsize()
    _LOG.info(
        "rfmesh-demo-replay: scenario %s finished; %d fixes published",
        scenario.scenario_id,
        n_fixes,
    )
    return 0


def run_replay_main(argv: list[str] | None = None) -> int:
    """``rfmesh-demo-replay`` entry point -- returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        _LOG.error("rfmesh-demo-replay: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_replay_main())
