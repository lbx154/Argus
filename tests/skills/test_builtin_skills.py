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
        "early-stop",
    ):
        assert required in text


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
        "paper/TEMPLATE_SOURCE.md",
        "paper/style_ref/STYLE_PROFILE.md",
        "paper/style_ref/EXEMPLAR.json",
        "Paper Exemplar PDF Learning",
        "local_pdf",
        "pdf_sha256",
        "text_extract",
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
        "kill-argument",
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
        "Abstract shape",
        "Section/page allocation",
        "Figure/table inventory",
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
