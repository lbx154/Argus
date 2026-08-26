"""A self-maintenance canary must not roll the running framework backwards.

Bugs #41 and #42, one cause, observed live in testbed run 5 (s-e25c3b7c).

At 01:05:37 the daemon handed itself off to a repair canary running out of
``self-maintenance/worktrees/a1e7d7f19c9fc4ef``. ``_prepare_worktree`` branches
from ``main``, and ``git worktree add`` materializes committed content only, so
that canary was 36 commits behind and — decisively — missing every uncommitted
edit in the operator's checkout. Two of those edits mattered:

* ``REQUIRE_INDEPENDENT_REVIEW = True`` on the math vertical. Absent from the
  canary, ``getattr(provider, "REQUIRE_INDEPENDENT_REVIEW", False)`` answered
  False and the next 14 missions closed on the Engineer's own say-so (#42).
* A third ``scope`` checklist item. The canary stamped a completion fingerprint
  computed from its own two-item checklist; the operator's framework recomputes
  three and rejects it. The Goal Gate then refused its own ledger until the
  daemon idled out at 02:59, and the rejection printed the *final* stage's
  fingerprint rather than the disputed one, so the number it complained about
  appeared nowhere in the file (#41).

Nothing here tests git plumbing. It tests that a canary which cannot contain the
running framework is declined, that a resolved vertical fails closed, and that a
mismatched certificate says which stage and which two hashes disagree.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from argus_skill.apps._runtime_supervisor import (
    _independent_review_required_for_project_root,
)
from argus_skill.daemon import self_maintenance
from argus_skill.skills import stage_machine
from argus_skill.skills.vertical_select import persist_vertical

# --- the canary must not be a downgrade -------------------------------------


class _Git:
    """Stand-in for the running framework's checkout."""

    def __init__(self, *, dirty: str = "", ahead: str = "0", is_repo: bool = True):
        self.dirty = dirty
        self.ahead = ahead
        self.is_repo = is_repo

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace

        if argv[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return SimpleNamespace(
                returncode=0 if self.is_repo else 128,
                stdout=f"{kwargs.get('cwd', '')}\n",
                stderr="",
            )
        if argv[:2] == ["git", "status"]:
            return SimpleNamespace(returncode=0, stdout=self.dirty, stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return SimpleNamespace(returncode=0, stdout=self.ahead + "\n", stderr="")
        raise AssertionError(f"unexpected git call: {argv}")


def _maintainer(tmp_path: Path) -> "self_maintenance.DaemonSelfMaintenance":
    return self_maintenance.DaemonSelfMaintenance.__new__(
        self_maintenance.DaemonSelfMaintenance
    )


@pytest.fixture
def maintainer(tmp_path: Path):
    obj = _maintainer(tmp_path)
    obj.root = tmp_path / "state"
    obj.framework_root = tmp_path / "Argus-0812"
    obj.framework_root.mkdir()
    (obj.framework_root / ".git").mkdir()
    return obj


def test_uncommitted_operator_work_declines_the_handoff(maintainer, monkeypatch):
    """The exact run-5 shape: the policy that mattered was never committed."""
    monkeypatch.setattr(
        self_maintenance,
        "_run",
        _Git(dirty=" M argus_skill/verticals/math/stages.py\n"),
    )

    reason = maintainer._handoff_regression("99b4de93")

    assert "uncommitted" in reason
    assert "argus_skill/verticals/math/stages.py" in reason


def test_commits_the_canary_never_saw_decline_the_handoff(maintainer, monkeypatch):
    monkeypatch.setattr(self_maintenance, "_run", _Git(ahead="36"))

    reason = maintainer._handoff_regression("99b4de93")

    assert "36 commit(s) ahead" in reason
    assert "99b4de93" in reason


def test_a_clean_checkout_at_the_canary_commit_still_hands_off(
    maintainer, monkeypatch
):
    """Self-repair has to keep working; this is how the system fixes itself."""
    monkeypatch.setattr(self_maintenance, "_run", _Git())

    assert maintainer._handoff_regression("99b4de93") == ""


def test_a_non_git_deployment_still_hands_off(maintainer, monkeypatch):
    """An installed (non-checkout) framework has no local work to lose."""
    monkeypatch.setattr(self_maintenance, "_run", _Git(is_repo=False))

    assert maintainer._handoff_regression("99b4de93") == ""


def test_the_declined_handoff_is_reported_not_swallowed():
    source = subprocess.run(
        ["grep", "-c", "handoff_declined", str(
            Path(self_maintenance.__file__)
        )],
        capture_output=True,
        text=True,
    )
    assert int(source.stdout.strip()) >= 4, "decline must set phase AND emit an event"


# --- a resolved vertical fails closed ---------------------------------------


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
