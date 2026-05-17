"""``rfmesh-node`` CLI entry point.

Loads a ``NodeConfig`` from YAML, constructs the appropriate receiver,
wires up a bearer, and runs ``Node.run()`` until SIGINT.

The receiver factory is intentionally simple in v1.0: ``driver="sim"``
gets a trivial simulator (the full ``SyntheticReceiver`` requires a
hand-built scenario, which the CLI does not load -- a follow-up
ticket wires the scenario YAML); real-hardware drivers raise
``NotImplementedError`` so the operator sees a loud, specific
message rather than a silent down-fall to the simulator. The
``rfmesh-demo-replay`` app is the path for real / replayed
end-to-end runs.
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
from rfmesh_contracts import BearerKind, NodeConfig

from ..bearer import BothBearer, LoraBearer, WifiBearer
from ..node import Node

if TYPE_CHECKING:
    from rfmesh_contracts import Bearer, Receiver


_LOG = logging.getLogger(__name__)


def _build_receiver(config: NodeConfig) -> Receiver:
    """Build a receiver for the given config.

    v1.0 supports only ``driver="sim"`` from the CLI; real-hardware
    receivers are constructed via ``apps/demo-replay`` (which knows
    how to load a scenario YAML). The simulator path here is the
    minimal smoke-test plumbing; the real demo path is
    ``rfmesh-demo-replay``.
    """
    if config.sdr.driver != "sim":
        msg = (
            f"run_node: SDR driver {config.sdr.driver!r} is not supported by "
            "the rfmesh-node CLI in v1.0. Use rfmesh-demo-replay for a full "
            "scenario-driven run, or write a custom entrypoint that "
            "instantiates the desired Receiver."
        )
        raise NotImplementedError(msg)
    msg = (
        "run_node: scenario-less simulator receivers are not constructible "
        "from a bare NodeConfig (the simulator requires a SimulationScenario). "
        "Use rfmesh-demo-replay to run with a scenario; for bench tests, "
        "construct Node(...) with an explicit Receiver."
    )
    raise NotImplementedError(msg)


def _build_bearer(config: NodeConfig) -> Bearer:
    """Build a bearer that matches ``config.bearer.kind``."""
    endpoint = str(config.fusion_endpoint)
    bearer_cfg = config.bearer
    if bearer_cfg.kind is BearerKind.WIFI:
        return WifiBearer(endpoint)
    if bearer_cfg.kind is BearerKind.LORA:
        assert bearer_cfg.lora_serial_port is not None
        return LoraBearer(bearer_cfg.lora_serial_port)
    # BOTH -- requires both halves.
    assert bearer_cfg.lora_serial_port is not None
    return BothBearer(
        WifiBearer(endpoint),
        LoraBearer(bearer_cfg.lora_serial_port),
    )


async def _run(config: NodeConfig) -> None:
    """Bring the node up; wait for SIGINT; tear down."""
    receiver = _build_receiver(config)
    bearer = _build_bearer(config)
    node = Node(config, receiver=receiver, bearer=bearer)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is unsupported on Windows; the CLI is
        # Linux-targeted, so we suppress the NotImplementedError there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(node.shutdown()))

    await node.run()


def run_node_main(argv: list[str] | None = None) -> int:
    """``rfmesh-node`` CLI -- returns a process exit code."""
    parser = argparse.ArgumentParser(prog="rfmesh-node")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to a NodeConfig YAML",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="logging level (DEBUG/INFO/WARNING/ERROR)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper())

    with args.config.open("r", encoding="utf-8") as fp:
        payload = yaml.safe_load(fp)
    config = NodeConfig.model_validate(payload)

    try:
        asyncio.run(_run(config))
    except NotImplementedError as exc:
        _LOG.error("rfmesh-node: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_node_main())
