"""Stage-aware checklist runner with LLM review at critical stages.

Reads the current pipeline stage from research/PIPELINE_STATE.json and
runs checks relevant to that stage. At critical stages (plan, run, draft),
also calls an LLM to do scientific quality review.

Usage:
    python -m argus_skill.tools.stage_check --project-root .
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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

# Stages that get LLM scientific review
LLM_REVIEW_STAGES: dict[str, tuple[str, str, list[str]]] = {
    # stage: (system_prompt, user_prompt_template, files_to_read)
    "plan": (
        "_LOAD_SKILL:experiment-plan-review.md",
        "Review this experiment plan using the review dimensions above. "
        "Answer in the JSON format specified in the skill.\n\n{content}",
        ["research/EXPERIMENT_PLAN.md"],
    ),
    "run": (
        "_LOAD_SKILL:experiment-results-review.md",
        "Review these experiment results using the review dimensions above. "
        "Answer in the JSON format specified in the skill.\n\n{content}",
        ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
    ),
    "draft": (
        "_LOAD_SKILL:academic-paper-peer-review-benchmark.md",
        "This is a DRAFT-stage progress check, NOT a final peer review. "
        "The goal is to verify the draft is structurally complete enough to move forward. "
        "Be lenient on polish, language quality, and minor gaps — those are fixed in later stages. "
        "Focus on: (1) are all required sections present? (2) do claims have at least placeholder evidence? "
        "(3) is the overall story coherent? (4) are there any fatal structural problems that would block progress? "
        "Answer in JSON with: "
        '{"score": 1-10, "pass": true/false, "recommendation": "Advance/Needs structure fixes", '
        '"strengths": ["..."], "weaknesses": ["..."], '
        '"verdict": "one sentence"}. '
        "Score 3+ = pass (this is a progress gate, not a quality gate).\n\n{content}",
        ["paper/main.tex"],
    ),
    "submission": (
        "_LOAD_SKILL:academic-paper-peer-review-benchmark.md",
        "This is the FINAL submission gate review. Be strict — this paper will be submitted to EMNLP. "
        "Evaluate as an actual EMNLP reviewer would. "
        "Review using the peer review benchmark above. Answer in JSON with: "
        '{"score": 1-10, "pass": true/false, "recommendation": "Accept/Weak Accept/Weak Reject/Reject", '
        '"strengths": ["..."], "weaknesses": ["..."], '
        '"strongest_reject_argument": "...", "verdict": "one sentence"}. '
        "Score 5+ = pass.\n\n{content}",
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


def _read_files(root: Path, paths: list[str], max_chars: int = 120000) -> str:
    """Read files for LLM review. Default 120k chars to fit full papers."""
    parts = []
    total = 0
    for p in paths:
        full = root / p
        if not full.exists():
            continue
        text = full.read_text(encoding="utf-8", errors="replace")
        remaining = max_chars - total
        if remaining <= 0:
            break
        parts.append(f"=== {p} ===\n{text[:remaining]}")
        total += len(parts[-1])
    return "\n\n".join(parts)


def _load_skill_as_system(system: str) -> str:
    """If system starts with _LOAD_SKILL:, load the builtin skill file."""
    if not system.startswith("_LOAD_SKILL:"):
        return system
    skill_name = system.split(":", 1)[1]
    # Try multiple locations
    for base in [
        Path(__file__).resolve().parents[1] / "builtin_skills",
        Path("argus_builtin_skills"),
    ]:
        path = base / skill_name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return f"You are a strict EMNLP peer reviewer. (Skill {skill_name} not found)"


def _llm_review(system: str, prompt: str) -> dict[str, Any] | None:
    """Call the reviewer model for scientific review."""
    system = _load_skill_as_system(system)
    try:
        from argus_skill.tools.capability_vault import load_model_api_route
    except ImportError:
        return None

    route = load_model_api_route("reviewer")
    if route is None or not route.usable:
        route = load_model_api_route("scientist")
    if route is None or not route.usable:
        return None

    payload = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }
    body = json.dumps(payload).encode("utf-8")
    url = f"{route.base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
        # Try to parse as JSON
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as exc:
        return {"error": str(exc), "pass": True, "score": 0, "verdict": f"Review failed: {exc}"}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="stage-check")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--stage", default=None)
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM reviews")
    args = parser.parse_args()

    root = args.project_root.resolve()
    stage = args.stage or _get_current_stage(root)
    python = sys.executable

    print(f"📋 Stage: {stage}")
    print()

    # 1. Run code checks
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

    # 2. Run LLM review if this is a critical stage
    llm_failed = False
    if not args.skip_llm and stage in LLM_REVIEW_STAGES:
        system, prompt_template, files = LLM_REVIEW_STAGES[stage]
        content = _read_files(root, files)
        if content:
            print()
            print(f"  🧠 LLM scientific review...")
            prompt = prompt_template.replace("{content}", content)
            review = _llm_review(system, prompt)
            if review:
                score = review.get("score", 0)
                passed_review = review.get("pass", True)
                verdict = review.get("verdict", "")
                recommendation = review.get("recommendation", "")
                issues = review.get("issues", review.get("weaknesses", []))
                strengths = review.get("strengths", [])
                strongest_reject = review.get("strongest_reject_argument", "")

                icon = "✅" if passed_review else "❌"
                label = f"score={score}"
                if recommendation:
                    label += f", {recommendation}"
                print(f"  {icon} Scientific review: {label}")
                if verdict:
                    print(f"     Verdict: {verdict}")
                if strengths:
                    print(f"     Strengths:")
                    for s in strengths:
                        print(f"       + {s}")
                if issues:
                    print(f"     Issues to fix:")
                    for issue in issues:
                        print(f"       - {issue}")
                if strongest_reject and not passed_review:
                    print(f"     ⚠️  Strongest reject argument: {strongest_reject}")

                # Dump full review JSON so engineer/reviewer see everything
                print()
                print(f"  📄 Full review JSON:")
                print(json.dumps(review, indent=2, ensure_ascii=False))

                if not passed_review:
                    llm_failed = True
                    failed += 1
                else:
                    passed += 1
            else:
                print(f"  ⚠️  LLM review unavailable (no model route)")
    
    total_failed = failed
    print(f"\n{'✅' if total_failed == 0 else '❌'} {passed} passed, {total_failed} failed")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
