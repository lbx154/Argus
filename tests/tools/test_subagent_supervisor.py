from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.core.token_usage import sum_token_counts
from argus_skill.tools import subagent as _sub
from argus_skill.tools.subagent import (
    _append_discussion,
    _build_report,
    _child_env,
    _clean_concern,
    _codex_last_agent_message,
    _discussion_path,
    _effective_run_dir,
    _engineer_turn_count,
    _format_metric_line,
    _next_monitor_interval,
    _norm_decision,
    _norm_health,
    _progress_summary,
    _read_discussion,
    _render_discussion,
    _reply_back_block,
    _reset_discussion,
    _run_dir_from_command,
    _strip_code_fence,
    _supervisor_check,
    _supervisor_discuss,
    _write_task,
    cmd_reply,
)

requires_fork = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="subagent daemonization uses POSIX fork",
)

_ORIGINAL_RUN_BACKEND_TURN = _sub._llm._run_backend_turn


@pytest.fixture(autouse=True)
def _guard_live_codex_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic unit tests must opt into local Codex fakes explicitly."""

    def blocked_turn(*args: object, **kwargs: object) -> RunnerResult:
        raise AssertionError(
            "Unexpected live subagent backend turn in a unit test; "
            "use _install_fake_codex(...) for intentional model-boundary coverage."
        )

    monkeypatch.setattr(_sub._llm, "_run_backend_turn", blocked_turn)


def _install_fake_codex(monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., object]) -> None:
    def fake_turn(
        prompt: str,
        model: str,
        cwd: str,
        thread_id: str | None,
        timeout: int,
        run_label: str,
        mission_id: str | None = None,
    ) -> RunnerResult:
        cmd = ["codex", "exec"]
        if thread_id:
            cmd += ["resume", thread_id]
        cmd += ["--json", "-m", model, "-"]
        raw = fake_run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        stdout = str(getattr(raw, "stdout", "") or "")
        usage = sum_token_counts(_sub._registry._parse_codex_jsonl_events(stdout))
        return RunnerResult(
            exit_code=int(getattr(raw, "returncode", 0) or 0),
            agent_messages=_sub._codex_agent_messages(stdout),
            thread_id=_sub._codex_thread_id(stdout) or thread_id,
            input_tokens=usage[0],
            cached_input_tokens=usage[1],
            output_tokens=usage[2],
            reasoning_output_tokens=usage[3],
            usage_model=model,
        )

    monkeypatch.setattr(_sub._llm, "_run_backend_turn", fake_turn)


def test_legacy_hashed_registry_record_is_read_and_migrated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.core.portable_filename import legacy_hashed_filename_components
    from argus_skill.tools.subagent import _registry

    monkeypatch.chdir(tmp_path)
    task_id = "team::task"
    legacy_component = legacy_hashed_filename_components(task_id)[0]
    legacy = _registry.REGISTRY_DIR / f"{legacy_component}.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"task_id": task_id, "status": "running"}) + "\n",
        encoding="utf-8",
    )
    legacy_exit = _registry.REGISTRY_DIR / f"{legacy_component}_logs" / "exit_code.run-1"
    legacy_exit.parent.mkdir()
    legacy_exit.write_text("7\n", encoding="utf-8")

    assert _registry._read_task(task_id)["status"] == "running"
    assert _registry._read_exit_code(task_id, "run-1") == 7
    _registry._write_task(task_id, {"task_id": task_id, "status": "done"})

    assert _registry._registry_path(task_id).exists()
    assert not legacy.exists()
    assert _registry._list_tasks() == [{"task_id": task_id, "status": "done"}]

    legacy_only_id = "~legacy"
    legacy_only = _registry.REGISTRY_DIR / f"{legacy_only_id}.json"
    legacy_only.write_text(
        json.dumps({"task_id": legacy_only_id, "state": "done"}) + "\n",
        encoding="utf-8",
    )

    assert _sub.cmd_clean(argparse.Namespace()) == 0
    assert not legacy_only.exists()

    other_id = "other::task"
    aliased = _registry.REGISTRY_DIR / (
        f"{legacy_hashed_filename_components(other_id)[0]}.json"
    )
    aliased.write_text(
        json.dumps({"task_id": other_id, "state": "done"}) + "\n",
        encoding="utf-8",
    )
    alias_id = aliased.stem

    _registry._unlink_task_records(alias_id)

    assert aliased.exists()


def _skip_supervisor_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_sub._reporting, "_supervisor_summarize_report", lambda *args, **kwargs: "")


def test_backoff_keeps_doubling_while_healthy() -> None:
    base = 120
    i = _next_monitor_interval("healthy", base, base)
    assert i == 240
    i = _next_monitor_interval("healthy", i, base)
    assert i == 480
    i = _next_monitor_interval("healthy", i, base)
    assert i == 960
    i = _next_monitor_interval("healthy", i, base)
    assert i == 1920


def test_backoff_snaps_back_to_base_when_unhealthy() -> None:
    base = 120
    for bad in ("degrading", "stuck", "diverging"):
        assert _next_monitor_interval(bad, 900, base) == base


def test_unknown_health_holds_steady_with_floor() -> None:
    base = 120
    assert _next_monitor_interval("unknown", 300, base) == 300
    assert _next_monitor_interval("unknown", 99999, base) == 99999
    assert _next_monitor_interval("unknown", 10, base) == base


def test_base_is_respected_as_the_floor() -> None:
    big_base = 1200
    assert _next_monitor_interval("healthy", big_base, big_base) == 2400
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


def test_clean_concern_treats_nothing_phrases_as_empty() -> None:
    assert _clean_concern("none") == ""
    assert _clean_concern("  ") == ""
    assert _clean_concern("No concerns.") == ""
    assert _clean_concern(None) == ""
    # Broadened no-op phrasings the supervisor might emit.
    assert _clean_concern("No concerns at this time") == ""
    assert _clean_concern("Nothing noteworthy.") == ""
    assert _clean_concern("All good, healthy progress") == ""
    # A real concern is normalized (whitespace collapsed) but preserved.
    assert _clean_concern("  clipped_ratio  is  1.0 ") == "clipped_ratio is 1.0"


def test_live_codex_boundary_guard_blocks_unfaked_calls() -> None:
    with pytest.raises(AssertionError, match="live subagent backend turn"):
        _sub._llm._run_backend_turn("", "", ".", None, 1, "test")


def test_supervisor_check_concern_now_means_stop_in_prompt(monkeypatch, tmp_path) -> None:
    # The recalibrated prompt must tell the supervisor that a concern STOPS the
    # run, so it only raises one for a genuine stop-worthy anomaly.
    monkeypatch.chdir(tmp_path)
    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl('{"decision": "continue", "health": "healthy", "concern": ""}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    out = tmp_path / "stdout.log"
    err = tmp_path / "stderr.log"
    out.write_text("step 1\n")
    err.write_text("")
    _supervisor_check("t", "python train.py", "run", out, err, 60.0, 1, "gpt-5.5", str(tmp_path))
    prompt = captured["prompt"]
    assert "STOPS the run" in prompt
    assert "EMPTY" in prompt


def test_supervisor_check_injects_rl_collapse_guidance(monkeypatch, tmp_path) -> None:
    # The supervisor prompt must carry the RL-collapse-diagnosis skill so the
    # model's stop/continue call is grounded in concrete collapse signatures
    # (e.g. tail-window reward-variance death) rather than vibes.
    monkeypatch.chdir(tmp_path)
    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl('{"decision": "continue", "health": "healthy", "concern": ""}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    out = tmp_path / "stdout.log"
    err = tmp_path / "stderr.log"
    out.write_text("step 1\n")
    err.write_text("")
    _supervisor_check("t", "python train.py", "run", out, err, 60.0, 1, "gpt-5.5", str(tmp_path))
    prompt = captured["prompt"]
    assert "when an RL run has COLLAPSED" in prompt
    # The transient-vs-sustained judgement is the crux of the skill.
    assert "tail-window" in prompt or "tail window" in prompt.lower()


def test_rl_collapse_guidance_loads_and_strips_frontmatter() -> None:
    from argus_skill.tools.subagent import _rl_collapse_guidance

    guidance = _rl_collapse_guidance()
    assert guidance, "RL collapse guidance should load from the bundled skill"
    assert not guidance.startswith("---"), "YAML frontmatter must be stripped"
    assert "reward-variance death" in guidance.lower()


def test_supervisor_verdict_parses_concern_alongside_decision() -> None:
    # A run can be healthy/continue yet still carry a concern for the engineer.
    verdict = (
        '{"decision": "continue", "health": "healthy",'
        ' "concern": "clipped_ratio is 1.0; max_completion_length 256 truncates'
        ' the math reasoning"}'
    )
    stdout = _codex_jsonl(verdict)
    data = json.loads(_strip_code_fence(_codex_last_agent_message(stdout)))
    assert _norm_decision(data["decision"]) == "continue"
    assert _norm_health(data["health"]) == "healthy"
    assert "256" in _clean_concern(data["concern"])


def test_build_report_surfaces_concern_for_stopped_task(monkeypatch) -> None:
    _skip_supervisor_summary(monkeypatch)
    report = _build_report(
        "train-B1",
        "EARLY-STOPPED",
        {
            "description": "B1 GRPO",
            "command": "python train.py",
            "concern": "clipped_ratio is 1.0; 256 too short",
            "mode": "supervised",
            "elapsed_seconds": 120,
            "run_dir": None,
        },
    )
    assert "Supervisor concern" in report
    assert "256 too short" in report
    # A concern now STOPS the run and opens a discussion the engineer must join.
    assert "STOPPED" in report
    assert "discussion" in report.lower()


def test_direct_report_never_calls_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        _sub._reporting,
        "_supervisor_summarize_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct mode called the model")
        ),
    )
    report = _build_report(
        "direct-1",
        "COMPLETED",
        {
            "description": "plain background command",
            "command": "echo ok",
            "mode": "direct",
            "elapsed_seconds": 1,
            "exit_code": 0,
        },
    )
    assert "plain background command" in report


def test_timeout_report_does_not_misclassify_timeout_as_failure() -> None:
    report = _build_report(
        "bounded-search",
        "TIMEOUT",
        {
            "description": "bounded solver run",
            "command": "solver problem.cnf",
            "mode": "direct",
            "elapsed_seconds": 3600,
        },
    )

    assert "treat the result as inconclusive" in report
    assert "Do not infer a workload failure from the timeout alone" in report
    assert "inspect stderr for root cause" not in report


def test_supervisor_authors_report_grounded_in_diagnosis(monkeypatch) -> None:
    # The summary + next step must be authored from the supervisor's own
    # diagnosis, not a signal-blind summarizer that only sees stdout.
    from argus_skill.tools import subagent as sub

    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl(
            "I stopped B2 because completions were truncated, not formatted. "
            "Next step: raise max_completion_length and re-check termination."
        )
        return r

    _install_fake_codex(monkeypatch, fake_run)

    out = sub._supervisor_summarize_report(
        "train-B2",
        "EARLY-STOPPED",
        {
            "concern": (
                "clipped_ratio=1.0 and mean_terminated_length=0; generations are "
                "truncated rather than formatted answers"
            ),
            "stop_reason": "supervisor early-stop",
            "last_supervisor_decision": "early_stop",
            "last_supervisor_health": "degrading",
            "command": "python train.py",
        },
    )
    prompt = captured["prompt"]
    # The supervisor's diagnosis is fed into the report it authors...
    assert "clipped_ratio=1.0" in prompt
    assert "early-stop" in prompt.lower()
    # ...and the next step is steered to the root cause, not a blind rerun.
    assert "root cause" in prompt.lower()
    assert "rerunning unchanged" in prompt.lower()
    # The authored report is returned.
    assert "max_completion_length" in out


def test_supervisor_report_usage_is_added_to_task_record(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    task = {
        "task_id": "train-report",
        "run_id": "train-report-run-1",
        "mode": "supervised",
        "cwd": str(tmp_path),
        "supervisor_usage_model": "gpt-5.5",
        "supervisor_input_tokens": 10,
        "supervisor_cached_input_tokens": 2,
        "supervisor_output_tokens": 3,
        "supervisor_reasoning_output_tokens": 1,
    }
    _write_task("train-report", dict(task))

    monkeypatch.setattr(
        _sub._llm,
        "_run_backend_turn",
        lambda *args, **kwargs: RunnerResult(
            exit_code=0,
            agent_messages=["terminal report"],
            thread_id="REPORT",
            input_tokens=7,
            cached_input_tokens=1,
            output_tokens=5,
            reasoning_output_tokens=2,
        ),
    )

    assert (
        _sub._supervisor_summarize_report("train-report", "COMPLETED", task)
        == "terminal report"
    )
    saved = json.loads(
        (tmp_path / ".argus_subagents" / "train-report.json").read_text()
    )
    assert saved["supervisor_input_tokens"] == 17
    assert saved["supervisor_cached_input_tokens"] == 3
    assert saved["supervisor_output_tokens"] == 8
    assert saved["supervisor_reasoning_output_tokens"] == 3


def test_supervisor_report_uses_persisted_submit_cwd(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    task = {
        "task_id": "train-cwd",
        "run_id": "train-cwd-run-1",
        "mode": "supervised",
    }
    _write_task("train-cwd", {**task, "cwd": str(project)})
    seen: dict[str, str] = {}

    def fake_turn(prompt, model, cwd, thread_id, run_label, mission_id=None):
        seen["cwd"] = cwd
        return RunnerResult(exit_code=0, agent_messages=["terminal report"])

    monkeypatch.setattr(_sub._llm, "_run_backend_turn", fake_turn)

    _sub._supervisor_summarize_report("train-cwd", "COMPLETED", task)

    assert seen["cwd"] == str(project)


def test_late_report_does_not_overwrite_reused_task_id(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    old_task = {
        "task_id": "reused",
        "run_id": "old-run",
        "mode": "supervised",
        "cwd": str(tmp_path),
        "supervisor_input_tokens": 10,
    }
    _write_task("reused", dict(old_task))

    def fake_turn(prompt, model, cwd, thread_id, run_label, mission_id=None):
        _write_task(
            "reused",
            {
                "task_id": "reused",
                "run_id": "new-run",
                "state": "running",
                "mode": "supervised",
                "supervisor_input_tokens": 1,
            },
        )
        return RunnerResult(
            exit_code=0,
            agent_messages=["old terminal report"],
            input_tokens=7,
        )

    monkeypatch.setattr(_sub._llm, "_run_backend_turn", fake_turn)

    _sub._supervisor_summarize_report("reused", "COMPLETED", old_task)

    saved = json.loads((tmp_path / ".argus_subagents" / "reused.json").read_text())
    assert saved["run_id"] == "new-run"
    assert saved["state"] == "running"
    assert saved["supervisor_input_tokens"] == 1


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
           ".venv/bin/python code/run_condition.py --method B0 "
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



# ---------------------------------------------------------------------------
# Supervisor <-> engineer discussion thread (stop-and-discuss)
# ---------------------------------------------------------------------------

def test_discussion_roundtrip_roles_and_partial_line(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-demo"
    assert _engineer_turn_count(tid) == 0
    _append_discussion(tid, "supervisor", "I stopped: pass rate is 0/120")
    _append_discussion(tid, "engineer", "  shorter is   chosen for\nstability ")
    _append_discussion(tid, "engineer", "second note")
    # Only engineer turns are counted (so the supervisor detects new replies).
    assert _engineer_turn_count(tid) == 2
    rendered = _render_discussion(tid)
    assert "[supervisor] I stopped" in rendered
    assert "[engineer] shorter is chosen for stability" in rendered
    assert "second note" in rendered
    # A concurrent half-written final line must not corrupt the transcript.
    with _discussion_path(tid).open("a") as f:
        f.write('{"role": "engineer", "message": "half')
    assert _engineer_turn_count(tid) == 2
    assert "half" not in _render_discussion(tid)
    assert len(_read_discussion(tid)) == 3
    _reset_discussion(tid)
    assert _engineer_turn_count(tid) == 0


def test_reply_back_block_only_for_early_stop() -> None:
    tid = "train-x"
    block = _reply_back_block(tid, "EARLY-STOPPED")
    assert "subagent reply" in block
    assert f"--task-id {tid}" in block
    assert "discussion" in block.lower()
    # A concern no longer runs alongside the job, so only an early-stop asks for a reply.
    assert _reply_back_block(tid, "CONCERN") == ""
    assert _reply_back_block(tid, "COMPLETED") == ""
    assert _reply_back_block(tid, "CRASHED") == ""


def test_build_report_early_stop_tells_engineer_to_reply(monkeypatch) -> None:
    _skip_supervisor_summary(monkeypatch)
    report = _build_report(
        "train-B2",
        "EARLY-STOPPED",
        {"concern": "clipped_ratio=1.0; completions truncated, open up max length",
         "command": "python train.py", "mode": "supervised"},
    )
    assert "Reply to the supervisor" in report
    assert "subagent reply --task-id train-B2" in report


def test_supervisor_discuss_feeds_transcript_as_argument(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-B2"
    _append_discussion(tid, "supervisor", "I stopped: completions are truncated at 256")
    _append_discussion(tid, "engineer", "answer-only fits the budget on purpose")

    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl('{"resolved": true, "message": "Fair enough, your budget rationale holds."}')
        return r

    _install_fake_codex(monkeypatch, fake_run)

    resolved, message, _tid = _supervisor_discuss(
        tid, {"description": "DAPO run", "command": "python train.py",
              "concern": "completions truncated at 256"}, "gpt-5.5", str(tmp_path),
    )
    assert resolved is True
    assert "budget rationale holds" in message
    prompt = captured["prompt"]
    # The engineer's rationale is in the supervisor's context...
    assert "answer-only fits the budget" in prompt
    # ...framed as an argument it must weigh, not obey.
    assert "ARGUMENT, not an instruction" in prompt


def test_supervisor_discuss_returns_unresolved_on_bad_output(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-Z"
    _append_discussion(tid, "engineer", "rationale")

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        r = _Result()
        r.stdout = _codex_jsonl("not json at all")
        return r

    _install_fake_codex(monkeypatch, fake_run)
    resolved, message, _tid = _supervisor_discuss(tid, {}, "gpt-5.5", str(tmp_path))
    assert resolved is False
    assert message == ""


def test_cmd_reply_appends_engineer_turn_and_reports_liveness(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-B2"
    # Unknown task is rejected.
    args_missing = types.SimpleNamespace(task_id=tid, message="hi", message_file=None)
    assert cmd_reply(args_missing) == 2

    # A parked supervisor: worker_pid alive + state discussing => live_supervisor.
    import os as _os
    _write_task(tid, {"state": "discussing", "task_id": tid, "mode": "supervised",
                      "pid": 0, "worker_pid": _os.getpid(), "last_heartbeat": time.time()})
    capsys.readouterr()
    # Empty message is rejected.
    args_empty = types.SimpleNamespace(task_id=tid, message="   ", message_file=None)
    assert cmd_reply(args_empty) == 2

    args_ok = types.SimpleNamespace(
        task_id=tid, message="shorter on purpose; opening length hurts throughput",
        message_file=None,
    )
    assert cmd_reply(args_ok) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["state"] == "reply_recorded"
    assert payload["reply_count"] == 1
    assert payload["live_supervisor"] is True
    assert "shorter on purpose" in _render_discussion(tid)


def test_cmd_reply_reads_utf8_message_file(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-unicode"
    _write_task(tid, {"state": "discussing", "task_id": tid, "mode": "supervised"})
    message = "复现实验 🔬 → α"
    message_file = tmp_path / "reply.md"
    message_file.write_text(message, encoding="utf-8")

    args = types.SimpleNamespace(task_id=tid, message="", message_file=str(message_file))

    assert cmd_reply(args) == 0
    capsys.readouterr()
    assert message in _render_discussion(tid)


def test_queue_fallback_writes_utf8_report(monkeypatch, tmp_path) -> None:
    """The forensic copy keeps its UTF-8 contract — and no longer passes for delivery.

    The alert file used to be the whole failure path: written, and then silently
    returned from as though the engineer had been told. It is still written, but
    the caller now learns the report never landed.
    """
    monkeypatch.setattr(_sub._reporting, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(
        "argus_skill.apps._inbox.queue_inbox_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    report = "实验报告 🔬 → α"

    with pytest.raises(_sub._reporting.InboxDeliveryError):
        _sub._reporting._queue_to_inbox(report, task_id="unicode")

    assert (tmp_path / "unicode_ALERT.md").read_text(encoding="utf-8") == report + "\n"


def test_cmd_reply_flags_closed_discussion(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-closed"
    # Worker has finished discussing: terminal state + a resolution recorded.
    _write_task(tid, {"state": "early_stopped", "task_id": tid, "mode": "supervised",
                      "pid": 0, "worker_pid": 999999999,
                      "discussion_resolution": "deadline"})
    capsys.readouterr()
    args = types.SimpleNamespace(task_id=tid, message="late rationale", message_file=None)
    assert cmd_reply(args) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # The reply is still recorded for the audit trail...
    assert payload["state"] == "reply_recorded"
    assert "late rationale" in _render_discussion(tid)
    # ...but the engineer is told nobody will answer it.
    assert payload["live_supervisor"] is False
    assert payload["will_be_answered"] is False
    assert "deadline" in payload["note"]


def test_run_discussion_processes_preexisting_engineer_turn(monkeypatch, tmp_path) -> None:
    # A reply that lands before the discussion loop starts must still be answered
    # (baseline starts at 0, not the observed count).
    monkeypatch.chdir(tmp_path)
    tid = "train-pre"
    _write_task(tid, {"state": "early_stopped", "task_id": tid, "mode": "supervised",
                      "pid": 0, "worker_pid": __import__("os").getpid()})
    _append_discussion(tid, "engineer", "I replied before you parked")

    seen: dict[str, int] = {"calls": 0}

    def fake_discuss(task_id, task_data, model, cwd, thread_id=None):
        seen["calls"] += 1
        return (
            True,
            "Acknowledged, your pre-emptive rationale resolves it.",
            thread_id,
            (0, 0, 0, 0),
        )

    monkeypatch.setattr(
        "argus_skill.tools.subagent._discuss_run._supervisor_discuss_with_usage",
        fake_discuss,
    )
    monkeypatch.setattr("argus_skill.tools.subagent._discuss_run.DISCUSSION_POLL_INTERVAL", 0)
    from argus_skill.tools.subagent import _run_discussion
    _write_task(tid, {"state": "discussing", "task_id": tid})
    _run_discussion(tid, {"concern": "x", "command": "python t.py"}, "gpt-5.5", str(tmp_path))

    # The pre-existing engineer turn got exactly one supervisor answer.
    assert seen["calls"] == 1
    rendered = _render_discussion(tid)
    assert "pre-emptive rationale resolves it" in rendered


# ---------------------------------------------------------------------------
# Redesign: persistent thread, forced-discussion gate, experiment memory
# ---------------------------------------------------------------------------

def test_run_codex_resumes_thread_through_backend(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_turn(prompt, model, cwd, thread_id, run_label, mission_id=None):
        calls.update(
            prompt=prompt,
            model=model,
            cwd=cwd,
            thread_id=thread_id,
            run_label=run_label,
        )
        return RunnerResult(
            exit_code=0,
            agent_messages=['{"decision": "continue"}'],
            thread_id="TID-123",
        )

    monkeypatch.setattr(_sub._llm, "_run_backend_turn", fake_turn)
    msgs, tid = _sub._run_codex("PROMPT-BODY", "gpt-5.5", str(tmp_path), thread_id="TID-123")
    assert tid == "TID-123"
    assert msgs == ['{"decision": "continue"}']
    assert calls["prompt"] == "PROMPT-BODY"
    assert calls["thread_id"] == "TID-123"
    assert calls["run_label"] == "subagent"


def test_run_codex_retries_fresh_when_resume_empty(monkeypatch, tmp_path) -> None:
    # A resume that yields no agent message (expired session) retries once fresh.
    seq: list[str | None] = []

    def fake_turn(prompt, model, cwd, thread_id, run_label, mission_id=None):
        seq.append(thread_id)
        if thread_id:
            return RunnerResult(exit_code=1, thread_id=thread_id)
        return RunnerResult(
            exit_code=0,
            agent_messages=['{"decision": "continue"}'],
            thread_id="FRESH",
        )

    monkeypatch.setattr(_sub._llm, "_run_backend_turn", fake_turn)
    msgs, tid = _sub._run_codex("P", "gpt-5.5", str(tmp_path), thread_id="DEAD")
    assert seq == ["DEAD", None]
    assert msgs
    assert tid == "FRESH"


def test_backend_turn_uses_accounted_agent_backend(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    class _Backend:
        def set_usage_context(self, **kwargs) -> None:
            calls["usage_context"] = kwargs

        def run_exec(self, **kwargs) -> RunnerResult:
            calls["run_exec"] = kwargs
            return RunnerResult(exit_code=0, agent_messages=["ok"], thread_id="T")

    monkeypatch.setattr(_sub._llm, "_supervisor_backend", lambda: _Backend())
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))

    result = _ORIGINAL_RUN_BACKEND_TURN(
        "inspect metrics",
        "gpt-5.5",
        str(tmp_path),
        "OLD",
        "subagent:train-1:health",
        "train-1-run-42",
    )

    assert result.last_agent_message == "ok"
    usage_context = calls["usage_context"]
    assert isinstance(usage_context, dict)
    assert usage_context["mission_id"] == "train-1-run-42"
    assert Path(usage_context["project_root"]).parent == tmp_path / "argus-home" / "projects"
    run_call = calls["run_exec"]
    assert isinstance(run_call, dict)
    assert run_call["resume_thread_id"] == "OLD"
    assert run_call["run_label"] == "subagent:train-1:health"
    assert run_call["options"].reasoning_effort == "low"
    assert run_call["options"].working_dir == str(tmp_path)


def test_supervisor_backend_inherits_configured_runner(monkeypatch) -> None:
    constructed: dict[str, object] = {}

    class _Backend:
        def __init__(self, **kwargs) -> None:
            constructed.update(kwargs)

    monkeypatch.setattr(_sub._llm, "_SUPERVISOR_BACKENDS", {})
    monkeypatch.setattr(_sub._llm, "AgentCliBackend", _Backend)
    monkeypatch.setattr(
        _sub._llm,
        "resolve_role_backend",
        lambda role: "copilot" if role == "supervisor" else "codex",
    )
    monkeypatch.setattr(
        _sub._llm,
        "resolve_runner_bin_setting",
        lambda role: "/opt/copilot" if role == "supervisor" else "",
    )
    monkeypatch.setattr(
        _sub._llm,
        "resolve_available_runner",
        lambda backend, configured: (backend, configured),
    )

    backend = _sub._llm._supervisor_backend()

    assert isinstance(backend, _Backend)
    assert constructed["backend"] == "copilot"
    assert constructed["runner_bin"] == "/opt/copilot"


def test_run_supervised_persists_supervisor_usage_totals(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _skip_supervisor_summary(monkeypatch)

    class _Proc:
        def __init__(self) -> None:
            self.pid = 4321
            self.returncode = 0
            self._wait_calls = 0

        def wait(self, timeout=None):
            self._wait_calls += 1
            if self._wait_calls == 1:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return 0

    proc = _Proc()
    monkeypatch.setattr(_sub._registry.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(_sub._registry, "_child_env", lambda: {})
    monkeypatch.setattr(_sub._supervised_run, "_tail_file", lambda *a, **k: "")
    monkeypatch.setattr(_sub._supervised_run, "_alert_engineer", lambda *a, **k: "report")
    monkeypatch.setattr(_sub._supervised_run, "_persist_experiment_record", lambda *a, **k: None)
    monkeypatch.setattr(
        _sub._supervised_run,
        "_supervisor_check_with_usage",
        lambda *a, **k: _sub._supervised_run.SupervisorCheck(
            decision="continue",
            health="healthy",
            concern="",
            thread_id="sup-thread",
            usage=(120, 15, 30, 6),
            error=None,
        ),
    )

    _sub._run_supervised(
        "train-1",
        f"{shlex.quote(sys.executable)} -c pass",
        "demo",
        timeout=999,
        monitor_interval=1,
        model="gpt-5.5",
        cwd=str(tmp_path),
        preflight=False,
    )

    record = json.loads((tmp_path / ".argus_subagents" / "train-1.json").read_text())
    assert record["supervisor_usage_model"] == "gpt-5.5"
    assert record["supervisor_input_tokens"] == 120
    assert record["supervisor_cached_input_tokens"] == 15
    assert record["supervisor_output_tokens"] == 30
    assert record["supervisor_reasoning_output_tokens"] == 6


def test_open_discussion_blockers_use_process_identity_not_heartbeat_age(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    # Live + fresh -> blocks.
    _write_task("live", {"state": "discussing", "task_id": "live",
                         "worker_pid": me, "last_heartbeat": time.time()})
    # Dead pid -> ignored.
    _write_task("dead", {"state": "discussing", "task_id": "dead",
                         "worker_pid": 999999, "last_heartbeat": time.time()})
    # An old heartbeat during a long model turn does not override live identity.
    _write_task("stale", {"state": "discussing", "task_id": "stale",
                          "worker_pid": me, "last_heartbeat": time.time() - 99999})
    ids = {t["task_id"] for t in _sub._open_discussion_blockers()}
    assert ids == {"live", "stale"}


def _submit_args(**kw) -> argparse.Namespace:
    base = dict(task_id="x", description="d", command="echo hi", mode="direct",
                timeout=10, monitor_interval=120, model="gpt-5.5", run_dir=None,
                cwd=None, override_discussion=None, clear_stop=False,
                no_preflight=False, cpu_count=0, cpu_ids=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cmd_submit_rejects_new_oversized_task_id(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    if hasattr(_sub._cli.os, "fork"):
        monkeypatch.setattr(
            _sub._cli.os,
            "fork",
            lambda: (_ for _ in ()).throw(AssertionError("forked")),
        )
    else:
        monkeypatch.setattr(
            _sub._cli,
            "_spawn_windows_worker",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
        )

    rc = _sub.cmd_submit(_submit_args(task_id="x" * 121))
    output = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert "invalid task id" in output["error"]


@requires_fork
def test_cmd_submit_blocks_on_open_discussion(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    _write_task("blk", {"state": "discussing", "task_id": "blk", "worker_pid": me,
                        "last_heartbeat": time.time(), "concern": "truncation",
                        "run_dir": str(tmp_path / "runs/blk")})
    # Guard: fork must never be reached when blocked.
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("forked")))
    rc = _sub.cmd_submit(_submit_args(task_id="new"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["blocking_task"] == "blk"
    assert "truncation" in out["supervisor_concern"]


@requires_fork
def test_cmd_submit_rejects_same_id_while_discussion_worker_lives(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_task(
        "same",
        {
            "state": "discussing",
            "task_id": "same",
            "run_id": "same-old-run",
            "pid": 999999,
            "worker_pid": __import__("os").getpid(),
            "last_heartbeat": time.time(),
        },
    )
    monkeypatch.setattr(
        _sub._cli.os,
        "fork",
        lambda: (_ for _ in ()).throw(AssertionError("forked")),
    )

    rc = _sub.cmd_submit(
        _submit_args(
            task_id="same",
            override_discussion="launch a replacement anyway",
        )
    )

    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "already discussing" in out["error"]


@requires_fork
def test_cmd_submit_rejects_terminal_record_while_report_worker_lives(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_task(
        "reporting",
        {
            "state": "done",
            "task_id": "reporting",
            "run_id": "reporting-old-run",
            "pid": 999999,
            "worker_pid": __import__("os").getpid(),
            "completed_at": time.time(),
        },
    )
    monkeypatch.setattr(
        _sub._cli.os,
        "fork",
        lambda: (_ for _ in ()).throw(AssertionError("forked")),
    )

    rc = _sub.cmd_submit(_submit_args(task_id="reporting"))

    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "already done" in out["error"]


@requires_fork
def test_cmd_submit_override_records_and_proceeds(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    _write_task("blk", {"state": "discussing", "task_id": "blk", "worker_pid": me,
                        "last_heartbeat": time.time(), "concern": "truncation"})
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: 4242)  # pretend parent
    rc = _sub.cmd_submit(_submit_args(task_id="new", override_discussion="I checked, proceed"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["state"] == "submitted"
    assert shlex.split(out["check_with"]) == [
        sys.executable,
        "-m",
        "argus_skill.tools.subagent",
        "status",
        "--task-id",
        "new",
    ]
    ledger = (tmp_path / _sub.EXPERIMENT_HISTORY_REL).read_text()
    assert "DISCUSSION-OVERRIDE" in ledger
    assert "I checked, proceed" in ledger


@requires_fork
def test_cmd_submit_refuses_poisoned_stop(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rd = tmp_path / "runs" / "r1"
    rd.mkdir(parents=True)
    (rd / "STOP").write_text("old")
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("forked")))
    rc = _sub.cmd_submit(_submit_args(task_id="r", run_dir=str(rd)))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and "STOP" in out["error"]
    # --clear-stop removes it and proceeds.
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: 4242)
    rc = _sub.cmd_submit(_submit_args(task_id="r", run_dir=str(rd), clear_stop=True))
    assert rc == 0
    assert not (rd / "STOP").exists()


@requires_fork
def test_cmd_submit_rejects_insufficient_cpus_before_artifacts(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "runs" / "cpu-heavy"
    monkeypatch.setattr(
        _sub._cpu_admission, "available_cpu_ids", lambda: (0, 1, 2, 3)
    )
    monkeypatch.setattr(
        _sub._cli.os,
        "fork",
        lambda: (_ for _ in ()).throw(AssertionError("forked")),
    )

    rc = _sub.cmd_submit(
        _submit_args(task_id="cpu-heavy", run_dir=str(run_dir), cpu_count=8)
    )
    out = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert "need 8 distinct CPUs, only 4 are free" in out["error"]
    assert not _sub._registry_path("cpu-heavy").exists()
    assert not (tmp_path / ".argus_subagents").exists()
    assert not run_dir.exists()


@requires_fork
def test_cmd_submit_rejects_cpu_conflict_before_new_task_record(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        _sub._cpu_admission, "available_cpu_ids", lambda: (0, 1, 2, 3)
    )
    _write_task(
        "existing",
        {
            "state": "running",
            "task_id": "existing",
            "pid": __import__("os").getpid(),
            "cpu_ids": [0, 1],
            "cpu_count": 2,
        },
    )
    monkeypatch.setattr(
        _sub._cli.os,
        "fork",
        lambda: (_ for _ in ()).throw(AssertionError("forked")),
    )

    rc = _sub.cmd_submit(_submit_args(task_id="new", cpu_ids="1,2"))
    out = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert "already leased" in out["error"]
    assert not _sub._registry_path("new").exists()


@requires_fork
def test_cmd_submit_reserves_disjoint_cpus_before_fork(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        _sub._cpu_admission, "available_cpu_ids", lambda: (0, 1, 2, 3)
    )
    _write_task(
        "existing",
        {
            "state": "running",
            "task_id": "existing",
            "pid": __import__("os").getpid(),
            "cpu_ids": [0],
            "cpu_count": 1,
        },
    )
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: 4242)

    rc = _sub.cmd_submit(_submit_args(task_id="new", cpu_count=2))
    out = json.loads(capsys.readouterr().out)
    record = _sub._read_task("new")

    assert rc == 0
    assert out["cpu_ids"] == [1, 2]
    assert record is not None
    assert record["cpu_ids"] == [1, 2]
    assert record["cpu_count"] == 2


class _OSNameProxy:
    """Override one module's platform branch without mutating global os.name."""

    def __init__(self, wrapped, name: str) -> None:
        self._wrapped = wrapped
        self.name = name

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)


