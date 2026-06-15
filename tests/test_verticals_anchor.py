"""Smoke test for the vertical re-export anchor.

The point of this test is structural, not behavioral: confirm that
``argus_skill.verticals.research`` exposes the paper-specific names
that future code is supposed to depend on, and that those names are
the same objects as the ones still served by the legacy
``argus_skill.skills`` / ``argus_skill.tools`` import paths. The
underlying modules can later be physically relocated under
``argus_skill/verticals/research/`` without breaking either contract.
"""
from __future__ import annotations


def test_research_vertical_reexports_paper_specific_names() -> None:
    from argus_skill.verticals import research
    expected = {
        # draft_outline
        "DRAFT_OUTLINE_PATH", "DraftOutline", "ExperimentPlaceholder",
        "FigurePlaceholder", "OutlineIssue", "SectionPlaceholder",
        "cross_check_figure_ids", "load_outline", "parse_outline",
        "validate_outline",
        # evidence_chain
        "ChainIssue", "ChainReport",
        # paper_structural_minimums
        "StructuralIssue", "StructuralReport",
        "validate_paper_structural_minimums",
        # stage_check
        "REVIEWER_CHECKLISTS", "STAGE_CHECKS", "STAGE_ORDER",
    }
    missing = expected - set(research.__all__)
    assert not missing, f"research vertical missing re-exports: {missing}"
    for name in expected:
        assert hasattr(research, name), f"research.{name} not importable"


def test_research_vertical_is_identity_reexport() -> None:
    """The re-exports must be the *same objects* as their legacy paths so
    callers can choose either import without semantic drift."""
    from argus_skill.skills import draft_outline as legacy_draft
    from argus_skill.skills import evidence_chain as legacy_chain
    from argus_skill.skills import paper_structural_minimums as legacy_struct
    from argus_skill.tools import stage_check as legacy_stage
    from argus_skill.verticals import research

    assert research.validate_outline is legacy_draft.validate_outline
    assert research.DraftOutline is legacy_draft.DraftOutline
    assert research.ChainReport is legacy_chain.ChainReport
    assert (
        research.validate_paper_structural_minimums
        is legacy_struct.validate_paper_structural_minimums
    )
    assert research.STAGE_ORDER is legacy_stage.STAGE_ORDER
    assert research.STAGE_CHECKS is legacy_stage.STAGE_CHECKS
    assert research.REVIEWER_CHECKLISTS is legacy_stage.REVIEWER_CHECKLISTS


def test_vertical_namespace_exists_for_future_plugins() -> None:
    """The ``verticals`` namespace package must exist so other verticals
    (quant, rollout, …) can be added next to ``research`` without
    touching argus core."""
    import argus_skill.verticals as v
    assert v.__doc__ and "vertical" in v.__doc__.lower()
