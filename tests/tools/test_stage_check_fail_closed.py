from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from argus_skill.tools import stage_check


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload))


def _seed_plan_files(root: Path) -> None:
    for path in (
        "research/EXPERIMENT_PLAN.md",
        "research/IDEA_REJECTION_LOG.md",
        "research/CODE_STUDY_NOTES.md",
        "research/BASELINE_AND_BENCHMARK_PLAN.md",
    ):
        _write(root / path)


def _seed_research_files(root: Path, *, bounded: bool = False) -> None:
    state = {
        "current_stage": "research",
        "stage": "research",
        "status": "active",
    }
    if bounded:
        state["scope"] = "bounded_train_free_reward_survey"
    _write_json(root / "research" / "PIPELINE_STATE.json", state)
    for path in (
        "research/RESEARCH_BRIEF.md",
        "research/LITERATURE_GROUNDING.json",
        "research/SOURCE_DISCOVERY.md",
        "research/TREND_INSIGHTS.md",
    ):
        if path.endswith(".json"):
            _write_json(root / path, {})
        else:
            _write(root / path)
    _write(root / "paper" / "refs.bib", "@misc{x, title={x}}\n")


def _seed_benchmark_blockers(root: Path) -> None:
    _write_json(
        root / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "plan_viability": {
                "status": "blocked_plan_stage_benchmark_package_viability",
                "reason": "only one authentic scored family is local",
                "local_authentic_scored_family_count": 1,
                "minimum_required_family_count": 3,
            }
        },
    )
    _write_json(
        root / "experiments" / "BENCHMARK_ACCESS_REVIEW.json",
        {"passed": False, "blockers": [{"family_id": "wise", "id": "license_access_not_cleared"}]},
    )
    _write_json(
        root / "experiments" / "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json",
        {"passed": False, "blockers": [{"family_id": "official_compbench20", "id": "artifact.missing_raw_artifact"}]},
    )
    _write_json(
        root / "experiments" / "BENCHMARK_EVALUATOR_AUTHENTICITY.json",
        {"passed": False, "blockers": [{"family_id": "oneig", "id": "raw_scored_artifact_missing_or_invalid"}]},
    )


def _seed_bounded_survey_artifacts(root: Path, *, source_cards: int = 7) -> None:
    rows = "\n".join(
        f"| {idx} | Paper {idx} | arXiv | Domain | Formula {idx} | Tradeoff {idx} | Bagel comparison {idx} |"
        for idx in range(1, 8)
    )
    improvements = "\n".join(f"{idx}. **Improvement {idx}.**" for idx in range(1, 6))
    _write(
        root / "reports" / "process_terminal_reward_survey_20260605.md",
        "# Survey\n\n"
        "| # | Paper | Source / venue | Domain | Compact reward decomposition | Key tradeoffs and failure modes | Explicit comparison to Bagel components |\n"
        "|---|---|---|---|---|---|---|\n"
        f"{rows}\n\n"
        "## Ranked Bagel Design Improvements\n\n"
        f"{improvements}\n",
    )
    source_dir = root / ".autors" / "unify_RL_argus" / "wiki" / "sources" / "papers"
    for idx in range(1, source_cards + 1):
        _write(
            source_dir / f"paper{idx}.md",
            "---\n"
            f"id: papers/paper{idx}\n"
            f"url: https://arxiv.org/abs/0000.{idx:05d}\n"
            f"title: Paper {idx}\n"
            "---\n\n"
            f"@misc{{paper{idx}, title={{Paper {idx}}}}}\n\n"
            "Decomposition summary: process and terminal reward decomposition.\n\n"
            "Bagel relevance: maps to Bagel reward components.\n",
        )


def test_stage_check_fails_closed_on_blocked_benchmark_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_plan_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "status": "blocked",
            "last_gate": {
                "verdict": "benchmark_deadlock_pivot_plan_blocked_no_training",
                "reason": "selected benchmark package requires unavailable artifacts",
            },
            "stages": {
                "plan": {
                    "status": "blocked",
                    "gate": "NO-GO for benchmark-stage advancement from local artifacts.",
                },
                "benchmark": {"status": "blocked", "reason": "external artifacts missing"},
            },
        },
    )
    _seed_benchmark_blockers(tmp_path)

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "plan"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "Fail-closed pipeline state" in out
    assert "plan viability is blocked" in out
    assert "local authentic scored benchmark family count below minimum: 1 < 3" in out
    assert "benchmark artifact bundle is blocked" in out


def test_stage_check_allows_bounded_reward_survey_with_external_benchmark_blockers(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_research_files(tmp_path, bounded=True)
    _seed_bounded_survey_artifacts(tmp_path)
    _seed_benchmark_blockers(tmp_path)

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "research"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "bounded survey artifacts complete" in out
    assert "External benchmark notes" in out
    assert "plan viability is blocked" in out
    assert "0 fail-closed state finding(s)" in out
    assert "0 bounded-survey finding(s)" in out


@pytest.mark.parametrize("missing", ["report", "source"])
def test_stage_check_fails_bounded_reward_survey_missing_required_artifact(
    tmp_path: Path,
    monkeypatch,
    capsys,
    missing: str,
) -> None:
    _seed_research_files(tmp_path, bounded=True)
    if missing == "report":
        _seed_bounded_survey_artifacts(tmp_path)
        (tmp_path / "reports" / "process_terminal_reward_survey_20260605.md").unlink()
    else:
        _seed_bounded_survey_artifacts(tmp_path, source_cards=6)
    _seed_benchmark_blockers(tmp_path)

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "research"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    if missing == "report":
        assert "bounded survey report is missing or empty" in out
    else:
        assert "bounded survey wiki paper source cards are incomplete" in out


def test_stage_check_allows_minimal_unblocked_plan(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_plan_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "status": "active",
            "stages": {"plan": {"status": "done"}},
        },
    )
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "plan_viability": {
                "status": "ready",
                "local_authentic_scored_family_count": 3,
                "minimum_required_family_count": 3,
            }
        },
    )
    for name in (
        "BENCHMARK_ACCESS_REVIEW.json",
        "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json",
        "BENCHMARK_EVALUATOR_AUTHENTICITY.json",
    ):
        _write_json(tmp_path / "experiments" / name, {"passed": True, "blockers": []})

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "plan"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "Fail-closed pipeline state" not in out
    assert "0 fail-closed state finding(s)" in out
