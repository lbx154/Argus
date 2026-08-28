"""A resolved vertical fails closed and reports stale stage evidence clearly."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.apps._runtime_supervisor import (
    _independent_review_required_for_project_root,
)
from argus_skill.skills import stage_machine
from argus_skill.skills.vertical_select import persist_vertical


def test_a_vertical_that_cannot_be_read_still_requires_review(tmp_path, monkeypatch):
    """"I cannot tell whether review is mandatory" is not "review is optional"."""
    persist_vertical(tmp_path, "math", research_target_level="exploratory")
    import argus_skill.verticals._base as base

    def boom(*_a, **_k):
        raise RuntimeError("vertical module is from another framework revision")

    monkeypatch.setattr(base, "load_vertical", boom)

    assert _independent_review_required_for_project_root(tmp_path) is True


def test_an_unresolved_project_keeps_the_legacy_default(tmp_path):
    assert _independent_review_required_for_project_root(tmp_path) is False


def test_the_math_vertical_requires_review(tmp_path):
    persist_vertical(tmp_path, "math", research_target_level="exploratory")

    assert _independent_review_required_for_project_root(tmp_path) is True


# --- the rejection has to name the disputed record --------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    persist_vertical(tmp_path, "math", research_target_level="exploratory")
    monkeypatch.setattr(
        stage_machine, "_ensure_stage_completion", lambda *a, **k: None
    )
    # These three tests are about what a *mismatched fingerprint* reports, and
    # they reach a completion record the cheapest way there is: complete at
    # ``scope``. That is early completion, which since run 13 requires standing
    # — ``direct`` workflow mode on the read side, the explicit argument on the
    # write side. Granting both here keeps the subject of these tests the
    # rejection message rather than the stage position. See
    # ``tests/skills/test_stage_completion_authority.py``.
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["workflow_mode"] = "direct"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return tmp_path


def _state(project: Path) -> dict:
    return json.loads(
        (project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )


def test_the_completion_records_which_framework_stamped_it(project):
    """When a reader cannot reproduce the hash, the first question is always
    "was this written by the code I am running?" — record the answer."""
    stage_machine.complete_final_stage(
        project, reason="scope is enough", allow_early_completion=True
    )

    record = _state(project)["stages"]["scope"]
    assert record["completion_contract_source"] == str(
        stage_machine.framework_source_root()
    )


def test_the_rejection_names_the_stage_that_holds_the_disputed_record(project):
    """The old message hashed ``stages[-1]`` — ``review``, a stage this project
    never reached — while the comparison that failed was on ``scope``."""
    from argus_skill.life.supervisor._planning_cycle_helpers import (
        _staged_goal_completion_issue,
    )

    stage_machine.complete_final_stage(
        project, reason="scope is enough", allow_early_completion=True
    )
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state = _state(project)
    expected = state["stages"]["scope"]["completion_contract_sha256"]
    state["stages"]["scope"]["completion_contract_sha256"] = "6248efde" + "0" * 56
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    issue = _staged_goal_completion_issue(project)

    assert "certified_stage=scope" in issue
    assert expected in issue, "must print the fingerprint the framework expects"
    assert "6248efde" in issue, "must print the fingerprint actually stored"
    assert "re-certify" in issue
    review_hash = stage_machine.completion_contract_fingerprint(
        project, "review", version=1
    )
    assert review_hash not in issue, "the stage that never ran is not the subject"


def test_a_matching_certificate_still_passes_the_gate(project):
    from argus_skill.life.supervisor._planning_cycle_helpers import (
        _staged_goal_completion_issue,
    )

    stage_machine.complete_final_stage(
        project, reason="scope is enough", allow_early_completion=True
    )

    assert _staged_goal_completion_issue(project) == ""
