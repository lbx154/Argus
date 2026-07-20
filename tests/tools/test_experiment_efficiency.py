from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from argus_skill.tools.experiment_efficiency import evaluate_stopping


def test_low_variance_effect_stops_early() -> None:
    result = evaluate_stopping(
        [1.20, 1.21, 1.19, 1.20],
        {
            "direction": "maximize",
            "baseline": 1.0,
            "min_improvement": 0.1,
            "min_repeats": 3,
            "max_repeats": 10,
        },
    )
    assert result["decision"] == "STOP_SUPPORTED_EFFECT"
    assert result["n"] == 4


def test_futility_stops_without_ten_full_repeats() -> None:
    result = evaluate_stopping(
        [1.01, 1.00, 1.02, 1.01],
        {
            "direction": "maximize",
            "baseline": 1.0,
            "min_improvement": 0.1,
            "min_repeats": 3,
            "max_repeats": 10,
        },
    )
    assert result["decision"] == "STOP_FUTILITY"


def test_noisy_observations_continue() -> None:
    result = evaluate_stopping(
        [0.8, 1.3, 0.9],
        {
            "direction": "maximize",
            "baseline": 1.0,
            "min_improvement": 0.05,
            "min_repeats": 3,
            "max_repeats": 10,
        },
    )
    assert result["decision"] == "CONTINUE"


def test_cli_reads_real_jsonl_and_writes_decision(tmp_path: Path) -> None:
    config = tmp_path / "protocol.json"
    observations = tmp_path / "observations.jsonl"
    output = tmp_path / "decision.json"
    config.write_text(json.dumps({
        "direction": "minimize",
        "baseline": 10.0,
        "min_improvement": 1.0,
        "min_repeats": 3,
        "max_repeats": 8,
    }))
    observations.write_text("\n".join(
        json.dumps({"latency": value}) for value in (8.0, 8.1, 7.9, 8.0)
    ) + "\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.tools.experiment_efficiency",
            "--config",
            str(config),
            "--observations",
            str(observations),
            "--field",
            "latency",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    assert payload["decision"] == "STOP_SUPPORTED_EFFECT"
    assert payload["all_observations_retained"] is True
