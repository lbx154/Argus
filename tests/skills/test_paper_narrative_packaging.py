from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from argus_skill.roles.prompts.reviewer import (
    COLD_READ,
    SCIENCE_LOSS_CHECK,
    evaluate_request,
)
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


def test_drafting_lets_the_venue_and_claim_decide_the_form() -> None:
    drafting = _research_skill("engineer/venue-paper-drafting.md")
    playbook = _research_skill("research-paper-playbook.md")

    for text in (drafting, playbook):
        assert "strong accepted paper at the selected venue" in text
        assert "no house quota" in text
        assert "170" not in text
        assert "five-sentence" not in text
    assert "hedge a sentence only when the evidence for that sentence" in drafting
    assert "internal task routing, review status and process bookkeeping" in drafting
    assert "preserve established scientific terminology" in drafting
    assert "do not rename legitimate terms to satisfy a word list" in drafting
    assert "legitimate paper when its evidence is as complete" in drafting


def test_drafting_follows_the_craft_reference() -> None:
    root = Path(argus_skill.__file__).parent / "verticals" / "research" / "skills"
    craft = (root / "engineer" / "references" / "paper-writing-craft.md").read_text(
        encoding="utf-8"
    )
    lowered = " ".join(craft.lower().split())
    drafting = _research_skill("engineer/venue-paper-drafting.md")
    playbook = _research_skill("research-paper-playbook.md")
    language = _research_skill("reviewer/venue-academic-language-review.md")

    for text in (drafting, playbook, language):
        assert "references/paper-writing-craft.md" in text
    assert "the introduction is written twice" in lowered
    assert "takeaway" in lowered
    assert "compress after expanding" in lowered
    assert "nothing here is a quota" in lowered
    assert "170" not in lowered and "five-sentence" not in lowered
    assert "draft 0 introduction" in drafting
    assert "abstract" in drafting and "last" in drafting
    assert "read as a stranger" in language


