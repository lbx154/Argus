"""SupervisedEngineer × FailedToolLedger integration.

Verifies that when the ledger has pending nudges (≥ threshold failures
for some tool that haven't yet been surfaced), the next round's
engineer prompt is prepended with the advisory block and an
``engineer.failure_nudge`` event is emitted exactly once.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from argus_skill.core.models import CheckResult, ReviewDecision, RunnerResult
from argus_skill.engineer.failed_tool_ledger import FailedToolLedger
from argus_skill.engineer.reviewer import ReviewerConfig
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
    _EffectiveProgressWatchdog,
    _is_effective_codex_session_line,
    fatal_error_looks_like_backend_failure,
    fatal_error_looks_like_recoverable_reconnect,
    should_clear_thread_id_after_outcome,
)


class _RecordingEngineer:
    """Captures the prompt of every round; emits empty assistant text."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run_exec(self, **kwargs):  # noqa: D401 — match RunnerBackend
        self.prompts.append(kwargs.get("prompt", ""))
        return RunnerResult(
            exit_code=0,
            agent_messages=["ok"],
            fatal_error=None,
        )


class _ExplodingEngineer:
    def run_exec(self, **kwargs):  # noqa: D401, ANN001
        raise RuntimeError("codex subprocess disappeared")


class _ScriptedEngineer:
    def __init__(self, results: list[RunnerResult]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def run_exec(self, **kwargs):  # noqa: D401, ANN001
        self.calls.append(kwargs)
        return self.results.pop(0)


class _ContinueReviewer:
    """Always says continue so we get every requested round."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs):
        from argus_skill.core.models import ReviewDecision
        self.calls.append(kwargs)
        return ReviewDecision(
            status="continue",
            confidence=0.4,
            reason="more work",
            next_action="keep going",
            round_summary_markdown="",
            completion_summary_markdown="",
        )


class _DoneReviewer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return ReviewDecision(
            status="done",
            confidence=0.95,
            reason="complete",
            next_action="",
            round_summary_markdown="done",
            completion_summary_markdown="complete",
        )


class _HandoffReviewer:
    """Distills failed validation into the engineer-facing next_action."""

    def __init__(self) -> None:
        self.seen_checks: list[CheckResult] = []

    def evaluate(self, **kwargs):  # noqa: ANN003
        self.seen_checks = list(kwargs["checks"])
        return ReviewDecision(
            status="continue",
            confidence=0.7,
            reason="validation failed",
            next_action=(
                "Professional handoff: Figure 1 must include the image-2 raster "
                "`paper/figures/method.png` in `paper/main.tex`; remove the "
                "conceptual PDF substitute and rerun validate-full-emnlp."
            ),
            round_summary_markdown="",
            completion_summary_markdown="",
        )


def _make_supervised(rec: _RecordingEngineer) -> SupervisedEngineer:
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = rec
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = _ContinueReviewer()
    se.reviewer_config = ReviewerConfig(model="stub")
    return cast(SupervisedEngineer, se)


def test_advisory_injected_into_round_prompt_when_ledger_has_pending(tmp_path: Path) -> None:
    rec = _RecordingEngineer()
    se = _make_supervised(rec)
    ledger = FailedToolLedger(nudge_threshold=2)
    # Pre-seed: simulate two prior failures (would normally come from
    # stream_progress on round 1; we seed before the loop to deterministically
    # verify the round-2 prompt picks them up).
    ledger.record("apply_patch", "sandbox mismatch", detail="add foo.py")
    ledger.record("apply_patch", "sandbox mismatch", detail="add bar.py")

    events: list[dict] = []
    status, rounds, _, _, _ = se.run(
        objective="demo",
        engineer_prompt_builder=lambda na: "BASE PROMPT",
        supervised_config=SupervisedConfig(max_rounds=2, check_commands=[]),
        workdir=tmp_path,
        on_event=events.append,
        failed_tool_ledger=ledger,
    )

    # Round 1 prompt must have advisory prepended.
    assert rec.prompts, "engineer should have been called"
    p1 = rec.prompts[0]
    assert "Repeated tool failures" in p1
    assert "apply_patch" in p1
    assert p1.endswith("BASE PROMPT")

    # The advisory fires exactly once (mark-as-nudged).
    nudge_events = [e for e in events if e.get("type") == "engineer.failure_nudge"]
    assert len(nudge_events) == 1, f"expected one nudge, got {len(nudge_events)}"

    # Round 2 prompt should NOT carry the same advisory again
    # (already nudged; ledger.render_advisory() returned "").
    if len(rec.prompts) >= 2:
        p2 = rec.prompts[1]
        assert "Repeated tool failures" not in p2
        assert p2 == "BASE PROMPT"


def test_no_advisory_when_ledger_below_threshold(tmp_path: Path) -> None:
    rec = _RecordingEngineer()
    se = _make_supervised(rec)
    ledger = FailedToolLedger(nudge_threshold=2)
    ledger.record("apply_patch", "boom")  # only one failure

    events: list[dict] = []
    se.run(
        objective="demo",
        engineer_prompt_builder=lambda na: "BASE",
        supervised_config=SupervisedConfig(max_rounds=1, check_commands=[]),
        workdir=tmp_path,
        on_event=events.append,
        failed_tool_ledger=ledger,
    )
    assert rec.prompts[0] == "BASE"
    assert not [e for e in events if e.get("type") == "engineer.failure_nudge"]


def test_runner_works_without_ledger(tmp_path: Path) -> None:
    """Backwards compat: omitting failed_tool_ledger is safe."""
    rec = _RecordingEngineer()
    se = _make_supervised(rec)
    se.run(
        objective="demo",
        engineer_prompt_builder=lambda na: "BASE",
        supervised_config=SupervisedConfig(max_rounds=1, check_commands=[]),
        workdir=tmp_path,
        on_event=None,
    )
    assert rec.prompts == ["BASE"]


def test_engineer_runner_exception_becomes_failed_round(tmp_path: Path) -> None:
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = _ExplodingEngineer()
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = _ContinueReviewer()
    se.reviewer_config = ReviewerConfig(model="stub")

    events: list[dict] = []
    status, rounds, _, reason, _ = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: "BASE",
        supervised_config=SupervisedConfig(max_rounds=1, check_commands=[]),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "max_rounds"
    assert "max_rounds=1" in reason
    assert len(rounds) == 1
    assert rounds[0].engineer_exit_code == -1
    assert "RuntimeError: codex subprocess disappeared" in (rounds[0].fatal_error or "")
    main_events = [e for e in events if e.get("type") == "round.main.completed"]
    assert len(main_events) == 1
    assert main_events[0]["exit_code"] == -1
    assert "RuntimeError: codex subprocess disappeared" in main_events[0]["fatal_error"]


def test_recoverable_reconnect_notice_is_not_backend_failure(tmp_path: Path) -> None:
    engineer = _ScriptedEngineer([
        RunnerResult(
            exit_code=0,
            thread_id="poison-thread",
            fatal_error="Reconnecting... 1/100 (stream disconnected before completion: response.failed event received)",
            agent_messages=["work continued after reconnect notice"],
        ),
        RunnerResult(
            exit_code=0,
            agent_messages=["second round work"],
            thread_id="same-thread",
        ),
    ])
    reviewer = _ContinueReviewer()
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = engineer
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = reviewer
    se.reviewer_config = ReviewerConfig(model="stub")

    events: list[dict] = []
    status, rounds, _, _, last_thread_id = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: f"BASE\nNEXT={na or ''}",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
        seed_thread_id="seed-thread",
    )

    assert status == "max_rounds"
    assert len(rounds) == 2
    assert len(reviewer.calls) == 2
    assert engineer.calls[0]["resume_thread_id"] == "seed-thread"
    assert engineer.calls[1]["resume_thread_id"] == "poison-thread"
    assert last_thread_id == "same-thread"
    skipped = [event for event in events if event.get("review_skipped")]
    assert skipped == []


def test_recoverable_reconnect_predicates_do_not_clear_thread() -> None:
    message = (
        "Reconnecting... 1/100 "
        "(stream disconnected before completion: response.failed event received)"
    )

    assert fatal_error_looks_like_recoverable_reconnect(message)
    assert not fatal_error_looks_like_backend_failure(message)
    assert not should_clear_thread_id_after_outcome(status="", fatal_error=message)
    assert fatal_error_looks_like_backend_failure(
        "Reconnecting... 100/100 "
        "(stream disconnected before completion: response.failed event received)"
    )


def test_backend_failure_skips_reviewer_retries_fresh_session(tmp_path: Path) -> None:
    engineer = _ScriptedEngineer([
        RunnerResult(
            exit_code=0,
            thread_id="poison-thread",
            fatal_error="stream disconnected before completion: response.failed event received",
        ),
        RunnerResult(
            exit_code=0,
            agent_messages=["recovered work"],
            thread_id="healthy-thread",
        ),
    ])
    reviewer = _DoneReviewer()
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = engineer
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = reviewer
    se.reviewer_config = ReviewerConfig(model="stub")

    events: list[dict] = []
    status, rounds, _, _, last_thread_id = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: f"BASE\nNEXT={na or ''}",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
        seed_thread_id="seed-thread",
    )

    assert status == "done"
    assert len(rounds) == 2
    assert len(reviewer.calls) == 1
    assert engineer.calls[0]["resume_thread_id"] == "seed-thread"
    assert engineer.calls[1]["resume_thread_id"] is None
    assert last_thread_id == "healthy-thread"
    skipped = [event for event in events if event.get("review_skipped")]
    assert len(skipped) == 1
    assert skipped[0]["failure_cause"] == "environmental"


def test_repeated_backend_failures_escalate_to_error_without_reviewer(tmp_path: Path) -> None:
    engineer = _ScriptedEngineer([
        RunnerResult(
            exit_code=0,
            fatal_error="429 Too Many Requests: rate limit",
            thread_id="bad-1",
        ),
        RunnerResult(
            exit_code=0,
            fatal_error="stream disconnected before completion: response.failed event received",
            thread_id="bad-2",
        ),
    ])
    reviewer = _DoneReviewer()
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = engineer
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = reviewer
    se.reviewer_config = ReviewerConfig(model="stub")

    status, rounds, _, reason, last_thread_id = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: f"BASE\nNEXT={na or ''}",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
    )

    assert status == "error"
    assert len(rounds) == 2
    assert reviewer.calls == []
    assert "backend_failure_streak=2/2" in reason
    assert last_thread_id is None
    assert engineer.calls[1]["resume_thread_id"] is None


def test_successful_stdout_work_signal_prevents_false_no_progress(tmp_path: Path) -> None:
    engineer = _ScriptedEngineer([
        RunnerResult(
            exit_code=0,
            agent_messages=[],
            stdout_lines=[
                (
                    '{"type":"item.completed","item":{"type":"command_execution",'
                    '"status":"completed","exit_code":0}}'
                )
            ],
        )
    ])
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = engineer
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = _ContinueReviewer()
    se.reviewer_config = ReviewerConfig(model="stub")

    status, rounds, _, reason, _ = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: "BASE",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            no_progress_threshold=1,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
    )

    assert status == "max_rounds"
    assert "no output" not in reason
    assert len(rounds) == 1


def test_supervised_engineer_passes_effective_progress_watchdog_provider(
    tmp_path: Path,
) -> None:
    engineer = _ScriptedEngineer([
        RunnerResult(
            exit_code=0,
            agent_messages=["concrete work"],
            thread_id="healthy-thread",
        )
    ])
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = engineer
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = _DoneReviewer()
    se.reviewer_config = ReviewerConfig(model="stub")

    status, _, _, _, _ = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: "BASE",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            effective_progress_timeout_seconds=600,
            effective_progress_check_interval_seconds=1,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    options = engineer.calls[0]["options"]
    assert options.external_interrupt_reason_provider is not None
    assert options.external_interrupt_reason_provider() is None
    assert options.watchdog_hard_idle_seconds == 900


def test_effective_progress_session_parser_ignores_token_count() -> None:
    assert not _is_effective_codex_session_line(
        '{"type":"event_msg","payload":{"type":"token_count"}}'
    )
    assert _is_effective_codex_session_line(
        '{"type":"response_item","payload":{"type":"custom_tool_call_output"}}'
    )


def test_effective_progress_watchdog_emits_waiting_heartbeat_for_token_only_activity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions" / "2026" / "05" / "26"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "rollout-demo.jsonl"
    session_path.write_text(
        '{"type":"session_meta","payload":{"cwd":"' + str(workdir) + '"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    events: list[dict] = []

    watchdog = _EffectiveProgressWatchdog(
        workdir=workdir,
        timeout_seconds=300,
        check_interval_seconds=1,
        on_event=events.append,
        run_label="engineer-r1",
    )
    watchdog.last_effective_progress_at = time.time() - 130
    watchdog._last_check_at = 0
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + '{"type":"event_msg","payload":{"type":"token_count"}}\n',
        encoding="utf-8",
    )

    assert watchdog.interrupt_reason() is None

    waiting = [event for event in events if event.get("type") == "round.watchdog.waiting"]
    assert len(waiting) == 1
    assert waiting[0]["run_label"] == "engineer-r1"
    assert waiting[0]["idle_seconds"] >= 120
    assert waiting[0]["limit_seconds"] == 300


def test_effective_progress_watchdog_interrupts_after_token_only_activity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions" / "2026" / "05" / "26"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    watchdog = _EffectiveProgressWatchdog(
        workdir=workdir,
        timeout_seconds=1,
        check_interval_seconds=1,
    )
    session_path = session_dir / "rollout-demo.jsonl"
    session_path.write_text(
        '{"type":"session_meta","cwd":"' + str(workdir) + '"}\n',
        encoding="utf-8",
    )
    watchdog._refresh_effective_progress()
    watchdog.last_effective_progress_at = time.time() - 2
    watchdog._last_check_at = 0
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + '{"type":"event_msg","payload":{"type":"token_count"}}\n',
        encoding="utf-8",
    )

    reason = watchdog.interrupt_reason()

    assert reason is not None
    assert "effective progress timeout" in reason


def test_effective_progress_watchdog_ignores_cross_session_path_mentions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    other_workdir = tmp_path / "other"
    other_workdir.mkdir()
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions" / "2026" / "05" / "26"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    watchdog = _EffectiveProgressWatchdog(
        workdir=workdir,
        timeout_seconds=1,
        check_interval_seconds=1,
    )
    session_path = session_dir / "rollout-other.jsonl"
    session_path.write_text(
        (
            '{"type":"session_meta","payload":{"cwd":"'
            + str(other_workdir)
            + '"}}\n'
            '{"type":"response_item","payload":{"type":"message","content":"'
            + str(workdir)
            + ' was mentioned by an operator shell"}}\n'
        ),
        encoding="utf-8",
    )
    watchdog.last_effective_progress_at = time.time() - 2
    watchdog._last_check_at = 0

    reason = watchdog.interrupt_reason()

    assert reason is not None
    assert "effective progress timeout" in reason


def test_failed_acceptance_check_reaches_reviewer_and_only_handoff_reaches_engineer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run_checks(commands, timeout_seconds, *, cwd=None):  # noqa: ANN001, ARG001
        assert cwd == str(tmp_path)
        return [
            CheckResult(
                command=commands[0],
                exit_code=1,
                passed=False,
                output_tail=(
                    "RAW_VALIDATOR_SENTINEL_SHOULD_NOT_REACH_ENGINEER "
                    "image2_conceptual_figure_not_included_in_main_tex "
                    "paper/main.tex: include paper/figures/method.png"
                ),
            )
        ]

    monkeypatch.setattr("argus_skill.engineer.runner.run_checks", fake_run_checks)
    rec = _RecordingEngineer()
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = rec
    se.engineer_config = EngineerConfig(model="stub")
    reviewer = _HandoffReviewer()
    se.reviewer = reviewer
    se.reviewer_config = ReviewerConfig(model="stub")

    events: list[dict] = []
    status, rounds, _, _, _ = cast(SupervisedEngineer, se).run(
        objective="demo",
        engineer_prompt_builder=lambda na: f"BASE\nNEXT={na or ''}",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            check_commands=[
                "python -m argus_skill.skills.pipeline_contracts "
                "validate-full-emnlp --project-root ."
            ],
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "max_rounds"
    assert len(rounds) == 2
    assert len(rec.prompts) == 2
    assert reviewer.seen_checks
    assert "RAW_VALIDATOR_SENTINEL_SHOULD_NOT_REACH_ENGINEER" in reviewer.seen_checks[0].output_tail
    second_prompt = rec.prompts[1]
    assert "Professional handoff: Figure 1 must include the image-2 raster" in second_prompt
    assert "paper/figures/method.png" in second_prompt
    assert "RAW_VALIDATOR_SENTINEL_SHOULD_NOT_REACH_ENGINEER" not in second_prompt
    assert "image2_conceptual_figure_not_included_in_main_tex" not in second_prompt
    assert not [e for e in events if e.get("type") == "checks.failure_guidance"]
