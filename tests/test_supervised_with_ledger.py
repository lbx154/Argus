"""SupervisedEngineer × FailedToolLedger integration.

Verifies that when the ledger has pending nudges (≥ threshold failures
for some tool that haven't yet been surfaced), the next round's
engineer prompt is prepended with the advisory block and an
``engineer.failure_nudge`` event is emitted exactly once.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.engineer.failed_tool_ledger import FailedToolLedger
from argus_skill.engineer.reviewer import Reviewer, ReviewerConfig
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
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


class _ContinueReviewer:
    """Always says continue so we get every requested round."""

    def evaluate(self, **kwargs):
        from argus_skill.core.models import ReviewDecision
        return ReviewDecision(
            status="continue",
            confidence=0.4,
            reason="more work",
            next_action="keep going",
            round_summary_markdown="",
            completion_summary_markdown="",
        )


def _make_supervised(rec: _RecordingEngineer) -> SupervisedEngineer:
    se = SupervisedEngineer.__new__(SupervisedEngineer)
    se.engineer_runner = rec  # type: ignore[attr-defined]
    se.engineer_config = EngineerConfig(model="stub")  # type: ignore[attr-defined]
    se.reviewer = _ContinueReviewer()  # type: ignore[attr-defined]
    se.reviewer_config = ReviewerConfig(model="stub")  # type: ignore[attr-defined]
    return se


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
