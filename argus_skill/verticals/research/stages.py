"""Research-vertical stage definitions and reviewer checklists.

Authoritative location for the 8 paper-pipeline stages
(research → plan → benchmark → run → analysis → draft → review →
submission) and the per-stage shell checks + reviewer checklists.

This module is the **vertical-specific** half of the stage system.
The generic shell-check runner lives at
``argus_skill.tools.stage_check`` and consumes ``STAGE_ORDER``,
``STAGE_CHECKS``, and ``REVIEWER_CHECKLISTS`` from this module via
re-export. Future verticals (quant, rollout, …) will define their
own ``stages.py`` next to this one with their own stage list.

The shell ``_PIPELINE_CHECK`` is the only check that is genuinely
generic across all verticals (every vertical needs a pipeline-state
file) — it is re-exported here for the research stages but should
migrate to ``argus_skill.core.contracts`` if a second vertical lands
that needs it.
"""
from __future__ import annotations

from ...skills.stage_checklists import CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS

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
        ("BibTeX has entries", "test -f paper/refs.bib && grep -c '@' paper/refs.bib"),
        # Research quality and task-specific de-risk evidence are certified by
        # the L2 reviewer against the active Planner-authored checklist below.
        # Shell checks stay structural; they must not infer a domain validator
        # from checklist prose or embed benchmark-specific answers.
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Experiment plan exists", "test -f research/EXPERIMENT_PLAN.md"),
        ("Idea rejection log exists", "test -f research/IDEA_REJECTION_LOG.md"),
        ("Code study notes exist", "test -f research/CODE_STUDY_NOTES.md"),
        ("Baseline plan exists", "test -f research/BASELINE_AND_BENCHMARK_PLAN.md"),
        # NOTE: the draft-first contract (paper/DRAFT_OUTLINE.md must be
        # filled by the end of plan stage) is enforced in-process via
        # `_plan_outline_findings`, not as a shell check. Two reasons:
        #   1. it must respect `--bounded` (a bounded survey/diagnostic
        #      mission is not expected to carry a full paper outline), and
        #      shell checks always count toward the exit code; findings
        #      flow through the M0.7 bounded downgrade.
        #   2. calling the validator in-process avoids a `python3 -c`
        #      subprocess that depends on `argus_skill` being importable by
        #      whatever `python3` happens to resolve to on PATH.
    ],
    "benchmark": [
        _PIPELINE_CHECK,
        (
            "Evaluation provenance exists",
            "test -f experiments/BENCHMARK_PROVENANCE.md || "
            "test -f experiments/BENCHMARK_PROVENANCE.json",
        ),
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
        # Accept either `ready` or `done` here. The submission stage is unique:
        # the reviewer can only flip `submission.status` from `ready` -> `done`
        # AFTER `stage_check --stage submission` passes. Requiring `done` at
        # check-time creates a tautological deadlock — observed empirically
        # on agent-multimodal-reasoning-v1 as a 15-round / 9-hour polish
        # loop where the reviewer kept rejecting because submission wasn't
        # `done`, and engineer kept failing because reviewer wouldn't verdict.
        # `ready` here means upstream stages are done and the cursor is at
        # this stage; the verdict itself is what promotes to `done`.
        ("Submission stage is ready or done", "test -f research/PIPELINE_STATE.json && python3 -c \"import json,sys; d=json.load(open('research/PIPELINE_STATE.json')); st=(d.get('stages') or {}).get('submission') or {}; sys.exit(0 if str(st.get('status','')).lower() in ('ready','done') else 1)\""),
    ],
}

