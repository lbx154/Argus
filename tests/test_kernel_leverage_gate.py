from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.verticals.kernel_engineering.leverage_gate import (
    analyze_leverage,
    main,
    validate_leverage,
)


def test_argus3_attempt_would_stop_before_low_leverage_edit() -> None:
    record = analyze_leverage(
        attempt_id="tilelang-bwd-dqkg-profile-20260718T145000Z",
        baseline_identity="B200 baseline 1.809264 ms",
        path_coverage="equal-head TileLang fused WY/DQKG dispatch",
        evidence="kernel 0.16749 ms from NCU; end-to-end from project runner",
        end_to_end_ms=1.8092640042304993,
        target_kernel_ms=0.16749,
        required_total_speedup=1.02,
        plausible_kernel_speedup=167.49 / 146.40,
    )

    assert record["target_share"] < 0.10
    assert record["predicted_total_speedup"] < 1.02
    assert record["verdict"] == "reject_insufficient_plausible_gain"
    assert validate_leverage(record) == []


def test_live_b200_timeline_confirms_low_leverage_rejection() -> None:
    record = analyze_leverage(
        attempt_id="live-b200-leverage-ab-timeline",
        baseline_identity="B200 live baseline fe8fce9f",
        path_coverage="equal-head TileLang fused WY/DQKG; torch CUDA timeline",
        evidence="timeline-summary.json",
        end_to_end_ms=1.8494559526443481,
        target_kernel_ms=0.110912,
        required_total_speedup=1.02,
        plausible_kernel_speedup=1.144057377,
    )

    assert record["target_share"] == pytest.approx(0.059970068409262875)
    assert record["predicted_total_speedup"] == pytest.approx(1.0076087651243133)
    assert record["required_kernel_speedup"] == pytest.approx(1.4857968807496196)
    assert record["verdict"] == "reject_insufficient_plausible_gain"


def test_high_leverage_target_can_proceed() -> None:
    record = analyze_leverage(
        attempt_id="high-share",
        baseline_identity="baseline",
        path_coverage="target kernel dispatch",
        evidence="profile.json",
        end_to_end_ms=2.0,
        target_kernel_ms=1.2,
        required_total_speedup=1.05,
        plausible_kernel_speedup=1.25,
    )

    assert record["verdict"] == "proceed"


def test_cli_writes_and_checks_leverage_record(tmp_path: Path) -> None:
    path = tmp_path / "LEVERAGE.json"
    rc = main([
        "analyze",
        "--attempt-id", "a1",
        "--baseline-identity", "main@abc",
        "--path-coverage", "dispatch proof",
        "--evidence", "profile.json",
        "--end-to-end-ms", "1.8",
        "--target-kernel-ms", "0.1",
        "--required-total-speedup", "1.02",
        "--plausible-kernel-speedup", "1.2",
        "--output", str(path),
    ])
    assert rc == 0
    assert json.loads(path.read_text())["verdict"].startswith("reject_")
    assert main(["check-file", str(path)]) == 0
    assert main(["check", "--project-root", str(tmp_path)]) == 2

    attempt = tmp_path / "attempts" / "a1"
    attempt.mkdir(parents=True)
    path.replace(attempt / "LEVERAGE.json")
    assert main(["check", "--project-root", str(tmp_path)]) == 0
