from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from benchmarks.prompt_only_tb2.run_pilot_row import (
    DEFAULT_CODEX_BASELINE_MODEL,
    _argus_cost_from_logs,
    _argus_stdin,
    _codex_command,
    _cost_model_for,
    _env_for_row,
)
from benchmarks.prompt_only_tb2.summarize_runs import (
    _normalized_zero_touch_success,
    latest_per_assignment,
    load_result_rows,
    merge_verification,
    reprice_codex_rows,
    summarize,
)
from benchmarks.prompt_only_tb2.verify_runs import (
    _candidate_container,
    _container_names_from_logs,
    _manual_attention_fields,
    _reward_passed,
)


def test_argus_stdin_disables_iteration_before_pasted_prompt() -> None:
    stdin_text = _argus_stdin("line one\n\nline two")

    assert stdin_text.startswith("/config iterate=false\n\x1b[200~")
    assert "line one\n\nline two" in stdin_text
    assert stdin_text.endswith("\x1b[201~\n/exit\n")


def test_argus_env_enables_benchmark_lean_mode(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_REVIEWER_MODEL", raising=False)
    env = _env_for_row({"condition": "argus"}, Path("/tmp/run-root"))

    assert env["ARGUS_SKILL_BENCHMARK_MODE"] == "1"
    assert env["ARGUS_SKILL_BENCHMARK_VERIFIER_GATE"] == "1"
    assert "ARGUS_SKILL_NO_REVIEWER" not in env
    assert env["ARGUS_SKILL_BENCHMARK_TERSE"] == "1"
    assert env["ARGUS_SKILL_DISTILL_ON_MISS"] == "0"
    assert env["ARGUS_SKILL_SKILL_WRITEBACK"] == "0"
    assert env["ARGUS_SKILL_REVIEWER_MODEL"] == env["ARGUS_SKILL_ENGINEER_MODEL"]
    assert env["ARGUS_SKILL_SCIENTIST_MODEL"] == env["ARGUS_SKILL_ENGINEER_MODEL"]
    assert env["ARGUS_SKILL_ENGINEER_REASONING_EFFORT"] == "low"
    assert env["ARGUS_SKILL_REVIEWER_REASONING_EFFORT"] == "low"


def test_codex_baseline_defaults_to_full_model() -> None:
    args = Namespace(
        codex_bin="codex",
        codex_sandbox="danger-full-access",
        codex_json=True,
        codex_model=DEFAULT_CODEX_BASELINE_MODEL,
        codex_extra_arg=[],
        cost_model="",
    )
    row = {"condition": "codex"}

    cmd = _codex_command(args, Path("/tmp/run-root"))

    assert ["--model", "gpt-5.4"] == cmd[cmd.index("--model") : cmd.index("--model") + 2]
    assert _cost_model_for(args, row) == "gpt-5.4"


def test_argus_cost_uses_child_env_model_defaults(tmp_path: Path) -> None:
    events_dir = tmp_path / ".argus-skill" / "projects" / "p1"
    events_dir.mkdir(parents=True)
    (events_dir / "events.jsonl").write_text(
        '{"type":"round.review.completed","input_tokens":1000,'
        '"cached_input_tokens":0,"output_tokens":100,"usage_scope":"delta"}\n',
        encoding="utf-8",
    )

    cost = _argus_cost_from_logs(
        tmp_path,
        env={
            "ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.4-mini",
            "ARGUS_SKILL_REVIEWER_MODEL": "gpt-5.4-mini",
            "ARGUS_SKILL_SCIENTIST_MODEL": "gpt-5.4-mini",
        },
    )

    assert cost["model_token_stats"]["gpt-5.4-mini"]["layers"] == ["reviewer"]
    assert cost["cost_model"] == "gpt-5.4-mini"


def test_prompt_only_summary_merges_verification_and_latest_attempt() -> None:
    rows = [
        {
            "order": "1",
            "condition": "codex",
            "task_id": "demo",
            "run_id": "20260513T010000Z-old",
            "needs_human": "True",
            "timed_out": "True",
            "cost_usd": "0.10",
            "wall_minutes": "30.00",
            "zero_touch_success": "False",
            "human_interactions_after_assignment": "2",
            "active_touch_minutes_after_assignment": "12.5",
            "intervention_severity": "needs_human",
            "manual_commands": "1",
            "manual_rescue": "",
        },
        {
            "order": "1",
            "condition": "codex",
            "task_id": "demo",
            "run_id": "20260513T020000Z-new",
            "needs_human": "False",
            "timed_out": "False",
            "cost_usd": "0.05",
            "wall_minutes": "2.00",
            "zero_touch_success": "True",
            "human_interactions_after_assignment": "0",
            "active_touch_minutes_after_assignment": "0.0",
            "intervention_severity": "zero_touch",
            "manual_commands": "0",
            "manual_rescue": "",
        },
        {
            "order": "2",
            "condition": "argus",
            "task_id": "demo",
            "run_id": "20260513T020500Z",
            "needs_human": "False",
            "cost_usd": "0.07",
            "wall_minutes": "3.00",
            "accepted": "True",
            "verification_reward": "1",
            "zero_touch_success": "True",
            "human_interactions_after_assignment": "0",
            "active_touch_minutes_after_assignment": "0.0",
            "intervention_severity": "zero_touch",
            "manual_commands": "0",
            "manual_rescue": "",
        },
    ]
    verification = {
        ("1", "codex", "demo"): {"accepted": "True", "reward": "1"},
        ("2", "argus", "demo"): {"accepted": "False", "reward": "0"},
    }

    merged = merge_verification(rows, verification)
    latest = latest_per_assignment(merged)
    summary = summarize(latest)

    assert summary["total_rows"] == 2
    assert summary["conditions"]["codex"]["accepted_rate"] == 1.0
    assert summary["conditions"]["codex"]["mean_reward"] == 1.0
    assert summary["conditions"]["codex"]["avg_cost_usd"] == 0.05
    assert summary["conditions"]["codex"]["timeouts"] == 0
    assert summary["conditions"]["argus"]["accepted_rate"] == 0.0
    assert summary["conditions"]["codex"]["zero_touch_success_rate"] == 1.0
    assert summary["conditions"]["codex"]["human_interactions_after_assignment"] == 0.0
    assert summary["conditions"]["codex"]["active_touch_minutes_after_assignment"] == 0.0
    assert summary["conditions"]["codex"]["manual_commands"] == 0.0
    assert summary["conditions"]["codex"]["rescue_rate"] is None


def test_prompt_only_summary_counts_only_positive_rescue_outcomes() -> None:
    rows = [
        {
            "order": "1",
            "condition": "argus",
            "task_id": "failed",
            "manual_rescue": "failed",
        },
        {
            "order": "2",
            "condition": "codex",
            "task_id": "rescued",
            "manual_rescue": "rescued",
        },
    ]

    summary = summarize(rows)

    assert summary["conditions"]["argus"]["rescued"] == 0
    assert summary["conditions"]["argus"]["rescue_rate"] == 0.0
    assert summary["conditions"]["codex"]["rescued"] == 1
    assert summary["conditions"]["codex"]["rescue_rate"] == 1.0


def test_prompt_only_summary_normalizes_zero_touch_without_manual_intervention() -> None:
    rows = [
        {
            "order": "1",
            "condition": "codex",
            "task_id": "demo",
            "accepted": "True",
            "needs_human": "False",
            "zero_touch_success": "False",
            "human_interactions_after_assignment": "0",
            "active_touch_minutes_after_assignment": "0.0",
            "manual_commands": "0",
            "manual_rescue": "",
            "intervention_severity": "zero_touch",
        }
    ]

    summary = summarize(rows)

    assert summary["conditions"]["codex"]["zero_touch_success"] == 1
    assert summary["conditions"]["codex"]["zero_touch_success_rate"] == 1.0


def test_prompt_only_summary_treats_reviewer_off_shortcut_as_zero_touch() -> None:
    row = {
        "order": "1",
        "condition": "codex",
        "task_id": "demo",
        "accepted": "False",
        "needs_human": "False",
        "zero_touch_success": "False",
        "human_interactions_after_assignment": "0",
        "active_touch_minutes_after_assignment": "0.0",
        "manual_commands": "0",
        "manual_rescue": "none",
        "intervention_severity": "reviewer_off_shortcut",
    }

    assert _normalized_zero_touch_success(row) is True


def test_prompt_only_verification_summary_normalizes_zero_touch_without_manual_intervention() -> None:
    fields = _manual_attention_fields(
        {
            "zero_touch_success": False,
            "human_interactions_after_assignment": 0,
            "active_touch_minutes_after_assignment": 0.0,
            "manual_commands": 0,
            "nudges": 0,
            "status_checks": 0,
            "manual_rescue": "",
            "intervention_severity": "zero_touch",
            "needs_human": False,
        }
    )

    assert fields["zero_touch_success"] is True


def test_prompt_only_verification_summary_treats_reviewer_off_shortcut_as_zero_touch() -> None:
    fields = _manual_attention_fields(
        {
            "zero_touch_success": False,
            "human_interactions_after_assignment": 0,
            "active_touch_minutes_after_assignment": 0.0,
            "manual_commands": 0,
            "nudges": 0,
            "status_checks": 0,
            "manual_rescue": "none",
            "intervention_severity": "reviewer_off_shortcut",
            "needs_human": False,
        }
    )

    assert fields["zero_touch_success"] is True


def test_prompt_only_summary_can_reprice_codex_rows() -> None:
    rows = [
        {
            "order": "1",
            "condition": "codex",
            "task_id": "demo",
            "cost_usd": "0.002500",
            "cost_model": "gpt-5.4-mini",
            "cost_source": "argus_pricing_codex_json_tokens",
            "model_token_stats": (
                '{"gpt-5.4-mini":{"input_tokens":1000,'
                '"cached_input_tokens":0,"output_tokens":100}}'
            ),
        },
        {
            "order": "2",
            "condition": "argus",
            "task_id": "demo",
            "cost_usd": "0.002500",
            "cost_model": "gpt-5.4-mini",
        },
    ]

    repriced = reprice_codex_rows(rows, model="gpt-5.4")

    assert repriced[0]["cost_usd"] == "0.002250"
    assert repriced[0]["cost_model"] == "gpt-5.4"
    assert repriced[0]["cost_source"] == "argus_pricing_codex_json_tokens+repriced"
    assert repriced[1]["cost_usd"] == "0.002500"


def test_prompt_only_summary_reads_root_results_csv(tmp_path: Path) -> None:
    (tmp_path / "results.csv").write_text(
        "order,condition,task_id,cost_usd,wall_minutes,needs_human\n"
        "1,codex,demo,0.01,1.5,False\n",
        encoding="utf-8",
    )

    rows = load_result_rows(tmp_path)

    assert len(rows) == 1
    assert rows[0]["condition"] == "codex"


def test_prompt_only_verifier_finds_container_names_from_logs(tmp_path: Path) -> None:
    (tmp_path / "stdout.log").write_text(
        "docker run --name tb2-argus-demo-task-123 image\n"
        "docker exec tb2-argus-demo-task-123 true\n",
        encoding="utf-8",
    )
    (tmp_path / "stderr.log").write_text(
        "also mentions tb2-argus-demo-task-456\n",
        encoding="utf-8",
    )

    names = _container_names_from_logs(tmp_path)

    assert names == ["tb2-argus-demo-task-123", "tb2-argus-demo-task-456"]


def test_prompt_only_verifier_prefers_logged_running_container(tmp_path: Path) -> None:
    (tmp_path / "stdout.log").write_text(
        "tb2-argus-demo-task-old tb2-argus-demo-task-new\n",
        encoding="utf-8",
    )
    row = {
        "run_root": str(tmp_path),
        "condition": "argus",
        "task_id": "demo-task",
    }

    container = _candidate_container(
        row,
        running={"tb2-argus-demo-task-new", "tb2-argus-demo-task-other"},
    )

    assert container == "tb2-argus-demo-task-new"


def test_prompt_only_verifier_reward_truthiness() -> None:
    assert _reward_passed("1")
    assert _reward_passed("0.5")
    assert not _reward_passed("0")
    assert not _reward_passed("")


def test_prompt_only_verification_summary_keeps_manual_attention_fields() -> None:
    fields = _manual_attention_fields(
        {
            "zero_touch_success": False,
            "human_interactions_after_assignment": 2,
            "active_touch_minutes_after_assignment": 6.0,
            "manual_commands": 1,
            "manual_rescue": "failed",
            "intervention_severity": "manual_rescue",
            "needs_human": True,
        }
    )

    assert fields == {
        "zero_touch_success": False,
        "human_interactions_after_assignment": 2,
        "active_touch_minutes_after_assignment": 6.0,
        "manual_commands": 1,
        "manual_rescue": "failed",
        "intervention_severity": "manual_rescue",
        "needs_human": True,
    }
