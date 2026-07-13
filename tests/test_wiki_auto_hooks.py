"""Tests for argus_skill.wiki.auto_hooks — harness-driven wiki maintenance."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import ReviewDecision, RoundRecord
from argus_skill.wiki.auto_hooks import discover_wikis, run_post_mission_hooks
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.lifecycle import (
    capture_reviewed_round,
    evolve_wikis_after_mission,
)

SAMPLE_BIB = """
@article{smith2025attention,
  title={Visual attention in VLMs},
  author={Smith, A.},
  year={2025},
  url={https://arxiv.org/abs/2501.12345}
}

@misc{lee2026probing,
  title={Probing hallucination},
  author={Lee, B.},
  year={2026},
  url={https://arxiv.org/abs/2603.99999}
}
""".strip()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    init_wiki(project="demo", base=tmp_path)
    return tmp_path


def test_discover_wikis_finds_initialized_wiki(project: Path):
    found = discover_wikis(project)
    assert len(found) == 1
    assert found[0] == project / ".autors" / "demo" / "wiki"


def test_discover_skips_uninitialized_dir(tmp_path: Path):
    (tmp_path / ".autors" / "scaffold-only" / "wiki").mkdir(parents=True)
    assert discover_wikis(tmp_path) == []


def test_discover_returns_empty_when_no_autors(tmp_path: Path):
    assert discover_wikis(tmp_path) == []


def test_run_hooks_backfills_refs_bib(project: Path):
    paper_dir = project / "paper"
    paper_dir.mkdir()
    (paper_dir / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    summary = run_post_mission_hooks(project, mission_id="t1", success=True)
    [(wiki_str, info)] = summary.items()
    assert info["sources_written"] == 2, info
    # ...and the mechanical lift turned each new source into a scratch card.
    assert info["scratch_written"] == 2, info
    wiki = Path(wiki_str)
    pages = list((wiki / "pages" / "techniques").glob("*.md"))
    assert len(pages) == 2
    sample = pages[0].read_text(encoding="utf-8")
    assert "status: scratch" in sample
    assert "Auto-created by wiki-auto-hook" in sample


def test_run_hooks_idempotent(project: Path):
    (project / "paper").mkdir()
    (project / "paper" / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    run_post_mission_hooks(project, mission_id="t1", success=True)
    s2 = run_post_mission_hooks(project, mission_id="t2", success=True)
    # Second pass writes nothing new — sources are immutable, scratch
    # cards already exist.
    [(_, info)] = s2.items()
    assert info["sources_written"] == 0
    assert info["scratch_written"] == 0


def test_mission_close_writes_immutable_reviewer_run_source(project: Path):
    review = SimpleNamespace(
        status="done",
        reason="Certified the exact finite classification.",
        verification_summary="Independent replay passed.",
        failure_cause="",
        next_action="Audit the general-rank extension.",
        research_result={
            "result_class": "theorem",
            "correctness": "verified",
        },
        planner_report={
            "forward_progress": True,
            "headline": "Finite classification certified",
            "blocker": "General rank remains open",
            "recommended_next": "Audit the general-rank extension.",
            "evidence_files": [
                {"path": "research/RESULT.md", "why": "reviewed theorem"},
            ],
        },
    )
    rounds = [SimpleNamespace(review=review)]

    summary = evolve_wikis_after_mission(
        rounds=rounds,
        workdir=project,
        task="Prove the finite classification.",
        mission_id="m-reviewed",
        success=True,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        apply_ops_enabled=False,
        auto_compact_enabled=False,
    )

    wiki = project / ".autors" / "demo" / "wiki"
    source = wiki / "sources" / "runs" / "m-reviewed.md"
    assert summary["sources"] == 1
    assert source.exists()
    text = source.read_text(encoding="utf-8")
    assert "outcome: success" in text
    assert "Certified the exact finite classification." in text
    assert "research/RESULT.md" in text
    assert "Audit the general-rank extension." in text
    assert "closed_at:" in text

    repeated = evolve_wikis_after_mission(
        rounds=rounds,
        workdir=project,
        task="Prove the finite classification.",
        mission_id="m-reviewed",
        success=True,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        apply_ops_enabled=False,
        auto_compact_enabled=False,
    )
    assert repeated["sources"] == 0


def test_reviewer_rounds_are_captured_continuously_without_close_duplicate(
    project: Path,
):
    first = RoundRecord(
        round_index=1,
        engineer_message="round one",
        engineer_exit_code=0,
        review=ReviewDecision(
            status="continue",
            reason="Verified a useful lemma; theorem remains open.",
            next_action="Prove the remaining branch.",
            planner_report={
                "forward_progress": True,
                "headline": "Lemma verified",
                "blocker": "Remaining branch",
                "recommended_next": "Prove the remaining branch.",
                "evidence_files": [],
            },
        ),
    )
    second = RoundRecord(
        round_index=2,
        engineer_message="round two",
        engineer_exit_code=0,
        review=ReviewDecision(
            status="done",
            reason="All branches verified.",
            next_action="",
            planner_report={
                "forward_progress": True,
                "headline": "Proof complete",
                "blocker": "",
                "recommended_next": "",
                "evidence_files": [],
            },
        ),
    )

    first_result = capture_reviewed_round(
        record=first,
        workdir=project,
        task="prove theorem",
        mission_id="m-live",
    )
    second_result = capture_reviewed_round(
        record=second,
        workdir=project,
        task="prove theorem",
        mission_id="m-live",
    )

    runs = project / ".autors" / "demo" / "wiki" / "sources" / "runs"
    assert first_result["sources"] == 1
    assert second_result["sources"] == 1
    assert (runs / "m-live-r001.md").exists()
    assert (runs / "m-live-r002.md").exists()

    close = evolve_wikis_after_mission(
        rounds=[first, second],
        workdir=project,
        task="prove theorem",
        mission_id="m-live",
        success=True,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        apply_ops_enabled=False,
        auto_compact_enabled=False,
    )
    assert close["sources"] == 0
    assert len(list(runs.glob("m-live*.md"))) == 2


def test_run_source_failure_is_isolated_from_other_hooks(project: Path):
    events: list[dict] = []
    review = SimpleNamespace(
        status="done",
        reason="reviewed",
        verification_summary="",
        failure_cause="",
        next_action="",
        research_result={},
        planner_report={
            "forward_progress": True,
            "non_json_value": date(2026, 7, 13),
        },
    )

    summary = evolve_wikis_after_mission(
        rounds=[SimpleNamespace(review=review)],
        workdir=project,
        task="task",
        mission_id=".invalid",
        success=True,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        apply_ops_enabled=False,
        auto_compact_enabled=False,
        on_event=events.append,
    )

    assert summary["sources"] == 0
    assert any(
        event.get("type") == "wiki.hook.warning"
        and event.get("operation") == "write_run_source"
        for event in events
    )


def test_mission_close_skips_synthetic_backend_failure_review(project: Path):
    review = SimpleNamespace(
        status="blocked",
        reason="reviewer backend unavailable",
        verification_summary="",
        failure_cause="environmental",
        next_action="retry",
        math_result={},
        planner_report={},
        backend_unavailable=True,
    )

    summary = evolve_wikis_after_mission(
        rounds=[SimpleNamespace(review=review)],
        workdir=project,
        task="task",
        mission_id="m-backend-failure",
        success=False,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        apply_ops_enabled=False,
        auto_compact_enabled=False,
    )

    assert summary["sources"] == 0
    runs = project / ".autors" / "demo" / "wiki" / "sources" / "runs"
    assert not list(runs.glob("m-backend-failure*.md"))


def test_wiki_evolution_compresses_cold_retired_history(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_WIKI_RETIRED_HOT_VERSIONS", "1")
    retired = project / ".autors" / "demo" / "wiki" / "pages" / "_retired" / "techniques"
    retired.mkdir(parents=True)
    (retired / "page.md").write_text("first", encoding="utf-8")
    (retired / "page.2.md").write_text("second", encoding="utf-8")
    events = []

    summary = evolve_wikis_after_mission(
        rounds=[],
        workdir=project,
        task="task",
        mission_id="m1",
        success=True,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        apply_ops_enabled=False,
        auto_compact_enabled=False,
        on_event=events.append,
    )

    assert summary["retired_compressed"] == 1
    assert (retired / "page.md.gz").exists()
    assert (retired / "page.2.md").exists()
    assert any(event["type"] == "wiki.retired.compressed" for event in events)


def test_run_hooks_skips_when_no_refs_bib(project: Path):
    s = run_post_mission_hooks(project, mission_id="t1", success=True)
    [(_, info)] = s.items()
    assert info["sources_written"] == 0
    assert info["scratch_written"] == 0


def test_run_hooks_fails_open_on_broken_bib(project: Path, capsys):
    (project / "paper").mkdir()
    # Garbage bib — should produce a warning event, not raise.
    (project / "paper" / "refs.bib").write_text("@@@ not valid bibtex @@@",
                                                  encoding="utf-8")
    events: list[dict] = []
    s = run_post_mission_hooks(
        project, mission_id="t1", success=True, emit=events.append
    )
    # No raise — and either succeeds with zero sources or emits a warning.
    assert s  # one wiki discovered
    # warnings (if any) are isolated; never blocks