# Stage → reviewer checklist, per venue.
# The reviewer agent is a codex agent with shell access in the same workdir.
# It will load the skill, read the files, and do the review itself.
#
# EMNLP and AAAI are peers: five stages (research/plan/benchmark/analysis/draft)
# are venue-neutral and shared verbatim; the three format-bearing stages
# (run/review/submission) have a NATIVE definition per venue. ``reviewer_
# checklists_for(venue)`` returns the venue's full native dict — there is no
# EMNLP-privileged path or per-string patching.
REVIEWER_CHECKLISTS_EMNLP: dict[str, tuple[str, str, list[str]]] = {
    # stage: (skill_to_load, review_instructions, files_to_read)
    "research": (
        "engineer/research-brief-to-experiment-plan.md",
        "Evaluate the research foundation on these dimensions:\n"
        "1. Problem clarity — is the research gap well-defined and grounded in literature?\n"
        "2. Lineage coverage (NOT paper count) — does the canonical literature "
        "ledger plus RESEARCH_BRIEF reconstruct the field from relevant foundations "
        "through the nearest competitors to the open frontier? A flat list is "
        "shallow regardless of how many papers it contains.\n"
        "3. Source fitness — are primary scholarly/official sources used for "
        "technical claims, with trend sources used only when they add a concrete "
        "testable signal?\n"
        "4. Trend grounding — are trend insights converted to testable research questions?\n"
        "5. Direction viability — is this a real frontier gap, not just an incremental tweak?\n"
        "6. Reference code — were related papers' official repos cloned and studied?\n"
        "7. **Source-integrity audit (HARD)** — retained literature must come from "
        "real primary URLs, not model memory. Validate the canonical ledger with "
        "`python -m argus_skill.verticals.research.literature_ledger check`. "
        "Independently refetch an entry only when its URL/provenance is missing, "
        "contradictory, implausible, or material to a disputed claim. Do not grade "
        "research quality by curl/query counts and do not repeat a source audit "
        "already certified in an earlier bounded mission without a concrete conflict.\n"
        "8. **Research de-risk audit (HARD)** — read the active "
        "`research.signal_derisk` project checklist item and audit exactly the "
        "task-specific evidence contract the Planner authored there. For the "
        "default measured-signal workflow, inspect SIGNAL_DERISK.json plus its raw "
        "log and optionally run `python -m argus_skill.skills.signal_derisk "
        "validate` as a consistency/provenance diagnostic. If the Planner replaced "
        "that item for a theorem, systems result, survey, or another research shape, "
        "follow the replacement evidence paths and reasoning directly. BLOCK when "
        "the evidence is missing, fabricated, internally inconsistent, or does not "
        "satisfy the active checklist; do not require inapplicable performance "
        "metrics and do not let a task-specific Python validator decide quality.\n"
        "Pass threshold: a clear gap with claim-complete primary-source backing, "
        "not agent brainstorming or recalled papers.",
        ["research/RESEARCH_BRIEF.md", "research/LITERATURE_GROUNDING.json",
         "research/CHECKLISTS.json", "research/SIGNAL_DERISK.json",
         "research/SIGNAL_DERISK_LOG.txt"],
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
        "Evaluate empirical-evidence preparation against the active Planner-authored "
        "benchmark checklist, not an assumed machine-learning benchmark shape:\n"
        "1. Provenance — are every used public source, planned source/cohort, version, "
        "license/access condition, analysis unit, and evidence ceiling recorded?\n"
        "2. Domain fit — computational projects must justify benchmark-family/task "
        "coverage; clinical or mechanism projects must instead verify public data, "
        "comparators/controls, planned cohorts, protocol status, and ethical gates. "
        "Never relabel participants, visits, or nights as benchmark tasks.\n"
        "3. Authentic implementation — does the real loader/evaluator/statistical "
        "analysis produce observation-level rows and uncertainty from source data, "
        "without stubs, fabricated labels, or success-shaped fallbacks?\n"
        "4. Claim boundary — are executed evidence and planned experiments separated, "
        "with null/cross-zero results and unsupported claims preserved?\n"
        "5. Reproducibility — can another researcher retrieve the public source and "
        "recompute every reported result under the stated access and ethics limits?\n"
        "Pass threshold: every item in research/CHECKLISTS.json for the benchmark "
        "stage is supported by authentic local artifacts.",
        [
            "research/CHECKLISTS.json",
            "experiments/BENCHMARK_PROVENANCE.md",
            "experiments/BENCHMARK_PROVENANCE.json",
        ],
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
        "If results are too weak to support an EMNLP paper, do NOT auto-pivot — apply the failure-decision ladder in reviewer/experiment-results-review.md: reflect on WHY it fell short (evidence-cited), recommend ONE bounded optimization/re-run pass if a concrete fix exists, else proceed to write the paper honestly on the current results as a negative / limited-gain finding; reserve a full pivot only when the results support neither a win nor an honest negative-result paper.",
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
        "4. Idea spine — is the draft organized around ONE central thesis, with the method and experiments visibly serving it (not scattered, unconnected results)? Flag an experiment dump with no stated insight even at this lenient stage.\n"
        "5. Are there fatal structural problems that would block progress?\n"
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
        "6. Idea-centricity and honest framing — does the paper revolve around one central thesis with a stated non-trivial insight, and where a comparison underperforms, is it scoped to a supported regime with an explicit boundary analysis rather than a flat concession or a hidden/cherry-picked table? Integrity floor: all planned claim-relevant comparisons remain reported; no cherry-picking; genuine nulls go to limitations/scope; only broken or inconclusive runs may be excluded, and only with a stated reason.\n"
        "If any review artifact has unresolved major issues, block until fixed.",
        ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
         "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual EMNLP reviewer.\n"
        "Review dimensions (all must pass):\n"
        "1. Central idea and insight — one thesis the whole paper serves, with a stated non-trivial insight; method and experiments visibly serve that thesis (not an experiment dump), and the contribution is a meaningful advance — a new mechanism, insight, or a surprising negative/boundary result — not an incremental tweak.\n"
        "2. Evidence strength and honest framing — experiments convincingly support the SCOPED claim; where the method underperforms a baseline, the paper scopes the claim to the supported regime and adds a boundary analysis WITHOUT hiding any planned claim-relevant comparison (no cherry-picking; genuine nulls in limitations/scope; only broken or inconclusive runs excluded with a stated reason).\n"
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


# AAAI-native overrides for the three format-bearing stages. The other five
# stages (research/plan/benchmark/analysis/draft) are venue-neutral and shared
# verbatim from the EMNLP dict below.
_AAAI_STAGE_OVERRIDES: dict[str, tuple[str, str, list[str]]] = {
    "run": (
        "reviewer/experiment-results-review.md",
        "Evaluate the experiment results on these dimensions:\n"
        "1. Statistical significance — are gains significant, not noise?\n"
        "2. Ablation fairness — does ablation isolate the claimed contribution?\n"
        "3. Effect size — are improvements meaningful, not cosmetic?\n"
        "4. Claim support — does data actually support each claim?\n"
        "5. Baseline competitiveness — did proposed method beat strong baselines?\n"
        "6. Completeness — all conditions run, no missing benchmark families?\n"
        "If results are too weak to support an AAAI paper, do NOT auto-pivot — apply the failure-decision ladder in reviewer/experiment-results-review.md: reflect on WHY it fell short (evidence-cited), recommend ONE bounded optimization/re-run pass if a concrete fix exists, else proceed to write the paper honestly on the current results as a negative / limited-gain finding; reserve a full pivot only when the results support neither a win nor an honest negative-result paper.",
        ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
    ),
    "review": (
        "reviewer/aaai-academic-language-review.md",
        "Evaluate the review artifacts on these dimensions:\n"
        "1. Layout review — does LAYOUT_REVIEW.json pass? Are pages well-balanced, figures readable?\n"
        "2. Academic language — does ACADEMIC_LANGUAGE_REVIEW.json pass? No hype, salesy language, or vague claims?\n"
        "3. Infrastructure leaks — does PAPER_INFRASTRUCTURE_REVIEW.json pass? No local paths, device names, or Argus/Codex references in manuscript?\n"
        "4. Citation quality — all citations author-year natbib, no dumping, no placeholders?\n"
        "5. Page budget — body ≤7 pages, conclusion by page 7, references start page 8+, Reproducibility Checklist after References?\n"
        "6. Idea-centricity and honest framing — does the paper revolve around one central thesis with a stated non-trivial insight, and where a comparison underperforms, is it scoped to a supported regime with an explicit boundary analysis rather than a flat concession or a hidden/cherry-picked table? Integrity floor: all planned claim-relevant comparisons remain reported; no cherry-picking; genuine nulls go to limitations/scope; only broken or inconclusive runs may be excluded, and only with a stated reason.\n"
        "If any review artifact has unresolved major issues, block until fixed.",
        ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
         "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual AAAI reviewer.\n"
        "Review dimensions (all must pass):\n"
        "1. Central idea and insight — one thesis the whole paper serves, with a stated non-trivial insight; method and experiments visibly serve that thesis (not an experiment dump), and the contribution is a meaningful advance — a new mechanism, insight, or a surprising negative/boundary result — not an incremental tweak.\n"
        "2. Evidence strength and honest framing — experiments convincingly support the SCOPED claim; where the method underperforms a baseline, the paper scopes the claim to the supported regime and adds a boundary analysis WITHOUT hiding any planned claim-relevant comparison (no cherry-picking; genuine nulls in limitations/scope; only broken or inconclusive runs excluded with a stated reason).\n"
        "3. Baseline quality — are comparisons against strong, relevant baselines?\n"
        "4. Writing quality — is the paper well-written and clear?\n"
        "5. Reproducibility — enough detail to reproduce results?\n"
        "6. Significance — would AAAI reviewers find this interesting?\n"
        "7. Format compliance — AAAI format, page budget, references, reproducibility checklist?\n"
        "8. Claim-evidence alignment — every claim backed by specific data?\n"
        "Score 5+/10 to pass. If the paper would get Reject at AAAI, fail it here.",
        ["paper/main.tex"],
    ),
}

#: AAAI-native reviewer checklists: neutral stages shared, format stages native.
REVIEWER_CHECKLISTS_AAAI: dict[str, tuple[str, str, list[str]]] = {
    **REVIEWER_CHECKLISTS_EMNLP,
    **_AAAI_STAGE_OVERRIDES,
}

_FRONTIERS_SLEEP_STAGE_OVERRIDES: dict[str, tuple[str, str, list[str]]] = {
    "run": (
        "reviewer/experiment-results-review.md",
        "Evaluate the evidence used by this Frontiers in Sleep Hypothesis and Theory article:\n"
        "1. Executed evidence — is every original analysis, if present, produced from authentic records with uncertainty?\n"
        "2. Planned evidence — is every proposed or unimplemented study labeled as a plan, never a result?\n"
        "3. Claim boundary — are prior evidence, original analysis, interpretation, and proposed direct tests kept distinct?\n"
        "4. Statistical integrity — are null, uncertain, or crossed-zero findings reported without spin?\n"
        "5. Reproducibility — can every original analysis and planning calculation used by the article be recomputed, or is the item explicitly N/A?\n"
        "Block any fabricated participant, outcome, registration, efficacy claim, or success-shaped fallback.",
        [
            "paper/artifacts/results_summary.tsv",
            "paper/artifacts/claims_evidence.tsv",
            "experiments/",
            "research/EXPERIMENT_PLAN.md",
        ],
    ),
    "review": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "Evaluate the final Frontiers in Sleep review artifacts:\n"
        "1. Academic language — international-standard English, bounded hypothesis framing, no internal workflow prose.\n"
        "2. Layout — official Frontiers Harvard basis, single spacing, page and line numbers; Frontiers has NO fixed page limit, so judge readability rather than conference page quotas.\n"
        "3. Authorship — single-anonymized review requires real author names, affiliations, corresponding email, CRediT contributions, conflicts, and funding.\n"
        "4. AI disclosure — public, journal-compliant disclosure of technology name/version/model/source; no internal routes, daemons, or orchestration details.\n"
        "5. Figures — every figure is authentic, reviewed, and has distinct alt text; the core overview is recorded in IMAGE2_FIGURES.json.\n"
        "6. Evidence — every numerical or headline claim traces to current canonical evidence; executed and planned evidence remain distinct.\n"
        "7. Idea-centricity and honest framing — does the article revolve around one central testable thesis with a stated conceptual insight, treat null or uncertain evidence honestly without spin, scope every claim to the supported evidence, and keep planned and executed evidence distinct? Do not hide evidence that was produced; genuine nulls are reported as findings.\n"
        "Block if any review artifact is stale, unavailable, or has unresolved major issues.",
        [
            "paper/main.tex",
            "paper/LAYOUT_REVIEW.json",
            "paper/ACADEMIC_LANGUAGE_REVIEW.json",
            "paper/PAPER_INFRASTRUCTURE_REVIEW.json",
            "paper/figures/IMAGE2_FIGURES.json",
        ],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual Frontiers in Sleep reviewer.\n"
        "All must pass: article-type fit; one central testable thesis with a stated conceptual insight that the article is organized around; testable theoretical contribution; honest claim-evidence alignment (scope claims to the supported evidence, report null or uncertain findings without spin, keep planned and executed evidence distinct, and hide no evidence that was produced); international-standard English; Frontiers Harvard source/PDF; main text ≤12,000 words with no fixed page quota; single spacing; page and line numbers; real single-anonymized author metadata; ethics/funding/conflict/CRediT/data/AI declarations; reviewed figures with alt text; reproducibility of any original analysis; and explicit operator approval for submission/APC exposure.\n"
        "An explicitly proposed study may remain unimplemented for a Hypothesis and Theory article, but its implementation status and every planning value must remain explicit. Do not pass until SUBMISSION_ASSURANCE.json and every upstream checklist are current and passing.",
        [
            "paper/main.tex",
            "paper/main.pdf",
            "paper/SUBMISSION_ASSURANCE.json",
            "paper/FORMAT_PREFLIGHT.md",
            "paper/artifacts/claims_evidence.tsv",
        ],
    ),
}

REVIEWER_CHECKLISTS_FRONTIERS_SLEEP: dict[
    str, tuple[str, str, list[str]]
] = {
    **REVIEWER_CHECKLISTS_EMNLP,
    **_FRONTIERS_SLEEP_STAGE_OVERRIDES,
}

#: Registry: venue key -> that venue's full native reviewer checklists. Both
#: venues are peers; add an entry here to onboard a new venue.
REVIEWER_CHECKLISTS_BY_VENUE: dict[str, dict[str, tuple[str, str, list[str]]]] = {
    "EMNLP": REVIEWER_CHECKLISTS_EMNLP,
    "AAAI": REVIEWER_CHECKLISTS_AAAI,
    "FRONTIERS_SLEEP": REVIEWER_CHECKLISTS_FRONTIERS_SLEEP,
}

#: Back-compat alias for importers predating the per-venue split (EMNLP
#: default). New callers use ``reviewer_checklists_for(venue)``.
REVIEWER_CHECKLISTS = REVIEWER_CHECKLISTS_EMNLP

#: The five venue-NEUTRAL stages, shared by every venue (built-in and dynamic).
#: The three format-bearing stages (run/review/submission) are native per
#: built-in venue, or generated from a VenueProfile by build_reviewer_checklists.
_NEUTRAL_STAGES = ("research", "plan", "benchmark", "analysis", "draft")
_NEUTRAL_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    stage: REVIEWER_CHECKLISTS_EMNLP[stage] for stage in _NEUTRAL_STAGES
}


