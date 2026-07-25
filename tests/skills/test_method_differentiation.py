"""Tests for the method_differentiation no-op-treatment gate."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.method_differentiation import (
    validate_method_differentiation,
)

# A minimal but realistic verl-style command shared by baseline + proposed.
_BASE_ARGS = {
    "algorithm.adv_estimator": "grpo",
    "actor_rollout_ref.actor.optim.lr": "5e-6",
    "actor_rollout_ref.rollout.n": "8",
    "actor_rollout_ref.actor.kl_loss_coef": "0.01",
    "data.train_batch_size": "1",
    "actor_rollout_ref.model.lora_rank": "64",
    "reward.custom_reward_function.path": "/p/code/reward.py",
    "reward.custom_reward_function.name": "compute_score",
    "trainer.total_training_steps": "1200",
}


def _write_run(
    project_root: Path,
    name: str,
    *,
    condition: str,
    args: dict[str, str],
    reward_mean: float,
    frac_zero_std: float,
    state: str = "completed",
    steps: int = 1200,
    noise: dict[str, str] | None = None,
) -> Path:
    run_dir = project_root / "experiments" / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "state": state,
                "optimizer_steps": steps,
                "condition": condition,
                "reward_trace_stats": {
                    "reward_mean": reward_mean,
                    "frac_reward_zero_std": frac_zero_std,
                    "reward_trace_rows": 9000,
                },
            }
        ),
        encoding="utf-8",
    )
    command = ["/venv/python", "-m", "verl.trainer.main_ppo"]
    merged = dict(args)
    if noise:
        merged.update(noise)
    command += [f"{k}={v}" for k, v in merged.items()]
    (run_dir / "config_snapshot.json").write_text(
        json.dumps({"condition": condition, "command": command}),
        encoding="utf-8",
    )
    return run_dir


def test_no_op_reward_name_only_flagged(tmp_path: Path):
    _write_run(
        tmp_path, "optimizer_vanilla_grpo_full1200",
        condition="vanilla_grpo", args=_BASE_ARGS,
        reward_mean=0.580, frac_zero_std=0.292,
    )
    cv_args = dict(_BASE_ARGS)
    cv_args["reward.custom_reward_function.name"] = "compute_score_cv"
    _write_run(
        tmp_path, "optimizer_cv_grpo_full1200",
        condition="cv_grpo", args=cv_args,
        reward_mean=0.578, frac_zero_std=0.287,
    )

    report = validate_method_differentiation(tmp_path)
    assert len(report.pairs) == 1
    pair = report.pairs[0]
    assert pair.proposed_condition == "cv_grpo"
    assert pair.baseline_condition == "vanilla_grpo"
    assert pair.config_diff_kind == "reward_name_only"
    assert pair.outcomes_indistinguishable is True
    assert pair.no_op_suspected is True
    assert pair.duplicate_condition is False




def test_infra_knob_diffs_do_not_mask_no_op(tmp_path: Path):
    # The two runs differ in throughput/memory knobs (vLLM) AND the reward-fn
    # name; the infra knobs must be treated as noise so the pair still reads as
    # reward-name-only.
    _write_run(
        tmp_path, "optimizer_vanilla_grpo_full1200",
        condition="vanilla_grpo", args=_BASE_ARGS,
        reward_mean=0.580, frac_zero_std=0.292,
        noise={
            "actor_rollout_ref.rollout.gpu_memory_utilization": "0.85",
            "actor_rollout_ref.rollout.max_num_seqs": "96",
            "trainer.save_freq": "300",
        },
    )
    cv_args = dict(_BASE_ARGS)
    cv_args["reward.custom_reward_function.name"] = "compute_score_cv"
    _write_run(
        tmp_path, "optimizer_cv_grpo_full1200",
        condition="cv_grpo", args=cv_args,
        reward_mean=0.578, frac_zero_std=0.287,
        noise={
            "actor_rollout_ref.rollout.gpu_memory_utilization": "0.80",
            "actor_rollout_ref.rollout.max_num_seqs": "64",
            "trainer.save_freq": "50",
        },
    )
    report = validate_method_differentiation(tmp_path)
    assert report.pairs[0].config_diff_kind == "reward_name_only"
    assert report.pairs[0].no_op_suspected is True






def test_reward_name_only_but_distinguishable_outcomes_not_no_op(tmp_path: Path):
    # Same config shape, only the reward-fn name differs, but the outcomes
    # genuinely diverge → the treatment IS doing something; not flagged.
    _write_run(
        tmp_path, "optimizer_vanilla_grpo_full1200",
        condition="vanilla_grpo", args=_BASE_ARGS,
        reward_mean=0.50, frac_zero_std=0.30,
    )
    cv_args = dict(_BASE_ARGS)
    cv_args["reward.custom_reward_function.name"] = "compute_score_cv"
    _write_run(
        tmp_path, "optimizer_cv_grpo_full1200",
        condition="cv_grpo", args=cv_args,
        reward_mean=0.70, frac_zero_std=0.10,
    )
    report = validate_method_differentiation(tmp_path)
    pair = report.pairs[0]
    assert pair.config_diff_kind == "reward_name_only"
    assert pair.outcomes_indistinguishable is False
    assert pair.no_op_suspected is False


def test_explicit_conditions_override_autodetect(tmp_path: Path):
    _write_run(
        tmp_path, "optimizer_method_x",
        condition="method_x", args=_BASE_ARGS,
        reward_mean=0.50, frac_zero_std=0.30,
    )
    cv_args = dict(_BASE_ARGS)
    cv_args["reward.custom_reward_function.name"] = "compute_score_cv"
    _write_run(
        tmp_path, "optimizer_method_y",
        condition="method_y", args=cv_args,
        reward_mean=0.50, frac_zero_std=0.30,
    )
    # Neither name is baseline-ish → auto-detect finds no pair.
    assert validate_method_differentiation(tmp_path).pairs == []
    # Explicit labels make the comparison.
    report = validate_method_differentiation(
        tmp_path, proposed_condition="method_y", baseline_condition="method_x",
    )
    assert len(report.pairs) == 1
    assert report.pairs[0].no_op_suspected is True




def test_probe_named_long_run_is_not_skipped(tmp_path: Path):
    # A real long resume tagged "...preflightfix" must still be compared
    # (regression: "preflight" is a probe token but the run trained 1200 steps).
    _write_run(
        tmp_path, "optimizer_vanilla_grpo_full1200",
        condition="vanilla_grpo", args=_BASE_ARGS,
        reward_mean=0.580, frac_zero_std=0.292,
    )
    cv_args = dict(_BASE_ARGS)
    cv_args["reward.custom_reward_function.name"] = "compute_score_cv"
    _write_run(
        tmp_path, "optimizer_cv_grpo_full1200_preflightfix",
        condition="cv_grpo", args=cv_args,
        reward_mean=0.578, frac_zero_std=0.287, state="running",
    )
    report = validate_method_differentiation(tmp_path)
    assert len(report.pairs) == 1
    assert report.pairs[0].proposed_run.endswith("preflightfix")
    assert report.pairs[0].no_op_suspected is True
