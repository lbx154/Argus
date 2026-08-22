"""Research-vertical stage definitions and checklists.

Authoritative location for the 8 paper-pipeline stages
(research → plan → benchmark → run → analysis → draft → review →
submission), the per-stage markdown ``CHECKLIST_ITEMS`` the L2 Reviewer
certifies against, and the venue-specific checklist rendering.

This module is the **vertical-specific** half of the stage system. Future
verticals define their own ``stages.py`` with their own stage list and
checklist items.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem
from . import library_preparation
from .prompt_policy import render_role_prompt_fragment
from .venue_profiles import VenueProfile, resolve_venue_profile

LIBRARY_PREPARER = library_preparation.prepare_skill_libraries

CANONICAL_STAGE_ORDER: tuple[str, ...] = (
    "research",
    "plan",
    "benchmark",
    "run",
    "analysis",
    "draft",
    "review",
    "submission",
)


def _checklist(*items: ChecklistItem) -> tuple[ChecklistItem, ...]:
    return tuple(items)


STAGE_CHECKLISTS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": _checklist(
        ChecklistItem(
            id="research.literature",
            statement=(
                "The literature ledger covers claim-critical competitors, the "
                "AI-venue/recent-arXiv frontier, foundations, and contradictions. "
                "Source-mix imbalance is an advisory risk, not a fixed quota or "
                "completion blocker. Retained sources need primary URLs and project "
                "implications; judge connected coverage."
            ),
            evidence_hint=(
                "research/LITERATURE_GROUNDING.json (canonical); "
                "research/LIT_MATRIX.tsv is generated with "
                "`python -m argus_skill.verticals.research.literature_ledger sync`"
            ),
        ),
        ChecklistItem(
            id="research.idea_portfolio",
            statement=(
                "A 12-route team explores concurrently; each result gets an independent "
                "review, and a fresh selector acts at the 80% review quorum without "
                "waiting for the final routes."
            ),
            evidence_hint=(
                "research/IDEA_PORTFOLIO.json + research/ideation/portfolios/**/"
                "{routes,reviews,probes} + team tasks/shards"
            ),
        ),
        ChecklistItem(
            id="research.adversarial_selection",
            statement=(
                "After at least 80% of reviews (10/12 by default), a fresh Agent selects "
                "a current-frontier contribution that is either a high-novelty method or "
                "a publication-scale empirical study. No-training convenience, shortest "
                "evidence path, cheapness, and single-GPU fit are not ranking advantages; "
                "resource gaps become an explicit staged compute plan. Probe metrics "
                "cannot veto the choice; final routes do not block planning."
            ),
            evidence_hint=(
                "research/IDEA_SELECTION.json + selected route/review/EVIDENCE.json"
            ),
        ),
        ChecklistItem(
            id="research.brief",
            statement=(
                "A research brief frames the problem, the gap in prior work, and "
                "the proposed direction in citation-grounded prose."
            ),
            evidence_hint="research/RESEARCH_BRIEF.md",
        ),
        ChecklistItem(
            id="research.thesis",
            statement=(
                "The selected thesis has a plausible nontrivial technical core, "
                "originality, formal/causal structure, field-level potential, and an "
                "evidence path sized to the contribution: a high-novelty method or a "
                "publication-scale empirical study, not merely a small diagnostic. "
                "Research review is qualitative: no finished theorem, fixed implementation, "
                "or reliable effect size is required. Reject clear duplicates, trivial "
                "prompt/schema/wrapper/scale variants, incoherent mechanisms, or decorative "
                "math. Before any probe is designed or executed, lock method reasonableness; "
                "the thesis may evolve later."
            ),
            evidence_hint=(
                "research/RESEARCH_BRIEF.md and research/ideation/{routes,debates}/"
            ),
        ),
        ChecklistItem(
            id="research.signal_derisk",
            statement=(
                "Research does not decide whether the selected empirical idea succeeds. "
                "After research.thesis admits a candidate, optionally run one <=10-minute "
                "feasibility observation only when it checks plumbing, data shape, or "
                "evaluator availability without masquerading as a hypothesis test. For "
                "training-heavy or large-scale empirical work, explicitly skip the probe "
                "as untested and advance to plan/benchmark/run. The Planner authors the "
                "evidence contract. Preserve raw evidence honestly. A weak, failed, or "
                "inconclusive probe cannot kill, block, downgrade, or re-rank a qualified "
                "idea or become a mechanical routing decision. Infrastructure failures, "
                "saturation, and missing predeclared power or headroom are limitations; "
                "later stages own scientific outcomes and decisive benchmarks. "
                "`argus_skill.verticals.research.signal_derisk validate` is available "
                "only for "
                "the default scalar-comparison shape and never decides quality."
            ),
            evidence_hint=(
                "Planner-authored research.signal_derisk evidence paths; for the "
                "default scalar shape use research/SIGNAL_DERISK.json + raw log; "
                "verdict in research/ideas/<id>/EVIDENCE.json, checked by "
                "`...verticals.research.idea_evidence check`"
            ),
        ),
    ),
    "plan": _checklist(
        ChecklistItem(
            id="plan.backbone",
            statement=(
                "For model-backed experiments, select the headline backbone from a "
                "current open model generation after checking the live model catalog, "
                "release dates, architecture, context support, and relevant leaderboard "
                "or official evaluations. Record exact org/model id, release date, "
                "parameter count, attention/KV architecture, and why it tests this claim. "
                "Previous-generation models may be plumbing or compatibility baselines, "
                "never the primary publication evidence merely because they are cached, "
                "familiar, or easy to fit. Read `argus_builtin_skills/engineer/"
                "training-infrastructure-guide.md` before locking the plan."
            ),
            evidence_hint=(
                "research/INFRA_CHOICE.md + research/EXPERIMENT_PLAN.md model table "
                "with dated current-generation comparison"
            ),
        ),
        ChecklistItem(
            id="plan.experiment",
            statement=(
                "Experiment plan states the hypothesis, the proposed method, the "
                "baselines (including the strongest feasible prior work), the "
                "ablations, the metrics, the interpretation and stopping criteria, "
                "and the compute / API budget. Numeric keep/reject cutoffs require "
                "an external utility, risk, domain-standard, prior-evidence, theory, "
                "or prospective-sensitivity basis; unsupported round-number gains "
                "must not become binary gates."
            ),
            evidence_hint="research/EXPERIMENT_PLAN.md",
        ),
        ChecklistItem(
            id="plan.benchmark",
            statement=(
                "The evaluation-source and comparator plan matches the empirical "
                "domain. Every final empirical claim includes at least one "
                "appropriate public benchmark, dataset, task suite, challenge, or "
                "official evaluation release with URL, version, license/access, "
                "evaluation unit, metric, and claim tested. The number of public "
                "sources, tasks, models, and repeats is justified by the claim scope "
                "and uncertainty method rather than a universal quota. "
                "Clinical or mechanism projects instead enumerate every real public "
                "data source, comparator/control, and planned cohort, including "
                "source URL (or the prospective registry plan), license/access "
                "conditions, observed or planned scale, implementation status, and "
                "the evidence ceiling. Unimplemented cohorts must be labeled "
                "planned with task_count=0; participant visits or nights must never "
                "be relabeled as benchmark tasks."
            ),
            evidence_hint="research/BASELINE_AND_BENCHMARK_PLAN.md, experiments/BENCHMARK_PROVENANCE.json",
        ),
        ChecklistItem(
            id="plan.publication_scale",
            statement=(
                "Calibrate the claim-bearing experiment plan against recent accepted "
                "same-area papers from the selected venue or a comparable top venue. "
                "Record official acceptance URLs and compare models/systems, public "
                "sources, evaluation units, repeats or proof obligations, strongest "
                "comparisons, and uncertainty/formal guarantees. Do not copy their "
                "exact scale as a quota; explain what evidence this claim needs. A "
                "small pilot may de-risk implementation, but it cannot be the planned "
                "final evidence merely because the claim could later be narrowed."
            ),
            evidence_hint=(
                "research/EXPERIMENT_PLAN.md publication-scale section + accepted "
                "paper official/PDF sources"
            ),
        ),
        ChecklistItem(
            id="plan.argument_organization",
            statement=(
                "Read at least two accepted same-area full papers with a similar "
                "contribution shape and inspect available official code at pinned "
                "revisions. Extract each paper's problem setup, gap move, organizing "
                "insight, contribution order, Method decomposition, evidence sequence, "
                "Figure 1 role, limitations role, and conclusion move. For code, map "
                "entry points, modules, config/evaluation flow, and artifact ownership. "
                "Write `paper/style_ref/ARGUMENT_ORGANIZATION.json` and transfer those "
                "roles to this paper's own claims/evidence. Reproduction is not "
                "required; copying prose, examples, figure design, or code is forbidden."
            ),
            evidence_hint=(
                "`python -m argus_skill.verticals.research.argument_organization "
                "--project-root .` + downloaded PDFs/text + official code URLs/revisions"
            ),
        ),
        ChecklistItem(
            id="plan.code_reuse",
            statement=(
                "Code-reuse plan lists every external repo we will run, fork, or "
                "extract from, with what we will reuse vs reimplement."
            ),
            evidence_hint="research/CODE_REUSE_PLAN.json or .md",
        ),
        ChecklistItem(
            id="plan.infra_choice",
            statement=(
                "If training or large-scale inference is required, a final "
                "training-infra and inference-infra choice is locked in after the "
                "idea survives research de-risk. Compare only credible candidates "
                "that materially differ for this workload; reuse previously "
                "certified framework evidence when current. Clone and inspect the "
                "chosen framework and any code-critical comparator, not an arbitrary "
                "quota. The choice must be maintained, open-source, non-trivial, and "
                "compatible with the method and resource budget; record the decisive "
                "tradeoff and one rejected alternative. Do not write a custom "
                "trainer/inference stub when a suitable maintained framework exists."
            ),
            evidence_hint=(
                "research/INFRA_CHOICE.md (short comparison + final choice) + "
                "research/EXPERIMENT_PLAN.md `## Infra` + chosen repo evidence"
            ),
        ),
    ),
    "benchmark": _checklist(
        ChecklistItem(
            id="benchmark.environment_preflight",
            statement=(
                "Before the first real evidence-producing call, the engineer ran the "
                "Environment Readiness Gate (`argus_builtin_skills/engineer/"
                "environment-readiness-gate.md`) and captured the verbatim "
                "output to `experiments/runs/<run_id>/preflight.txt`. The "
                "preflight verifies only resources the experiment actually uses: "
                "the declared project environment, required framework/compiler "
                "imports, public data access, evaluator availability, storage, and "
                "the selected compute backend. CUDA, HF caches, model weights, or API "
                "routes are required only when the run uses them. No applicable "
                "preflight evidence means downstream benchmark items remain open."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt",
        ),
        ChecklistItem(
            id="benchmark.tasks",
            statement=(
                "Each selected public evidence source has a reproducible loader or "
                "retrieval path and its official labels, outcomes, evaluator, or "
                "analysis semantics. Locally generated diagnostics are clearly "
                "separated and never presented as the public benchmark."
            ),
            evidence_hint="public benchmark/data manifest + loader/evaluator",
        ),
        ChecklistItem(
            id="benchmark.provenance",
            statement=(
                "One canonical machine-readable benchmark provenance record lists "
                "every selected public source with "
                "version/date, license/access, split or cohort, evaluation unit, "
                "metric/evaluator, filtering, claim tested, and execution-readiness. "
                "Coverage breadth is justified by the claim. Markdown may be a "
                "generated view, but duplicate prose is not a separate gate."
            ),
            evidence_hint="experiments/BENCHMARK_PROVENANCE.json (canonical)",
        ),
        ChecklistItem(
            id="benchmark.smoke",
            statement=(
                "A faithful smoke run produced real evidence through each distinct "
                "evaluation path that the main experiment depends on."
            ),
            evidence_hint="experiments/**/smoke/*.jsonl",
        ),
        ChecklistItem(
            id="benchmark.evaluator_authentic",
            statement=(
                "The evaluation or analysis implementation is authentic for the "
                "project's empirical domain, not a stub or success-shaped oracle. "
                "For computational benchmarks, inspect that it loads actual outputs "
                "and calls the official scorer/metric rather than returning a "
                "constant. For clinical or mechanism projects, inspect that the "
                "pipeline loads the cited public source records, constructs the "
                "prespecified observation-level outcome, retains exclusions and "
                "failures, and computes the reported estimate and uncertainty from "
                "those records. Candidate and baseline prediction paths cannot read "
                "gold labels, expected outcomes, or scorer-derived fields; removing "
                "or permuting hidden labels must not change predictions. Online "
                "intervention claims require executable comparisons with the same "
                "decision-time information, not only historical traces or post-hoc "
                "judges. Never invent an evaluator, gold label, participant, visit, "
                "or task merely to satisfy this item."
            ),
            evidence_hint=(
                "computational: evaluator source + official scorer outputs; "
                "clinical/mechanism: public-source loader/analysis code + derived "
                "rows + machine-readable result and uncertainty"
            ),
        ),
    ),
    "run": _checklist(
        ChecklistItem(
            id="run.backbone",
            statement=(
                "Headline result artifacts identify and actually execute the planned "
                "current-generation backbone. If the live catalog has materially moved "
                "since planning, refresh the choice before expensive reruns. Older-model "
                "results remain compatibility evidence, not the paper's main result."
            ),
            evidence_hint="experiment manifests + model revision/release metadata",
        ),
        ChecklistItem(
            id="run.manifests",
            statement=(
                "Each long-running experiment writes manifest.json, status.json, "
                "progress.jsonl, raw scored rows, and obeys the STOP-file "
                "cancellation contract."
            ),
            evidence_hint="experiments/<run>/{manifest,status}.json + progress.jsonl + raw rows",
        ),
        ChecklistItem(
            id="run.matrix",
            statement=(
                "The proposed contribution, strongest relevant comparisons, and "
                "claim-critical controls/ablations have completed on the selected "
                "public evidence sources, or have explicit evidence-backed "
                "exclusions."
            ),
            evidence_hint="experiments/**/scored.jsonl across all method × family cells",
        ),
        ChecklistItem(
            id="run.scale",
            statement=(
                "Final empirical claims include executed public benchmark/data "
                "evidence at a scale justified by the claim and uncertainty method. "
                "Synthetic/generated diagnostics are labeled supplementary and are "
                "not the sole final evidence. A run marked full is not publication-"
                "scale merely because its manifest says so: compare the executed "
                "evidence dimensions with recent accepted same-area work, and run "
                "what is missing to reach that bar. A claim worth making is worth "
                "the evidence that carries it."
            ),
            evidence_hint="experiments/**/manifest.json declares scale=full",
        ),
        ChecklistItem(
            id="run.score_variance",
            statement=(
                "Spot-check scored rows per evidence family. A file with >3 rows "
                "and one identical score throughout is stub evidence unless the "
                "official task genuinely permits that outcome; require the authentic "
                "scorer before completing run."
            ),
            evidence_hint=(
                "`python -m argus_skill.verticals.research.integrity_check scores` "
                "fails on any scored_rows.jsonl whose scorer returned one value"
            ),
        ),
        ChecklistItem(
            id="run.method_diagnosis_recall",
            statement=(
                "Treat an underperforming selected idea as an engineering/debugging "
                "signal first, not evidence that the idea is wrong. Before accepting a "
                "scientific failure, run a positive-recovery diagnosis loop: compare "
                "against trusted reference behavior, inspect actual executed knobs "
                "and loaded checkpoint identity/capability when relevant, inspect "
                "evaluator semantics, diagnose optimization/tuning/capacity/data "
                "limits, verify gradients/learning signals and treatment activation, "
                "reproduce a relevant competitive baseline, and iteratively test concrete "
                "plausible method/implementation repairs while they have scientific "
                "rationale and information value. Aim to recover a genuine positive "
                "result with evidence proportional to the claim and budget. There is no "
                "universal requirement that every seed, benchmark, or strongest baseline "
                "must succeed. Evaluators, conditions, and comparisons may evolve for a "
                "documented methodological reason when earlier outcomes remain visible "
                "and the final claim is scoped accordingly. Classify the result as genuine "
                "method failure only after an independent Reviewer finds the "
                "implementation competitive and no credible repair remains or the "
                "approved resource budget is exhausted. Classify interim outcomes as "
                "misconfigured, under-engineered, inconclusive, or still-repairable. Do "
                "not stop because of a fixed retry count."
            ),
            evidence_hint=(
                "reference reproduction + implementation source + executed manifests "
                "+ diagnostics + targeted repair results; use the matched diagnosis skill"
            ),
        ),
    ),
    "analysis": _checklist(
        ChecklistItem(
            id="analysis.claims",
            statement=(
                "Every quantified claim the paper will make is bound to its "
                "supporting raw evidence rows and to the figure / table that will "
                "show it."
            ),
            evidence_hint=(
                "paper/claims_to_evidence.tsv + result tables/figures + canonical "
                "raw outputs"
            ),
        ),
        ChecklistItem(
            id="analysis.report",
            statement=(
                "Results report summarizes headline numbers, statistical tests / "
                "confidence intervals, ablation findings, and failure analysis "
                "with numbers grounded in raw experiment files."
            ),
            evidence_hint="paper/RESULTS_REPORT.md",
        ),
        ChecklistItem(
            id="analysis.publication_scale",
            statement=(
                "Write `paper/PUBLICATION_SCALE_ASSESSMENT.json` from current "
                "accepted-paper comparators and real local artifacts, then build the "
                "primary evidence out until it stands on its own beside them rather "
                "than only beside a pilot or a proxy. A negative, null, diagnostic or "
                "boundary finding earns the paper on exactly the same terms as a "
                "positive one: establish it at that scale and it is a contribution."
            ),
            evidence_hint=(
                "`python -m argus_skill.verticals.research.publication_scale "
                "--project-root .` + paper/PUBLICATION_SCALE_ASSESSMENT.json"
            ),
        ),
        ChecklistItem(
            id="analysis.figure1",
            statement=(
                "Design and render the paper's reader-facing Figure 1 teaser or "
                "framework overview from a written communication brief. It must "
                "show the problem, core mechanism/architecture or taxonomy, and "
                "claim-bearing flow in one coherent visual. Route it through the "
                "Research Visualization Router: prefer PPT Master for polished "
                "editable composition, or browser-rendered HTML, FigureSpec, "
                "Draw.io, Mermaid/Graphviz as appropriate. image-2 is optional and "
                "its absence is never a reason to omit Figure 1. Preserve editable "
                "source, export SVG/PDF/PNG, inspect it at final paper size, and "
                "plan its caption and in-text callout. A LaTeX table, prose box, "
                "or rule-bar diagnostic is not a framework figure."
            ),
            evidence_hint=(
                "paper/figures editable source + exported Figure 1 asset + "
                "paper/DRAFT_OUTLINE.md figure slot"
            ),
        ),
        ChecklistItem(
            id="analysis.gaps",
            statement=(
                "Known evidence gaps are explicitly enumerated, each with the "
                "supplement or ablation that would close it — and a claim "
                "downgrade only where none is affordable. No missing evidence "
                "is silently absorbed."
            ),
            evidence_hint="paper/main.tex limitations + Reviewer notes + raw results",
        ),
        ChecklistItem(
            id="analysis.thesis",
            statement=(
                "Convert the completed evidence into the strongest honest, venue-relevant "
                "paper thesis. Positive, mixed, null, and negative outcomes are all valid "
                "starting points. When the original headline fails, characterize the "
                "boundary, mechanism, scaling regime, failure law, benchmark lesson, or "
                "practical decision it reveals, and make that insight the paper rather "
                "than treating result sign as a reason to abandon drafting. Internal "
                "records preserve all valid outcomes; the manuscript remains a selective "
                "argument that leads with its strongest evidence and includes contrary "
                "evidence when it changes interpretation. Before settling on a negative "
                "thesis, complete the run-stage positive-recovery loop and incorporate "
                "credible engineering repairs with clear information value. This is "
                "active method development, not defensive paperwork. Return upstream "
                "only when the measurement is invalid "
                "or no truthful, useful conclusion can be formed after this reframing."
            ),
            evidence_hint="paper/main.tex + canonical raw evidence + Reviewer judgment",
        ),
    ),
    "draft": _checklist(
        ChecklistItem(
            id="draft.tex",
            statement=(
                "paper/main.tex uses the selected venue's official structure and tells "
                "one coherent argument, not a chronological experiment report. The "
                "title, abstract, introduction, method, and experiments all serve the "
                "same thesis. Lead with what the work establishes: the abstract's "
                "first sentence states the result at full strength and the "
                "introduction earns it, rather than opening with scope, caveats, or "
                "what the paper does not claim. Its paragraph/section roles follow "
                "the accepted-paper "
                "argument transfer plan in `ARGUMENT_ORGANIZATION.json`, adapted to "
                "local claims and evidence without copied prose. If a proposed method "
                "does not win, write the paper around "
                "the robustly characterized boundary, mechanism, scaling behavior, "
                "failure mode, or practical decision that the experiments establish; "
                "do not write an apologetic failure log and do not abandon a truthful "
                "paper merely because the sign is negative. A "
                "literature review instead aligns its scope, taxonomy/comparison "
                "frame, source evidence, synthesis, limitations, and conclusions; it "
                "must not invent a method or experiment section merely to mimic an "
                "empirical paper."
            ),
            evidence_hint="paper/main.tex + research/VENUE_PROFILE.json + research/NARRATIVE_REPORT.md",
        ),
        ChecklistItem(
            id="draft.pdf",
            statement=(
                "paper/main.pdf compiles cleanly: no '??' citations, no undefined "
                "references, no material overflow, and no LaTeX errors. Its body and "
                "back matter obey the selected venue's actual page and format rules; "
                "do not pad a weak argument merely to fill a page quota."
            ),
            evidence_hint="paper/main.pdf + paper/main.log",
        ),
        ChecklistItem(
            id="draft.bibliography",
            statement=(
                "Every BibTeX entry is verified through a scholarly source (arXiv, "
                "ACL Anthology, DBLP, CrossRef, Semantic Scholar) — none invented "
                "or auto-completed."
            ),
            evidence_hint=(
                "paper/references.bib + verification log; resolve mechanically "
                "with `python -m argus_skill.verticals.research.integrity_check "
                "citations`"
            ),
        ),
        ChecklistItem(
            id="draft.figures",
            statement=(
                "The paper embeds a real external Figure 1 teaser/method/framework "
                "overview plus any claim-bearing result visuals needed by the "
                "argument. Figure 1 communicates the problem, mechanism and flow at "
                "a glance; it is not a LaTeX table, prose box, or rule-bar diagnostic "
                "inside a figure environment. image-2 is optional: when unavailable, "
                "use PPT Master, browser-rendered HTML, FigureSpec, Draw.io, "
                "Mermaid/Graphviz, or another truthful editable route. All figures "
                "are clear, readable at final size, coherent, and attractive enough "
                "for the venue. Minor stylistic imperfections are not blockers."
            ),
            evidence_hint=(
                "paper/main.pdf rendered pages and the actual figure files; optional "
                "FIGURE_PROVENANCE.json may help locate the source renderer"
            ),
        ),
    ),
    "review": _checklist(
        ChecklistItem(
            id="review.infrastructure",
            statement=(
                "Paper prose contains no local paths (/root/, /home/), no internal "
                "orchestration or daemon route names, no capability-vault references, no "
                "device IDs, no API keys — the manuscript is publication-clean."
            ),
            evidence_hint="grep main.tex for '/root/', 'CUDA_VISIBLE_DEVICES', 'argus-skill', 'codex', 'OPENAI_API_KEY'",
        ),
        ChecklistItem(
            id="review.tables",
            statement=(
                "Tables are readable and organized around the paper's claims. They "
                "include every comparison needed to assess the thesis, but do not "
                "force an irrelevant cross-benchmark matrix or a universal house style."
            ),
            evidence_hint="paper/main.tex tables + canonical result artifacts",
        ),
        ChecklistItem(
            id="review.citations",
            statement=(
                "Each related-work paragraph cites the specific papers it "
                "discusses; no mega-paragraphs dumping all citations, no "
                "citations buried in the bibliography with no local discussion."
            ),
            evidence_hint="paper/main.tex Related Work section",
        ),
        ChecklistItem(
            id="review.language",
            statement=(
                "Academic prose reads like a real selected-venue paper, not generic agent "
                "output: the Abstract states problem, gap, article approach, evidence, and "
                "implication (no result-first opening, no validator-checklist "
                "phrasing); the Introduction grounds the gap in cited prior work, "
                "then gives the organizing insight and contribution roadmap. For an "
                "empirical article, Method/Setup identifies the system, baselines, "
                "task source, metrics, backend, budget, and result preview. For a "
                "literature review, the scope/method explains source selection and "
                "the body provides a defensible taxonomy, fair comparisons, conflicts, "
                "gaps, and limitations. Every "
                "headline claim is tied to reported evidence; no unsupported hype, "
                "template LLM openings, experiment-report narration, or repeated "
                "not-X-but-Y caveats. Limitations are one honest paragraph naming "
                "the real constraint, not a comprehensive defence: a page of what "
                "the method cannot do reads as a weaker contribution and buys no "
                "protection from a reviewer who wanted more anyway. Write for what "
                "reviewers actually weigh — is the problem shown to be real, is the "
                "idea interesting, is the comparison fair, does the claim match what "
                "was shown, is the related work placed. Seeds, intervals and "
                "significance belong wherever the claim rests on a small margin, "
                "and nowhere else; they are not the spine of a paper. The "
                "model-backed reviewer (academic_language_review) is advisory "
                "input — this checklist, judged by the reviewer agent, is the "
                "source of truth."
            ),
            evidence_hint="paper/main.tex Abstract/Introduction/Method + paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)",
        ),
        ChecklistItem(
            id="review.publication_value",
            statement=(
                "Act as a constructive senior coauthor before acting as a gatekeeper: "
                "identify and strengthen the best accept argument supported by the "
                "actual evidence. Result sign, failure to beat a baseline, modest effect "
                "size, or a changed thesis is not by itself a rejection reason. Positive "
                "or negative original research may contribute a method/system, theorem, "
                "mechanism, scaling law, robust boundary, benchmark lesson, or "
                "decision-relevant finding only when that contribution has standalone "
                "publication-scale evidence. Where the evidence outruns the prose, push "
                "the claim up; where it does not yet reach, name the run that would get "
                "it there rather than the sentence that would avoid it. Compare the "
                "evidence dimensions in "
                "`paper/PUBLICATION_SCALE_ASSESSMENT.json` with its accepted-paper "
                "comparators and the actual artifacts. Request at most the few claim-critical "
                "repairs that would change the decision; keep lesser concerns advisory "
                "and do not reopen settled stages. A "
                "literature review must deliver valuable coverage, synthesis, critique, "
                "and a defensible map of the field rather than a paper-by-paper list."
            ),
            evidence_hint="paper/main.tex + paper/main.pdf + canonical evidence",
        ),
    ),
    "submission": _checklist(
        ChecklistItem(
            id="submission.result_stands",
            statement=(
                "The result this paper is about beat the baseline it was chosen "
                "against, at the scale named at selection. If it did not, say which "
                "of implementation, optimization, data, scale or evaluator the "
                "shortfall is made of and what the next round buys — a "
                "shortfall is a gap to close, not a finding to package. No number "
                "here judges the idea until the baseline reproduces in this "
                "harness and the method does what it says, because an unfinished "
                "implementation looks exactly like a wrong idea. Scoping a "
                "diagnostic down until it certifies is how a campaign delivers a "
                "paper without delivering a result. Three decisions live here and "
                "must not be collapsed: whether the claim is supported, whether "
                "this campaign keeps spending, and whether anything is submitted. "
                "Closing a campaign because the next round is worth less than "
                "another candidate is an opportunity-cost call, not a verdict that "
                "the idea was false — and no qualifying result inside the budget "
                "is an honest ending, since a system that must always ship a paper "
                "will eventually weaken its own contract to ship one."
            ),
            evidence_hint=(
                "the endpoint number beside the baseline it was measured against, "
                "and the margin declared at selection"
            ),
        ),
        ChecklistItem(
            id="submission.upstream",
            statement=(
                "All upstream stage checklists (research \u2192 review) are themselves "
                "marked done by a prior reviewer round or explicitly skipped by a "
                "recorded Manager decision because they do not apply to this article "
                "form. Submission readiness is not a way to retro-fix missing evidence."
            ),
            evidence_hint=(
                "stage checklist state for research\u2026review: status=done, or "
                "status=skipped with skip_reason/skipped_by and stage_history evidence"
            ),
        ),
        ChecklistItem(
            id="submission.readiness",
            statement=(
                "The independent Reviewer has read the current manuscript and its "
                "claim-critical sources and judges the paper ready for the selected "
                "venue. No separate assurance memo or evidence package is required."
            ),
            evidence_hint="paper/main.tex + paper/main.pdf + canonical results and sources",
        ),
        ChecklistItem(
            id="submission.package",
            statement=(
                "Final PDF, BibTeX, supplementary material, and (if required) "
                "anonymous submission packaging are present and consistent."
            ),
            evidence_hint="paper/main.pdf + paper/references.bib + paper/supplementary/",
        ),
        ChecklistItem(
            id="submission.anonymous",
            statement=(
                "The compiled PDF uses the selected venue's required author and "
                "anonymity mode without contradictory or placeholder metadata."
            ),
            evidence_hint="paper/main.tex author block + selected venue submission mode",
        ),
    ),
}


def list_stages() -> tuple[str, ...]:
    """Return the canonical stage order (research → submission)."""

    return CANONICAL_STAGE_ORDER


def get_stage_checklist(stage: str) -> tuple[ChecklistItem, ...]:
    """Return the checklist items for ``stage``; empty tuple if unknown."""

    return STAGE_CHECKLISTS.get(str(stage).strip().lower(), ())


def stage_completion_issues(
    stage: str,
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> tuple[str, ...]:
    normalized = str(stage or "").strip().lower()
    issues: list[str] = []
    if normalized in {"plan", "analysis", "draft", "review", "submission"}:
        from ...core.research_contract import resolve_research_target_level
        from .argument_organization import argument_organization_issues

        target = resolve_research_target_level(state_root or project_root)
        issues.extend(
            f"[argument_organization] {issue}"
            for issue in argument_organization_issues(
                project_root,
                research_target_level=target,
            )
        )
    if normalized in {"analysis", "draft", "review", "submission"}:
        from ...core.research_contract import resolve_research_target_level
        from .publication_scale import publication_scale_issues

        target = resolve_research_target_level(state_root or project_root)
        issues.extend(
            f"[publication_scale] {issue}"
            for issue in publication_scale_issues(
                project_root,
                research_target_level=target,
            )
        )
    if normalized in {"draft", "review", "submission"}:
        from .paper_structural_minimums import validate_paper_structural_minimums

        report = validate_paper_structural_minimums(project_root)
        issues.extend(f"[{issue.code}] {issue.detail}" for issue in report.issues)
    if issues:
        return tuple(issues)
    if normalized != "research":
        return ()
    from .idea_portfolio import idea_portfolio_completion_issues

    return idea_portfolio_completion_issues(project_root)



RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")

STAGE_ORDER = [
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
]


def _resolve_checklist_venue(project_root) -> VenueProfile:
    """Resolve the venue profile for checklist rendering.

    ``project_root`` may be None (resolved from env/cwd, matching how the
    overlay locates the project). Missing or unknown venue selection propagates
    ``KeyError`` so it cannot be silently certified against unrelated rules.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    return resolve_venue_profile(Path(project_root))


