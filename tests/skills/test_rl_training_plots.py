"""Tests for the rl_training_plots gate (operator requirement: every
substantive RL optimizer run must carry a training-curve plot)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.rl_training_plots import (
    MIN_OPTIMIZER_STEPS,
    validate_rl_training_plots,
)


def _seed_run(
    root: Path,
    name: str,
    *,
    state: str = "completed",
    steps: int | None = 3,
    plot: str | None = None,
    progress_steps: int | None = None,
    extra_files: tuple[str, ...] = (),
) -> Path:
    run = root / "experiments" / "runs" / name
    run.mkdir(parents=True, exist_ok=True)
    status: dict = {"state": state}
    if steps is not None:
        status["optimizer_steps"] = steps
    (run / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if progress_steps is not None:
        lines = [
            json.dumps({"event": "optimizer_step", "step": i + 1})
            for i in range(progress_steps)
        ]
        (run / "progress.jsonl").write_text("\n".join(lines), encoding="utf-8")
    if plot is not None:
        p = run / "plots" / plot
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n")
    for rel in extra_files:
        f = run / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    return run


# --------------------------------------------------------------------------
# no-op cases
# --------------------------------------------------------------------------


def test_no_runs_dir_is_noop(tmp_path: Path) -> None:
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible == []


def test_no_optimizer_runs_is_noop(tmp_path: Path) -> None:
    _seed_run(tmp_path, "benchmark_env_preflight_x", state="completed", steps=5,
              plot=None)  # not optimizer_ prefix
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible == []


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------


def test_completed_run_without_plot_fails(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_vanilla_grpo_a", steps=10, plot=None)
    report = validate_rl_training_plots(tmp_path)
    assert not report.ok
    assert len(report.missing) == 1
    assert report.eligible[0].optimizer_steps == 10


def test_completed_run_with_named_plot_passes(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_vanilla_grpo_a", steps=10,
              plot="training_curve.png")
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible[0].has_plot
    assert report.eligible[0].plot_examples


def test_pdf_and_svg_curve_accepted(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_a", steps=3, plot="optimizer_metrics.pdf")
    _seed_run(tmp_path, "optimizer_b", steps=3, plot="reward_curve.svg")
    report = validate_rl_training_plots(tmp_path)
    assert report.ok


def test_failed_run_excluded(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_crash_a", state="failed", steps=0)
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible == []


def test_running_run_excluded(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_live_a", state="running", steps=4)
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible == []


def test_below_min_steps_excluded(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_tiny_a", steps=MIN_OPTIMIZER_STEPS - 1,
              plot=None)
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible == []


def test_probe_runs_excluded(tmp_path: Path) -> None:
    for tok in ("microfit", "memfit", "stepfit", "smoke", "preflight"):
        _seed_run(tmp_path, f"optimizer_vanilla_{tok}_run", steps=5, plot=None)
    report = validate_rl_training_plots(tmp_path)
    assert report.ok
    assert report.eligible == []


# --------------------------------------------------------------------------
# plot contract strictness
# --------------------------------------------------------------------------


def test_stray_unrelated_image_does_not_count(tmp_path: Path) -> None:
    # an image under plots/ but without a curve token in its name
    _seed_run(tmp_path, "optimizer_a", steps=5, plot="screenshot.png")
    report = validate_rl_training_plots(tmp_path)
    assert not report.ok
    assert len(report.missing) == 1


def test_image_outside_plots_dir_does_not_count(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_a", steps=5, plot=None,
              extra_files=("training_curve.png",))  # at run root, not plots/
    report = validate_rl_training_plots(tmp_path)
    assert not report.ok


# --------------------------------------------------------------------------
# progress.jsonl fallback for optimizer_steps
# --------------------------------------------------------------------------


def test_steps_from_progress_fallback(tmp_path: Path) -> None:
    _seed_run(tmp_path, "optimizer_a", state="completed", steps=None,
              progress_steps=4, plot=None)
    report = validate_rl_training_plots(tmp_path)
    assert not report.ok
    assert report.eligible[0].optimizer_steps == 4


# --------------------------------------------------------------------------
# stage wiring: advisory at run, structural at analysis
# --------------------------------------------------------------------------






