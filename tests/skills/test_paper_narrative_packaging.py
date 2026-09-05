from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import argus_skill
from argus_skill.core.manuscript_narrative_runtime import (
    isolated_pdf_workspace,
    prepare_narrative_snapshot,
    rendered_pdf_freshness,
    snapshot_after_edit,
)
from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.reviewer._core import ReviewerConfig, _parallel_final_review_passes
from argus_skill.roles.prompts import ChecklistMode, resolve_role_prompt
from argus_skill.roles.prompts.engineer import NARRATIVE_EDIT, mission_request
from argus_skill.roles.prompts.reviewer import COLD_READ, evaluate_request
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical_contract
from argus_skill.verticals.research.academic_language_review import (
    _abstract_quality_issue_specs,
    _neutral_language_facts,
    generate_academic_language_review,
)
from argus_skill.verticals.research.prompt_policy import render_role_prompt_fragment
from argus_skill.verticals.research.stages import STAGE_CHECKLISTS


def _research_skill(relative: str) -> str:
    root = Path(argus_skill.__file__).parent / "verticals" / "research" / "skills"
    return " ".join((root / relative).read_text(encoding="utf-8").lower().split())


def _paper_stage() -> str:
    return " ".join(item.statement.lower() for item in STAGE_CHECKLISTS["paper"])


def test_drafting_keeps_existing_density_requirements() -> None:
    drafting = _research_skill("engineer/venue-paper-drafting.md")
    playbook = _research_skill("research-paper-playbook.md")

    for text in (drafting, playbook):
        assert "five-sentence abstract" in text
        assert "at least 170 words" in text
        assert "numerical takeaway" in text
        assert "exact headline" in text


def test_drafting_selects_and_packages_without_dropping_coverage() -> None:
    drafting = _research_skill("engineer/venue-paper-drafting.md")

    for role in (
        "headline",
        "mechanism",
        "disambiguating control",
        "scope-changing",
        "completeness",
    ):
        assert role in drafting
    assert "complete method, baseline, control" in drafting
    assert "selection changes emphasis" not in drafting
    assert "packaging, not deletion" in drafting


def test_paper_stage_allows_role_bearing_repetition_not_matrix_recital() -> None:
    paper = _paper_stage()

    assert "five-sentence" in paper
    assert "at-least-170-word" in paper
    assert "same headline number may recur" in paper
    assert "mechanical repetition cap" in paper
    assert "full result matrix" in paper


def test_paper_engineer_prompt_carries_packaging_contract() -> None:
    prompt = render_role_prompt_fragment(
        role="engineer",
        operation="mission",
        stage="paper",
        scope="",
        project_root=None,
    ).lower()

    assert "paper evidence selection and packaging" in prompt
    assert "five-sentence abstract of at least 170 words" in prompt
    assert "numerical takeaway" in prompt
    assert "same exact headline number may recur" in prompt
    assert "universal repetition cap" in prompt
    assert "evidence-chain language" in prompt


def test_integrated_reviewer_judges_roles_not_raw_repetition_count() -> None:
    prompt = render_role_prompt_fragment(
        role="reviewer",
        operation="evaluate",
        stage="review",
        scope="final_submission",
        project_root=None,
    ).lower()

    assert "at-least-170-word abstract" in prompt
    assert "numerical-caption requirements" in prompt
    assert "allow exact headline numbers to recur" in prompt
    assert "not to repetition by a mechanical count" in prompt


def test_operation_prompts_enforce_narrative_and_cold_read_input_boundaries(
    tmp_path: Path,
) -> None:
    (tmp_path / "HANDOFF.md").write_text(
        "# HANDOFF — PAPER\n\nUNIQUE_EVIDENCE_ROLE_MAP",
        encoding="utf-8",
    )
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "REVIEW.md").write_text("SECRET_PRIOR_REVIEW", encoding="utf-8")

    narrative = render_role_prompt_fragment(
        role="engineer",
        operation=NARRATIVE_EDIT,
        stage="review",
        scope="",
        project_root=tmp_path,
    )
    cold = render_role_prompt_fragment(
        role="reviewer",
        operation=COLD_READ,
        stage="review",
        scope="",
        project_root=tmp_path,
    )

    assert "UNIQUE_EVIDENCE_ROLE_MAP" in narrative
    assert "SECRET_PRIOR_REVIEW" not in narrative
    assert "Fresh-context Narrative Editor" in narrative
    assert "Rendered-PDF cold read" in cold
    assert "UNIQUE_EVIDENCE_ROLE_MAP" not in cold
    assert "SECRET_PRIOR_REVIEW" not in cold