VENUE_DEPENDENT_STAGES = frozenset({"draft", "review", "submission"})


def _unresolved_venue_checklist(
    header: str,
    *,
    role: str,
    error: KeyError,
) -> str:
    """Render a fail-closed venue gate without crashing prompt construction."""
    if role == "reviewer":
        instruction = (
            "Keep this item unchecked and do not return `done`; ask the engineer "
            "to resolve it in `next_action`."
        )
    else:
        instruction = (
            "Resolve this item before doing venue-specific drafting, review, or "
            "submission work."
        )
    return (
        f"{header}\n\n"
        "### venue resolution\n"
        f"- [ ] `venue.profile` — {error}. `target_venue` must name a real "
        "publication venue, not planning commentary. If no venue was specified, "
        "do not infer or search for one; ask the operator to name a venue or "
        "explicitly request venue discovery. For an explicit venue, record its "
        "official CFP/deadline evidence in `research/VENUE_SELECTION.md` and write "
        "`research/VENUE_PROFILE.json` from its official author kit. "
        f"{instruction}"
    )


def _apply_venue_to_checklist_body(body: str, venue: VenueProfile) -> str:
    """Render the generic paper floor with the selected venue's real rules."""
    persona = venue.reviewer_persona
    page_phrase = (
        (
            f"up to {venue.body_page_limit} pages, References starts on "
            f"page {venue.references_min_page} or later"
        )
        if venue.has_fixed_page_budget
        else venue.page_budget_line()
    )
    section_label = (
        f"{venue.display_name} two-column paper sections"
        if venue.layout_format_persona.startswith("two-column")
        else f"{venue.display_name} journal-article sections"
    )
    body = body.replace(
        "paper/main.tex uses the selected venue's official structure",
        f"paper/main.tex uses the official {section_label}",
    )
    body = body.replace(
        "Its body and back matter obey the selected venue's actual page and "
        "format rules",
        f"Its body and back matter obey {page_phrase}",
    )
    body = body.replace(
        "The title, abstract, introduction, method, and experiments all serve "
        "the same thesis.",
        f"Required venue end matter: {venue.draft_section_tail()}. The title, "
        "abstract, introduction, method, and experiments all serve the same thesis.",
    )
    if venue.key == "FRONTIERS_SLEEP":
        replacements = {
            (
                "paper/main.tex uses the official Frontiers in Sleep journal-article "
                "sections and tells one coherent argument"
            ): (
                "paper/main.tex uses the Frontiers in Sleep Hypothesis and "
                "Theory sections in a coherent order: one-paragraph Abstract, "
                "Introduction, subject-relevant evidence and theory subsections, "
                "discriminating tests or proposed study, Discussion, Conclusion, "
                "required declarations, and References. The article tells one "
                "coherent argument"
            ),
            (
                "Every BibTeX entry is verified through a scholarly source (arXiv, "
                "ACL Anthology, DBLP, CrossRef, Semantic Scholar) — none invented "
                "or auto-completed."
            ): (
                "Every BibTeX entry is verified through a scholarly or canonical "
                "data source (for example PubMed, Crossref, DOI resolver, or the "
                "official repository); none is invented or auto-completed."
            ),
            (
                "Paper prose contains no local paths (/root/, /home/), no internal "
                "orchestration or daemon route names, no capability-vault references, no "
                "device IDs, no API keys — the manuscript is publication-clean."
            ): (
                "Paper prose contains no local paths, credentials, capability-vault "
                "references, device IDs, private routes, daemons, or internal "
                "reviewer/engineer workflow labels. Any generative-AI use is "
                "disclosed only in the public Frontiers-required form (technology "
                "name, version, model, source, use, and author responsibility)."
            ),
            (
                "Each related-work paragraph cites the specific papers it discusses; "
                "no mega-paragraphs dumping all citations, no citations buried in "
                "the bibliography with no local discussion."
            ): (
                "Each evidence or prior-theory paragraph cites the specific papers "
                "it discusses; no citation dumping and no bibliography entries "
                "without a reader-facing role in the manuscript."
            ),
            (
                "Final PDF, BibTeX, supplementary material, and (if required) "
                "anonymous submission packaging are present and consistent."
            ): (
                "Final PDF, TEX/BibTeX sources, figures with alt text, supplementary "
                "audit material, and required single-anonymized author/declaration "
                "metadata are present and mutually consistent."
            ),
            "paper/references.bib + verification log": (
                "paper/refs.bib (or declared bibliography source) + verification log"
            ),
            "paper/main.tex table* envs + caption": (
                "paper/main.tex table environments + captions + canonical evidence"
            ),
            "paper/main.tex Related Work section": (
                "paper/main.tex evidence/prior-theory sections"
            ),
            "paper/main.tex Abstract/Introduction/Method + "
            "paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)": (
                "paper/main.tex Abstract/Introduction/theory/evidence/Discussion + "
                "paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)"
            ),
        }
        for old, new in replacements.items():
            body = body.replace(old, new)
        body = body.replace(
            "irrelevant cross-benchmark matrix",
            "irrelevant omnibus benchmark matrix",
        )
        body = body.replace(
            "Academic prose reads like a real EMNLP paper, not generic agent output:",
            "Academic prose reads like a real Frontiers in Sleep Hypothesis and "
            "Theory article:",
        )
        body = body.replace(
            "the Method/Setup lets an outside reviewer identify the evaluated system, "
            "baselines, task source, metrics, evaluated model/backend, and budget;",
            "the body separates prior theory, original analysis, interpretation, "
            "alternatives, falsifiers, and planned work;",
        )
    generic_author_rule = (
        "The compiled PDF uses the selected venue's required author and "
        "anonymity mode without contradictory or placeholder metadata."
    )
    generic_author_evidence = (
        "paper/main.tex author block + selected venue submission mode"
    )
    if venue.requires_real_author_metadata:
        body = body.replace(
            generic_author_rule,
            "The compiled PDF and source use the real author names, affiliations, "
            "corresponding-author email, and required contribution metadata for "
            f"{venue.review_model} {venue.display_name} review; no anonymous "
            "placeholder remains.",
        )
        body = body.replace(
            generic_author_evidence,
            "paper/main.tex author/address/correspondence/contribution fields + "
            "compiled PDF metadata",
        )
    else:
        body = body.replace(
            generic_author_rule,
            f"Anonymous submission for {venue.display_name}: the compiled PDF uses "
            f"the {persona} author block without real author names, affiliations, "
            "or self-deanonymizing strings.",
        )
        body = body.replace(
            generic_author_evidence,
            f"grep paper/main.tex for '{venue.anon_author_string}' + "
            f"{venue.style_package} submission mode",
        )
    substitutions = {
        "reads like a real selected-venue paper": f"reads like a real {persona} paper",
    }
    for old, new in substitutions.items():
        body = body.replace(old, new)
    return body


