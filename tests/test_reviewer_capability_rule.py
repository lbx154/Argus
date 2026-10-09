"""The Reviewer's evidence rule follows the tools its call actually has.

Every Reviewer call is read-only. On most backends that leaves file read and
search tools only; told to rerun a check "yourself", such a Reviewer refused
the Engineer's work round after round and asked for an execution surface no
role could grant. A Reviewer that can run commands still reruns the decisive
check. A read-only one judges from what the host recorded of a run and from
its own reading, and may ask for one named check whose run the host records.
In both forms the Engineer's cited claim alone is not evidence.
"""
from __future__ import annotations

import pytest

from argus.core.model_visible_text import (
    REVIEW_EVIDENCE_RULE_EXECUTING,
    REVIEW_EVIDENCE_RULE_READ_ONLY,
    review_evidence_rule,
)
from argus.reviewer import Reviewer
from argus.reviewer.tools import ReviewActions, reviewer_can_execute
from argus.reviewer.validation import IMAGE_ENV


class _Runner:
    def __init__(self, backend: str) -> None:
        self.backend = backend


def _static(runner) -> str:
    reviewer = Reviewer(runner=runner, skill_store=None)
    return reviewer._build_static_preamble(
        objective="Make the parser accept the documented grammar.",
        operator_messages=[], planner_review_instruction="", round_index=1,
        session_id=None, main_summary="All tests pass.", main_error=None,
        prior_checkpoint={},
    )


@pytest.fixture(autouse=True)
def _no_validation_image(monkeypatch):
    monkeypatch.delenv(IMAGE_ENV, raising=False)
    monkeypatch.setattr("argus.core.knob_store.read_persisted_knobs", lambda: {})


@pytest.mark.parametrize("backend", ["copilot", "claude", "pi", "qoder", "memory", ""])
def test_read_only_tool_surfaces_get_the_read_only_rule(backend: str) -> None:
    runner = _Runner(backend)
    assert reviewer_can_execute(runner) is False
    prompt = _static(runner)
    assert REVIEW_EVIDENCE_RULE_READ_ONLY in prompt
    assert REVIEW_EVIDENCE_RULE_EXECUTING not in prompt


def test_a_sandboxed_shell_reruns_the_check_itself() -> None:
    runner = _Runner("codex")
    assert reviewer_can_execute(runner) is True
    prompt = _static(runner)
    assert REVIEW_EVIDENCE_RULE_EXECUTING in prompt
    assert REVIEW_EVIDENCE_RULE_READ_ONLY not in prompt


def test_a_validation_image_gives_any_backend_a_command_tool(monkeypatch) -> None:
    monkeypatch.setenv(IMAGE_ENV, "python:3.12-slim")
    runner = _Runner("copilot")
    assert reviewer_can_execute(runner) is True
    assert REVIEW_EVIDENCE_RULE_EXECUTING in _static(runner)


def test_the_static_prompt_differs_only_by_the_rule() -> None:
    read_only = _static(_Runner("copilot"))
    executing = _static(_Runner("codex"))
    assert read_only.replace(REVIEW_EVIDENCE_RULE_READ_ONLY, "") == executing.replace(
        REVIEW_EVIDENCE_RULE_EXECUTING, ""
    )


def test_both_forms_refuse_the_engineers_word_alone() -> None:
    for can_execute in (True, False):
        rule = review_evidence_rule(can_execute)
        assert "the Engineer's cited claim alone is not evidence" in rule


def test_the_read_only_form_never_demands_execution_it_cannot_do() -> None:
    rule = REVIEW_EVIDENCE_RULE_READ_ONLY
    assert "yourself" not in rule
    assert "never ask for execution" in rule
    assert "host-recorded runs" in rule
    assert "your own reading of code and tests" in rule
    assert "one named check whose run the host records" in rule


def test_the_masked_display_sentence_follows_the_same_logic() -> None:
    masked = "`******` or `<REDACTED:…>` is display masking"
    for can_execute in (True, False):
        rule = review_evidence_rule(can_execute)
        evidence, _, display = rule.partition(masked)
        assert display, "the masked-display sentence is part of both forms"
        assert "proves neither a defect nor a fix" in display
        # Settled the way this Reviewer settles any check: rerun it itself, or
        # weigh a host-recorded run; never by the Engineer's account.
        assert "settle it the same way" in display
        assert "rerunnable assertion on a placeholder value" in display
    assert "rerun the decisive check yourself" in REVIEW_EVIDENCE_RULE_EXECUTING
    assert "whose run the host records" in REVIEW_EVIDENCE_RULE_READ_ONLY


def test_manager_attention_does_not_route_a_check_the_reviewer_cannot_run() -> None:
    tools = {tool["name"]: tool for tool in ReviewActions().tools}
    description = tools["revise_review"]["inputSchema"]["properties"]["manager_attention"]["description"]
    assert "evidence you cannot observe" not in description
    assert "A check you cannot run is not one" in description
    assert "agreement on something unverified" in description
