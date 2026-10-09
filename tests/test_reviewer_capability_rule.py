"""The Reviewer's evidence rule follows its tools and what the host records.

Every Reviewer call is read-only. On most backends that leaves file read and
search tools only; told to rerun a check "yourself", such a Reviewer refused
the Engineer's work round after round and asked for an execution surface no
role could grant. Three forms now exist:

- a Reviewer that can run commands reruns the decisive check, and falls back
  to recorded runs and its own reading when the sandbox blocks the rerun;
- a read-only Reviewer whose Engineer's backend reports exit codes judges from
  host-recorded runs, reading what each ran, and may ask for one named check;
- a read-only Reviewer without such records asks for evidence it can read.

In every form the Engineer's cited claim alone is not evidence.
"""
from __future__ import annotations

import pytest

from argus.core.command_record import backend_records_command_results
from argus.core.model_visible_text import (
    EVIDENCE_EXECUTE,
    EVIDENCE_READ,
    EVIDENCE_RECORDED,
    REVIEW_EVIDENCE_RULE_EXECUTING,
    REVIEW_EVIDENCE_RULE_READ_ONLY,
    REVIEW_EVIDENCE_RULE_UNRECORDED,
    review_evidence_rule,
)
from argus.reviewer import Reviewer
from argus.reviewer import tools as review_tools
from argus.reviewer.tools import ReviewActions, reviewer_can_execute, reviewer_evidence_mode
from argus.reviewer.validation import IMAGE_ENV

_RULES = (REVIEW_EVIDENCE_RULE_EXECUTING, REVIEW_EVIDENCE_RULE_READ_ONLY, REVIEW_EVIDENCE_RULE_UNRECORDED)


class _Runner:
    def __init__(self, backend: str) -> None:
        self.backend = backend


def _static(runner, *, records: bool) -> str:
    reviewer = Reviewer(runner=runner, skill_store=None)
    return reviewer._build_static_preamble(
        objective="Make the parser accept the documented grammar.",
        operator_messages=[], planner_review_instruction="", round_index=1,
        session_id=None, main_summary="All tests pass.", main_error=None,
        prior_checkpoint={},
        evidence_mode=reviewer_evidence_mode(runner, engineer_records_commands=records),
    )


@pytest.fixture(autouse=True)
def _no_validation_image(monkeypatch):
    monkeypatch.delenv(IMAGE_ENV, raising=False)
    monkeypatch.setattr("argus.core.knob_store.read_persisted_knobs", lambda: {})


@pytest.mark.parametrize("backend", ["copilot", "claude", "pi", "qoder", "memory", ""])
def test_a_read_only_reviewer_relies_on_host_records_only_when_they_exist(backend: str) -> None:
    runner = _Runner(backend)
    assert reviewer_can_execute(runner) is False
    recorded = _static(runner, records=True)
    assert REVIEW_EVIDENCE_RULE_READ_ONLY in recorded
    unrecorded = _static(runner, records=False)
    assert REVIEW_EVIDENCE_RULE_UNRECORDED in unrecorded
    assert "host" not in REVIEW_EVIDENCE_RULE_UNRECORDED


def test_a_sandboxed_shell_reruns_the_check_itself() -> None:
    runner = _Runner("codex")
    assert reviewer_can_execute(runner) is True
    for records in (True, False):
        prompt = _static(runner, records=records)
        assert REVIEW_EVIDENCE_RULE_EXECUTING in prompt
        assert REVIEW_EVIDENCE_RULE_READ_ONLY not in prompt