def render_stage_checklist_body(
    body: str,
    *,
    project_root,
    role: str,
    stage: str,
) -> str:
    if stage not in VENUE_DEPENDENT_STAGES:
        return body
    try:
        return _apply_venue_to_checklist_body(
            body,
            _resolve_checklist_venue(project_root),
        )
    except KeyError as exc:
        return body + "\n\n" + _unresolved_venue_checklist(
            "## Venue selection",
            role=role,
            error=exc,
        )


def render_full_checklist_body(
    body: str,
    *,
    project_root,
    role: str,
) -> str:
    try:
        return _apply_venue_to_checklist_body(
            body,
            _resolve_checklist_venue(project_root),
        )
    except KeyError as exc:
        return body + "\n\n" + _unresolved_venue_checklist(
            "## Venue selection",
            role=role,
            error=exc,
        )


# ===========================================================================
# System (B) — markdown stage checklists for the research vertical
# ===========================================================================
#
# Research owns its stage order, checklist seeds, and venue-specific rendering.
CHECKLIST_STAGE_ORDER = CANONICAL_STAGE_ORDER
CHECKLIST_ITEMS = STAGE_CHECKLISTS

#: Research missions complete on the selected venue's full-paper submission gate.
completion_gate = "certified"
MISSION_KIND = "research"
PAPER_MISSION = True

