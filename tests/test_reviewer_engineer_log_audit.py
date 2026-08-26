"""Reviewer engineer execution-log audit (process-correctness review).

Operator directive (2026-06-26): the reviewer runs in the project work-tree and
only receives the engineer's final summary, so it can verify that the OUTCOME
traces to the checklist but NOT that the PROCESS that produced it was honest
(no hardcoded answer, no skipped step, no cheat method, no method contradiction).
The supervisor threads the absolute path to this mission's execution log to
``reviewer.evaluate`` as a one-line fallback pointer after the Engineer's own
account, avoiding routine re-derivation.

Pinned here:
  * non-empty engineer_log_path -> prompt contains the audit section + the path
  * empty engineer_log_path     -> prompt is byte-for-byte the legacy prompt
                                   (back-compat: memory backend / tests / no life_dir)
  * MEASURED-BENCHMARK mode      -> audit is RED-FLAG-ONLY (no incentive clash
                                   with "trust the frozen scorer")
  * the path threads structurally: SupervisedConfig.engineer_log_path is what
    the supervised loop hands reviewer.evaluate(...)
  * the engineer-process-audit reviewer skill exists in the reviewer pool
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import persist_vertical

_LOG_PATH = "/abs/global/projects/deadbeef/events.jsonl"
_CALL_ID = "0123456789abcdef"


def _build(
    path: str,
    *,
    call_id: str = "",
    monkeypatch=None,
    measured: bool = False,
    workflow_mode: str | None = "staged",
    scope: str = "",
    main_error: str | None = None,
    objective: str = "implement and verify the kernel",
) -> str:
    if monkeypatch is not None:
        if workflow_mode is not None:
            monkeypatch.setattr(
                "argus_skill.skills.vertical_select.resolve_evidence_mode",
                lambda _root: workflow_mode,
            )
        if measured:
            monkeypatch.setenv("ARGUS_SKILL_MEASURED_MODE", "1")
        else:
            monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective=objective,
        operator_messages=[],
        planner_review_instruction="",
        round_index=4,
        session_id=None,
        main_summary="HANDOFF: did X. artifact at out.json",
        main_error=main_error,
        prior_checkpoint={},
        engineer_log_path=path,
        engineer_call_id=call_id,
        scope=scope,
    )


# --------------------------------------------------------------------------- #
# Prompt shape: present when path set, absent (legacy) when empty
# --------------------------------------------------------------------------- #
def test_audit_section_present_when_log_path_set(monkeypatch) -> None:
    p = _build(_LOG_PATH, monkeypatch=monkeypatch)
    assert "The event log is at" in p
    assert _LOG_PATH in p
    assert "if the Engineer's account seems wrong" in p
    assert "## Engineer's account of this round" in p
    assert "use_attach" not in p


def test_no_audit_section_when_log_path_empty(monkeypatch) -> None:
    p = _build("", monkeypatch=monkeypatch)
    assert "Engineer execution-log audit" not in p
    assert "events.jsonl" not in p


def test_engineer_account_is_labeled_and_capped_at_6000_chars(monkeypatch) -> None:
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="implement and verify the kernel",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="a" * 7000,
        main_error=None,
    )

    account = prompt.split("## Engineer's account of this round\n", 1)[1]
    assert account.startswith("a" * 6000)
    assert not account.startswith("a" * 6001)


def test_reviewer_rejects_retroactive_audit_reconstruction(monkeypatch) -> None:
    p = _build(
        "",
        monkeypatch=monkeypatch,
        objective="Review an append-only audit ledger and exact COMMAND_LOG.",
    )

    assert "operator mutation freeze or append-only requirement" in p
    assert "compare directive order with file-write, install, and command events" in p
    assert "cannot make an overwritten or reconstructed ledger contemporaneous" in p
    assert "unless the cited objective text states it" in p
    assert "missing byte-faithful command" in p


def test_log_pointer_scopes_to_current_engineer_call_without_recipe(monkeypatch) -> None:
    p = _build(
        _LOG_PATH,
        call_id=_CALL_ID,
        monkeypatch=monkeypatch,
        scope="final_submission",
        main_error="suspicious execution",
    )

    assert f"current call `{_CALL_ID}`" in p
    assert "event_log_query" not in p
    assert "rg -F" not in p
    assert "\n    grep -nE 'use_attach" not in p
    assert "\n    grep -nE 'pytest" not in p


def test_missing_call_id_does_not_invite_unscoped_keyword_scan(monkeypatch) -> None:
    p = _build(
        _LOG_PATH,
        monkeypatch=monkeypatch,
        scope="final_submission",
        main_error="suspicious execution",
    )

    assert "Current engineer call id:" not in p
    assert "The event log is at" in p
    assert "grep -nE 'use_attach" not in p
    assert "grep -nE 'pytest" not in p


def test_proportional_research_uses_compact_on_demand_audit(monkeypatch) -> None:
    p = _build(
        _LOG_PATH,
        call_id=_CALL_ID,
        monkeypatch=monkeypatch,
        workflow_mode="proportional",
    )

    assert "The event log is at" in p
    assert "if the Engineer's account seems wrong" in p
    assert _CALL_ID in p
    assert "use_attach" not in p
    assert "grep recipes" not in p.lower()


def test_manager_staged_research_uses_compact_on_demand_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persist_vertical(tmp_path, "research", workflow_mode="staged")
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))

    p = _build(
        _LOG_PATH,
        call_id=_CALL_ID,
        monkeypatch=monkeypatch,
        workflow_mode=None,
    )

    assert "The event log is at" in p
    assert "if the Engineer's account seems wrong" in p


def test_empty_path_is_byte_for_byte_legacy_prompt() -> None:
    # Back-compat: passing "" must produce exactly the same prompt as omitting
    # the new kwarg entirely (the memory backend / tests / no-life_dir case).
    r = Reviewer(runner=None, skill_store=None)
    common = dict(
        objective="implement and verify the kernel",
        operator_messages=[],
        planner_review_instruction="",
        round_index=4,
        session_id=None,
        main_summary="HANDOFF: did X. artifact at out.json",
        main_error=None,
        prior_checkpoint={},
    )
    assert r._build_prompt(engineer_log_path="", **common) == r._build_prompt(**common)


# --------------------------------------------------------------------------- #
# Measured mode: red-flag-only (no clash with "trust the frozen scorer")
# --------------------------------------------------------------------------- #
def test_measured_mode_audit_is_red_flag_only(monkeypatch) -> None:
    p = _build(_LOG_PATH, monkeypatch=monkeypatch, measured=True)
    # measured framing is still injected
    assert "MEASURED-BENCHMARK MODE" in p
    assert "The event log is at" in p
    assert "grep recipes" not in p.lower()


def test_final_submission_uses_on_demand_process_audit(monkeypatch) -> None:
    p = _build(
        _LOG_PATH,
        monkeypatch=monkeypatch,
        measured=False,
        scope="final_submission",
    )
    assert "The event log is at" in p
    assert "if the Engineer's account seems wrong" in p
    assert "use_attach" not in p


def test_engineer_error_still_uses_one_line_log_pointer(monkeypatch) -> None:
    p = _build(
        _LOG_PATH,
        monkeypatch=monkeypatch,
        main_error="worker exited unexpectedly",
    )
    assert "The event log is at" in p
    assert "event_log_query" not in p
    assert "grep recipes" not in p.lower()


# --------------------------------------------------------------------------- #
# Structural path threading: SupervisedConfig -> reviewer.evaluate
# --------------------------------------------------------------------------- #
def test_supervised_config_has_engineer_log_path_field() -> None:
    fields = {f.name for f in dataclasses.fields(SupervisedConfig)}
    assert "engineer_log_path" in fields
    assert SupervisedConfig().engineer_log_path == ""  # back-compat default


class _OneRoundEngineerRunner:
    """Engineer succeeds once so the loop reaches the reviewer call."""

    def run_exec(self, **_kwargs):
        return RunnerResult(
            exit_code=0,
            agent_messages=["implemented the increment; artifact at out.json"],
            thread_id="t1",
            call_id=_CALL_ID,
            call_id_log_correlated=True,
            fatal_error=None,
        )


class _CapturingReviewer:
    """Captures the engineer_log_path the supervised loop hands evaluate()."""

    def __init__(self) -> None:
        self.seen_log_path: str | None = None
        self.seen_call_id: str | None = None
        self.seen_round_max: int | None = None

    def evaluate(self, **kwargs) -> ReviewDecision:
        self.seen_log_path = kwargs.get("engineer_log_path")
        self.seen_call_id = kwargs.get("engineer_call_id")
        self.seen_round_max = kwargs.get("round_max")
        return ReviewDecision(
            status="done",
            reason="ok",
            next_action="",
        )


class _UncorrelatedEngineerRunner:
    def run_exec(self, **_kwargs):
        return RunnerResult(
            exit_code=0,
            agent_messages=["implemented without a backend audit log"],
            thread_id="t2",
        )


def test_config_path_is_threaded_into_evaluate(tmp_path: Path) -> None:
    reviewer = _CapturingReviewer()
    engine = SupervisedEngineer(
        engineer_runner=_OneRoundEngineerRunner(),
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    config = SupervisedConfig(
        max_rounds=1,
        background_subagent_advisory=False,
        engineer_log_path=_LOG_PATH,
    )
    engine.run(
        objective="implement the increment",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do the increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=lambda _e: None,
    )
    assert reviewer.seen_log_path == _LOG_PATH
    assert reviewer.seen_call_id == _CALL_ID
    assert reviewer.seen_round_max == 1


def test_gateway_synthesized_call_id_uses_legacy_unscoped_audit(
    tmp_path: Path,
) -> None:
    reviewer = _CapturingReviewer()
    engine = SupervisedEngineer(
        engineer_runner=_UncorrelatedEngineerRunner(),
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    config = SupervisedConfig(
        max_rounds=1,
        background_subagent_advisory=False,
        engineer_log_path=_LOG_PATH,
    )

    engine.run(
        objective="implement the increment",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do it",
        supervised_config=config,
        workdir=tmp_path,
        on_event=lambda _e: None,
    )

    assert reviewer.seen_call_id == ""


def test_empty_config_path_threads_empty_string(tmp_path: Path) -> None:
    reviewer = _CapturingReviewer()
    engine = SupervisedEngineer(
        engineer_runner=_OneRoundEngineerRunner(),
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="gpt-5.5"),
        reviewer_config=ReviewerConfig(model="gpt-5.5"),
    )
    config = SupervisedConfig(
        max_rounds=1,
        background_subagent_advisory=False,
    )
    engine.run(
        objective="implement the increment",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "do the increment",
        supervised_config=config,
        workdir=tmp_path,
        on_event=lambda _e: None,
    )
    assert reviewer.seen_log_path == ""
    assert reviewer.seen_call_id == _CALL_ID


def test_checkpoint_path_is_internal_and_directly_editable(tmp_path: Path) -> None:
    import argparse

    from argus_skill.apps._runtime import _checkpoint_path_for

    session_dir = tmp_path / "projects" / "s-1d7da0e9"
    workdir = tmp_path / "some-worktree"
    path = _checkpoint_path_for(
        argparse.Namespace(project_state_dir=str(session_dir), life_dir=None),
        workdir,
    )

    assert path == session_dir / "CHECKPOINT.md"


# --------------------------------------------------------------------------- #
# The reviewer skill exists in the reviewer pool
# --------------------------------------------------------------------------- #
def test_engineer_process_audit_skill_exists_in_reviewer_pool() -> None:
    from argus_skill.skills.builtins import iter_builtin_skill_texts

    names = {fn for fn, _ in iter_builtin_skill_texts()}
    assert "reviewer/engineer-process-audit.md" in names

    from argus_skill.skills.missions import ReviewerMission

    # must NOT be hard-excluded, or the matcher could never surface it
    assert "engineer-process-audit.md" not in ReviewerMission.default_exclude
