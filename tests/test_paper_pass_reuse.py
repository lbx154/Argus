"""Reuse only evidence whose complete input boundary is known and unchanged."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.manuscript_narrative_runtime import prepare_narrative_snapshot
from argus.core.models import RunnerResult
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.reviewer._core import ReviewerConfig, _parallel_final_review_passes
from argus.skills.vertical_select import persist_vertical


class PaperRunner:
    def __init__(self, shared=None):
        self.shared = shared or SimpleNamespace(calls=[], fail=False)

    def fork(self):
        return PaperRunner(self.shared)

    def run_exec(self, **kwargs):
        label = kwargs["run_label"]
        options = kwargs["options"]
        self.shared.calls.append(label)
        if label in {"reviewer-visual", "reviewer-coldread"}:
            files = {p.relative_to(options.working_dir).as_posix()
                     for p in Path(options.working_dir).rglob("*") if p.is_file()}
            assert files == {"paper/main.pdf", "paper/main.txt", "paper/pages/page-001.png"}
            assert "paper/pages/page-*.png" in kwargs["prompt"]
        if label == "reviewer-visual":
            assert "alone must not reopen the design search" in kwargs["prompt"]
            assert "paper's actual template and column layout" in kwargs["prompt"]
            assert "natural-language assessment" in kwargs["prompt"]
            assert "that alone is not broken reading order" in kwargs["prompt"]
            assert "polish, not grounds to fail the paper" in kwargs["prompt"]
        # The Engineer's access (here the config defaults), never a read-only mode.
        assert options.sandbox_mode is None and not options.force_safe_mode
        return SimpleNamespace(
            exit_code=1 if self.shared.fail else 0,
            fatal_error="unavailable" if self.shared.fail else None,
            agent_messages=[] if self.shared.fail else [f"{label}: pass"],
            input_tokens=10, cached_input_tokens=0, output_tokens=2,
            reasoning_output_tokens=0, premium_requests=0.0,
        )


@pytest.fixture
def paper_review(tmp_path, monkeypatch):
    from argus.core import manuscript_narrative_runtime

    # These tests exercise caching and dispatch; real PDF rendering is covered
    # by the readable-workspace tests with an actual multi-page PDF.
    def render_fixture(paper):
        (paper / "pages").mkdir()
        (paper / "pages" / "page-001.png").write_bytes(b"rendered-page")
        (paper / "main.txt").write_text("PDF-derived text")

    monkeypatch.setattr(manuscript_narrative_runtime, "_prepare_readable_pdf", render_fixture)
    project, state = tmp_path / "project", tmp_path / "state"
    paper = project / "paper"
    paper.mkdir(parents=True)
    (paper / "main.tex").write_text("a scientific claim", encoding="utf-8")
    (paper / "main.pdf").write_bytes(b"%PDF-current")
    persist_vertical(state, "research")
    payload = read_pipeline_state(state)
    payload.update(current_stage="review", current_verdict="continue")
    write_pipeline_state(state, payload)
    snapshot = prepare_narrative_snapshot(project, state, mission_id="one")
    return project, ReviewerConfig(
        model="test-reviewer", active_vertical="research", working_dir=str(project),
        vertical_state_root=str(state), narrative_snapshot_root=str(snapshot),
    )


def test_unavailable_page_rendering_never_launches_or_caches_a_visual_pass(
    paper_review, monkeypatch,
):
    from argus.core import manuscript_narrative_runtime

    def unavailable(_paper):
        raise RuntimeError("PDF renderer unavailable")

    monkeypatch.setattr(manuscript_narrative_runtime, "_prepare_readable_pdf", unavailable)
    _, config = paper_review
    runner = PaperRunner()
    result = _parallel_final_review_passes(runner, config)
    assert result.backend_unavailable
    assert "PDF renderer unavailable" in result.reason
    assert runner.shared.calls == []
    assert not config.paper_pass_cache


def test_unchanged_paper_skips_loss_and_reuses_pdf_assessments(paper_review, monkeypatch):
    from argus.core import manuscript_narrative_runtime

    renders = []
    original = manuscript_narrative_runtime._prepare_readable_pdf

    def render_once(paper):
        renders.append(paper)
        original(paper)

    monkeypatch.setattr(manuscript_narrative_runtime, "_prepare_readable_pdf", render_once)
    project, config = paper_review
    runner = PaperRunner()
    first = _parallel_final_review_passes(runner, config)
    assert sorted(runner.shared.calls) == ["reviewer-coldread", "reviewer-visual"]
    assert first.input_tokens == 20
    assert "not scientific correctness" in first.reason
    assert len(renders) == 1  # Both PDF-only passes share one immutable render.
    (project / "paper" / "REVIEW.md").write_text("new integrated verdict")
    second = _parallel_final_review_passes(runner, config)
    assert len(runner.shared.calls) == 2
    assert second.input_tokens == second.output_tokens == 0
    assert second.status == "continue"  # Cached passes never certify the mission.
    assert "reviewer-visual: pass" in second.reason
    assert "reviewer-coldread: pass" in second.reason
    assert len(renders) == 1


def test_empty_final_assessment_does_not_promote_progress_to_evidence(paper_review, monkeypatch):
    _, config = paper_review
    from argus.reviewer import _core

    monkeypatch.setattr(
        _core, "gateway_run_exec",
        lambda *_args, **_kwargs: RunnerResult(
            exit_code=0, agent_messages=["I will inspect the PDF.", "   "],
        ),
    )
    result = _parallel_final_review_passes(PaperRunner(), config)
    assert result.backend_unavailable
    assert "returned no assessment" in result.reason
    assert not config.paper_pass_cache


def test_round_delivers_current_account_to_source_reviewers(
    paper_review, monkeypatch,
):

    from argus.core.models import ReviewDecision
    from argus.engineer import round_reviewer
    from argus.engineer.round_config import SupervisedConfig
    from argus.engineer.round_reviewer import RoundReviewerMixin
    from argus.engineer.round_state import RoundLoopState
    from argus.reviewer import _core

    project, config = paper_review
    account = "The real counterexample is repaired; the full replacement is pending."
    prompts = {}
    original = _core.gateway_run_exec
    live_directive = ["Initial operator guidance."]
    monkeypatch.setattr(
        round_reviewer, "_active_manager_directive_for_reviewer",
        lambda _config: list(live_directive),
    )

    def capture(*args, **kwargs):
        prompts[kwargs["run_label"]] = kwargs["prompt"]
        return original(*args, **kwargs)

    monkeypatch.setattr(_core, "gateway_run_exec", capture)
    original_passes = _core._parallel_final_review_passes

    def finish_passes(*args, **kwargs):
        findings = original_passes(*args, **kwargs)
        live_directive[:] = ["New observation: distinguish old failed outputs from the replacement."]
        return findings

    monkeypatch.setattr(_core, "_parallel_final_review_passes", finish_passes)

    class IntegratedReviewer:
        runner = PaperRunner()

        def evaluate(self, **kwargs):
            assert kwargs["main_summary"] == account
            assert "Independent final-paper passes" in kwargs["background_context"]
            assert "## External work status" not in kwargs["background_context"]
            assert kwargs["operator_messages"] == live_directive
            assert "New observation" in kwargs["operator_messages"][0]
            return ReviewDecision(
                status="continue", reason="Verify the pending evidence.",
                next_action="Inspect the completed replacement before updating the paper.",
            )

    owner = SimpleNamespace(reviewer=IntegratedReviewer(), reviewer_config=config)
    decision = RoundReviewerMixin._call_reviewer_once(
        owner, objective="Bring the paper to ICLR strong accept", original_objective=None,
        round_index=1, supervised_config=SupervisedConfig(), workdir=project,
        scope="mission", checkpoint_path=None, reviewer_skill_block=None,
        engineer_result=RunnerResult(exit_code=0),
        engineer_message=account, safe_fatal_error=None, process_ownership_note="",
        state=RoundLoopState(), on_event=None,
    )
    for label in ("reviewer-scientific", "reviewer-language"):
        assert account in prompts[label]
        assert "ICLR strong accept" in prompts[label]
    assert account not in prompts["reviewer-visual"]
    assert decision.status == "continue"


def test_unreadable_pdf_facts_run_fresh_isolated_passes(paper_review, monkeypatch):
    _, config = paper_review
    from argus.reviewer import _paper_pass_cache

    monkeypatch.setattr(_paper_pass_cache, "pdf_facts", lambda _: [])
    runner = PaperRunner()
    _parallel_final_review_passes(runner, config)
    _parallel_final_review_passes(runner, config)
    assert len(runner.shared.calls) == 4
    assert not config.paper_pass_cache