def test_cmd_submit_spawns_windows_worker(monkeypatch, tmp_path, capsys) -> None:
    from argus_skill.tools.subagent import _cli

    monkeypatch.chdir(tmp_path)
    workload = tmp_path / "workload"
    workload.mkdir()
    spawned: list[dict] = []

    class _Worker:
        pid = 9876

    def fake_spawn_windows_worker(**kwargs):
        spawned.append(kwargs)
        return _Worker()

    monkeypatch.setattr(_cli, "os", _OSNameProxy(os, "nt"))
    monkeypatch.setattr(_cli, "_spawn_windows_worker", fake_spawn_windows_worker)

    rc = _sub.cmd_submit(
        _submit_args(
            task_id="win-task",
            command="Write-Output hi",
            cwd=str(workload),
        )
    )
    out = json.loads(capsys.readouterr().out)
    record = _sub._read_task("win-task")

    assert rc == 0
    assert out["state"] == "submitted"
    assert out["pid"] == 9876
    assert spawned[0]["command"] == "Write-Output hi"
    assert spawned[0]["cwd"] == str(workload)
    assert spawned[0]["registry_cwd"] == str(tmp_path)
    assert record is not None
    assert record["state"] == "starting"
    assert record["cwd"] == str(workload)
    assert record["worker_pid"] == 9876


