"""Unit tests for ``rfmesh_servo.cli.servo_calibrate``.

Exercises the pure ``compute_calibration`` helper and the interactive
``run_calibration`` procedure against a mock :class:`ServoDriver`.
``input_fn``/``print_fn`` are injected so no real TTY is required.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock, call

import pytest
from rfmesh_servo.cli import servo_calibrate
from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.messages import Calibration

# ---------------------------------------------------------------------------
# compute_calibration
# ---------------------------------------------------------------------------


def test_compute_calibration_happy_path() -> None:
    cal = servo_calibrate.compute_calibration(
        axis=0,
        pulse_min_us=500,
        pulse_max_us=2500,
        angle_at_pulse_min_deg=-88.5,
        angle_at_pulse_max_deg=91.2,
    )
    assert cal.axis == 0
    assert cal.pulse_min_us == 500
    assert cal.pulse_max_us == 2500
    assert cal.angle_min_deg == -88.5
    assert cal.angle_max_deg == 91.2


def test_compute_calibration_rejects_inverted_angles() -> None:
    with pytest.raises(ValueError, match="angle"):
        servo_calibrate.compute_calibration(
            axis=0,
            pulse_min_us=500,
            pulse_max_us=2500,
            angle_at_pulse_min_deg=90.0,
            angle_at_pulse_max_deg=-90.0,
        )


def test_compute_calibration_rejects_out_of_range_pulse() -> None:
    with pytest.raises(ValueError, match="pulse"):
        servo_calibrate.compute_calibration(
            axis=0,
            pulse_min_us=200,
            pulse_max_us=2500,
            angle_at_pulse_min_deg=-90.0,
            angle_at_pulse_max_deg=90.0,
        )


def test_compute_calibration_rejects_inverted_pulses() -> None:
    with pytest.raises(ValueError, match="pulse_min"):
        servo_calibrate.compute_calibration(
            axis=0,
            pulse_min_us=2500,
            pulse_max_us=500,
            angle_at_pulse_min_deg=-90.0,
            angle_at_pulse_max_deg=90.0,
        )


# ---------------------------------------------------------------------------
# run_calibration
# ---------------------------------------------------------------------------


def _mock_driver() -> MagicMock:
    return cast(MagicMock, MagicMock(spec_set=ServoDriver))


def _scripted_input(answers: list[str]) -> tuple[Iterator[str], list[str]]:
    """Return an iterator over ``answers`` and a list capturing prompts seen."""
    prompts: list[str] = []
    answer_iter = iter(answers)

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answer_iter)

    return cast(Iterator[str], fake_input), prompts


def _capture_output() -> tuple[list[str], MagicMock]:
    out: list[str] = []
    return out, cast(MagicMock, out.append)


def test_run_calibration_writes_set_then_persist_on_yes() -> None:
    driver = _mock_driver()
    fake_input, _prompts = _scripted_input(["", "-88.5", "", "91.2", "y"])
    out, print_fn = _capture_output()

    cal = servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )

    assert cal.angle_min_deg == -88.5
    assert cal.angle_max_deg == 91.2
    # Two MOVEs (to default ±90), bootstrap CAL_SET, real CAL_SET, persist.
    driver.move.assert_has_calls(
        [call(0, -90.0), call(0, 90.0)],
        any_order=False,
    )
    assert driver.set_calibration.call_count == 2  # bootstrap + final
    final_cal = driver.set_calibration.call_args_list[-1].args[0]
    assert final_cal == cal
    driver.persist_calibration.assert_called_once_with(0)
    assert any("Persisted." in line for line in out)


def test_run_calibration_skips_persist_on_no() -> None:
    driver = _mock_driver()
    fake_input, _ = _scripted_input(["", "-90.0", "", "90.0", "n"])
    out, print_fn = _capture_output()

    servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )

    driver.persist_calibration.assert_not_called()
    assert any("Not persisted" in line for line in out)


def test_run_calibration_default_persist_is_no() -> None:
    driver = _mock_driver()
    # Empty answer to persist prompt should be treated as "no".
    fake_input, _ = _scripted_input(["", "-90.0", "", "90.0", ""])
    _, print_fn = _capture_output()

    servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )
    driver.persist_calibration.assert_not_called()


def test_run_calibration_reprompts_on_inverted_angles() -> None:
    driver = _mock_driver()
    # First angle_at_max (-95) inverts the range; user re-enters
    # angle_at_min (-90) and angle_at_max (+90).
    fake_input, _ = _scripted_input(["", "90.0", "", "-95.0", "-90.0", "90.0", "n"])
    out, print_fn = _capture_output()

    cal = servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )
    assert cal.angle_min_deg == -90.0
    assert cal.angle_max_deg == 90.0
    assert any("rejected:" in line for line in out)


def test_run_calibration_reprompt_re_asks_both_endpoints() -> None:
    """On validation failure, both pulse_min and pulse_max prompts re-fire.

    Models the common operator error of mistyping ``angle_at_max`` (the
    prompt under more time pressure). The first attempt has a correct
    ``angle_at_min=-90`` but ``angle_at_max=-95`` (sign typo for +95);
    the retry path must give the operator a chance to correct either
    value, so both prompts re-issue.
    """
    driver = _mock_driver()
    fake_input, prompts = _scripted_input(["", "-90.0", "", "-95.0", "-90.0", "95.0", "n"])
    out, print_fn = _capture_output()

    cal = servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )

    assert cal.angle_min_deg == -90.0
    assert cal.angle_max_deg == 95.0
    re_prompt_pmin = [p for p in prompts if "Re-enter angle at pulse_min" in p]
    re_prompt_pmax = [p for p in prompts if "Re-enter angle at pulse_max" in p]
    assert len(re_prompt_pmin) == 1, prompts
    assert len(re_prompt_pmax) == 1, prompts
    assert any("Re-enter both endpoint angles." in line for line in out)


def test_run_calibration_reprompts_on_non_numeric() -> None:
    driver = _mock_driver()
    fake_input, _ = _scripted_input(["", "not-a-number", "-88.0", "", "92.0", "n"])
    out, print_fn = _capture_output()

    cal = servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )
    assert cal.angle_min_deg == -88.0
    assert any("not a number" in line for line in out)


def test_run_calibration_writes_bootstrap_with_default_endpoints() -> None:
    driver = _mock_driver()
    fake_input, _ = _scripted_input(["", "-90.0", "", "90.0", "n"])
    _, print_fn = _capture_output()

    servo_calibrate.run_calibration(
        driver,
        axis=0,
        input_fn=fake_input,  # type: ignore[arg-type]
        print_fn=print_fn,
    )
    bootstrap_cal = driver.set_calibration.call_args_list[0].args[0]
    assert isinstance(bootstrap_cal, Calibration)
    assert bootstrap_cal.angle_min_deg == Calibration.DEFAULT_ANGLE_MIN_DEG
    assert bootstrap_cal.angle_max_deg == Calibration.DEFAULT_ANGLE_MAX_DEG
    assert bootstrap_cal.pulse_min_us == Calibration.DEFAULT_PULSE_MIN_US
    assert bootstrap_cal.pulse_max_us == Calibration.DEFAULT_PULSE_MAX_US


def test_run_calibration_rejects_bad_pulse_args_upfront() -> None:
    driver = _mock_driver()
    with pytest.raises(ValueError, match="pulse_min_us"):
        servo_calibrate.run_calibration(
            driver,
            axis=0,
            pulse_min_us=200,
            input_fn=input,
            print_fn=print,
        )
    driver.set_calibration.assert_not_called()


def test_run_calibration_rejects_inverted_pulse_args_upfront() -> None:
    driver = _mock_driver()
    with pytest.raises(ValueError, match="pulse_min_us"):
        servo_calibrate.run_calibration(
            driver,
            axis=0,
            pulse_min_us=2500,
            pulse_max_us=500,
            input_fn=input,
            print_fn=print,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_help_runs_clean(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        servo_calibrate.main(["--help"])
    assert info.value.code == 0
    captured = capsys.readouterr()
    assert "rfmesh-servo-calibrate" in captured.out
    assert "--axis" in captured.out
    assert "--pulse-min" in captured.out


def test_main_rejects_invalid_pulse_min(capsys: pytest.CaptureFixture[str]) -> None:
    rc = servo_calibrate.main(["--port", "/dev/null", "--pulse-min", "100"])
    assert rc == 2
    assert "--pulse-min" in capsys.readouterr().err


def test_main_rejects_inverted_pulses(capsys: pytest.CaptureFixture[str]) -> None:
    rc = servo_calibrate.main(["--port", "/dev/null", "--pulse-min", "2500", "--pulse-max", "500"])
    assert rc == 2
    assert "--pulse-min must be < --pulse-max" in capsys.readouterr().err