def test_no_research_prompt_or_skill_carries_a_writing_quota() -> None:
    root = Path(argus_skill.__file__).parent / "verticals" / "research"
    offenders = []
    for path in list(root.rglob("*.py")) + list(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        if "five-sentence" in text or "170-word" in text or "at least 170" in text:
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


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
    assert "appendix changes its placement; it does not remove it" in drafting


def test_paper_stage_allows_role_bearing_repetition_not_matrix_recital() -> None:
    paper = _paper_stage()

    assert "no house quota" in paper
    assert "170" not in paper
    assert "same headline number may recur" in paper
    assert "mechanical repetition cap" in paper
    assert "full result matrix" in paper


def test_paper_engineer_prompt_carries_the_writing_standard() -> None:
    prompt = render_role_prompt_fragment(
        role="engineer",
        operation="mission",
        stage="paper",
        scope="",
        project_root=None,
    ).lower()

    assert "paper writing standard" in prompt
    assert "no house quota" in prompt
    assert "let the claim decide the form" in prompt
    assert "170" not in prompt
    assert "a headline number may recur" in prompt
    assert "method-by-dataset-by-metric" in prompt
    assert "translate any workflow or evidence-bookkeeping language" in prompt
    assert "preserve established scientific terminology" in prompt
    assert "`certified bounds`, `communication gates`, `communication rounds`" in prompt
    assert "never appear in the manuscript" not in prompt


def test_integrated_reviewer_judges_as_a_venue_reviewer() -> None:
    prompt = render_role_prompt_fragment(
        role="reviewer",
        operation="evaluate",
        stage="review",
        scope="final_submission",
        project_root=None,
    ).lower()

    assert "as a reviewer at the selected venue would" in prompt
    assert "do not enforce an abstract length" in prompt
    assert "170" not in prompt
    assert "headline figure that recurs" in prompt
    assert "do not ask for more hedging than the evidence requires" in prompt
    assert "judge terms by their scientific meaning, not a banned-word list" in prompt
    assert "preserve legitimate scientific terminology" in prompt


def test_operation_prompts_enforce_narrative_and_cold_read_input_boundaries(
    tmp_path: Path,
) -> None:
    (tmp_path / "RESEARCH_NOTES.md").write_text(
        "# Research notes — Paper stage\n\nUNIQUE_EVIDENCE_ROLE_MAP",
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
    science = resolve_role_prompt(
        evaluate_request(
            tmp_path, vertical="research", stage="review",
            checklist_mode=ChecklistMode.NONE, operation=SCIENCE_LOSS_CHECK,
        )
    )
    integrated = resolve_role_prompt(
        evaluate_request(
            tmp_path, vertical="research", stage="review",
            checklist_mode=ChecklistMode.NONE,
        )
    )
    assert cold.role_banner == render_role_prompt_fragment(
        role="reviewer", operation=COLD_READ, stage="review", scope="",
        project_root=tmp_path,
    )
    assert science.role_banner == render_role_prompt_fragment(
        role="reviewer", operation=SCIENCE_LOSS_CHECK, stage="review", scope="",
        project_root=tmp_path,
    )
    assert "overwrite paper/REVIEW.md" not in cold.role_banner
    assert "overwrite paper/REVIEW.md" not in science.role_banner
    assert contract.banner("reviewer") in integrated.role_banner
    assert "you own paper/REVIEW.md" in integrated.role_banner
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.core import manuscript_narrative_runtime

    def render_fixture(paper):
        (paper / "pages").mkdir()
        (paper / "pages" / "page-001.png").write_bytes(b"rendered-page")
        (paper / "main.txt").write_text("PDF-derived text")

    monkeypatch.setattr(manuscript_narrative_runtime, "_prepare_readable_pdf", render_fixture)
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
    assert cold_call["files"] == [
        "paper/main.pdf", "paper/main.txt", "paper/pages/page-001.png",
    ]
    assert cold_call["working_dir"] != project
    assert "SECRET_PRIOR_REVIEW" not in cold_call["prompt"]


def test_readable_review_workspace_renders_every_page_from_only_the_current_pdf(
    tmp_path: Path,
) -> None:
    fitz = pytest.importorskip("pymupdf")
    project = tmp_path / "project"
    paper = project / "paper"
    paper.mkdir(parents=True)
    (paper / "main.tex").write_text("PRIVATE_SOURCE_DO_NOT_COPY")
    (paper / "REVIEW.md").write_text("STALE_PASS_DO_NOT_COPY")
    (paper / "preview").mkdir()
    (paper / "preview" / "page-001.png").write_bytes(b"STALE_PREVIEW")
    with fitz.open() as doc:
        doc.new_page(width=300, height=400).insert_text((25, 40), "Current rendered finding")
        doc.new_page(width=400, height=300).insert_text((25, 40), "Second landscape page")
        doc.save(paper / "main.pdf")
    original = (paper / "main.pdf").read_bytes()

    with isolated_pdf_workspace(project, readable=True) as isolated:
        isolated_paper = isolated / "paper"
        assert (isolated_paper / "main.pdf").read_bytes() == original
        text = (isolated_paper / "main.txt").read_text()
        assert "## PDF page 1" in text and "Current rendered finding" in text
        assert "## PDF page 2" in text and "Second landscape page" in text
        assert "PRIVATE_SOURCE" not in text and "STALE_PASS" not in text
        images = sorted((isolated_paper / "pages").glob("*.png"))
        assert [p.name for p in images] == ["page-001.png", "page-002.png"]
        assert [(fitz.Pixmap(str(p)).width, fitz.Pixmap(str(p)).height) for p in images] == [
            (600, 800), (800, 600),
        ]
        assert not (isolated_paper / "REVIEW.md").exists()
        assert not (isolated_paper / "main.tex").exists()
        assert not (isolated_paper / "preview").exists()
    assert not isolated.exists()
    assert not (paper / "pages").exists()
    assert not (paper / "main.txt").exists()


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
