from __future__ import annotations

import json

from argus_skill.tools.subagent import (
    SUPERVISOR_INTERVAL_CAP,
    _child_env,
    _codex_last_agent_message,
    _effective_run_dir,
    _format_metric_line,
    _next_monitor_interval,
    _norm_decision,
    _norm_health,
    _progress_summary,
    _run_dir_from_command,
    _strip_code_fence,
)


def test_backoff_doubles_while_healthy_up_to_cap() -> None:
    base = 120
    i = _next_monitor_interval("healthy", base, base)
    assert i == 240
    i = _next_monitor_interval("healthy", i, base)
    assert i == 480
    i = _next_monitor_interval("healthy", i, base)
    assert i == min(960, SUPERVISOR_INTERVAL_CAP)
    # Never exceeds the cap.
    i = _next_monitor_interval("healthy", SUPERVISOR_INTERVAL_CAP, base)
    assert i == SUPERVISOR_INTERVAL_CAP


def test_backoff_snaps_back_to_base_when_unhealthy() -> None:
    base = 120
    for bad in ("degrading", "stuck", "diverging"):
        assert _next_monitor_interval(bad, 900, base) == base


def test_unknown_health_holds_steady_within_bounds() -> None:
    base = 120
    assert _next_monitor_interval("unknown", 300, base) == 300
    # Held value is still capped and floored.
    assert _next_monitor_interval("unknown", 99999, base) == SUPERVISOR_INTERVAL_CAP
    assert _next_monitor_interval("unknown", 10, base) == base


def test_cap_is_never_below_base() -> None:
    # A base larger than the default cap must still be respected as the floor.
    big_base = 1200
    assert _next_monitor_interval("healthy", big_base, big_base) == big_base
    assert _next_monitor_interval("degrading", 5000, big_base) == big_base


def test_norm_decision_maps_variants_and_defaults_safe() -> None:
    assert _norm_decision("early-stop") == "early_stop"
    assert _norm_decision("EARLY_STOP") == "early_stop"
    assert _norm_decision("save_checkpoint") == "save_checkpoint"
    assert _norm_decision("continue") == "continue"
    # Anything unrecognized defaults to the safe non-intervention decision.
    assert _norm_decision("blow_up") == "continue"
    assert _norm_decision(None) == "continue"


def test_norm_health_maps_aliases_else_unknown() -> None:
    assert _norm_health("degraded") == "degrading"
    assert _norm_health("diverged") == "diverging"
    assert _norm_health("stalled") == "stuck"
    assert _norm_health("HEALTHY") == "healthy"
    assert _norm_health("good") == "healthy"
    assert _norm_health("???") == "unknown"
    assert _norm_health(None) == "unknown"


def _codex_jsonl(*messages: str) -> str:
    """Build codex ``exec --json`` JSONL output carrying agent_message events."""
    lines = [json.dumps({"type": "thread.started", "thread_id": "t1"})]
    for m in messages:
        lines.append(json.dumps(
            {"type": "item.completed",
             "item": {"id": "i", "type": "agent_message", "text": m}}))
    lines.append(json.dumps({"type": "turn.completed", "usage": {}}))
    return "\n".join(lines) + "\n"


def test_codex_parser_extracts_last_agent_message() -> None:
    # The real codex schema: JSONL with item.completed/agent_message/text.
    stdout = _codex_jsonl("first reply", '{"decision": "continue"}')
    assert _codex_last_agent_message(stdout) == '{"decision": "continue"}'


def test_codex_parser_ignores_non_agent_events_and_bad_lines() -> None:
    stdout = (
        "not json at all\n"
        + json.dumps({"type": "item.completed",
                      "item": {"type": "reasoning", "text": "thinking"}}) + "\n"
        + _codex_jsonl("the answer")
    )
    assert _codex_last_agent_message(stdout) == "the answer"


def test_codex_parser_returns_empty_when_no_agent_message() -> None:
    assert _codex_last_agent_message("") == ""
    assert _codex_last_agent_message("garbage\nmore garbage") == ""


def test_supervisor_verdict_json_survives_round_trip() -> None:
    # End-to-end: a fenced JSON verdict from codex parses into decision/health.
    verdict = '```json\n{"decision": "early_stop", "health": "diverging"}\n```'
    stdout = _codex_jsonl(verdict)
    msg = _codex_last_agent_message(stdout)
    data = json.loads(_strip_code_fence(msg))
    assert _norm_decision(data["decision"]) == "early_stop"
    assert _norm_health(data["health"]) == "diverging"


