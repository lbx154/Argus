"""A current final review is graded against the manuscript the Reviewer read.

The daemon keeps session state in one root and runs the project in another.
The Reviewer binds ``paper/main.tex`` from the execution workdir; the settlement
used to hash the state root instead, found no manuscript there, and stamped a
perfectly current ``done`` verdict as stale. FuseHead's certified final review
was held for exactly that reason on 2026-09-06.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.apps._runtime_execute import SkillLoopExecuteMixin
from argus.core.manuscript_snapshot import manuscript_snapshot
from argus.core.models import ReviewDecision


def _settle(state_root: Path, workdir: Path, review: ReviewDecision) -> SimpleNamespace:
    runtime = SimpleNamespace(
        _artifact_root=state_root,
        last_thread_id=None,
        _next_seed_thread_id=None,
        _consume_auth_failure=lambda: False,
        manager=None,
    )
    ex_state = SimpleNamespace(
        workdir=workdir,
        mission_scope="final_submission",
        outcome=SimpleNamespace(
            status="done",
            stop_reason="",
            stop_kind=None,
            last_thread_id=None,
            rounds=[SimpleNamespace(review=review)],
        ),
    )
    SkillLoopExecuteMixin._extract_execute_outcome_fields(runtime, ex_state)
    return ex_state


def test_current_review_stays_done_when_state_root_holds_no_manuscript(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    workdir = tmp_path / "project"
    (workdir / "paper").mkdir(parents=True)
    (workdir / "paper" / "main.tex").write_text("\\title{FuseHead}", encoding="utf-8")

    review = ReviewDecision(
        status="done",
        reason="Reject-level issues: none.",
        next_action="",
        manuscript_snapshot=manuscript_snapshot(workdir),
    )
    ex_state = _settle(state_root, workdir, review)

    assert review.status == "done"
    assert ex_state.final_review_status == "done"
    assert ex_state.final_submission_certified is True


def test_review_of_an_older_manuscript_is_still_stale(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    workdir = tmp_path / "project"
    (workdir / "paper").mkdir(parents=True)
    manuscript = workdir / "paper" / "main.tex"
    manuscript.write_text("\\title{before}", encoding="utf-8")
    review = ReviewDecision(
        status="done",
        reason="reviewed before the edit",
        next_action="",
        manuscript_snapshot=manuscript_snapshot(workdir),
    )
    manuscript.write_text("\\title{after}", encoding="utf-8")

    ex_state = _settle(state_root, workdir, review)

    assert review.status == "stale"
    assert ex_state.final_review_status == "stale"
    assert ex_state.final_submission_certified is False
