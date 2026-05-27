from __future__ import annotations

from pathlib import Path

from argus_skill.life import GlobalMemory
from argus_skill.skills.builtins import (
    builtin_skill_count,
    seed_builtin_skills,
)
from argus_skill.skills.store import SkillStore


def test_seed_builtin_skills_creates_parseable_research_defaults(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"

    created = seed_builtin_skills(skills_dir)

    assert len(created) == builtin_skill_count()
    assert all(created.values())
    summaries = SkillStore(skills_dir).list_summaries()
    names = {summary["name"] for summary in summaries}
    assert "EMNLP Paper Drafting" in names
    assert "Claims Evidence Audit" in names
    assert "Research Brief To Experiment Plan" in names
    assert "Auto Research Pipeline" in names
    assert "Research Submission Assurance Gate" in names
    assert "EMNLP Paper Skill Router" in names
    assert "EMNLP Academic Language Review" in names
    assert "EMNLP Format Preflight" in names
    assert "Paper Exemplar PDF Learning" in names
    assert "Academic Paper Peer Review Benchmark" in names
    assert "Argus Engineer Role" in names
    assert "Argus Reviewer Role" in names
    assert "Argus Critic Role" in names
    assert "Argus Planner Role" in names
    assert "Argus Scientist Role" in names
    assert "AGENTS.md New Project Template" in names
    assert "AGENTS.md Existing Project Optimization Template" in names


def test_seed_builtin_skills_preserves_existing_user_edits(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    target = skills_dir / "emnlp-paper-drafting.md"
    target.write_text("user edit\n", encoding="utf-8")

    created = seed_builtin_skills(skills_dir)

    assert created["emnlp-paper-drafting.md"] is False
    assert target.read_text(encoding="utf-8") == "user edit\n"


def test_global_memory_init_seeds_builtin_skills(tmp_path: Path) -> None:
    mem = GlobalMemory.open(tmp_path)

    state = mem.init()

    assert state == {"identity": True, "journal": True}
    assert (tmp_path / "skills" / "emnlp-paper-drafting.md").exists()
    assert (tmp_path / "skills" / "research-results-analysis-and-figures.md").exists()


def test_research_experiment_skill_requires_live_progress_protocol(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "agent-research-benchmark-runner.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "progress.jsonl",
        "status.json",
        "STOP",
        "flush/fsync",
        "background process",
        "Completed-run handoff is mandatory",
        "validate-full-scale-evidence",
        "token-only waiting",
        "early-stop",
    ):
        assert required in text


def test_builtin_skills_require_full_scale_experiment_evidence_gate(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)

    required_by_skill = {
        "auto-research-pipeline.md": (
            "validate-full-scale-evidence",
            "Benchmark construction is not execution",
            "missing_full_scale_experiment_run",
            "incomplete_full_scale_experiment_run",
            "missing_baseline_condition_run",
            "pilot_pdf_without_full_scale_evidence",
            "status.json",
            "raw result rows",
        ),
        "agent-research-benchmark-runner.md": (
            "validate-full-scale-evidence",
            "Benchmark construction is not execution",
            "status.json task_count",
            "raw scored rows",
            ">=240 distinct scored main tasks/episodes",
            "missing_baseline_condition_run",
        ),
        "research-results-analysis-and-figures.md": (
            "validate-full-scale-evidence",
            "benchmarks/full/tasks.jsonl",
            "declared `status.json task_count`",
            "completed raw scored rows per required method/baseline condition",
            "pilot_pdf_without_full_scale_evidence",
        ),
        "emnlp-paper-drafting.md": (
            "validate-full-scale-evidence",
            "Benchmark construction is not executed evidence",
            "raw `experiments/**` rows",
            "every required method/baseline condition",
            "pilot_pdf_without_full_scale_evidence",
        ),
        "emnlp-academic-language-review.md": (
            "validate-full-scale-evidence",
            "benchmark construction",
            "status.json task_count",
            "every required method/baseline condition",
            "pilot_pdf_without_full_scale_evidence",
        ),
        "research-submission-assurance-gate.md": (
            "validate-full-scale-evidence",
            "benchmark construction presented as execution",
            "fewer than 240 distinct scored rows for any required condition",
            "missing_baseline_condition_run",
            "pilot_pdf_without_full_scale_evidence",
        ),
        "agent-md-new-project-template.md": (
            "validate-full-scale-evidence",
            "Benchmark construction is not execution",
            "benchmarks/full/tasks.jsonl",
            "status.json task_count",
            "every required method/baseline condition",
            "pilot_pdf_without_full_scale_evidence",
        ),
        "agent-md-existing-project-optimization-template.md": (
            "validate-full-scale-evidence",
            "Benchmark construction is not execution",
            "benchmarks/full/tasks.jsonl",
            "status.json task_count",
            "every required method/baseline condition",
            "pilot_pdf_without_full_scale_evidence",
        ),
    }

    for filename, required_tokens in required_by_skill.items():
        text = (skills_dir / filename).read_text(encoding="utf-8")
        for required in required_tokens:
            assert required in text, f"{filename} missing {required!r}"


def test_agent_md_templates_are_emnlp_paper_oriented_and_seeded(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    new_project = (skills_dir / "agent-md-new-project-template.md").read_text(
        encoding="utf-8"
    )
    repair_project = (
        skills_dir / "agent-md-existing-project-optimization-template.md"
    ).read_text(encoding="utf-8")

    for text in (new_project, repair_project):
        assert "Copy-ready `AGENTS.md`" in text
        assert "v7" not in text
        assert "v8" not in text
        assert "v9" not in text
        assert "Mind2Web" not in text
        assert "SWE-bench" not in text
        assert "BoundaryTrap" not in text
        assert "EMNLP/ACL long-paper" in text
        assert "/home/argustest/research.md" in text
        assert "/home/argustest/argus-skill" in text
        assert "/home/argustest/argus-skill/argus_skill/builtin_skills/" in text
        assert "argus_skill.builtin_skills" in text
        assert "--export-builtin-skills ./argus_builtin_skills" in text
        assert "argus_builtin_skills/*.md" in text
        assert "/home/argustest/miniconda3/bin/python" in text
        assert "validate-full-emnlp" in text
        assert "image-2/codex-image2" in text
        assert "actual generated image-2 raster" in text
        assert "Paper Exemplar PDF Learning" in text
        assert "validate-exemplar" in text
        assert "PAPER_STRUCTURE_BLUEPRINT.md" in text
        assert "STRUCTURE_CONFORMANCE.md" in text
        assert "STRUCTURE_CONFORMANCE.json" in text
        assert "conformance_schema_version: 1" in text
        assert "maps_to_exemplar_phase" in text
        assert "deviation_rationale" in text
        assert "unmapped" in text
        assert "section order, page budget, paragraph roles" in text
        assert "validate-research-md-format" in text
        assert "ACADEMIC_LANGUAGE_REVIEW.json" in text
        assert "LAYOUT_REVIEW.json" in text
        assert "progress.jsonl" in text
        assert "STOP-file cancellation contract" in text
        assert "Review artifacts, calibration files, and readiness reports are evidence" in text
        assert "operator" in text
        assert "operator's most recent explicit instruction wins" in text
        assert "Starter targets for memory, agent-skill, and hallucination papers" in text
        assert "literature matrix" in text
        assert "Semantic Scholar" in text
        assert "liu2023agentbench" in text
        assert "do not dump all citations into one dense paragraph" in text or (
            "do not concentrate all citations in one giant paragraph" in text
        )
        assert "~/.argus-skill/capabilities/model_api.json" in text
        assert "--model-api-status" in text
        assert "--init-model-api" in text
        assert "load_model_api_route" in text
        assert "code/llm.py" in text
        assert "code/generate_image2_figure.py" in text
        assert "ARGUS_SKILL_IMAGE_MODEL=gpt-image-2" in text
        assert "generation_provenance_path" in text
        assert "output_sha256" in text
        assert "General style" in text
        assert "Pinned content" in text
        assert "SPELL EXACTLY" in text
        assert "Layout variant" in text
        assert "Negative prompt / Avoid" in text
        assert "6--20 layout variants" in text
        assert "Figma-style block diagram" in text
        assert "rounded cards" in text
        assert "Abstract" in text and "0.3" in text
        assert "Related Work" in text and "0.5--0.8" in text
        assert "Experimental Setup" in text and "0.5--1" in text
        assert "Main Results" in text and "1--1.5" in text
        assert "Failure Cases" in text and "0.3--0.5" in text
        assert "validate-full-scale-evidence" in text
        assert "Benchmark construction is not execution" in text
        assert "evaluated paper system" in text
        assert "LLM/model identifiers" in text
        assert "missing_full_scale_experiment_run" in text
        assert "incomplete_full_scale_experiment_run" in text
        assert "missing_baseline_condition_run" in text
        assert "pilot_pdf_without_full_scale_evidence" in text
        assert "write-validation-priority-policy" in text
        assert "refresh-artifact-freshness" in text
        assert "refresh-manifest" in text

    for required in (
        "clean-slate project",
        "Allowed starting inputs",
        "literature/source discovery -> idea provenance -> benchmark/code",
        "10 recent high-quality papers",
        "3 classic anchors",
        "not_agent_brainstorm: true",
        "no_skill",
        "raw_memory",
        "reflexion",
        "static_skill_lib",
        "50--60 tasks as complete final evidence",
        "We propose X. We show X improves Y by Z because W.",
        "Anonymous EMNLP Submission",
        "Overfull \\hbox > 5pt",
        "1536x1024",
        "Do not copy a previous project",
        "do not preserve the older thesis",
        "must not contain a specific project title",
    ):
        assert required in new_project

    for required in (
        "existing project",
        "Canonical state",
        "freshness chains synchronized",
        "Current operator goal",
        "Existing research and evidence repair",
        "Existing paper repair",
        "Figure repair",
        "Exemplar/style repair",
        "Final review and assurance repair",
        "Telemetry and long-run visibility",
        "Review artifacts, calibration files, and readiness reports are evidence",
        "50--60 tasks are pilot evidence",
        "If the overview is ugly",
        "Do not restart from scratch",
        "raw data may still be selectively listed",
    ):
        assert required in repair_project


def test_emnlp_paper_skill_requires_official_template_page_budget_and_style_ref(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "emnlp-paper-drafting.md").read_text(encoding="utf-8")

    for required in (
        "https://github.com/acl-org/acl-style-files",
        "7.5--8 main-content pages",
        "paper/PAGE_BUDGET.md",
        "Abstract 0.3 pages",
        "Related Work 0.5--0.8 pages",
        "Experimental Setup 0.5--1 page",
        "Main Results 1--1.5 pages",
        "Failure Cases 0.3--0.5 pages",
        "paper/TEMPLATE_SOURCE.md",
        "paper/style_ref/STYLE_PROFILE.md",
        "paper/style_ref/EXEMPLAR.json",
        "paper/style_ref/EXEMPLAR_SUITABILITY.json",
        "Paper Exemplar PDF Learning",
        "local_pdf",
        "pdf_sha256",
        "text_extract",
        "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md",
        "paper/style_ref/STRUCTURE_CONFORMANCE.md",
        "paper/style_ref/STRUCTURE_CONFORMANCE.json",
        "conformance_schema_version: 1",
        "maps_to_exemplar_phase",
        "deviation_rationale",
        "unmapped",
        "Use this blueprint as the paper organizer",
        "instead of writing freehand LaTeX",
        "primary exemplar's skeleton",
        "paper/CLAIM_GRAPH.json",
        "paper/EVIDENCE_GAPS.json",
        "Drafting and experimentation are allowed to interleave",
        "paper/FIGURE_TABLE_STYLE_GUIDE.json",
        "paper/ARTIFACT_FRESHNESS.json",
        "paper/VALIDATION_PRIORITY_POLICY.json",
        "write-validation-priority-policy",
        "refresh-artifact-freshness",
        "validate-claim-graph",
        "validate-paper-quality-contracts",
        "paper/figures/IMAGE2_FIGURES.json",
        "paper/ACADEMIC_LANGUAGE_REVIEW.json",
        "paper/FORMAT_PREFLIGHT.md",
        "academic_language_review",
        "validate-academic-language-review",
        "validate-research-md-format",
        "image-2",
        "paper/PAPER_DRAFT_REPORT.json",
        "target_venue: \"EMNLP\"",
        "paper_scope: \"long-paper\"",
        "Benchmark provenance",
        "Never copy prose",
        "validator worksheet",
        "normal EMNLP abstract",
        "Distribute citations by claim/topic/paragraph",
        "research/LIT_MATRIX.tsv",
        "citation pile",
        "single thin prompt",
        "draw method overview",
        "6--20 image-2 layout variants",
    ):
        assert required in text


def test_research_plan_skill_requires_common_benchmark_provenance(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "research-brief-to-experiment-plan.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "research/LITERATURE_REVIEW.md",
        "research/LIT_MATRIX.tsv",
        "research/LITERATURE_GROUNDING.json",
        "research/IDEA_PROVENANCE.json",
        "research/CODE_REUSE_PLAN.json",
        "not_agent_brainstorm: true",
        "license/terms",
        "classic_papers",
        "research/SOURCE_DISCOVERY.md",
        "research/TREND_INSIGHTS.md",
        "research/NOVELTY_MAP.md",
        "research/BASELINE_AND_BENCHMARK_PLAN.md",
        "10 recent high-quality papers",
        "3 classic anchor papers",
        "机器之心",
        "新智元",
        "aiera.com.cn",
        "testable research question",
        "discovery signals only",
        "do not need paper/benchmark/code backing",
        "ToolBench",
        "WebArena",
        "GAIA",
        "MultiAgentBench",
        "Benchmark provenance",
        "validate-idea-provenance",
        "validate-code-reuse",
    ):
        assert required in text


def test_auto_research_pipeline_skill_requires_state_machine_gates(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "auto-research-pipeline.md").read_text(encoding="utf-8")

    for required in (
        "research/PIPELINE_STATE.json",
        "research/GO_NO_GO.md",
        "Literature grounding and source-discovery gate",
        "research/LITERATURE_REVIEW.md",
        "research/LIT_MATRIX.tsv",
        "research/LITERATURE_GROUNDING.json",
        "research/IDEA_PROVENANCE.json",
        "research/CODE_REUSE_PLAN.json",
        "not_agent_brainstorm: true",
        "classic_papers",
        "research/SOURCE_DISCOVERY.md",
        "research/TREND_INSIGHTS.md",
        "机器之心",
        "新智元",
        "aiera.com.cn",
        "source access status",
        "testable research question",
        "non-peer-reviewed discovery signals",
        "do not need paper/benchmark/code backing",
        "free-form agent brainstorming",
        "license-compatible official paper code",
        "pivot",
        "rejected",
        "research/NARRATIVE_REPORT.md",
        "paper/SUBMISSION_ASSURANCE.md",
        "paper/PAPER_QUALITY_CALIBRATION.json",
        "Paper Exemplar PDF Learning",
        "local exemplar PDFs/text extracts",
        "paper/style_ref/EXEMPLAR.json",
        "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md",
        "concrete paper organizer before prose",
        "paper/figures/IMAGE2_FIGURES.json",
        "paper/ACADEMIC_LANGUAGE_REVIEW.json",
        "paper/FORMAT_PREFLIGHT.md",
        "paper/PAPER_DRAFT_REPORT.json",
        "target_venue: EMNLP",
        "image-2",
        "long-paper",
        "negative fresh-demo pilot pattern",
        "argus_skill.skills.pipeline_contracts",
        "validate-full-emnlp",
        "validate-research-md-format",
        "validate-academic-language-review",
        "Final EMNLP completion contract",
        "paper_contribution",
        "We propose X. We show X improves Y by Z because W.",
        "negative-result paper",
        "proposed artifact/protocol",
        "final_submission",
        "bounded",
        "small pilot is never sufficient final evidence",
        "nontrivial baselines",
        "ablations/failure analysis",
        "defensive caveat lists",
        "must not be called EMNLP-ready",
        "unrelated domains need their own literature-derived retrieval targets",
        "Citation placement is part of paper quality",
        "one giant related-work paragraph",
    ):
        assert required in text


def test_submission_assurance_gate_skill_requires_audit_layers(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "research-submission-assurance-gate.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "experiment integrity",
        "result-to-claim",
        "paper-claim audit",
        "idea provenance and code reuse",
        "literature and exemplar grounding",
        "citation audit",
        "fatal objection",
        "paper-quality calibration",
        "negative:fresh-demo-pilot-pattern",
        "positive:emnlp2025-best-infini-gram-mini",
        "paper/SUBMISSION_ASSURANCE.json",
        "paper/PAPER_QUALITY_CALIBRATION.json",
        "paper/ACADEMIC_LANGUAGE_REVIEW.json",
        "paper/FORMAT_PREFLIGHT.md",
        "academic-language review",
        "research/LITERATURE_GROUNDING.json",
        "research/IDEA_PROVENANCE.json",
        "research/CODE_REUSE_PLAN.json",
        "agent_generated",
        "license/attribution",
        "do not need paper/benchmark/code backing",
        "paper/style_ref/EXEMPLAR.json",
        "local downloaded PDFs",
        "PDF SHA-256",
        "paper/figures/IMAGE2_FIGURES.json",
        "image-2",
        "paper/PAPER_DRAFT_REPORT.json",
        "validate-full-emnlp",
        "validate-research-md-format",
        "validate-academic-language-review",
        "result-first or validator-shaped abstract",
        "long-paper",
        "PASS | WARN | FAIL | BLOCKED | ERROR | NOT_APPLICABLE",
        "paired-significance table when comparative binary outcomes",
        "review artifacts as evidence, not targets",
        "never a `WARN` for final EMNLP readiness",
        "citation dumping",
        "each paragraph should cite the papers it actually discusses",
    ):
        assert required in text


def test_paper_exemplar_skill_requires_pdf_text_hash_and_thick_profile(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "paper-exemplar-pdf-learning.md").read_text(encoding="utf-8")

    for required in (
        "Paper Exemplar PDF Learning",
        "URL-only exemplars are not enough",
        "paper/style_ref/exemplars/<slug>/paper.pdf",
        "paper/style_ref/exemplars/<slug>/paper.txt",
        "pdf_sha256",
        "exemplar_schema_version: 2",
        "at least two",
        "best/outstanding/award paper",
        "STYLE_PROFILE.md",
        "EXEMPLAR_SUITABILITY.json",
        "primary_exemplar",
        "task type, method family, experiment shape",
        "page rhythm",
        "PAPER_STRUCTURE_BLUEPRINT.md",
        "Abstract shape",
        "Section/page allocation",
        "Figure/table inventory",
        "section order, page budget, paragraph roles",
        "paper organizer",
        "primary exemplar skeleton",
        "No prose copy policy",
        "validate-exemplar",
    ):
        assert required in text


def test_format_related_skills_embed_research_md_preflight_constraints(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)

    required_by_skill = {
        "emnlp-paper-drafting.md": (
            "Anonymous EMNLP Submission",
            "Overfull \\hbox > 5pt",
            "validate-research-md-format",
            "pages 4--7",
            "paired-significance table",
            "tabcolsep=3-4pt",
            "arraystretch=1.15",
            "1536x1024 or 1920x1080",
            "1024x1024",
        ),
        "auto-research-pipeline.md": (
            "Anonymous EMNLP Submission",
            "Overfull \\hbox > 5pt",
            "EMNLP Format Preflight",
            "validate-research-md-format",
            "pages 4--7",
            "paired-significance table",
            "<=5 body figures",
            "tabcolsep=3-4pt",
            "1536x1024 or 1920x1080",
        ),
        "research-results-analysis-and-figures.md": (
            "Overfull \\hbox > 5pt",
            "paired-significance table",
            "body figures <=5",
            "tabcolsep=3-4pt",
            "arraystretch=1.15",
            "1536x1024 or 1920x1080",
            "derive data figures and tables from local raw data",
            "does not make them acceptable final EMNLP evidence",
        ),
        "paper-review-revision-loop.md": (
            "Anonymous EMNLP Submission",
            "Overfull \\hbox > 5pt",
            "pages 4--7",
            "paired-significance table",
            "tabcolsep=3-4pt",
            "1536x1024 or 1920x1080",
            "Prompt, provenance, generation-setting",
            "do not regenerate an already accepted image merely to refresh metadata",
        ),
        "claims-evidence-audit.md": (
            "Overfull \\hbox > 5pt",
            "pages 4--7",
            "paired-significance table",
            "% UNVERIFIED",
            "numerical headline",
        ),
        "research-submission-assurance-gate.md": (
            "Anonymous EMNLP Submission",
            "Overfull \\hbox > 5pt",
            "research_md_format_preflight",
            "validate-research-md-format",
            "pages 4--7",
            "paired-significance table",
            "tabcolsep=3-4pt",
            "arraystretch=1.15",
            "1536x1024 or 1920x1080",
        ),
        "emnlp-format-preflight.md": (
            "Anonymous EMNLP Submission",
            "Overfull \\hbox > 5pt",
            "validate-research-md-format",
            "pages 4--7",
            "paired-significance table",
            "tabcolsep=3-4pt",
            "arraystretch=1.15",
            "1536x1024 or 1920x1080",
            "FORMAT_PREFLIGHT.md",
        ),
        "academic-paper-peer-review-benchmark.md": (
            "Overfull \\hbox > 5pt",
            "validate-full-emnlp",
            "pages 4--7",
            "paired-significance",
            "tabcolsep=3-4pt",
            "arraystretch=1.15",
            "image-2/codex-image2",
            "review artifacts, calibration files, and readiness reports as evidence",
        ),
    }

    for filename, required_tokens in required_by_skill.items():
        text = (skills_dir / filename).read_text(encoding="utf-8")
        for required in required_tokens:
            assert required in text, f"{filename} missing {required!r}"


def test_academic_peer_review_benchmark_skill_sets_reviewer_standard(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "academic-paper-peer-review-benchmark.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "Simulate a strict EMNLP/ACL-style program-committee reviewer",
        "ARIS-style paper audit loops",
        "Auto Research Pipeline final contract",
        "Strong Accept",
        "Weak Reject",
        "240 unique semantic scored main tasks/episodes",
        "duplicated benchmark expansion",
        "selected benchmark sources/components",
        "at least 2 independent sources",
        "35 verified BibTeX entries",
        "30 unique cited keys",
        "self-drawn substitutes",
        "validator vocabulary",
        "gpt-5.4",
        "next_action",
    ):
        assert required in text


def test_emnlp_paper_skill_router_maps_validator_issue_codes(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)
    text = (skills_dir / "emnlp-paper-skill-router.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "Validator Issue-Code Quick Route",
        "missing_full_scale_experiment_run",
        "Agent Research Benchmark Runner",
        "rendered_main_body_underfilled",
        "Do not pad with generic prose",
        "table_caption_missing_number",
        "caption must include the key numerical result",
        "placeholder_bibtex_author_others",
        "rendered_placeholder_reference_authors",
        "conceptual_body_figure_not_image2",
        "preserve the exact accepted raster",
        "mismatched_image2_sidecar_prompt_sha256",
        "stale_layout_review_artifact",
        "paper_layout_review --review-mode vision --write",
        "stale_academic_language_review_source",
        "academic_language_review --review-mode model --write",
        "academic_language_missing_method_model_identifier",
        "framework/runtime or benchmark harness",
        "repair-emnlp-contract-artifacts",
        "submission_not_ready_verdict",
        "Run this last",
    ):
        assert required in text


def test_argus_role_identity_skills_cover_agent_contracts(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    seed_builtin_skills(skills_dir)

    required_by_skill = {
        "argus-engineer-role.md": (
            "execution arm",
            "Reviewer decides",
            "concrete verification",
            "validate-full-emnlp",
        ),
        "argus-reviewer-role.md": (
            "evidence gate",
            "done",
            "continue",
            "blocked",
            "Academic Paper Peer Review Benchmark",
            "short, deterministic shell checks",
            "bounded paper tasks",
        ),
        "argus-critic-role.md": (
            "post-review quality filter",
            "operator-visible value",
            "vanity",
            "impact score",
        ),
        "argus-planner-role.md": (
            "manager/director",
            "bounded",
            "final_submission",
            "restart_daemon",
        ),
        "argus-scientist-role.md": (
            "skill-memory researcher",
            "Match skills conservatively",
            "Distill capability-level guidance",
            "gpt-5.4-mini",
            "relatively small engineer model",
            "coverage check",
        ),
    }

    for filename, required_tokens in required_by_skill.items():
        text = (skills_dir / filename).read_text(encoding="utf-8")
        for required in required_tokens:
            assert required in text, f"{filename} missing {required!r}"
