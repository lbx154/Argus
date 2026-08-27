from __future__ import annotations

from pathlib import Path

from argus_skill.core.manuscript_snapshot import (
    manuscript_review_status,
    manuscript_snapshot,
)
from argus_skill.core.mission_view._snapshot import _apply_manuscript_review_freshness


def test_matching_manuscript_sha_reports_current(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("matching\n", encoding="utf-8")
    payload = {"manuscript_snapshot": manuscript_snapshot(tmp_path)}

    assert manuscript_review_status(payload, tmp_path)["status"] == "current"


def test_old_sha_reports_stale_and_does_not_enqueue_review(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("reviewed\n", encoding="utf-8")
    payload = {"manuscript_snapshot": manuscript_snapshot(
        tmp_path, recorded_at="2026-08-27T00:00:00+00:00"
    )}
    reviewed = payload["manuscript_snapshot"]["sha256"]
    manuscript.write_text("current\n", encoding="utf-8")

    status = manuscript_review_status(payload, tmp_path)

    assert status["status"] == "stale"
    assert status["message"].startswith(
        f"stale (reviewed {reviewed[:8]} at 2026-08-27T00:00:00+00:00, manuscript now "
    )
    assert not (tmp_path / "backlog.jsonl").exists()


def test_ui_downgrades_stale_certification(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("reviewed\n", encoding="utf-8")
    binding = manuscript_snapshot(tmp_path, recorded_at="review-time")
    manuscript.write_text("changed\n", encoding="utf-8")
    view = {
        "outcome": {
            "final_submission_certified": True,
            "manuscript_snapshot": binding,
        },
        "review": {"status": "done", "reason": "passed"},
        "delivery": {"kind": "submission_certified"},
    }

    _apply_manuscript_review_freshness(view, {"workdir": str(tmp_path)})

    assert view["outcome"]["final_submission_certified"] is False
    assert view["review"]["status"] == "stale"
    assert view["delivery"]["kind"] == "submission_stale"
    assert "stale (reviewed" in view["review"]["reason"]
