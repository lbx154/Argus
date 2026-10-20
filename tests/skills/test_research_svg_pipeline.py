from __future__ import annotations

from argus.skills.builtins import iter_vertical_skill_texts
from argus.verticals.research.prompt_policy import render_role_prompt_fragment
from argus.verticals.research.stages import STAGE_CHECKLISTS


def test_svg_workflow_is_removed_and_paper_uses_method_d_with_b_fallback() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    skill = "engineer/research-svg-pipeline.md"
    assert skill not in texts
    assert skill not in texts["research-paper-playbook.md"]
    assert skill not in texts["research-review-playbook.md"]
    prompt = render_role_prompt_fragment(
        role="engineer", operation="author_draft", stage="paper", scope="",
        project_root=None,
    )
    assert "engineer/paper-framework-figure-studio.md" in prompt
    assert "PPT Master" in prompt
    assert "Method D is the default" in prompt
    assert "Method B is the fallback" in prompt
    assert "native editable PPT" in prompt
    assert "ECharts" in prompt
    assert "TikZ" not in prompt
    assert "argus.tools.ppt_master status" in prompt
    assert "pipeline_figure" not in prompt
    assert "Reuse an existing suitable figure" in prompt
    assert "after the Introduction" in prompt
    assert "page 2 or 3" in prompt
    for stage in ("paper", "review"):
        checklist = " ".join(item.statement for item in STAGE_CHECKLISTS[stage])
        assert "PPT Master" in checklist
        assert "visual hierarchy" in checklist
    for stage in ("idea", "experiment", "review"):
        prompt = render_role_prompt_fragment(
            role="engineer", operation="", stage=stage, scope="", project_root=None,
        )
        assert "pipeline_figure" not in prompt
        if stage == "review":
            assert "Method D is the default" in prompt
            assert "Method B is the fallback" in prompt
            assert "native editable PPT" in prompt
            assert "TikZ" not in prompt
            assert "engineer/paper-framework-figure-studio.md" in prompt
            assert "operator-rejected figure needs a fresh composition" in prompt
    for stage in ("paper", "review"):
        prompt = render_role_prompt_fragment(
            role="engineer", operation="narrative_edit", stage=stage, scope="",
            project_root=None,
        )
        assert "pipeline_figure" not in prompt
        assert "Fresh-context Narrative Editor" in prompt
