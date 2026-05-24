"""CLI tests -- the YAML-only path + legacy-flag refusal (ADR-022)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rfmesh_node.cli.run_node import _legacy_flag_check, run_node_main


def test_legacy_flag_refused_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any pre-ADR-022 flag triggers a loud SystemExit by default."""
    monkeypatch.delenv("RFMESH_NODE_ALLOW_LEGACY_FLAGS", raising=False)
    with pytest.raises(SystemExit) as ei:
        _legacy_flag_check(["--config", "x.yaml", "--servo-port", "/dev/ttyACM0"])
    assert "removed in ADR-022" in str(ei.value)
    assert "servo_port" in str(ei.value)


def test_legacy_flag_warns_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """The deprecation-window env var demotes the refusal to a warning."""
    monkeypatch.setenv("RFMESH_NODE_ALLOW_LEGACY_FLAGS", "1")
    _legacy_flag_check(["--config", "x.yaml", "--peer-id", "node-b"])
    deprecation = [w for w in recwarn.list if issubclass(w.category, DeprecationWarning)]
    assert deprecation, "expected DeprecationWarning"
    assert "peer_node_id" in str(deprecation[0].message)


def test_legacy_flag_with_equals(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--servo-port=/dev/ttyACM0` form is also detected (argparse-style)."""
    monkeypatch.delenv("RFMESH_NODE_ALLOW_LEGACY_FLAGS", raising=False)
    with pytest.raises(SystemExit) as ei:
        _legacy_flag_check(["--config=x.yaml", "--servo-port=/dev/ttyACM0"])
    assert "servo_port" in str(ei.value)


def test_no_legacy_flags_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vanilla `--config X --log-level INFO` invocation is not flagged."""
    monkeypatch.delenv("RFMESH_NODE_ALLOW_LEGACY_FLAGS", raising=False)
    # Should not raise.
    _legacy_flag_check(["--config", "x.yaml", "--log-level", "INFO"])


def test_cli_rejects_missing_config(tmp_path: Path) -> None:
    """A non-existent --config path returns exit code 2 with a loud error."""
    missing = tmp_path / "nope.yaml"
    rc = run_node_main(["--config", str(missing)])
    assert rc == 2


def test_cli_rejects_bad_yaml(tmp_path: Path) -> None:
    """Malformed YAML is a loud config-load failure, not a Python traceback."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("node: {{{\n", encoding="utf-8")
    rc = run_node_main(["--config", str(bad)])
    assert rc == 2


def test_cli_rejects_schema_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A YAML that loads but fails Pydantic validation returns exit code 2."""
    monkeypatch.delenv("RFMESH_NODE_ALLOW_LEGACY_FLAGS", raising=False)
    cfg = tmp_path / "bad-schema.yaml"
    cfg.write_text("node: {schema_version: '1.4.0'}\n", encoding="utf-8")
    rc = run_node_main(["--config", str(cfg)])
    assert rc == 2


# Note: a "loads valid YAML then fails at runtime" test would need to
# stand up the receiver / servo / bearer stack, and exercising it without
# mocks risks blocking on the actual RTL-SDR library. The load path
# itself is covered by test_runtime_config.py (model_validate).