# Research proceeds through strict stage gates, but evidence reuse within those
# stages is proportional: once a Reviewer certifies a source or artifact, later
# bounded missions verify only the new claim/delta unless a concrete conflict
# reopens it. This keeps scientific integrity without repeatedly rebuilding the
# same provenance tree.
WORKFLOW_MODE = "proportional"
VERIFICATION_STAGE_PROFILES = {
    "research": "explore",
    "plan": "explore",
    "benchmark": "develop",
    "run": "develop",
    "analysis": "develop",
    "draft": "develop",
    "review": "certify",
    "submission": "certify",
}

# Scientific implementation and experiment claims always require a fresh,
# independent Reviewer; an Engineer verifier cannot waive this review.
REQUIRE_INDEPENDENT_REVIEW = True

_AMBITIOUS_RESEARCH_POLICY = (
    "Ambitious paper policy: go after a result that changes what people in the field "
    "do. Pick that target while the work is still shapeable — ambition is a choice made "
    "at the start, and no amount of care at review recovers a timid one. Every stage "
    "exists to make the paper stronger. "
    "Make the boldest claim your evidence carries, and say it in the first sentence of "
    "the abstract. Papers get remembered for what they establish, never for what they "
    "carefully decline to establish; an abstract spent listing non-claims has thrown "
    "away its own result. When the evidence is strong, commit to the strong reading. "
    "When it is not yet strong, the answer is better evidence, not a smaller sentence — "
    "so treat a negative or mixed result as a debugging signal and chase the "
    "implementation, data, scale, evaluator, and method fixes that could turn it "
    "positive. Before a shortfall counts as one, check that the run could have seen "
    "the win: put the spread of your own repeated measurements beside the margin "
    "declared at selection, and when the noise is wider than the margin, the run has "
    "not tested the idea, only failed to look at it — the fix is a run that resolves "
    "what you are claiming, not a smaller claim. "
    "A boundary or mechanism finding is worth writing when it is genuinely "
    "the interesting thing you found and you can show it at real scale, not when it is "
    "what is left after giving up. "
    "What gets a reviewer excited is narrow and worth aiming at: explaining something the field assumed it already understood, a connection between two areas nobody had joined, a principled method where the principle does the work, or a result that contradicts what everyone expected. None of those is a bigger number. What loses a reviewer is equally narrow: a problem never shown to be real, an increment with no new idea, and above all a claim the results do not support. Say the thing you found and let it be judged. "
    "Apply requirements proportionally to the actual claim and contribution shape; "
    "mark inapplicable items instead of manufacturing work. Reuse certified upstream "
    "evidence and do not reopen it without a concrete contradiction. Default to advance "
    "with explicit limitations and a small number of high-value next actions. Stop for "
    "fabricated evidence, invalid measurement, or a headline claim the data "
    "contradicts — those cost the paper everything and are the reason the bold claim "
    "has to be a real one. "
)