def test_verdict_survives_trailing_non_json_chatter() -> None:
    # If codex emits the verdict and then a trailing prose message, the most
    # recent *parseable* verdict must still win (not a no-op continue/unknown).
    from argus_skill.tools.subagent import _codex_agent_messages

    stdout = _codex_jsonl(
        '{"decision": "early_stop", "health": "diverging"}',
        "Done. The run looked unhealthy so I stopped it.",
    )
    decision = health = None
    for message in reversed(_codex_agent_messages(stdout)):
        try:
            data = json.loads(_strip_code_fence(message))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "decision" in data:
            decision = _norm_decision(data["decision"])
            health = _norm_health(data["health"])
            break
    assert decision == "early_stop"
    assert health == "diverging"


def test_strip_code_fence_handles_plain_and_fenced() -> None:
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'
    assert _strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_child_env_quiets_framework_logs_by_default(monkeypatch) -> None:
    monkeypatch.setenv("NCCL_DEBUG", "INFO")
    monkeypatch.delenv("ARGUS_SUBAGENT_QUIET_LOGS", raising=False)
    monkeypatch.delenv("VLLM_LOGGING_LEVEL", raising=False)
    monkeypatch.delenv("TQDM_DISABLE", raising=False)
    env = _child_env()
    # Inherited INFO is forced down so the supervisor tail isn't drowned.
    assert env["NCCL_DEBUG"] == "WARN"
    assert env["VLLM_LOGGING_LEVEL"] == "WARNING"
    assert env["TQDM_DISABLE"] == "1"


def test_child_env_respects_explicit_vllm_and_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("VLLM_LOGGING_LEVEL", "DEBUG")
    monkeypatch.setenv("ARGUS_SUBAGENT_QUIET_LOGS", "0")
    monkeypatch.setenv("NCCL_DEBUG", "INFO")
    env = _child_env()
    # Opt-out leaves the inherited environment untouched.
    assert env["NCCL_DEBUG"] == "INFO"
    assert env["VLLM_LOGGING_LEVEL"] == "DEBUG"


def test_run_dir_parsed_from_command_space_and_equals() -> None:
    cmd = ("python -m argus_skill.tools.gpu_lease run -- env CUDA_VISIBLE_DEVICES=0 "
           ".venv/bin/python code/run_benchmark_condition.py --method B0 "
           "--run-dir experiments/runs/full-B0-math500 --use-runwriter")
    assert _run_dir_from_command(cmd) == "experiments/runs/full-B0-math500"
    assert _run_dir_from_command("foo --run-dir=out/x bar") == "out/x"
    assert _run_dir_from_command("no run dir here") is None
    assert _run_dir_from_command("") is None


def test_effective_run_dir_falls_back_to_command() -> None:
    # Stored run_dir wins.
    assert _effective_run_dir(
        {"run_dir": "/abs/path", "command": "x --run-dir other"}) == "/abs/path"
    # Otherwise recovered from the command (the black-box case).
    assert _effective_run_dir(
        {"command": "x --run-dir experiments/runs/foo"}) == "experiments/runs/foo"
    assert _effective_run_dir({"command": "x"}) is None


def test_progress_summary_surfaces_status_and_reward(tmp_path) -> None:
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "progress.jsonl").write_text(
        '{"event": "start"}\n{"event": "done"}\n', encoding="utf-8")
    (rd / "results.jsonl").write_text('{"r": 1}\n{"r": 0}\n', encoding="utf-8")
    (rd / "status.json").write_text(
        json.dumps({"state": "running", "method": "B0"}), encoding="utf-8")
    (rd / "summary.tsv").write_text(
        "row_kind\tcondition\tdataset_id\treward\tn_total_trials\t"
        "n_completed_trials\tn_errored_trials\n"
        "aggregate\tB0\tmath500\t0.539773\t176\t176\t0\n",
        encoding="utf-8")
    summ = _progress_summary(str(rd))
    assert summ["progress_rows"] == 2
    assert summ["result_rows"] == 2
    assert summ["state"] == "running"
    assert summ["method"] == "B0"
    assert summ["metrics"][0]["reward"] == 0.539773
    assert summ["metrics"][0]["completed"] == 176
    assert summ["metrics"][0]["total"] == 176
    assert summ["metrics"][0]["errored"] == 0


def test_progress_summary_empty_for_missing_dir() -> None:
    assert _progress_summary(None) == {}
    assert _progress_summary("/nonexistent/run/dir/xyz") == {}


def test_format_metric_line_is_compact_and_readable(tmp_path) -> None:
    summ = {
        "state": "completed",
        "metrics": [{"dataset": "bfcl", "reward": 0.3333, "completed": 240,
                     "total": 240, "errored": 0}],
    }
    line = _format_metric_line(summ)
    assert "completed" in line
    assert "bfcl" in line
    assert "reward=0.3333" in line
    assert "240/240" in line
    assert _format_metric_line({}) == ""