@requires_fork
def test_cmd_submit_normalizes_relative_cwd_and_run_dir(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: 4242)

    rc = _sub.cmd_submit(
        _submit_args(
            task_id="relative-paths",
            cwd="project",
            run_dir="experiments/run-1",
        )
    )
    record = _sub._read_task("relative-paths")
    capsys.readouterr()

    assert rc == 0
    assert record is not None
    assert record["cwd"] == str(project)
    assert record["run_dir"] == str(project / "experiments" / "run-1")


@requires_fork
def test_experiment_submit_without_timeout_records_no_timeout(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_sub._cli.os, "fork", lambda: 4242)

    rc = _sub.cmd_submit(_submit_args(
        task_id="default-timeout",
        run_dir="experiments/run-1",
        timeout=None,
    ))
    output = json.loads(capsys.readouterr().out)
    record = _sub._read_task("default-timeout")

    assert rc == 0
    assert record is not None
    assert record["timeout_seconds"] is None
    assert record["timeout_defaulted"] is False
    assert output["timeout_seconds"] is None
    assert output["timeout_defaulted"] is False
    assert "timeout_notice" not in output
    assert record["worker_process_identity"]["pid"] == 4242


def test_submit_without_timeout_survives_past_old_default(
    monkeypatch,
    tmp_path,
) -> None:
    from argus_skill.tools.subagent import _direct_run

    monkeypatch.chdir(tmp_path)
    clock = [100.0]
    _write_task("timed", {
        "state": "starting",
        "task_id": "timed",
        "run_id": "timed-run-1",
        "timeout_seconds": None,
        "timeout_defaulted": False,
    })

    class _Proc:
        pid = os.getpid()
        returncode = None

        def wait(self, timeout=None):
            assert timeout is None
            clock[0] += 7201
            self.returncode = 0

    proc = _Proc()
    writes: list[dict] = []
    alerts: list[str] = []
    real_write = _sub._registry._write_task

    def record_write(task_id, data):
        writes.append(dict(data))
        real_write(task_id, data)

    monkeypatch.setattr(
        _direct_run,
        "experiment_launch_preflight",
        lambda **_kwargs: (False, ""),
    )
    monkeypatch.setattr(
        _direct_run,
        "release_experiment_launch_claim",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(_direct_run, "_launch_durable_command", lambda **_kwargs: proc)
    monkeypatch.setattr(_direct_run, "acquire_for_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(_direct_run.time, "time", lambda: clock[0])
    monkeypatch.setattr(_direct_run, "_write_task", record_write)
    monkeypatch.setattr(
        _direct_run,
        "_alert_engineer",
        lambda task_id, event, data: alerts.append(
            event
        ),
    )

    _direct_run._run_direct(
        "timed",
        "python train.py",
        "long experiment",
        timeout=None,
        cwd=str(tmp_path),
        run_dir=str(tmp_path / "run"),
    )

    record = _sub._read_task("timed")
    running = next(row for row in writes if row.get("state") == "running")
    assert running["timeout_seconds"] is None
    assert running["timeout_defaulted"] is False
    if os.name == "posix":
        assert "start_time_ticks" in running["process_identity"]
    assert record is not None and record["state"] == "done"
    assert record["elapsed_seconds"] == 7201.0
    assert alerts == ["COMPLETED"]


def test_launch_durable_command_uses_native_powershell_on_windows(
    monkeypatch,
    tmp_path,
) -> None:
    from argus_skill.tools.subagent import _registry

    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    class _Proc:
        pid = 4242

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(_registry, "os", _OSNameProxy(os, "nt"))
    monkeypatch.setattr(_registry.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_registry, "_child_env", lambda: {})

    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    with stdout.open("w") as out, stderr.open("w") as err:
        proc = _registry._launch_durable_command(
            task_id="team::windows",
            run_id="run-1",
            command="Write-Output ok",
            cwd=str(tmp_path),
            stdout=out,
            stderr=err,
        )

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert proc.pid == 4242
    assert isinstance(args, list)
    assert args[0] == "powershell.exe"
    assert "bash" not in args
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(tmp_path)
    env = kwargs["env"]
    assert env["ARGUS_DURABLE_COMMAND"] == "Write-Output ok"
    exit_path = _registry._exit_status_path("team::windows", "run-1").resolve()
    assert env["ARGUS_DURABLE_TMP"] == str(
        exit_path.with_name("exit_code.run-1.tmp")
    )
    assert env["ARGUS_DURABLE_EXIT"] == str(exit_path)
    assert _registry._exit_status_path("team::windows", "run-1").parent.exists()


def test_terminate_proc_uses_windows_tree_before_root_fallback(monkeypatch) -> None:
    from argus_skill.tools.subagent import _direct_run

    calls: list[int] = []

    class _Proc:
        pid = 6161
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 1
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            raise AssertionError("tree termination succeeded; kill is unnecessary")

    proc = _Proc()

    def terminate_tree(pid: int, *, identity_check) -> bool:
        assert identity_check() is True
        calls.append(pid)
        proc.returncode = 1
        return True

    monkeypatch.setattr(_direct_run, "os", _OSNameProxy(os, "nt"))
    monkeypatch.setattr(_direct_run, "terminate_windows_process_tree", terminate_tree)

    _direct_run._terminate_proc(proc)

    assert calls == [6161]
    assert proc.terminated is False


def test_persist_experiment_record_writes_artifacts_and_dedups(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "exp1"
    rd = tmp_path / "runs" / "exp1"
    rd.mkdir(parents=True)
    _append_discussion(tid, "supervisor", "stopped: truncation")
    td = {"run_id": "exp1-1", "task_id": tid, "state": "early_stopped",
          "command": "python train.py", "run_dir": str(rd),
          "concern": "truncation", "discussion_resolution": "resolved"}
    _sub._persist_experiment_record(tid, "EARLY-STOPPED", td, str(tmp_path),
                                    verdict_text="Raise the cap and re-test.")
    assert (rd / "SUPERVISOR_VERDICT.md").exists()
    assert "Raise the cap" in (rd / "SUPERVISOR_VERDICT.md").read_text()
    assert (rd / "DISCUSSION.md").exists()
    ledger = tmp_path / _sub.EXPERIMENT_HISTORY_REL
    assert ledger.exists()
    assert len(ledger.read_text().strip().splitlines()) == 1
    # Idempotent on run_id.
    _sub._persist_experiment_record(tid, "EARLY-STOPPED", td, str(tmp_path))
    assert len(ledger.read_text().strip().splitlines()) == 1


def test_persist_experiment_record_keeps_terminal_state_on_ledger_error(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / _sub.EXPERIMENT_HISTORY_REL
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"run_id":"torn"')
    task = {
        "run_id": "run-1",
        "task_id": "task-1",
        "state": "done",
        "exit_code": 0,
        "command": "python train.py",
    }

    _sub._persist_experiment_record(
        "task-1",
        "COMPLETED",
        task,
        str(tmp_path),
    )

    persisted = _sub._read_task("task-1")
    assert persisted is not None
    assert persisted["state"] == "done"
    assert persisted["exit_code"] == 0
    assert "torn final row" in persisted["evidence_persistence_error"]
    assert "terminal result preserved" in capsys.readouterr().err


def test_cmd_status_surfaces_open_discussion(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    _write_task("d", {"state": "discussing", "task_id": "d", "worker_pid": me,
                      "pid": me, "last_heartbeat": time.time(),
                      "run_dir": str(tmp_path / "runs/d")})
    rc = _sub.cmd_status(argparse.Namespace(task_id="d"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "ACTION_REQUIRED" in out
    assert "DISCUSSION.md" in out["discussion_file"]
    assert shlex.split(out["reply_with"]) == [
        sys.executable,
        "-m",
        "argus_skill.tools.subagent",
        "reply",
        "--task-id",
        "d",
        "--message",
        "<your rationale>",
    ]


def test_supervisor_check_prompt_demands_parameter_level_concern(monkeypatch, tmp_path) -> None:
    # A stop must point the engineer at a specific flag/value to change, not just
    # name the symptom — the supervisor reads the launch command's hyperparameters.
    monkeypatch.chdir(tmp_path)
    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl('{"decision": "continue", "health": "healthy", "concern": ""}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    out = tmp_path / "stdout.log"
    err = tmp_path / "stderr.log"
    out.write_text("step 1\n")
    err.write_text("")
    _supervisor_check(
        "t", "python train.py --num-generations 2 --learning-rate 1e-5",
        "run", out, err, 60.0, 1, "gpt-5.5", str(tmp_path),
    )
    prompt = captured["prompt"]
    assert "hyperparameter engineer" in prompt
    assert "suggested change" in prompt
    # The concern field itself must ask for the specific flag/value to change.
    assert "specific launch-command flag/value" in prompt


def test_reply_back_block_demands_concrete_fix_not_bare_agreement() -> None:
    block = _reply_back_block("train-x", "EARLY-STOPPED")
    # Asserted contract preserved.
    assert "subagent reply" in block
    assert "--task-id train-x" in block
    assert "discussion" in block.lower()
    # The engineer must diagnose + name a concrete change, not just agree on failure.
    assert "failure" in block.lower()
    assert "root cause" in block.lower()
    assert "hyperparameter" in block.lower()


def test_supervisor_discuss_prompt_requires_concrete_fix_resolution(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-fix"
    _append_discussion(tid, "engineer", "agree, the criterion was not met")
    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl('{"resolved": false, "message": "name the parameter change first"}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    _supervisor_discuss(
        tid,
        {"description": "GRPO run", "command": "python train.py --num-generations 2",
         "concern": "reward collapse"},
        "gpt-5.5", str(tmp_path),
    )
    prompt = captured["prompt"]
    assert "CONCRETE fix" in prompt
    assert "criterion was not met" in prompt.lower()
    # It should reason in terms of the actual hyperparameters.
    assert "hyperparameters in the Command" in prompt


# --- Pre-launch RL config preflight -----------------------------------------

def test_rl_training_gate_matches_rl_launches_only() -> None:
    from argus_skill.tools.subagent import _looks_like_rl_training

    assert _looks_like_rl_training(
        ".venv/bin/python code/train_rl_lora_adapter.py --num-generations 2")
    assert _looks_like_rl_training("python t.py --method MGR_RLVR --rollouts 8")
    assert _looks_like_rl_training("python grpo_train.py")
    # Non-RL commands must not pay the preflight cost.
    assert not _looks_like_rl_training("python code/eval.py --benchmark geneval")
    assert not _looks_like_rl_training("python sft_train.py --epochs 3")
    assert not _looks_like_rl_training("")


def test_parse_launch_flags_normalizes_space_and_equals_forms() -> None:
    from argus_skill.tools.subagent import _parse_launch_flags

    flags = _parse_launch_flags(
        "python x.py --num-generations 2 --max-completion-length=256 "
        "--load-in-4bit --method MGR_RLVR")
    assert flags["num_generations"] == "2"
    assert flags["max_completion_length"] == "256"
    # A bare boolean flag becomes "true".
    assert flags["load_in_4bit"] == "true"
    assert flags["method"] == "MGR_RLVR"
    # Malformed input never raises.
    assert _parse_launch_flags("python 'unterminated") == {}


def test_preflight_prompt_hard_blocks_only_mechanical_degeneracy(monkeypatch, tmp_path) -> None:
    # The preflight must instruct the model to block ONLY mechanically-unlearnable
    # configs (e.g. GRPO group<=1) and explicitly NOT block a maybe-short
    # max_completion_length, which is data-dependent and left to the in-flight check.
    from argus_skill.tools import subagent as sub

    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "") or cmd[-1]
        r = _Result()
        r.stdout = _codex_jsonl('{"reject": false, "reason": "ok", "concern": ""}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    sub._supervisor_preflight(
        "t", "python train.py --num-generations 1", "GRPO smoke", "gpt-5.5", str(tmp_path))
    prompt = captured["prompt"]
    assert "MECHANICALLY UNLEARNABLE" in prompt
    assert "num_generations" in prompt
    # Untrusted-input defense and the don't-block-on-length carve-out.
    assert "UNTRUSTED" in prompt
    assert "max_completion_length" in prompt


def test_preflight_rejects_degenerate_group_with_actionable_fix(monkeypatch, tmp_path) -> None:
    from argus_skill.tools import subagent as sub

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        r = _Result()
        r.stdout = _codex_jsonl(
            '{"reject": true, "reason": "GRPO group of 1 has zero advantage",'
            ' "concern": "num_generations=1 -> 8 because a GRPO group of 1 has'
            ' identically zero advantage"}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    reject, concern = sub._supervisor_preflight(
        "t", "python train.py --num-generations 1 --method GRPO", "smoke",
        "gpt-5.5", str(tmp_path))
    assert reject is True
    assert "num_generations" in concern


def test_preflight_reject_without_actionable_fix_is_noop(monkeypatch, tmp_path) -> None:
    # A vague reject with no concrete fix must NOT wedge a launch.
    from argus_skill.tools import subagent as sub

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        r = _Result()
        r.stdout = _codex_jsonl('{"reject": true, "reason": "bad", "concern": ""}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    reject, concern = sub._supervisor_preflight(
        "t", "python train.py --num-generations 1", "smoke", "gpt-5.5", str(tmp_path))
    assert reject is False
    assert concern == ""


def test_preflight_fails_soft_on_unparseable_verdict(monkeypatch, tmp_path) -> None:
    from argus_skill.tools import subagent as sub

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        r = _Result()
        r.stdout = _codex_jsonl("the config looks fine to me, no JSON here")
        return r

    _install_fake_codex(monkeypatch, fake_run)
    reject, concern = sub._supervisor_preflight(
        "t", "python train.py --num-generations 1", "smoke", "gpt-5.5", str(tmp_path))
    assert reject is False
    assert concern == ""


def test_preflight_discussion_opening_signals_pre_launch_block(monkeypatch, tmp_path) -> None:
    # The engineer-facing opening must make clear nothing launched (pre-launch
    # block) and demand a concrete parameter fix, not mere agreement.
    monkeypatch.chdir(tmp_path)
    tid = "pf-task"
    _write_task(tid, {"state": "discussing", "task_id": tid, "mode": "supervised",
                      "pid": 0, "worker_pid": __import__("os").getpid()})

    def fake_discuss(task_id, task_data, model, cwd, thread_id=None):
        return (True, "I'll set num_generations=8.", thread_id, (0, 0, 0, 0))

    monkeypatch.setattr("argus_skill.tools.subagent._discuss_run._supervisor_discuss_with_usage", fake_discuss)
    monkeypatch.setattr("argus_skill.tools.subagent._discuss_run.DISCUSSION_POLL_INTERVAL", 0)
    from argus_skill.tools.subagent import _run_discussion
    td = {
        "preflight": True,
        "concern": "num_generations=1 -> 8 because a GRPO group of 1 has zero advantage",
        "command": "python train.py --num-generations 1",
    }
    _append_discussion(tid, "engineer", "Why blocked? I'll fix it.")
    _write_task(tid, {"state": "discussing", "task_id": tid})
    _run_discussion(tid, td, "gpt-5.5", str(tmp_path))
    rendered = _render_discussion(tid)
    assert "BEFORE launch" in rendered
    # The non-preflight wording ("I stopped this run") must NOT be used.
    assert "I stopped this run" not in rendered


def test_nonpreflight_discussion_opening_uses_stopped_wording(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "rt-task"
    _write_task(tid, {"state": "discussing", "task_id": tid, "mode": "supervised",
                      "pid": 0, "worker_pid": __import__("os").getpid()})

    def fake_discuss(task_id, task_data, model, cwd, thread_id=None):
        return (True, "ack", thread_id, (0, 0, 0, 0))

    monkeypatch.setattr("argus_skill.tools.subagent._discuss_run._supervisor_discuss_with_usage", fake_discuss)
    monkeypatch.setattr("argus_skill.tools.subagent._discuss_run.DISCUSSION_POLL_INTERVAL", 0)
    from argus_skill.tools.subagent import _run_discussion
    _append_discussion(tid, "engineer", "ack, fixing")
    _write_task(tid, {"state": "discussing", "task_id": tid})
    _run_discussion(tid, {"concern": "x", "command": "python t.py"}, "gpt-5.5", str(tmp_path))
    rendered = _render_discussion(tid)
    assert "I stopped this run" in rendered
    assert "BEFORE launch" not in rendered


def test_superseded_discussion_cannot_overwrite_reused_task_id(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_task(
        "reused-discussion",
        {
            "task_id": "reused-discussion",
            "run_id": "new-run",
            "state": "running",
        },
    )
    monkeypatch.setattr(
        "argus_skill.tools.subagent._discuss_run.DISCUSSION_POLL_INTERVAL",
        0,
    )

    _sub._run_discussion(
        "reused-discussion",
        {
            "task_id": "reused-discussion",
            "run_id": "old-run",
            "concern": "old concern",
        },
        "gpt-5.5",
        str(tmp_path),
    )

    saved = json.loads(
        (tmp_path / ".argus_subagents" / "reused-discussion.json").read_text()
    )
    assert saved["run_id"] == "new-run"
    assert saved["state"] == "running"


def test_preflight_strict_bool_reject_fails_soft(monkeypatch, tmp_path) -> None:
    # A non-bool "reject" (string "false", 1, etc.) is an LLM formatting hiccup
    # and must NEVER hard-block a launch.
    from argus_skill.tools import subagent as sub

    class _Result:
        stdout = ""

    def make_run(payload):
        def fake_run(cmd, **kwargs):
            r = _Result()
            r.stdout = _codex_jsonl(payload)
            return r
        return fake_run

    for payload in (
        '{"reject": "true", "concern": "num_generations=1 -> 8"}',
        '{"reject": 1, "concern": "num_generations=1 -> 8"}',
    ):
        _install_fake_codex(monkeypatch, make_run(payload))
        reject, concern = sub._supervisor_preflight(
            "t", "python train.py --num-generations 1", "smoke", "gpt-5.5", str(tmp_path))
        assert reject is False, payload
        assert concern == ""


def test_preflight_reject_without_flagref_concern_is_noop(monkeypatch, tmp_path) -> None:
    # A non-empty but non-actionable concern (no flag/value reference) must not
    # wedge a launch — the contract requires a concrete flag+value to change.
    from argus_skill.tools import subagent as sub

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        r = _Result()
        r.stdout = _codex_jsonl(
            '{"reject": true, "reason": "bad", "concern": "this config is hopeless"}')
        return r

    _install_fake_codex(monkeypatch, fake_run)
    reject, concern = sub._supervisor_preflight(
        "t", "python train.py --num-generations 1", "smoke", "gpt-5.5", str(tmp_path))
    assert reject is False
    assert concern == ""


def test_preflight_reject_td_omits_run_dir_and_reads_no_stale_metrics(tmp_path) -> None:
    # A preflight reject never launched: _effective_run_dir must NOT recover a
    # run dir from the command's --run-dir (which could hold a prior run's
    # metrics), preserving the no-phantom-run invariant.
    from argus_skill.tools.subagent import _effective_run_dir

    td = {
        "preflight": True,
        "command": "python train.py --num-generations 1 --run-dir /tmp/old_run",
    }
    assert _effective_run_dir(td) is None
    # A real launched task still recovers its run dir from the command.
    td2 = {"command": "python train.py --run-dir /tmp/live_run"}
    assert _effective_run_dir(td2) == "/tmp/live_run"