_REVIEWER_RESEARCH_JUDGEMENT = (
    "For experiment claims, inspect implementation and raw rows once, then reuse "
    "them until a dependency changes. Separate method results from infrastructure "
    "or evaluator failure. Research-stage smoke probes are short advisory "
    "observations, not gates: weak or underpowered ones cannot by themselves "
    "trigger replan. Short of the baseline, name what the gap is made of and "
    "buy that fix. "
    "A miss is evidence about the tested system; it weighs against the claim "
    "once the baseline reproduced, the method did what it says and the run "
    "could resolve the effect. Then repeated misses count. "
    "Retiring is the Manager's call, and a "
    "loss is never the paper. If it "
    "is stronger than the writing says, "
    "push the claim up. A missing certificate or field belongs in "
    "next_action, not in a returned verdict.\n"
)

_PLANNER_RESEARCH_ORCHESTRATION = (
    _AMBITIOUS_RESEARCH_POLICY +
    "Research orchestration: run routes and reviews concurrently. At an 80% review "
    "quorum (10/12 by default), let a fresh selector Agent choose a current-frontier "
    "high-novelty method or publication-scale empirical contribution. Choose first "
    "the consequential uncertainty whose resolution would change what the field "
    "builds or believes, then treat named mechanisms as competing, disposable bets "
    "on that question. Never optimize "
    "selection for no training, the shortest evidence path, cheapness, or single-GPU "
    "fit; require a credible staged resource plan instead. Verify latest-12-month "
    "arXiv and current major-venue coverage before selection; do not "
    "wait for the final routes. Probe only that winner when a sub-ten-minute observation "
    "can verify feasibility without pretending to decide the full hypothesis; otherwise "
    "record it untested and advance. Never use a full benchmark, training run, broad "
    "sweep, or publication-scale multi-seed study as a research probe. Research-stage "
    "outcomes steer how the selected problem is pursued; claim-bearing evidence at "
    "the faithful scale named at selection is what the campaign optimizes against. "
    "Name at selection the end-task claim, the strongest resource-matched "
    "baseline, the size of win that would matter — derived from something "
    "observable, not invented: the spread this benchmark already reports "
    "between seeds or methods, or the gap between the last two published "
    "results on it. A round number picked because it sounds decisive is a "
    "threshold nobody can argue with or fail — and the cheapest faithful run that "
    "measures the gap — then buy that measurement early, so there is a number to "
    "improve for the rest of the campaign. Short of the baseline is a gap with a "
    "size, and the campaign's job is to close it: each round names what the "
    "shortfall is made of, buys the implementation, optimization, data, scale or "
    "evaluator fix that addresses it, and measures again. Papers are won this "
    "way, over many rounds, and an early miss is the normal starting position "
    "rather than a verdict on the idea. Keep only research-stage route "
    "selection and feasibility probing below one hour when default resources allow it; "
    "claim-bearing publication-scale runs are not subject to that time box. A failed "
    "direction is project memory, not automatic completion or a forced next action; "
    "only the independently reviewed research target closes the project.\n"
    "Each mission advances the argument the paper will make: the experiment that "
    "decides a claim, the comparison that earns it, the rewrite that makes one "
    "insight carry the paper. Name missions after the question they answer, not "
    "after the defect they repair. Certification, scope prescription, package "
    "assembly, schema conformance and checklist bookkeeping are not missions of "
    "their own — they are finishing steps inside the mission whose work they "
    "certify, and scheduling them separately spends the campaign on the harness "
    "instead of the paper.\n"
)

