"""Stage-aware checklist runner for the research pipeline.

Reads the current pipeline stage from research/PIPELINE_STATE.json and
runs shell checks relevant to that stage. Outputs a reviewer checklist
for critical stages — the reviewer agent (codex) reads the checklist,
loads the referenced skill, and inspects the artifacts itself.

Usage:
    python -m argus_skill.tools.stage_check --project-root .
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

STAGE_ORDER = [
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
]

# Common check: pipeline state must be valid (includes stage ordering)
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

# Stage → code checks (description, shell command)
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "research": [
        _PIPELINE_CHECK,
        ("Research brief exists", "test -f research/RESEARCH_BRIEF.md"),
        ("Literature grounding exists", "test -f research/LITERATURE_GROUNDING.json"),
        ("Source discovery exists", "test -f research/SOURCE_DISCOVERY.md"),
        ("Trend insights exists", "test -f research/TREND_INSIGHTS.md"),
        ("BibTeX has entries", "test -f paper/refs.bib && grep -c '@' paper/refs.bib"),
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Experiment plan exists", "test -f research/EXPERIMENT_PLAN.md"),
        ("Idea rejection log exists", "test -f research/IDEA_REJECTION_LOG.md"),
        ("Code study notes exist", "test -f research/CODE_STUDY_NOTES.md"),
        ("Baseline plan exists", "test -f research/BASELINE_AND_BENCHMARK_PLAN.md"),
    ],
    "benchmark": [
        _PIPELINE_CHECK,
        ("Benchmark provenance exists", "test -f experiments/BENCHMARK_PROVENANCE.md"),
    ],
    "run": [
        _PIPELINE_CHECK,
        ("Project venv exists", "test -d .venv && test -f .venv/bin/python"),
        ("Results exist", "find experiments -name 'summary.tsv' -o -name 'eval_results.jsonl' 2>/dev/null | head -1 | grep -q ."),
        ("Baseline reproduction recorded", "test -f research/BASELINE_REPRODUCTION.md"),
    ],
    "analysis": [
        _PIPELINE_CHECK,
        ("Results report exists", "test -f paper/RESULTS_REPORT.md"),
        ("Results table exists", "test -f paper/artifacts/results_table.tsv"),
        ("Figures exist", "ls paper/figures/*.png paper/figures/*.pdf 2>/dev/null | head -1 | grep -q ."),
    ],
    "draft": [
        _PIPELINE_CHECK,
        ("main.tex exists", "test -f paper/main.tex"),
        ("PDF compiles", "test -f paper/main.pdf"),
        ("Image2 figures manifest present", "test -f paper/figures/IMAGE2_FIGURES.json"),
    ],
    "review": [
        _PIPELINE_CHECK,
        ("Layout review present", "test -f paper/LAYOUT_REVIEW.json"),
        ("Academic-language review present", "test -f paper/ACADEMIC_LANGUAGE_REVIEW.json"),
    ],
    "submission": [
        _PIPELINE_CHECK,
        ("Reviewer marked submission stage done", "test -f research/PIPELINE_STATE.json && grep -q '\"submission\".*\"status\": *\"done\"' research/PIPELINE_STATE.json"),
    ],
}

# Stage → reviewer checklist
# The reviewer agent is a codex agent with shell access in the same workdir.
# It will load the skill, read the files, and do the review itself.
REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    # stage: (skill_to_load, review_instructions, files_to_read)
    "research": (
        "engineer/research-brief-to-experiment-plan.md",
        "Evaluate the research foundation on these dimensions:\n"
        "1. Problem clarity — is the research gap well-defined and grounded in literature?\n"
        "2. Literature coverage — ≥10 recent papers + ≥3 classic anchors surveyed?\n"
        "3. Source diversity — both scholarly (arXiv, Semantic Scholar) and trend sources (机器之心 etc.) checked?\n"
        "4. Trend grounding — are trend insights converted to testable research questions?\n"
        "5. Direction viability — is this a real frontier gap, not just an incremental tweak?\n"
        "6. Reference code — were related papers' official repos cloned and studied?\n"
        "Pass threshold: clear gap identified with literature backing, not just agent brainstorming.",
        ["research/RESEARCH_BRIEF.md", "research/LITERATURE_GROUNDING.json",
         "research/SOURCE_DISCOVERY.md", "research/TREND_INSIGHTS.md"],
    ),
    "plan": (
        "reviewer/experiment-plan-review.md",
        "Evaluate the experiment plan on these dimensions:\n"
        "1. **Research taste** — does this have a genuine insight/surprising angle, not just 'applied A to B'?\n"
        "2. Method competitiveness — is the proposed method strong enough vs SOTA?\n"
        "3. Idea novelty — is this a real gap, not a manufactured/incremental one? Check IDEA_REJECTION_LOG.md\n"
        "4. Baseline strength — is at least ONE baseline a reproduced published method (not just random/no-skill)?\n"
        "5. Reference code study — were top related papers' code repos cloned and studied? Check CODE_STUDY_NOTES.md\n"
        "6. Evaluation fairness — same compute/data budget for all conditions?\n"
        "7. Benchmark adequacy — ≥3 independent real benchmark families?\n"
        "8. Infrastructure choice — is the right training/inference framework selected?\n"
        "9. Feasibility — can this be executed with available resources?\n"
        "10. RL config sanity (RL post-training plans only) — if the method is "
        "PPO/GRPO/RLVR/DPO/reasoning-RL, is the config learnable at a glance? "
        "Group size/num_generations >=4 (never 1) for within-group contrast; a "
        "reward that varies across rollouts (not constant-by-construction) with a "
        "validated answer-extractor; max_completion_length long enough for gold "
        "answers; RL-scale LR (<< SFT) with sane KL/clip; enough steps to show "
        "learning; init/warm-start matched to the reward. BLOCK structurally "
        "unlearnable RL configs before any GPU spend (see the skill's RL "
        "post-training auto-fails).\n"
        "If research taste is missing (no insight, just engineering), BLOCK the plan.",
        ["research/EXPERIMENT_PLAN.md", "research/IDEA_REJECTION_LOG.md",
         "research/CODE_STUDY_NOTES.md", "research/BASELINE_AND_BENCHMARK_PLAN.md"],
    ),
    "benchmark": (
        "engineer/agent-research-benchmark-runner.md",
        "Evaluate benchmark preparation on these dimensions:\n"
        "1. Benchmark provenance — are all benchmarks from real public sources (not synthetic)?\n"
        "2. Coverage — ≥3 independent benchmark families with ≥240 tasks per condition?\n"
        "3. Gold answers — are ground truth labels verified, not assumed?\n"
        "4. Baseline readiness — are baseline implementations ready to run?\n"
        "5. Reproducibility — can someone else download and run these benchmarks?\n"
        "Pass threshold: all benchmarks sourced, verified, and ready for experiment execution.",
        ["experiments/BENCHMARK_PROVENANCE.md"],
    ),
    "run": (
        "reviewer/experiment-results-review.md",
        "Evaluate the experiment results on these dimensions:\n"
        "1. Statistical significance — are gains significant, not noise?\n"
        "2. Ablation fairness — does ablation isolate the claimed contribution?\n"
        "3. Effect size — are improvements meaningful, not cosmetic?\n"
        "4. Claim support — does data actually support each claim?\n"
        "5. Baseline competitiveness — did proposed method beat strong baselines?\n"
        "6. Completeness — all conditions run, no missing benchmark families?\n"
        "If results are too weak to support an EMNLP paper, recommend pivot or more experiments.",
        ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
    ),
    "analysis": (
        "engineer/research-results-analysis-and-figures.md",
        "Evaluate the analysis artifacts on these dimensions:\n"
        "1. Results report — does RESULTS_REPORT.md accurately summarize all experiment outcomes?\n"
        "2. Results table — does results_table.tsv have all conditions × benchmarks × metrics?\n"
        "3. Claim mapping — does each claim trace back to specific experimental evidence?\n"
        "4. Figures — are figures data-driven (not placeholder) and do they communicate key findings?\n"
        "5. Consistency — do numbers in the report match raw experiment outputs?\n"
        "Pass threshold: analysis is complete, figures are generated, claims are evidence-backed.",
        ["paper/RESULTS_REPORT.md", "paper/artifacts/results_table.tsv"],
    ),
    "draft": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "DRAFT-stage progress check (lenient, not a final peer review).\n"
        "Focus on whether the draft can move forward:\n"
        "1. Are all required sections present (abstract, intro, method, experiments, results, conclusion)?\n"
        "2. Do claims have at least placeholder evidence from actual experiments?\n"
        "3. Is the overall story coherent and the narrative structure sound?\n"
        "4. Are there fatal structural problems that would block progress?\n"
        "Do NOT block on: language polish, minor formatting, incomplete related work.\n"
        "Pass threshold: structure complete enough to proceed to review stage.",
        ["paper/main.tex"],
    ),
    "review": (
        "reviewer/emnlp-academic-language-review.md",
        "Evaluate the review artifacts on these dimensions:\n"
        "1. Layout review — does LAYOUT_REVIEW.json pass? Are pages well-balanced, figures readable?\n"
        "2. Academic language — does ACADEMIC_LANGUAGE_REVIEW.json pass? No hype, salesy language, or vague claims?\n"
        "3. Infrastructure leaks — does PAPER_INFRASTRUCTURE_REVIEW.json pass? No local paths, device names, or Argus/Codex references in manuscript?\n"
        "4. Citation quality — all citations author-year natbib, no dumping, no placeholders?\n"
        "5. Page budget — body ≤8 pages, conclusion on page 8, references start page 9+?\n"
        "If any review artifact has unresolved major issues, block until fixed.",
        ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
         "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual EMNLP reviewer.\n"
        "Review dimensions (all must pass):\n"
        "1. Novelty — does this make a meaningful contribution beyond incremental?\n"
        "2. Evidence strength — do experiments convincingly support claims?\n"
        "3. Baseline quality — are comparisons against strong, relevant baselines?\n"
        "4. Writing quality — is the paper well-written and clear?\n"
        "5. Reproducibility — enough detail to reproduce results?\n"
        "6. Significance — would EMNLP reviewers find this interesting?\n"
        "7. Format compliance — ACL format, page budget, references, appendix?\n"
        "8. Claim-evidence alignment — every claim backed by specific data?\n"
        "Score 5+/10 to pass. If the paper would get Reject at EMNLP, fail it here.",
        ["paper/main.tex"],
    ),
}


def _get_current_stage(project_root: Path) -> str:
    state_path = project_root / "research" / "PIPELINE_STATE.json"
    if not state_path.exists():
        return "research"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("current_stage", "research")
    except (json.JSONDecodeError, OSError):
        return "research"


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="stage-check")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--stage", default=None)
    args = parser.parse_args()

    root = args.project_root.resolve()
    stage = args.stage or _get_current_stage(root)
    python = sys.executable

    print(f"📋 Stage: {stage}")
    print()

    # 1. Run shell checks
    checks = STAGE_CHECKS.get(stage, [])
    passed = 0
    failed = 0
    for desc, cmd in checks:
        cmd = cmd.replace("{python}", python)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=120, cwd=str(root),
            )
            if result.returncode == 0:
                print(f"  ✅ {desc}")
                passed += 1
            else:
                output = (result.stdout + result.stderr).strip()
                print(f"  ❌ {desc}")
                if output:
                    for line in output.splitlines():
                        print(f"     {line}")
                failed += 1
        except subprocess.TimeoutExpired:
            print(f"  ⏰ {desc} (timeout)")
            failed += 1

    # 2. Run automated F4 (structural) + F3 (advisory) gates that apply
    #    at this stage. STRUCTURAL gate failures count into the round
    #    exit code — they are anti-fraud / provenance guards (e.g. broken
    #    evidence chains). ADVISORY findings (mediocrity facts) NEVER
    #    count — they are facts the reviewer reads to make their own
    #    judgment. The gate map + kind lives in
    #    argus_skill.skills.automated_gates.{STAGE_GATES,GATE_KINDS}.
    from argus_skill.skills.automated_gates import (
        run_stage_gates,
    )

    gate_results = run_stage_gates(
        root,
        stage=stage,
        proposed_condition=os.environ.get("ARGUS_SKILL_PROPOSED_CONDITION") or None,
        baseline_condition=os.environ.get("ARGUS_SKILL_BASELINE_CONDITION") or None,
    )
    structural_block = 0
    advisory_count = 0
    structural_pass = 0
    structural_fail = 0
    if gate_results:
        print()
        print(f"🛡  Automated gates for stage '{stage}':")
        for gate in gate_results:
            if gate.kind == "advisory":
                mark = "📋"  # advisory — surface, never block
                advisory_count += 1
            elif gate.passed:
                mark = "✅"
                structural_pass += 1
            else:
                mark = "❌"
                structural_fail += 1
                if gate.is_blocking:
                    structural_block += 1
            print(f"  {mark} {gate.name} ({gate.kind}) — {gate.summary}")
            if gate.detail:
                for line in gate.detail.splitlines():
                    print(f"     {line}")

    # 3. Output reviewer checklist for critical stages
    #    Reviewer is a codex agent — it reads the skill and files itself.
    if stage in REVIEWER_CHECKLISTS:
        skill_name, instructions, files = REVIEWER_CHECKLISTS[stage]
        print()
        print(f"📋 REVIEWER CHECKLIST for stage '{stage}'")
        print(f"   Load skill: argus_builtin_skills/{skill_name}")
        print(f"   Read and review: {', '.join(files)}")
        print()
        print(instructions)

    # Exit code: shell-check failures + STRUCTURAL gate failures only.
    # Advisory findings (mediocrity facts) never appear here.
    total_failed = failed + structural_block
    print(
        f"\n{'✅' if total_failed == 0 else '❌'} "
        f"{passed} shell pass, {failed} shell fail, "
        f"{structural_pass} structural-gate pass, "
        f"{structural_fail} structural-gate fail, "
        f"{advisory_count} advisory finding(s) "
        f"(reviewer rules)"
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
