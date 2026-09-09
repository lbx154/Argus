"""Reuse only evidence whose complete input boundary is known and unchanged."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.manuscript_narrative_runtime import prepare_narrative_snapshot
from argus_skill.core.models import RunnerResult
from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.reviewer._core import ReviewerConfig, _parallel_final_review_passes
from argus_skill.skills.vertical_select import persist_vertical


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
        assert options.sandbox_mode == "read-only"
        assert options.force_safe_mode
        return SimpleNamespace(
            exit_code=1 if self.shared.fail else 0,
            fatal_error="unavailable" if self.shared.fail else None,
            agent_messages=[] if self.shared.fail else [f"{label}: pass"],
            input_tokens=10, cached_input_tokens=0, output_tokens=2,
            reasoning_output_tokens=0, premium_requests=0.0,
        )


@pytest.fixture
def paper_review(tmp_path, monkeypatch):
    from argus_skill.core import manuscript_narrative_runtime

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
    from argus_skill.core import manuscript_narrative_runtime

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
    from argus_skill.core import manuscript_narrative_runtime

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
    assert len(renders) == 1  # Both read-only passes share one immutable render.
    (project / "paper" / "REVIEW.md").write_text("new integrated verdict")
    second = _parallel_final_review_passes(runner, config)
    assert len(runner.shared.calls) == 2
    assert second.input_tokens == second.output_tokens == 0
    assert second.status == "continue"  # Cached passes never certify the mission.
    assert "reviewer-visual: pass" in second.reason
    assert "reviewer-coldread: pass" in second.reason
    assert len(renders) == 1


def test_source_reviewers_get_current_pdf_derivatives_despite_stale_project_previews(
    paper_review, monkeypatch,
):
    from argus_skill.core import manuscript_narrative_runtime

    project, config = paper_review
    config = replace(config, narrative_snapshot_root=None)
    stale = project / "paper/preview/page-01.png"
    stale.parent.mkdir()
    stale.write_bytes(b"Old preview: We derive a new optimizer.")
    renders = []
    original = manuscript_narrative_runtime._prepare_readable_pdf

    def render_current(paper):
        renders.append(paper.parent)
        original(paper)
        (paper / "main.txt").write_text("Current PDF: We adapt the known optimizer.")

    monkeypatch.setattr(manuscript_narrative_runtime, "_prepare_readable_pdf", render_current)

    class CurrentPaperRunner(PaperRunner):
        def fork(self):
            return CurrentPaperRunner(self.shared)

        def run_exec(self, **kwargs):
            label, prompt, options = kwargs["run_label"], kwargs["prompt"], kwargs["options"]
            bundle = renders[-1]
            assert (bundle / "paper/main.pdf").read_bytes() == b"%PDF-current"
            if label in {"reviewer-scientific", "reviewer-language"}:
                # Keep code/raw-evidence access, but supply the actual PDF text
                # and images rather than asking the model to find a preview.
                assert Path(options.working_dir) == project
                assert str(bundle / "paper/main.txt") in prompt
                assert str(bundle / "paper/pages") in prompt
                assert "do not substitute project preview directories" in prompt
                assert (bundle / "paper/main.txt").read_text().startswith("Current PDF")
                assert (bundle / "paper/pages/page-001.png").is_file()
            else:
                assert Path(options.working_dir) == bundle
            return super().run_exec(**kwargs)

    runner = CurrentPaperRunner()
    _parallel_final_review_passes(runner, config)
    assert len(renders) == 1
    _parallel_final_review_passes(runner, config, current_work="New raw data need review.")
    assert len(renders) == 2
    assert runner.shared.calls.count("reviewer-visual") == 1
    assert runner.shared.calls.count("reviewer-scientific") == 2
    assert runner.shared.calls.count("reviewer-language") == 2
    assert stale.read_bytes() == b"Old preview: We derive a new optimizer."
    assert all(not bundle.exists() for bundle in renders)


def test_only_complete_final_assessments_are_forwarded_and_cached(paper_review, monkeypatch):
    _, config = paper_review
    from argus_skill.reviewer import _core

    calls = []

    def assess(_runner, **kwargs):
        assert "complete assessment and all required repairs in your final response" in kwargs["prompt"]
        label = kwargs["run_label"]
        calls.append(label)
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                "I will inspect the PDF.",
                "Tentative concern: the control might be missing.",
                f"{label}: fail. Page 2: labels overlap; separate them. "
                "The control is present in Table 1.",
            ],
            input_tokens=30,
            output_tokens=12,
        )

    monkeypatch.setattr(_core, "gateway_run_exec", assess)
    first = _parallel_final_review_passes(PaperRunner(), config)
    assert len(calls) == 2
    for label in calls:
        assert f"{label}: fail. Page 2: labels overlap; separate them." in first.reason
    assert first.reason.count("The control is present in Table 1.") == 2
    assert "I will inspect" not in first.reason
    assert "Tentative concern" not in first.reason
    assert (first.input_tokens, first.output_tokens) == (60, 24)
    assert first.status == "continue"

    cached = _parallel_final_review_passes(PaperRunner(), config)
    assert len(calls) == 2
    assert cached.reason == first.reason
    assert cached.input_tokens == cached.output_tokens == 0


def test_empty_final_assessment_does_not_promote_progress_to_evidence(paper_review, monkeypatch):
    _, config = paper_review
    from argus_skill.reviewer import _core

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


def test_source_change_is_reviewed_even_when_rendered_bytes_match(paper_review):
    project, config = paper_review
    runner = PaperRunner()
    _parallel_final_review_passes(runner, config)
    (project / "paper" / "main.tex").write_text("a changed scientific claim")
    # Rebuilt output can be byte-identical; the source comparison still matters.
    (project / "paper" / "main.pdf").write_bytes(b"%PDF-current")
    result = _parallel_final_review_passes(runner, config)
    assert runner.shared.calls.count("reviewer-scientificloss") == 1
    assert result.input_tokens == 10
    _parallel_final_review_passes(runner, config)
    assert runner.shared.calls.count("reviewer-scientificloss") == 2


@pytest.mark.parametrize("comparison_mode", [False, True])
def test_current_science_context_reaches_source_passes_without_reopening_pdf_passes(
    paper_review, monkeypatch, comparison_mode,
):
    from argus_skill.reviewer import _core

    project, config = paper_review
    if comparison_mode:
        (project / "paper" / "main.tex").write_text("a revised scientific claim")
        (project / "paper" / "main.pdf").write_bytes(b"%PDF-current")
        source_labels = {"reviewer-scientificloss"}
    else:
        config = replace(config, narrative_snapshot_root=None)
        source_labels = {"reviewer-scientific", "reviewer-language"}
    prompts = []
    original = _core.gateway_run_exec

    def capture(*args, **kwargs):
        prompts.append((kwargs["run_label"], kwargs["prompt"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(_core, "gateway_run_exec", capture)
    runner = PaperRunner()
    first_context = "The old completed panel failed; the replacement is running."
    second_context = "The replacement panel finished; its raw results need verification."
    _parallel_final_review_passes(runner, config, current_work=first_context)
    first_count = len(prompts)
    second = _parallel_final_review_passes(runner, config, current_work=second_context)

    for label, prompt in prompts[:first_count]:
        if label in source_labels:
            assert first_context in prompt
            assert "account is unreviewed" in prompt
        else:
            assert first_context not in prompt
            assert "## Current revision context" not in prompt
    assert {label for label, _ in prompts[first_count:]} == source_labels
    assert all(second_context in prompt for _, prompt in prompts[first_count:])
    assert runner.shared.calls.count("reviewer-visual") == 1
    assert second.status == "continue"
    assert second.input_tokens == 10 * len(source_labels)


def test_round_delivers_current_account_and_live_jobs_to_source_reviewers(
    paper_review, monkeypatch,
):
    import json
    import os

    from argus_skill.core.models import ReviewDecision
    from argus_skill.engineer import round_reviewer
    from argus_skill.engineer.round_config import SupervisedConfig
    from argus_skill.engineer.round_reviewer import RoundReviewerMixin
    from argus_skill.engineer.round_state import RoundLoopState
    from argus_skill.reviewer import _core

    project, config = paper_review
    registry = project / ".argus_subagents"
    registry.mkdir()
    (registry / "replacement-panel.json").write_text(json.dumps({
        "task_id": "replacement-panel", "state": "running", "mode": "direct",
        "pid": os.getpid(),
    }))
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
        record = registry / "replacement-panel.json"
        payload = json.loads(record.read_text())
        payload["state"] = "completed"
        record.write_text(json.dumps(payload))
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
        escalate_hint="", engineer_result=RunnerResult(exit_code=0),
        engineer_message=account, safe_fatal_error=None, process_ownership_note="",
        state=RoundLoopState(), on_event=None,
    )
    for label in ("reviewer-scientific", "reviewer-language"):
        assert account in prompts[label]
        assert "replacement-panel" in prompts[label]
        assert "RUNNING_HEALTHY" in prompts[label]
        assert "ICLR strong accept" in prompts[label]
    assert account not in prompts["reviewer-visual"]
    assert "replacement-panel" not in prompts["reviewer-visual"]
    assert decision.status == "continue"


@pytest.mark.parametrize("change", ["pdf", "policy", "model", "venue"])
def test_changed_pdf_or_policy_invalidates_assessments(paper_review, change):
    project, config = paper_review
    runner = PaperRunner()
    _parallel_final_review_passes(runner, config)
    if change == "pdf":
        (project / "paper" / "main.pdf").write_bytes(b"%PDF-different")
    elif change == "policy":
        config = replace(config, review_policy_context="stricter operator criterion")
    elif change == "venue":
        (project / "research").mkdir()
        (project / "research/VENUE_PROFILE.json").write_text('{"max_pages": 8}')
    else:
        config = replace(config, model="different-reviewer-model")
    _parallel_final_review_passes(runner, config)
    assert runner.shared.calls.count("reviewer-visual") == 2
    assert runner.shared.calls.count("reviewer-coldread") == 2


def test_failed_assessments_are_not_reused(paper_review):
    _, config = paper_review
    runner = PaperRunner()
    runner.shared.fail = True
    first = _parallel_final_review_passes(runner, config)
    assert first.backend_unavailable
    assert not config.paper_pass_cache
    runner.shared.fail = False
    second = _parallel_final_review_passes(runner, config)
    assert not second.backend_unavailable
    assert len(runner.shared.calls) == 4


def test_uncertain_pdf_fingerprint_runs_fresh_isolated_passes(paper_review, monkeypatch):
    _, config = paper_review
    from argus_skill.reviewer import _paper_pass_cache

    monkeypatch.setattr(_paper_pass_cache, "pdf_sha256", lambda _: "")
    runner = PaperRunner()
    _parallel_final_review_passes(runner, config)
    _parallel_final_review_passes(runner, config)
    assert len(runner.shared.calls) == 4
    assert not config.paper_pass_cache


def test_manifest_alone_cannot_suppress_scientific_comparison(paper_review):
    _, config = paper_review
    before = Path(config.narrative_snapshot_root) / "before" / "paper" / "main.tex"
    before.write_text("changed snapshot despite its old manifest")
    runner = PaperRunner()
    _parallel_final_review_passes(runner, config)
    assert "reviewer-scientificloss" in runner.shared.calls


def test_unspecified_model_does_not_reuse_assessments(paper_review):
    _, config = paper_review
    config = replace(config, model=None)
    runner = PaperRunner()
    _parallel_final_review_passes(runner, config)
    _parallel_final_review_passes(runner, config)
    assert len(runner.shared.calls) == 4
    assert not config.paper_pass_cache


def test_pdf_changed_during_assessment_is_not_cached(paper_review, monkeypatch):
    project, config = paper_review
    from argus_skill.reviewer import _core

    original = _core.gateway_run_exec

    def run_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        (project / "paper" / "main.pdf").write_bytes(b"%PDF-changed-during-review")
        return result

    monkeypatch.setattr(_core, "gateway_run_exec", run_then_change)
    _parallel_final_review_passes(PaperRunner(), config)
    assert not config.paper_pass_cache