_ENGINEER_RESEARCH_EXECUTION = (
    _AMBITIOUS_RESEARCH_POLICY +
    "Research execution: keep independent work file-disjoint and parallel. Respect "
    "the route/review/selector/probe time boxes, stop searching once the novelty "
    "boundary is credible, and treat source-balance gaps and smoke outcomes as "
    "documented limitations rather than reasons to stall. Reviewers read the model you chose as a claim about how current the work is, so pick from what is strong now rather than what you remember: list what the registry actually serves today, take a current-generation checkpoint that fits the budget, and treat a family you can name from memory as probably two generations stale. Any checkpoint, library version, benchmark split or baseline number you can name from memory is a hypothesis about a world that moved after training: probe it before the plan hardens, per `engineer/stale-world-model.md`. A checkpoint that will not download is a substitution to record, not a mission to block on. When a method is short of its baseline, `engineer/research-grind.md` is how the gap gets closed: the first number is a first draft, the loop is measure-diagnose-fix-measure, flat stretches are the middle of the problem rather than a verdict, and the method you end up with is the one the paper is about.\n"
)


_MANAGER_RESEARCH_STEWARDSHIP = (
    _AMBITIOUS_RESEARCH_POLICY +
    "Research stewardship: the campaign's normal state is closing the gap to the "
    "baseline named at selection — round after round of the fix that the current "
    "shortfall points at, the way a leaderboard result is earned. Missing is the "
    "starting position, not news about the idea, and no Reviewer verdict or "
    "mission outcome retires one. Only you can judge that an idea is genuinely "
    "dead, and that judgement is rare and expensive: it wants sustained "
    "optimization already spent across implementation, data, scale and evaluator, "
    "the gap unmoved by any of it, and a reason the next round would fail that is "
    "not simply that the last one did. Fewer rounds than that is impatience "
    "wearing the costume of judgement — the shortfall is still an engineering "
    "shortfall until the engineering has actually been done. When you do retire "
    "an idea, roll back to selection with the accumulated evidence; what a dead "
    "idea never becomes is the paper. `engineer/research-grind.md` is what a campaign is supposed to look like between the first measurement and the result.\n"
)