def build_reviewer_checklists(
    profile: object,
) -> dict[str, tuple[str, str, list[str]]]:
    """Generate a full reviewer-checklist dict for a venue that has no hand-
    written native dict (a dynamically-researched :class:`VenueProfile`).

    The five neutral stages are shared verbatim; the three format-bearing
    stages (run/review/submission) are generated from the profile's own fields
    (``reviewer_persona``, ``page_budget_line()``, ``end_matter_prose()``), so a
    NeurIPS/ICML/... paper is graded against its own venue, not EMNLP. Uses the
    venue-neutral ``academic-paper-peer-review-benchmark`` review skill (a
    dynamic venue has no bespoke academic-language-review skill).
    """
    persona = getattr(profile, "reviewer_persona", None) or "the target venue"
    budget = profile.page_budget_line()
    end_matter = profile.end_matter_prose()
    generated: dict[str, tuple[str, str, list[str]]] = {
        "run": (
            "reviewer/experiment-results-review.md",
            "Evaluate the experiment results on these dimensions:\n"
            "1. Statistical significance — are gains significant, not noise?\n"
            "2. Ablation fairness — does ablation isolate the claimed contribution?\n"
            "3. Effect size — are improvements meaningful, not cosmetic?\n"
            "4. Claim support — does data actually support each claim?\n"
            "5. Baseline competitiveness — did proposed method beat strong baselines?\n"
            "6. Completeness — all conditions run, no missing benchmark families?\n"
            f"If results are too weak to support a {persona} paper, do NOT auto-pivot — apply the failure-decision ladder in reviewer/experiment-results-review.md: reflect on WHY it fell short (evidence-cited), recommend ONE bounded optimization/re-run pass if a concrete fix exists, else proceed to write the paper honestly on the current results as a negative / limited-gain finding; reserve a full pivot only when the results support neither a win nor an honest negative-result paper.",
            ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
        ),
        "review": (
            "reviewer/academic-paper-peer-review-benchmark.md",
            "Evaluate the review artifacts on these dimensions:\n"
            "1. Layout review — does LAYOUT_REVIEW.json pass? Are pages well-balanced, figures readable?\n"
            "2. Academic language — does ACADEMIC_LANGUAGE_REVIEW.json pass? No hype, salesy language, or vague claims?\n"
            "3. Infrastructure leaks — does PAPER_INFRASTRUCTURE_REVIEW.json pass? No local paths, device names, or Argus/Codex references in manuscript?\n"
            "4. Citation quality — all citations author-year, no dumping, no placeholders?\n"
            f"5. Page budget — {budget}?\n"
            "6. Idea-centricity and honest framing — does the paper revolve around one central thesis with a stated non-trivial insight, and where a comparison underperforms, is it scoped to a supported regime with an explicit boundary analysis rather than a flat concession or a hidden/cherry-picked table? Integrity floor: all planned claim-relevant comparisons remain reported; no cherry-picking; genuine nulls go to limitations/scope; only broken or inconclusive runs may be excluded, and only with a stated reason.\n"
            "If any review artifact has unresolved major issues, block until fixed.",
            ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
             "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
        ),
        "submission": (
            "reviewer/academic-paper-peer-review-benchmark.md",
            f"FINAL submission gate — be STRICT, evaluate as an actual {persona} reviewer.\n"
            "Review dimensions (all must pass):\n"
            "1. Central idea and insight — one thesis the whole paper serves, with a stated non-trivial insight; method and experiments visibly serve that thesis (not an experiment dump), and the contribution is a meaningful advance — a new mechanism, insight, or a surprising negative/boundary result — not an incremental tweak.\n"
            "2. Evidence strength and honest framing — experiments convincingly support the SCOPED claim; where the method underperforms a baseline, the paper scopes the claim to the supported regime and adds a boundary analysis WITHOUT hiding any planned claim-relevant comparison (no cherry-picking; genuine nulls in limitations/scope; only broken or inconclusive runs excluded with a stated reason).\n"
            "3. Baseline quality — are comparisons against strong, relevant baselines?\n"
            "4. Writing quality — is the paper well-written and clear?\n"
            "5. Reproducibility — enough detail to reproduce results?\n"
            f"6. Significance — would {persona} reviewers find this interesting?\n"
            f"7. Format compliance — {persona} format ({budget}), references, {end_matter}?\n"
            "8. Claim-evidence alignment — every claim backed by specific data?\n"
            f"Score 5+/10 to pass. If the paper would get Reject at {persona}, fail it here.",
            ["paper/main.tex"],
        ),
    }
    return {**_NEUTRAL_CHECKLISTS, **generated}


