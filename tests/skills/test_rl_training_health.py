"""Tests for the rl_training_health advisory gate."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.rl_training_health import (
    TAIL_WINDOW,
    validate_rl_training_health,
)


def _write_run(
    project_root: Path,
    name: str,
    *,
    state: str = "completed",
    steps: int = 10,
    adv_spans: list[float] | None = None,
    reward_means: list[float] | None = None,
    grad_norms: list[float] | None = None,
    entropies: list[float] | None = None,
    task_ids: list[str] | None = None,
    admitted: int | None = None,
    minimum: int | None = None,
    partial_final_verl: bool = False,
    frac_zero_std: float = 0.0,
) -> Path:
    run_dir = project_root / "experiments" / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    n = steps
    adv_spans = adv_spans if adv_spans is not None else [2.0] * n
    reward_means = reward_means if reward_means is not None else [0.5] * n
    grad_norms = grad_norms if grad_norms is not None else [0.1] * n
    entropies = entropies if entropies is not None else [0.5] * n

    status = {"state": state, "optimizer_steps": steps}
    if admitted is not None:
        status["admitted_math_ids"] = admitted
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")

    if minimum is not None:
        (run_dir / "manifest.json").write_text(
            json.dumps({"minimum_accepted_optimizer_steps": minimum}),
            encoding="utf-8",
        )

    verl_lines = []
    for i in range(n):
        span = adv_spans[i]
        data = {
            "critic/advantages/min": -span / 2,
            "critic/advantages/max": span / 2,
            "critic/rewards/min": reward_means[i],
            "actor/grad_norm": grad_norms[i],
            "actor/entropy": entropies[i],
            "actor/pg_loss": 0.0 if span == 0 else 0.3,
        }
        verl_lines.append(json.dumps({"step": i + 1, "data": data}))
    verl_text = "\n".join(verl_lines) + "\n"
    if partial_final_verl:
        verl_text += '{"step": 999, "data": {"actor/gr'  # truncated
    (run_dir / "verl_metrics.jsonl").write_text(verl_text, encoding="utf-8")

    prog_lines = []
    for i in range(n):
        prog_lines.append(
            json.dumps(
                {
                    "event": "optimizer_step",
                    "optimizer_steps": i + 1,
                    "reward_trace_stats": {
                        "reward_mean": reward_means[i],
                        "reward_std": 0.3,
                        "frac_reward_zero_std": frac_zero_std,
                    },
                }
            )
        )
    (run_dir / "progress.jsonl").write_text(
        "\n".join(prog_lines) + "\n", encoding="utf-8"
    )

    if task_ids is None:
        task_ids = [f"math_train_{i:04d}" for i in range(50)]
    trace_lines = [
        json.dumps({"task_id": tid, "score": 1.0})
        for tid in task_ids
    ]
    (run_dir / "reward_trace.jsonl").write_text(
        "\n".join(trace_lines) + "\n", encoding="utf-8"
    )
    return run_dir


def test_noop_when_no_runs(tmp_path: Path) -> None:
    report = validate_rl_training_health(tmp_path)
    assert report.runs == []
    assert report.to_text().startswith("No live or completed")


def test_saturated_run_flags_signals(tmp_path: Path) -> None:
    # zero advantage across the whole tail + reward at ceiling + 10 ids
    _write_run(
        tmp_path,
        "optimizer_sat",
        steps=TAIL_WINDOW,
        adv_spans=[0.0] * TAIL_WINDOW,
        reward_means=[1.0] * TAIL_WINDOW,
        grad_norms=[1e-5] * TAIL_WINDOW,
        entropies=[0.5, 0.45, 0.4, 0.3, 0.2, 0.15, 0.1, 0.08],
        task_ids=[f"math_train_{i % 10:04d}" for i in range(200)],
        admitted=10,
        minimum=200,
    )
    report = validate_rl_training_health(tmp_path)
    assert len(report.runs) == 1
    sig = report.runs[0].signals
    assert "zero_advantage" in sig
    assert "near_zero_grad_norm" in sig
    assert "reward_ceiling_saturation" in sig
    assert "low_task_diversity" in sig
    # frac_reward_zero_std reads 0.0 (looks healthy) while reward is at the
    # ceiling -> the contradiction must be surfaced, not silently trusted.
    assert "variance_metric_masks_saturation" in sig
    # entropy 0.5 -> 0.08 is a >50% decline, last is also below the low cap
    assert "low_entropy" in sig or "entropy_declining" in sig


def test_high_frac_zero_std_does_not_emit_masking_signal(tmp_path: Path) -> None:
    # honest variance metric (frac high) at the ceiling: ceiling saturation may
    # fire, but the masking-contradiction signal must NOT — nothing is masked.
    _write_run(
        tmp_path,
        "optimizer_honest_frac",
        steps=TAIL_WINDOW,
        adv_spans=[0.0] * TAIL_WINDOW,
        reward_means=[1.0] * TAIL_WINDOW,
        frac_zero_std=1.0,
    )
    report = validate_rl_training_health(tmp_path)
    sig = report.runs[0].signals
    assert "reward_ceiling_saturation" in sig
    assert "variance_metric_masks_saturation" not in sig


def test_zero_advantage_only_last_step_is_not_sustained(tmp_path: Path) -> None:
    spans = [3.0] * (TAIL_WINDOW - 1) + [0.0]
    _write_run(
        tmp_path,
        "optimizer_lastzero",
        steps=TAIL_WINDOW,
        adv_spans=spans,
        reward_means=[0.6] * TAIL_WINDOW,
        task_ids=[f"math_train_{i:04d}" for i in range(60)],
    )
    report = validate_rl_training_health(tmp_path)
    sig = report.runs[0].signals
    assert "zero_advantage_last_step" in sig
    assert "zero_advantage" not in sig


def test_healthy_run_has_no_signals(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "optimizer_healthy",
        steps=TAIL_WINDOW,
        adv_spans=[2.5] * TAIL_WINDOW,
        reward_means=[0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7],
        grad_norms=[0.2] * TAIL_WINDOW,
        entropies=[0.6] * TAIL_WINDOW,
        task_ids=[f"math_train_{i:04d}" for i in range(400)],
    )
    report = validate_rl_training_health(tmp_path)
    assert report.runs[0].signals == []


def test_probe_run_is_skipped(tmp_path: Path) -> None:
    _write_run(tmp_path, "optimizer_smoke_probe", steps=TAIL_WINDOW)
    report = validate_rl_training_health(tmp_path)
    assert report.runs == []


def test_running_state_is_included(tmp_path: Path) -> None:
    _write_run(tmp_path, "optimizer_live", state="running", steps=TAIL_WINDOW)
    report = validate_rl_training_health(tmp_path)
    assert len(report.runs) == 1
    assert report.runs[0].state == "running"
    assert "progress_age_sec" in report.runs[0].facts


def test_partial_final_line_is_tolerated(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "optimizer_partial",
        steps=TAIL_WINDOW,
        partial_final_verl=True,
    )
    report = validate_rl_training_health(tmp_path)
    assert len(report.runs) == 1
    warns = report.runs[0].warnings
    assert any("ignored_partial_final_line" in w for w in warns)


def test_tiny_tail_suppresses_sustained_labels(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "optimizer_tiny",
        steps=2,
        adv_spans=[0.0, 0.0],
        reward_means=[1.0, 1.0],
        grad_norms=[1e-5, 1e-5],
        task_ids=[f"math_train_{i % 3:04d}" for i in range(40)],
    )
    report = validate_rl_training_health(tmp_path)
    sig = report.runs[0].signals
    # tail too short to assert sustained collapse
    assert "zero_advantage" not in sig
    assert "near_zero_grad_norm" not in sig
    assert "insufficient_tail_for_sustained_collapse" in sig




