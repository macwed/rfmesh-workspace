"""Environment-driven config for the both3-poc backend.

Every knob is an env var so the same image runs in dev (bundled FreeTAKServer)
and on the VPS (repoint COT_ENDPOINT_URL at a real ATAK/FTS) with no rebuild.
See deployment/.env.example for the documented set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    cot_endpoint_url: str
    cot_callsign: str
    send_fresh: bool
    stale_window_s: float
    seed_demo: bool
    seed_as_fresh: bool
    seed_file: Path
    seed_bearings_file: Path
    cors_allow_origins: list[str]
    frontend_dir: Path
    lob_length_m: float

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.environ.get("CORS_ALLOW_ORIGINS", "*")
        return cls(
            cot_endpoint_url=os.environ.get("COT_ENDPOINT_URL", "tcp://freetakserver:8087"),
            cot_callsign=os.environ.get("COT_CALLSIGN", "rfmesh"),
            send_fresh=_bool("SEND_FRESH", True),
            stale_window_s=float(os.environ.get("STALE_WINDOW_S", "30")),
            seed_demo=_bool("SEED_DEMO", True),
            seed_as_fresh=_bool("SEED_AS_FRESH", True),
            seed_file=Path(os.environ.get("SEED_FILE", "/app/seed/demo_fixes.json")),
            seed_bearings_file=Path(
                os.environ.get("SEED_BEARINGS_FILE", "/app/seed/demo_bearings.json")
            ),
            cors_allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
            frontend_dir=Path(os.environ.get("FRONTEND_DIR", "/app/frontend")),
            lob_length_m=float(os.environ.get("LOB_LENGTH_M", "6000")),
        )