def search_altitude_context(project_root: object) -> str:
    """Everything a role should have in view before it judges its own work.

    Two facts the campaign wrote down itself and then never reopened: what it
    promised at selection, and which accepted papers it said it would learn
    from. Both are rendered; neither is scored.
    """
    return _selection_contract_block(project_root) + _accepted_papers_block(
        project_root
    )


def role_banner(role: str = "engineer") -> str:
    """Add research-only role policy without affecting other verticals."""
    return {
        "planner": _PLANNER_RESEARCH_ORCHESTRATION,
        "reviewer": _REVIEWER_RESEARCH_JUDGEMENT,
        "engineer": _ENGINEER_RESEARCH_EXECUTION,
        "manager": _MANAGER_RESEARCH_STEWARDSHIP,
    }.get(role, "")


def _selection_contract_block(project_root: object) -> str:
    """Put what this campaign promised at selection back in front of it.

    Selection records the end task, the baseline to beat and the margin that
    would count. Nothing reopened that file afterwards: across a full campaign
    the phrase never appeared in a role session again, so the campaign both set
    the bar and reported against it without the two ever meeting. A soft
    baseline or a conveniently small margin then costs nothing, and a claim can
    drift for days without anyone noticing it moved.

    The file is written by an Agent, so its shape differs every campaign — the
    same promise has been filed as ``meaningful_win_threshold``,
    ``meaningful_win_size`` and ``claim_contract.end_task``. Fields are matched
    by intent at any depth rather than by a fixed schema, because a campaign
    that had to satisfy a schema would write to the schema.

    This renders the promise and stops. Whether the baseline was the strongest
    available, whether the margin was honest, and whether today's number clears
    it are the reading Agent's calls — a harness that compared them itself
    would only teach the next campaign to promise less. Fail-soft throughout.
    """
    try:
        import json
        from pathlib import Path as _Path

        root = _Path(str(project_root)).resolve()
        path = root / "research" / "IDEA_SELECTION.json"
        if not path.is_file():
            return ""
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return ""

        # (label, key fragments) — first match at the shallowest depth wins.
        wanted = (
            ("question", ("central_uncertainty", "consequential_uncertainty")),
            ("end task", ("end_task", "headline_claim", "final_claim", "claim_scope")),
            ("baseline to beat", ("strongest_resource_matched_baseline",)),
            ("margin that would count", ("meaningful_win",)),
        )

        def flatten(value: object) -> str:
            if isinstance(value, dict):
                parts = [f"{k}: {flatten(v)}" for k, v in value.items()]
            elif isinstance(value, (list, tuple)):
                parts = [flatten(v) for v in value]
            else:
                return " ".join(str(value).split())
            return "; ".join(p for p in parts if p)

        found: dict[str, tuple[int, str]] = {}

        def walk(node: object, depth: int = 0) -> None:
            if not isinstance(node, dict) or depth > 4:
                return
            for key, value in node.items():
                low = str(key).lower()
                for label, fragments in wanted:
                    if any(fragment in low for fragment in fragments):
                        text = flatten(value)
                        if text and depth < found.get(label, (99, ""))[0]:
                            found[label] = (depth, text)
                walk(value, depth + 1)

        walk(payload)
        lines = [
            f"- {label}: {found[label][1][:400]}"
            for label, _ in wanted
            if label in found
        ]
        if not lines:
            return ""
        missing = [label for label, _ in wanted if label not in found]
        if missing:
            lines.append(f"- never filed: {', '.join(missing)}")
        return (
            "## What this campaign promised at selection\n"
            "From `research/IDEA_SELECTION.json`, written before the work began.\n"
            + "\n".join(lines)
            + "\nIf the claim has moved since, say so and why: drift you argue for "
            "is research, and drift nobody mentions is how a soft baseline "
            "becomes a result.\n\n"
        )
    except Exception:  # noqa: BLE001 — a missing promise must never block a role
        return ""


