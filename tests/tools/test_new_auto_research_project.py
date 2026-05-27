from __future__ import annotations

import json
import py_compile
from pathlib import Path

from argus_skill.skills.builtins import builtin_skill_count
from argus_skill.tools.new_auto_research_project import (
    LaunchConfig,
    create_project,
    extract_copy_ready_agents_md,
    load_template_text,
    next_version,
    render_agents_md,
)


def test_extract_copy_ready_agents_md_omits_skill_frontmatter() -> None:
    body = extract_copy_ready_agents_md(load_template_text())

    assert body.startswith("# AGENTS.md\n")
    assert "## Copy-ready `AGENTS.md`" not in body
    assert "validate-full-emnlp" in body
    assert "validate-paper-quality-contracts" in body


def test_render_agents_md_fills_placeholders_and_quality_contracts() -> None:
    rendered = render_agents_md(
        load_template_text(),
        project_name="agent-emnlp-auto-research-v15",
        version="v15",
    )

    assert "[write the target research problem and deliverable]" not in rendered
    assert "| [input] | [source] | [status] | [allowed use] | [rationale] |" not in rendered
    assert "agent-emnlp-auto-research-v15" in rendered
    assert "EXEMPLAR_SUITABILITY.json" in rendered
    assert "CLAIM_GRAPH.json" in rendered
    assert "FIGURE_TABLE_STYLE_GUIDE.json" in rendered
    assert "VALIDATION_PRIORITY_POLICY.json" in rendered
    assert "ARTIFACT_FRESHNESS.json" in rendered
    assert "validate-paper-quality-contracts" in rendered
    assert "## Skill route" in rendered
    assert "argus_builtin_skills/emnlp-paper-skill-router.md" in rendered
    assert "argus_builtin_skills/research-results-analysis-and-figures.md" in rendered
    assert "argus_builtin_skills/research-submission-assurance-gate.md" in rendered


def test_create_project_without_daemon_exports_template_and_skills(tmp_path: Path) -> None:
    result = create_project(
        LaunchConfig(
            parent=tmp_path,
            version="v15",
            start_daemon=False,
            init_git=False,
        )
    )

    agents = result.agents_path.read_text(encoding="utf-8")
    assert result.project_dir == tmp_path / "agent-emnlp-auto-research-v15"
    assert result.daemon_started is False
    assert "agent-emnlp-auto-research-v15" in agents
    assert "validate-paper-quality-contracts" in agents
    exported = sorted(result.skills_dir.rglob("*.md"))
    assert len(exported) == builtin_skill_count()
    assert (result.skills_dir / "agent-md-new-project-template.md").exists()
    assert (result.skills_dir / "domains" / "agents-rag" / "langchain.md").exists()

    code_dir = result.project_dir / "code"
    llm = code_dir / "llm.py"
    generate_image_2 = code_dir / "generate_image_2.py"
    compat_generate_image = code_dir / "generate_image2_figure.py"
    assert (code_dir / "__init__.py").exists()
    assert llm.exists()
    assert generate_image_2.exists()
    assert compat_generate_image.exists()
    llm_text = llm.read_text(encoding="utf-8")
    assert "load_model_api_route" in llm_text
    assert "TRANSIENT_HTTP_STATUS_CODES" in llm_text
    assert "_retry_delay_seconds" in llm_text
    assert "IMAGE2_FIGURES.json" in generate_image_2.read_text(encoding="utf-8")
    assert "generate_image_2 import main" in compat_generate_image.read_text(encoding="utf-8")
    for path in (llm, generate_image_2, compat_generate_image):
        py_compile.compile(str(path), doraise=True)

    pipeline_state = json.loads(
        (result.project_dir / "research" / "PIPELINE_STATE.json").read_text(
            encoding="utf-8"
        )
    )
    assert pipeline_state["current_stage"] == "literature"
    assert pipeline_state["stages"]["brief"]["status"] == "done"
    assert pipeline_state["stages"]["literature"]["status"] == "pending"
    assert pipeline_state["stages"]["submission"]["status"] == "missing"
    assert (result.project_dir / "research" / "RESEARCH_BRIEF.md").exists()
    assert (result.project_dir / "research" / "EXPERIMENT_PLAN.md").exists()
    assert (result.project_dir / "research" / "CLAIMS_TO_TEST.md").exists()
    assert (result.project_dir / "research" / "GO_NO_GO.md").exists()
    assert (result.project_dir / "experiments" / "BENCHMARK_PROVENANCE.md").exists()


def test_create_project_with_domain_exports_only_relevant_domain_skills(
    tmp_path: Path,
) -> None:
    result = create_project(
        LaunchConfig(
            parent=tmp_path,
            version="v16",
            domain="cv",
            start_daemon=False,
            init_git=False,
        )
    )

    exported = sorted(result.skills_dir.rglob("*.md"))
    assert 0 < len(exported) < builtin_skill_count()
    assert result.domain == "cv"
    assert (result.skills_dir / "auto-research-pipeline.md").exists()
    assert (result.skills_dir / "domains" / "cv-multimodal" / "clip.md").exists()
    assert (result.skills_dir / "domains" / "optimization" / "flash-attention.md").exists()
    assert (result.skills_dir / "domains" / "research-ops" / "run-experiment.md").exists()
    assert not (result.skills_dir / "domains" / "agents-rag" / "langchain.md").exists()
    assert not list(result.skills_dir.glob("domain--*.md"))


def test_next_version_uses_highest_existing_workspace(tmp_path: Path) -> None:
    (tmp_path / "agent-emnlp-auto-research-v2").mkdir()
    (tmp_path / "agent-emnlp-auto-research-v10").mkdir()
    (tmp_path / "agent-emnlp-auto-research-not-a-version").mkdir()

    assert next_version(tmp_path) == "v11"
