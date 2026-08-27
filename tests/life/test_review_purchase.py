from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus_skill.core.manuscript_snapshot import manuscript_snapshot
from argus_skill.verticals._base import load_vertical_contract
from argus_skill.verticals.research.method_freeze import declare_method_freeze
from argus_skill.verticals.research.review_purchase import (
    paper_review_purchase_defer_reason,
)


def _task(*, objective: str = "Run the paper-wide publication assessment.") -> SimpleNamespace:
    return SimpleNamespace(
        title="publication-scale-assessment",
        objective=objective,
        acceptance_check="Assess the final paper.",
    )


def test_unfrozen_current_review_defers_purchase(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("paper\n", encoding="utf-8")
    assessment = tmp_path / "paper/PUBLICATION_SCALE_ASSESSMENT.json"
    assessment.write_text(json.dumps({
        "manuscript_snapshot": manuscript_snapshot(tmp_path),
    }), encoding="utf-8")

    decision = load_vertical_contract(
        "research", project_root=tmp_path
    ).review_purchase(
        project_root=tmp_path,
        task=_task(),
        existing_items=[],
        semantic_duplicate=None,
        stage_reviewed_at=None,
    )

    assert decision is not None
    assert "unexpired current-SHA review" in decision.defer_reason


def test_stale_unfrozen_review_is_fact_not_repurchase_trigger(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("reviewed\n", encoding="utf-8")
    assessment = tmp_path / "paper/PUBLICATION_SCALE_ASSESSMENT.json"
    assessment.write_text(json.dumps({
        "manuscript_snapshot": manuscript_snapshot(tmp_path),
    }), encoding="utf-8")
    manuscript.write_text("small prose edit\n", encoding="utf-8")

    reason = paper_review_purchase_defer_reason(
        _task(), vertical="research", project_root=tmp_path, existing_items=[]
    )

    assert "staleness is a planning fact, not a review trigger" in reason


def test_freeze_releases_prior_review_purchase_deferral(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("paper\n", encoding="utf-8")
    assessment = tmp_path / "paper/PUBLICATION_SCALE_ASSESSMENT.json"
    assessment.write_text(json.dumps({
        "manuscript_snapshot": manuscript_snapshot(tmp_path),
    }), encoding="utf-8")
    declare_method_freeze(
        tmp_path,
        method_identity="final",
        method_description="fixed",
        confirmation_command="python confirm.py",
        data_split_identity="heldout",
    )

    completed = SimpleNamespace(
        id="pre-freeze-review",
        status="done",
        title="publication-scale-assessment",
        objective="Run the paper-wide publication assessment.",
        acceptance_check="Assess the final paper.",
        finished_ts=1.0,
    )
    decision = load_vertical_contract(
        "research", project_root=tmp_path
    ).review_purchase(
        project_root=tmp_path,
        task=_task(),
        existing_items=[completed],
        semantic_duplicate=completed,
        stage_reviewed_at=0.0,
    )

    assert decision is not None
    assert decision.defer_reason == ""
    assert decision.discard_semantic_duplicate is True
    assert decision.release_stage_closing_blocker is True


def test_semantically_equal_pending_review_defers_purchase(tmp_path: Path) -> None:
    pending = SimpleNamespace(id="pending-review", status="pending")

    reason = paper_review_purchase_defer_reason(
        _task(),
        vertical="research",
        project_root=tmp_path,
        existing_items=[pending],
        semantic_duplicate=pending,
    )

    assert reason == (
        "semantically equal paper-wide review is already active (pending-review)"
    )


def test_current_static_review_does_not_defer_missing_model_review(
    tmp_path: Path,
) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("paper\n", encoding="utf-8")
    (tmp_path / "paper/PAPER_INFRASTRUCTURE_REVIEW.json").write_text(
        json.dumps({
            "manuscript_snapshot": manuscript_snapshot(tmp_path),
            "review_method": "deterministic_static_scan",
            "model_review": None,
        }),
        encoding="utf-8",
    )

    completed_static = SimpleNamespace(
        id="static-review",
        status="done",
        title="paper infrastructure review",
        objective="Run deterministic static review.",
        acceptance_check="Assess the final paper.",
    )
    reason = paper_review_purchase_defer_reason(
        _task(objective="Regenerate the model-backed paper review."),
        vertical="research",
        project_root=tmp_path,
        existing_items=[completed_static],
    )

    assert reason == ""


def test_current_model_review_defers_identical_mode_repeat(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("paper\n", encoding="utf-8")
    (tmp_path / "paper/PAPER_INFRASTRUCTURE_REVIEW.json").write_text(
        json.dumps({
            "manuscript_snapshot": manuscript_snapshot(tmp_path),
            "review_method": "model",
            "model_review": {"verdict": "PASS"},
        }),
        encoding="utf-8",
    )

    reason = paper_review_purchase_defer_reason(
        _task(objective="Regenerate the model-backed paper review."),
        vertical="research",
        project_root=tmp_path,
        existing_items=[],
    )

    assert "unexpired current-SHA review" in reason