def _accepted_papers_block(project_root: object) -> str:
    """Put the accepted papers this work claims to learn from within reach.

    ``ARGUMENT_ORGANIZATION.json`` already records same-area accepted papers
    whose full text was pulled to disk, and the validator has confirmed those
    files exist. Nothing then reopened them: review compared the manuscript
    against the *plan* to reuse them rather than against the papers, so a paper
    could carry a detailed transfer plan and a body still ordered by run
    chronology.

    This states where those papers are and nothing else. Whether this
    manuscript would stand next to them is the reviewing Agent's judgement, and
    a harness that scored headings would only teach the next draft to rename
    its sections. Fail-soft: any error yields no block.
    """
    try:
        import json
        from pathlib import Path as _Path

        from .argument_organization import ARGUMENT_ORGANIZATION_PATH

        root = _Path(str(project_root)).resolve()
        payload = json.loads(
            (root / ARGUMENT_ORGANIZATION_PATH).read_text(encoding="utf-8")
        )
        exemplars = payload.get("exemplars")
        if not isinstance(exemplars, list):
            return ""
        lines: list[str] = []
        for exemplar in exemplars:
            if not isinstance(exemplar, dict):
                continue
            title = str(exemplar.get("title") or "").strip()
            venue = str(exemplar.get("venue") or "").strip()
            if not title:
                continue
            entry = [f"- {title}" + (f" ({venue})" if venue else "")]
            for field, label in (
                ("text_extract", "full text"),
                ("local_pdf", "pdf"),
            ):
                value = str(exemplar.get(field) or "").strip()
                if value and (root / value).is_file():
                    entry.append(f"    {label}: `{value}`")
            code = exemplar.get("official_code")
            if isinstance(code, dict):
                checkout = str(code.get("local_checkout") or "").strip()
                revision = str(code.get("revision") or "").strip()
                if checkout and (root / checkout).is_dir():
                    pin = f" @ {revision[:12]}" if revision else ""
                    entry.append(f"    official code: `{checkout}`{pin}")
            lines.extend(entry)
        if not lines:
            return ""
        return (
            "## Accepted same-area papers on disk\n"
            "The full text of each is local and readable now:\n"
            + "\n".join(lines)
            + "\n"
        )
    except Exception:  # noqa: BLE001 - prompt building never fails on this
        return ""


__all__ = [
    "STAGE_ORDER",
    "CANONICAL_STAGE_ORDER",
    "STAGE_CHECKLISTS",
    "list_stages",
    "get_stage_checklist",
    "VENUE_DEPENDENT_STAGES",
    "render_stage_checklist_body",
    "render_full_checklist_body",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "WORKFLOW_MODE",
    "VERIFICATION_STAGE_PROFILES",
    "REQUIRE_INDEPENDENT_REVIEW",
    "role_banner",
    "search_altitude_context",
    "render_role_prompt_fragment",
    "stage_completion_issues",
    "completion_gate",
    "PAPER_MISSION",
]