def reviewer_checklists_for(venue: object) -> dict[str, tuple[str, str, list[str]]]:
    """Return the reviewer checklists for ``venue``.

    - A built-in venue key ("EMNLP"/"AAAI"/"FRONTIERS_SLEEP") -> its
      hand-written NATIVE dict.
    - A dynamic :class:`VenueProfile` (a researched venue not in the registry)
      -> checklists generated from the profile by :func:`build_reviewer_checklists`.
    - A bare unknown venue-key string with no profile -> raises, so a new venue
      is never silently graded against another venue's checklist.
    """
    key = str(getattr(venue, "key", venue)).upper()
    if key in REVIEWER_CHECKLISTS_BY_VENUE:
        return REVIEWER_CHECKLISTS_BY_VENUE[key]
    # Dynamic venue: only a real VenueProfile (with the fields we template on)
    # can be built; a bare unknown string cannot.
    if hasattr(venue, "reviewer_persona") and callable(
        getattr(venue, "page_budget_line", None)
    ):
        return build_reviewer_checklists(venue)
    raise KeyError(
        f"no reviewer checklists for venue {key!r}: pass a built-in key or a "
        "VenueProfile (dynamic venues are built from their profile)"
    )


# ===========================================================================
# System (B) — markdown stage checklists for the research vertical
# ===========================================================================
#
# The research vertical's System-(B) definitions are RE-EXPORTS of the existing
# paper floor authored in ``argus_skill.skills.stage_checklists``: same stage
# order, same per-stage ``ChecklistItem`` dict. This is a no-behavior-change
# declaration — it lets ``stage_checklists`` resolve the active vertical through
# the ``argus_skill.verticals._base`` optional-hook contract and, for the
# research vertical, get back the IDENTICAL order + items object it already
# rendered, so the research/paper checklist output stays byte-identical.
CHECKLIST_STAGE_ORDER = CANONICAL_STAGE_ORDER
CHECKLIST_ITEMS = STAGE_CHECKLISTS

#: Research missions complete on the full EMNLP/paper final-submission gate.
completion_gate = "full_paper"

# Research proceeds through strict stage gates, but evidence reuse within those
# stages is proportional: once a Reviewer certifies a source or artifact, later
# bounded missions verify only the new claim/delta unless a concrete conflict
# reopens it. This keeps scientific integrity without repeatedly rebuilding the
# same provenance tree.
WORKFLOW_MODE = "proportional"


def role_banner(_role: str = "engineer") -> str:
    """No top-of-prompt override for the research vertical (the default).

    The planner/reviewer/engineer prompts are already authored for the paper
    pipeline, so the research vertical injects no banner.
    """
    return ""


__all__ = [
    "STAGE_ORDER",
    "STAGE_CHECKS",
    "REVIEWER_CHECKLISTS",
    "_PIPELINE_CHECK",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "WORKFLOW_MODE",
    "role_banner",
    "completion_gate",
]
