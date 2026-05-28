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
import subprocess
import sys
from pathlib import Path

STAGE_ORDER = [
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
]

# Stage → code checks (description, shell command)
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "research": [
        ("Research brief exists", "test -f research/RESEARCH_BRIEF.md"),
        ("Literature grounding exists", "test -f research/LITERATURE_GROUNDING.json"),
        ("Source discovery exists", "test -f research/SOURCE_DISCOVERY.md"),
        ("Trend insights exists", "test -f research/TREND_INSIGHTS.md"),
        ("BibTeX has entries", "test -f paper/refs.bib && grep -c '@' paper/refs.bib"),
    ],
    "plan": [
        ("Experiment plan exists", "test -f research/EXPERIMENT_PLAN.md"),
    ],
    "benchmark": [
        ("Benchmark provenance exists", "test -f experiments/BENCHMARK_PROVENANCE.md"),
    ],
    "run": [
        ("Results exist", "find experiments -name 'summary.tsv' -o -name 'eval_results.jsonl' 2>/dev/null | head -1 | grep -q ."),
    ],
    "analysis": [
        ("Results report exists", "test -f paper/RESULTS_REPORT.md"),
        ("Results table exists", "test -f paper/artifacts/results_table.tsv"),
        ("Figures exist", "ls paper/figures/*.png paper/figures/*.pdf 2>/dev/null | head -1 | grep -q ."),
    ],
    "draft": [
        ("main.tex exists", "test -f paper/main.tex"),
        ("PDF compiles", "test -f paper/main.pdf"),
        ("Image2 figures valid", "{python} -m argus_skill.skills.pipeline_contracts validate-image2-figures --project-root ."),
    ],
    "review": [
        ("Layout review", "{python} -m argus_skill.skills.pipeline_contracts validate-layout-review --project-root ."),
        ("Academic review", "{python} -m argus_skill.skills.pipeline_contracts validate-academic-language-review --project-root ."),
    ],
    "submission": [
        ("Full EMNLP gate", "{python} -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root . 2>/dev/null | { ! grep -q .; }"),
    ],
}

# Stage → reviewer checklist
# The reviewer agent is a codex agent with shell access in the same workdir.
# It will load the skill, read the files, and do the review itself.
REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    # stage: (skill_to_load, review_instructions, files_to_read)
    "plan": (
        "experiment-plan-review.md",
        "Evaluate the experiment plan on these dimensions:\n"
        "1. Method competitiveness — is the proposed method strong enough vs SOTA?\n"
        "2. Baseline strength — are baselines non-trivial and representative?\n"
        "3. Evaluation fairness — same compute/data budget for all conditions?\n"
        "4. Benchmark adequacy — ≥3 independent real benchmark families?\n"
        "5. Feasibility — can this be executed with available resources?\n"
        "If any dimension scores below 3/5, the plan needs revision before proceeding.",
        ["research/EXPERIMENT_PLAN.md"],
    ),
    "run": (
        "experiment-results-review.md",
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
    "draft": (
        "academic-paper-peer-review-benchmark.md",
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
    "submission": (
        "academic-paper-peer-review-benchmark.md",
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
        return "brief"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("current_stage", "brief")
    except (json.JSONDecodeError, OSError):
        return "brief"


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

    # 2. Output reviewer checklist for critical stages
    #    Reviewer is a codex agent — it reads the skill and files itself.
    if stage in REVIEWER_CHECKLISTS:
        skill_name, instructions, files = REVIEWER_CHECKLISTS[stage]
        print()
        print(f"📋 REVIEWER CHECKLIST for stage '{stage}'")
        print(f"   Load skill: argus_builtin_skills/{skill_name}")
        print(f"   Read and review: {', '.join(files)}")
        print()
        print(instructions)

    total_failed = failed
    print(f"\n{'✅' if total_failed == 0 else '❌'} {passed} passed, {total_failed} failed")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