def test_prompt_catalog_accepts_research_operations(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research")
    narrative = resolve_role_prompt(
        mission_request(
            tmp_path,
            vertical="research",
            stage="review",
            operation=NARRATIVE_EDIT,
        )
    )
    cold = resolve_role_prompt(
        evaluate_request(
            tmp_path,
            vertical="research",
            stage="review",
            checklist_mode=ChecklistMode.NONE,
            operation=COLD_READ,
        )
    )

    assert narrative.operation == NARRATIVE_EDIT
    assert cold.operation == COLD_READ
    assert "narrative_edit" in narrative.fragment_ids[-1]
    assert "cold_read" in cold.fragment_ids[-1]

    contract = load_vertical_contract("research", project_root=tmp_path)
    assert contract.engineer_operation("paper") == "author_draft"
    assert contract.engineer_operation("review") == "narrative_edit"


def test_internal_snapshot_is_immutable_and_cold_workspace_contains_only_pdf(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    paper = project / "paper"
    paper.mkdir(parents=True)
    (paper / "main.tex").write_text("before", encoding="utf-8")
    (paper / "main.pdf").write_bytes(b"%PDF-before")
    (paper / "REVIEW.md").write_text("not snapshot input", encoding="utf-8")

    root = prepare_narrative_snapshot(project, state, mission_id="mission/one")
    (paper / "main.tex").write_text("after", encoding="utf-8")
    pair = snapshot_after_edit(project, root)

    assert (pair.before_paper / "main.tex").read_text(encoding="utf-8") == "before"
    assert (pair.after_paper / "main.tex").read_text(encoding="utf-8") == "after"
    assert pair.before_sha256 != pair.after_sha256
    assert not (pair.before_paper / "REVIEW.md").exists()
    assert not (project / ".narrative-runtime").exists()
    assert rendered_pdf_freshness(project)[0] is False

    with isolated_pdf_workspace(project) as cold_root:
        files = sorted(
            path.relative_to(cold_root).as_posix()
            for path in cold_root.rglob("*")
            if path.is_file()
        )
        assert files == ["paper/main.pdf"]


def test_post_edit_passes_use_snapshot_and_pdf_only_cold_workspace(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    paper = project / "paper"
    paper.mkdir(parents=True)
    (paper / "main.tex").write_text("before 0.970", encoding="utf-8")
    (paper / "main.pdf").write_bytes(b"%PDF-current")
    (paper / "REVIEW.md").write_text("SECRET_PRIOR_REVIEW", encoding="utf-8")
    persist_vertical(state, "research")
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    payload["current_verdict"] = "in_progress"
    write_pipeline_state(state, payload)
    snapshot = prepare_narrative_snapshot(project, state, mission_id="run-1")
    (paper / "main.tex").write_text("after 0.970", encoding="utf-8")
    (paper / "main.pdf").write_bytes(b"%PDF-after-edit")

    class Runner:
        def __init__(self, shared=None) -> None:
            self.shared = shared or SimpleNamespace(
                barrier=threading.Barrier(3),
                lock=threading.Lock(),
                calls={},
            )

        def fork(self):
            return Runner(self.shared)

        def run_exec(self, **kwargs):
            options = kwargs["options"]
            working_dir = Path(options.working_dir)
            files = sorted(
                path.relative_to(working_dir).as_posix()
                for path in working_dir.rglob("*")
                if path.is_file()
            )
            with self.shared.lock:
                self.shared.calls[kwargs["run_label"]] = {
                    "prompt": kwargs["prompt"],
                    "working_dir": working_dir,
                    "files": files,
                }
            self.shared.barrier.wait(timeout=2)
            return SimpleNamespace(
                exit_code=0,
                fatal_error=None,
                stop_kind=None,
                agent_messages=["pass"],
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                premium_requests=0.0,
            )

    runner = Runner()
    decision = _parallel_final_review_passes(
        runner,
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(project),
            artifact_root=str(project),
            vertical_state_root=str(state),
            narrative_snapshot_root=str(snapshot),
        ),
    )

    assert decision is not None
    assert "ScientificLoss:" in decision.reason
    assert "ColdRead:" in decision.reason
    assert set(runner.shared.calls) == {
        "reviewer-scientificloss",
        "reviewer-visual",
        "reviewer-coldread",
    }
    science_prompt = runner.shared.calls["reviewer-scientificloss"]["prompt"]
    cold_call = runner.shared.calls["reviewer-coldread"]
    assert str(snapshot / "before" / "paper") in science_prompt
    assert cold_call["files"] == ["paper/main.pdf"]
    assert cold_call["working_dir"] != project
    assert "SECRET_PRIOR_REVIEW" not in cold_call["prompt"]


def test_narrative_measurements_are_candidates_not_repetition_penalties() -> None:
    tex = r"""
    \begin{abstract}The score is 0.970.\end{abstract}
    A claim-bearing validation gate passed all checks. The control, ablation,
    positive control, and robustness check support the alternative explanation.
    Results are 0.970, 0.925, 0.708, and 39.8.
    \caption{Accuracy is 0.970, which supports the main inference.}
    """
    facts = _neutral_language_facts(tex)["narrative_packaging"]

    assert facts["audit_language_count"] >= 1
    assert facts["control_checklist_candidates"]
    assert facts["dense_numeric_sentence_candidates"]
    assert facts["numerical_caption_count"] == 1
    assert "not defects" in facts["interpretation"]


def test_language_review_persistence_is_explicit_opt_in() -> None:
    assert generate_academic_language_review.__kwdefaults__["write"] is False


def test_abstract_shape_and_word_floor_are_reviewer_judgment() -> None:
    venue = SimpleNamespace(reviewer_persona="Test venue")
    short_five = " ".join(
        f"Sentence {index} has selected evidence and meaning."
        for index in range(1, 6)
    )
    long_six = " ".join(
        ("Evidence " * 30).strip() + f" supports claim {index}."
        for index in range(1, 7)
    )

    short_codes = {
        code
        for code, _message, _penalty, _cap in _abstract_quality_issue_specs(
            short_five, venue=venue
        )
    }
    long_codes = {
        code
        for code, _message, _penalty, _cap in _abstract_quality_issue_specs(
            long_six, venue=venue
        )
    }

    assert "thin_abstract" not in short_codes
    assert "weak_abstract_shape" not in short_codes
    assert "weak_abstract_shape" not in long_codes
    assert "thin_abstract" not in long_codes
