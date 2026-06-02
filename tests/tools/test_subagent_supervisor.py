from __future__ import annotations

import json
import time
import types

from argus_skill.tools.subagent import (
    SUPERVISOR_INTERVAL_CAP,
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

    monkeypatch.setattr("argus_skill.tools.subagent._find_codex", lambda: "codex")
    monkeypatch.setattr("argus_skill.tools.subagent.subprocess.run", fake_run)
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

    monkeypatch.setattr("argus_skill.tools.subagent._find_codex", lambda: "codex")
    monkeypatch.setattr("argus_skill.tools.subagent.subprocess.run", fake_run)
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


def test_build_report_surfaces_concern_for_stopped_task() -> None:
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


def test_supervisor_authors_report_grounded_in_diagnosis(monkeypatch) -> None:
    # The summary + next step must be authored from the supervisor's own
    # diagnosis, not a signal-blind summarizer that only sees stdout.
    from argus_skill.tools import subagent as sub

    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = cmd[-1]
        r = _Result()
        r.stdout = _codex_jsonl(
            "I stopped B2 because completions were truncated, not formatted. "
            "Next step: raise max_completion_length and re-check termination."
        )
        return r

    monkeypatch.setattr(sub, "_find_codex", lambda: "codex")
    monkeypatch.setattr(sub.subprocess, "run", fake_run)

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


def test_build_report_early_stop_tells_engineer_to_reply() -> None:
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

    monkeypatch.setattr("argus_skill.tools.subagent._find_codex", lambda: "codex")
    monkeypatch.setattr("argus_skill.tools.subagent.subprocess.run", fake_run)

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

    monkeypatch.setattr("argus_skill.tools.subagent._find_codex", lambda: "codex")
    monkeypatch.setattr("argus_skill.tools.subagent.subprocess.run", fake_run)
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
        return (True, "Acknowledged, your pre-emptive rationale resolves it.", thread_id)

    monkeypatch.setattr("argus_skill.tools.subagent._supervisor_discuss", fake_discuss)
    monkeypatch.setattr("argus_skill.tools.subagent.DISCUSSION_POLL_INTERVAL", 0)
    from argus_skill.tools.subagent import _run_discussion
    _run_discussion(tid, {"concern": "x", "command": "python t.py"}, "gpt-5.5", str(tmp_path))

    # The pre-existing engineer turn got exactly one supervisor answer.
    assert seen["calls"] == 1
    rendered = _render_discussion(tid)
    assert "pre-emptive rationale resolves it" in rendered


# ---------------------------------------------------------------------------
# Redesign: persistent thread, forced-discussion gate, experiment memory
# ---------------------------------------------------------------------------

import argparse  # noqa: E402

from argus_skill.tools import subagent as _sub  # noqa: E402


def test_run_codex_resumes_thread_and_streams_prompt(monkeypatch, tmp_path) -> None:
    # A persistent supervisor turn must `exec resume <thread_id>` and stream the
    # prompt via stdin (never positionally), and surface the thread id back.
    calls: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["input"] = kwargs.get("input")
        r = _Result()
        r.stdout = (
            json.dumps({"type": "thread.started", "thread_id": "TID-123"}) + "\n"
            + _codex_jsonl('{"decision": "continue"}')
        )
        return r

    monkeypatch.setattr(_sub, "_find_codex", lambda: "codex")
    monkeypatch.setattr(_sub.subprocess, "run", fake_run)
    msgs, tid = _sub._run_codex("PROMPT-BODY", "gpt-5.5", str(tmp_path), thread_id="TID-123")
    assert tid == "TID-123"
    assert calls["input"] == "PROMPT-BODY"
    assert "resume" in calls["cmd"]
    assert "TID-123" in calls["cmd"]
    assert calls["cmd"][-1] == "-"  # prompt streamed via stdin
    assert "PROMPT-BODY" not in calls["cmd"]


def test_run_codex_retries_fresh_when_resume_empty(monkeypatch, tmp_path) -> None:
    # A resume that yields no agent message (expired session) retries once fresh.
    seq = []

    class _Result:
        returncode = 1
        stdout = ""

    def fake_run(cmd, **kwargs):
        seq.append("resume" in cmd)
        r = _Result()
        if "resume" in cmd:
            r.stdout = ""  # session gone, nothing back
        else:
            r.returncode = 0
            r.stdout = _codex_jsonl('{"decision": "continue"}')
        return r

    monkeypatch.setattr(_sub, "_find_codex", lambda: "codex")
    monkeypatch.setattr(_sub.subprocess, "run", fake_run)
    msgs, tid = _sub._run_codex("P", "gpt-5.5", str(tmp_path), thread_id="DEAD")
    assert seq == [True, False]  # resumed, then retried fresh
    assert msgs  # got a message from the fresh run


def test_open_discussion_blockers_only_counts_live_fresh(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    # Live + fresh -> blocks.
    _write_task("live", {"state": "discussing", "task_id": "live",
                         "worker_pid": me, "last_heartbeat": time.time()})
    # Dead pid -> ignored.
    _write_task("dead", {"state": "discussing", "task_id": "dead",
                         "worker_pid": 999999, "last_heartbeat": time.time()})
    # Stale heartbeat -> ignored.
    _write_task("stale", {"state": "discussing", "task_id": "stale",
                          "worker_pid": me, "last_heartbeat": time.time() - 99999})
    ids = {t["task_id"] for t in _sub._open_discussion_blockers()}
    assert ids == {"live"}


def _submit_args(**kw) -> argparse.Namespace:
    base = dict(task_id="x", description="d", command="echo hi", mode="direct",
                timeout=10, monitor_interval=120, model="gpt-5.5", run_dir=None,
                cwd=None, override_discussion=None, clear_stop=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cmd_submit_blocks_on_open_discussion(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    _write_task("blk", {"state": "discussing", "task_id": "blk", "worker_pid": me,
                        "last_heartbeat": time.time(), "concern": "truncation",
                        "run_dir": str(tmp_path / "runs/blk")})
    # Guard: fork must never be reached when blocked.
    monkeypatch.setattr(_sub.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("forked")))
    rc = _sub.cmd_submit(_submit_args(task_id="new"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["blocking_task"] == "blk"
    assert "truncation" in out["supervisor_concern"]


def test_cmd_submit_override_records_and_proceeds(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    me = __import__("os").getpid()
    _write_task("blk", {"state": "discussing", "task_id": "blk", "worker_pid": me,
                        "last_heartbeat": time.time(), "concern": "truncation"})
    monkeypatch.setattr(_sub.os, "fork", lambda: 4242)  # pretend parent
    rc = _sub.cmd_submit(_submit_args(task_id="new", override_discussion="I checked, proceed"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["state"] == "submitted"
    ledger = (tmp_path / _sub.EXPERIMENT_HISTORY_REL).read_text()
    assert "DISCUSSION-OVERRIDE" in ledger
    assert "I checked, proceed" in ledger


def test_cmd_submit_refuses_poisoned_stop(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rd = tmp_path / "runs" / "r1"
    rd.mkdir(parents=True)
    (rd / "STOP").write_text("old")
    monkeypatch.setattr(_sub.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("forked")))
    rc = _sub.cmd_submit(_submit_args(task_id="r", run_dir=str(rd)))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and "STOP" in out["error"]
    # --clear-stop removes it and proceeds.
    monkeypatch.setattr(_sub.os, "fork", lambda: 4242)
    rc = _sub.cmd_submit(_submit_args(task_id="r", run_dir=str(rd), clear_stop=True))
    assert rc == 0
    assert not (rd / "STOP").exists()


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
    assert "reply --task-id d" in out["reply_with"]


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

    monkeypatch.setattr("argus_skill.tools.subagent._find_codex", lambda: "codex")
    monkeypatch.setattr("argus_skill.tools.subagent.subprocess.run", fake_run)
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
    # New: the engineer must diagnose + name a concrete change, not just agree no-go.
    assert "no-go" in block.lower()
    assert "root cause" in block.lower()
    assert "hyperparameter" in block.lower()


def test_supervisor_discuss_prompt_requires_concrete_fix_resolution(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    tid = "train-fix"
    _append_discussion(tid, "engineer", "agree, it's no-go")
    captured: dict[str, str] = {}

    class _Result:
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["prompt"] = kwargs.get("input", "")
        r = _Result()
        r.stdout = _codex_jsonl('{"resolved": false, "message": "name the parameter change first"}')
        return r

    monkeypatch.setattr("argus_skill.tools.subagent._find_codex", lambda: "codex")
    monkeypatch.setattr("argus_skill.tools.subagent.subprocess.run", fake_run)
    _supervisor_discuss(
        tid,
        {"description": "GRPO run", "command": "python train.py --num-generations 2",
         "concern": "reward collapse"},
        "gpt-5.5", str(tmp_path),
    )
    prompt = captured["prompt"]
    assert "CONCRETE fix" in prompt
    assert "no-go" in prompt.lower()
    # It should reason in terms of the actual hyperparameters.
    assert "hyperparameters in the Command" in prompt
