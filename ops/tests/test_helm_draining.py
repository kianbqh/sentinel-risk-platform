"""Prevent a configurable drain from consuming the application's shutdown budget."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def render(sleep, grace):
    return subprocess.run([
        "helm", "template", "m1b", "deploy/helm/sentinel", "--set-string",
        "auth.postgresPassword=lint-only,auth.jwtSecret=lint-only-0000000000000000000000000000",
        "--set", f"termination.preStopSeconds={sleep},termination.graceSeconds={grace}",
    ], cwd=ROOT, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("sleep,grace", [(5, 14), (-1, 20)])
def test_invalid_drain_budget_is_rejected(sleep, grace):
    result = render(sleep, grace)
    assert result.returncode != 0
    assert "termination." in result.stderr


def test_zero_drain_can_reproduce_previous_behavior_without_a_sleep_hook():
    result = render(0, 20)
    assert result.returncode == 0, result.stderr
    assert "sleep:" not in result.stdout