def test_a_validation_image_counts_only_when_docker_can_run_it(monkeypatch) -> None:
    monkeypatch.setenv(IMAGE_ENV, "python:3.12-slim")
    monkeypatch.setattr(review_tools.sys, "platform", "linux")
    monkeypatch.setattr(review_tools, "_docker_image_present", lambda image: True)
    assert reviewer_evidence_mode(_Runner("copilot"), engineer_records_commands=False) == EVIDENCE_EXECUTE
    monkeypatch.setattr(review_tools, "_docker_image_present", lambda image: False)
    assert reviewer_evidence_mode(_Runner("copilot"), engineer_records_commands=True) == EVIDENCE_RECORDED
    monkeypatch.setattr(review_tools.sys, "platform", "darwin")
    monkeypatch.setattr(review_tools, "_docker_image_present", lambda image: True)
    assert reviewer_can_execute(_Runner("copilot")) is False


def test_only_backends_that_report_exit_codes_count_as_recording() -> None:
    assert backend_records_command_results(_Runner("copilot"))
    assert backend_records_command_results(_Runner("codex"))
    for backend in ("claude", "cursor", "pi", "opencode", "qoder", "memory", ""):
        assert not backend_records_command_results(_Runner(backend))


def test_the_static_prompt_differs_only_by_the_rule() -> None:
    runner = _Runner("copilot")
    bodies = {
        _static(runner, records=True).replace(REVIEW_EVIDENCE_RULE_READ_ONLY, ""),
        _static(runner, records=False).replace(REVIEW_EVIDENCE_RULE_UNRECORDED, ""),
        _static(_Runner("codex"), records=True).replace(REVIEW_EVIDENCE_RULE_EXECUTING, ""),
    }
    assert len(bodies) == 1


def test_every_form_refuses_the_engineers_word_alone() -> None:
    for mode in (EVIDENCE_EXECUTE, EVIDENCE_RECORDED, EVIDENCE_READ, "unknown"):
        assert "the Engineer's cited claim alone is not evidence" in review_evidence_rule(mode)
    assert review_evidence_rule("unknown") == REVIEW_EVIDENCE_RULE_UNRECORDED


def test_a_read_only_reviewer_never_asks_for_an_execution_tool() -> None:
    for rule in (REVIEW_EVIDENCE_RULE_READ_ONLY, REVIEW_EVIDENCE_RULE_UNRECORDED):
        assert "never ask for an execution tool" in rule
        assert "yourself" not in rule
    assert "ask for the decisive evidence in a form you can read" in REVIEW_EVIDENCE_RULE_UNRECORDED


def test_host_records_are_read_for_what_they_ran() -> None:
    rule = REVIEW_EVIDENCE_RULE_READ_ONLY
    assert "The host vouches only that a command ran and its exit code" in rule
    assert "read the test or script it ran" in rule
    for shape in ("pipes", "`|| true`", "test selection", "tests or checks edited this round"):
        assert shape in rule
    assert "An Engineer-written check counts only once you have read it" in rule
    assert "one named check whose run the host records" in rule


def test_an_executing_reviewer_falls_back_when_its_rerun_is_blocked() -> None:
    rule = REVIEW_EVIDENCE_RULE_EXECUTING
    assert "rerun the decisive check yourself" in rule
    assert "If the sandbox blocks the rerun (write or network denied)" in rule
    assert "judge from recorded runs and your own reading" in rule


def test_the_masked_display_sentence_follows_each_form() -> None:
    masked = "`******` or `<REDACTED:…>` is display masking"
    for rule in _RULES:
        _evidence, _, display = rule.partition(masked)
        assert display, "the masked-display sentence is part of every form"
        assert "proves neither a defect nor a fix" in display
        assert "settle it the same way" in display
        assert "rerunnable assertion on a placeholder value" in display


def test_manager_attention_keeps_operator_evidence_and_drops_missing_checks() -> None:
    tools = {tool["name"]: tool for tool in ReviewActions().tools}
    description = tools["revise_review"]["inputSchema"]["properties"]["manager_attention"]["description"]
    assert "evidence you cannot observe" not in description
    assert "evidence only the operator can supply (credentials, hardware, human judgment)" in description
    assert "A check you lack is not one: run it if you can, else ask the Engineer for it" in description
    assert "agreement on something unverified" in description
