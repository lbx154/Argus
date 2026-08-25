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

import logging
from pathlib import Path
from typing import Any

from ...core.vertical_contract import IterationAssessment
from ...skills.stage_machine import ChecklistItem
from . import library_preparation
from .prompt_policy import render_role_prompt_fragment
from .venue_profiles import VenueProfile, resolve_venue_profile

log = logging.getLogger(__name__)

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
                "familiar, or easy to fit. Being able to name a model from memory is "
                "evidence that it is old, not that it is suitable: recall means your "
                "training data covered it heavily, which means it was already "
                "widespread well before now, and every campaign here reached for the "
                "same stale families without ever checking. Look up what this field "
                "is publishing against this month; where the live catalog disagrees "
                "with what you remember, the catalog is right. Read `argus_builtin_skills/engineer/"
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
                "must not become binary gates. A baseline carrying a published "
                "method's name must be that method — its own implementation, or a "
                "reimplementation of the mechanism that paper is about. Renaming a "
                "local heuristic after a paper is the most common way a comparison "
                "quietly stops being one: a single lexical routing score has "
                "appeared three times as H2O, SnapKV and PyramidKV, and a lower "
                "learning rate has appeared as SAR. When the real method cannot be "
                "run, name the baseline for what it does, drop the published name, "
                "and say plainly that no published method was run. That resolves "
                "the misattribution and not the weakness: if renaming leaves no "
                "published method anywhere in the table, this work has not been "
                "compared to the field, and honest labelling only stops a reviewer "
                "accusing you of something worse. One real comparator at your own "
                "budget is worth more than a page of correctly labelled "
                "self-built ones. Strip the labels "
                "and count families before filing the table: nine rows that are "
                "five variants of one method have not compared you to the field."
            ),
            evidence_hint="research/EXPERIMENT_PLAN.md",
        ),
        ChecklistItem(
            id="plan.benchmark",
            statement=(
                "The evaluation-source and comparator plan matches the empirical "
                "domain. Take the field's own harness before writing any of your "
                "own: the official evaluation repository for this benchmark, the "
                "released implementation of each baseline, the standard serving "
                "and decoding stack. Across seven campaigns, 68 percent of every "
                "mission went to repairing self-built measurement code and 6 "
                "percent to improving the method — each campaign wrote thousands "
                "of lines of its own evaluation stack and then spent its life "
                "debugging them, which is where every twelve-token cap, "
                "unexecuted tool step, keyword scorer, CPU-bound generation loop "
                "and train-mode baseline came from. Those defects never look like "
                "bugs from inside; they look like negative results. Write "
                "evaluation code only for the part that is genuinely new, name in "
                "the plan which existing harness and which baseline "
                "implementations you are adopting, and spend the hours that frees "
                "on method iteration and scale, which is what the paper is judged "
                "on. Every final empirical claim includes at least one "
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
                "or task merely to satisfy this item. Where the model and benchmark "
                "have a published number, look it up and put yours beside it before "
                "trusting any comparison built on this harness: a harness can "
                "reproduce its own broken baseline all day, and a score far below "
                "the published one means your pipeline is what you measured. "
                "Reproduce it under the protocol that produced it — the prompt "
                "format, tool access, decoding and generation budget the published "
                "result used — and name any deviation beside both numbers; a "
                "CoT-prompted score is not comparable to a tool-integrated one. A "
                "generation budget is not a number anyone picks: read it off the "
                "length distribution of this model's own completions, so the budget "
                "is what lets it finish rather than what fits. Report "
                "the rate at which generation hits its own limits, since a run cut "
                "off before it can answer scores like a method that cannot. Every "
                "evaluation has a case it must be able to detect: a model told "
                "outright to do the thing, a known-correct answer, an oracle "
                "condition. Run that positive control and report it beside the "
                "results. Read it before the run finishes, not after: it costs one "
                "cell and it decides whether the rest of the sweep means anything. "
                "When it cannot be separated from random, the instrument "
                "is broken and nothing from that harness means anything, whatever "
                "the methods scored — stop the run rather than completing it, "
                "because a dead detector reproduced sixteen times is still a dead "
                "detector and the compute is the paper's remaining budget. One "
                "sweep launched to rescue exactly this defect ran at the same "
                "twelve-token budget that caused it, and its own finished cells "
                "reported every hit rate at 0.0 across a thousand prompts with "
                "thirty-seven percent of generations echoing the prompt back, "
                "while it kept going. One sweep concluded five steering methods sat "
                "at chance while its own concept-prompting control sat at chance "
                "beside them, on twelve-token generations. An instrument that "
                "overturns a body of results must be stronger than the one that "
                "produced them: a null established with a lexical detector does "
                "not unseat judge-scored findings, and a reviewer will read the "
                "weaker instrument as the cause of the null rather than as "
                "evidence against the field. When the claim is that an "
                "established measurement misleads, measure with the established "
                "one and then with the better one, and show where they part."
            ),
            evidence_hint=(
                "computational: evaluator source + official scorer outputs, plus the "
                "published score for this model and benchmark next to yours and the "
                "truncation rate; clinical/mechanism: public-source loader/analysis "
                "code + derived rows + machine-readable result and uncertainty"
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
                "cancellation contract. Before committing hours to it, check that "
                "it runs on the hardware you think it does: record device "
                "placement and observed throughput on the first few examples, and "
                "compare GPU utilisation against CPU time. One campaign burned "
                "four days of CPU time in four wall-clock hours at 2472 percent "
                "CPU while the GPU holding its model sat at zero, and paid for it "
                "twice — once in wall-clock, and again by shrinking its generation "
                "budget to twelve tokens to compensate, which turned a performance "
                "bug into a scientific one by making every concept undetectable. "
                "A run that is slow for a reason you have not identified is not a "
                "reason to measure less."
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
                "what is missing to reach that bar. Narrowing the claim until the "
                "run you have already paid for covers it is not justification, it "
                "is the same run with a smaller result attached: size the evidence "
                "to the claim worth making, never the claim to the evidence you "
                "happen to hold. A claim worth making is worth "
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
                "than only beside a pilot or a proxy. Where it falls short of what it "
                "was chosen against, that shortfall is the next experiment, not a "
                "framing choice — narrower prose does not close it."
            ),
            evidence_hint=(
                "`python -m argus_skill.verticals.research.publication_scale "
                "--scaffold --project-root .` writes the schema and lists what "
                "is still unanswered + paper/PUBLICATION_SCALE_ASSESSMENT.json"
            ),
        ),
        ChecklistItem(
            id="analysis.figure1",
            statement=(
                "Design the paper's visual argument, then render it. Go through "
                "the claims this paper will make and decide for each whether a "
                "reader can see the evidence or only read that it exists; the "
                "ones they cannot see are the figures, and each is its own "
                "mission that ends with the figure in the compiled paper. Figure "
                "1 is one of them, not the whole of it: the reader-facing teaser "
                "or framework overview, built from a written communication brief, "
                "showing the problem, core mechanism/architecture or taxonomy, and "
                "claim-bearing flow in one coherent visual. Route every one "
                "through the "
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
                "paper merely because the sign is negative. That boundary still has to "
                "be a finding someone wants, and the title has to name what you found "
                "rather than the genre you retreated to: 'A Boundary Study' announces a "
                "shape, and a title carrying a substitution, a layer index or any other "
                "apology has put the excuse where the result belongs. A qualifier "
                "you chose is itself a claim: a title saying frozen, layer-20 or "
                "substituted promises that the paper shows unfreezing, another "
                "layer or the real model changing the answer — show that, or "
                "narrow the claim to what you did test. Dropping the word is "
                "honest only when the evidence reaches the wider class: one "
                "campaign deleted frozen from its title without ever running an "
                "unfrozen variant, turning a scoped result into a claim about "
                "every environment-invariant causal subspace, which is worse "
                "than the apology it replaced. Otherwise a reader cannot tell "
                "whether the idea "
                "failed or only your restriction of it did. Before writing up any "
                "negative result, answer whether it is a fact about the world or "
                "about your run. Almost always it is the run: one campaign "
                "measured 6.0% on MATH-500 for a model published at 79.7, and "
                "raising a token cap took it to 68.8% and executing the tool the "
                "protocol assumes took it to 76.4%. Each of those was one "
                "engineering fix, and at every stage the number looked like a "
                "finding. A negative result is first an optimization signal — the "
                "idea, its implementation, its scale, or its evaluator needs work "
                "— and writing it up is what you do only after the engineering is "
                "actually good and the experiment is actually large. Nobody wants "
                "to read a result restricted until it was cheap enough to certify. "
                "If it survives all that and still holds, name in the abstract the "
                "belief it kills. Claim as far as "
                "the evidence reaches and no further, but a claim narrowed past what you "
                "showed is not caution -- it throws the result away, and no tightly "
                "fenced claim was ever the reason a paper mattered. Say each caveat "
                "once, where it belongs: one campaign repeated 'pending adequately "
                "powered validation' fourteen times across its abstract, "
                "introduction, results, a table cell, a subsection heading and its "
                "discussion, which is not honesty but a manuscript organised around "
                "an absence, and a reader calls it unfinished long before reaching "
                "the evidence. State the caveat in Limitations and let every other "
                "section say what the work does establish. A "
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
                "Every claim this paper asks a reader to accept either shows its "
                "evidence in a figure or has a stated reason a table serves it "
                "better. Ask which claims a reader currently has to take on trust "
                "because nothing shows them, and draw those. Accepted work in this "
                "area carries a mechanism diagram, the headline result, the ablation "
                "that rules out the obvious alternative explanation, and the case "
                "where the method stops working; arriving at one figure means the "
                "argument was never asked what it needed. The paper embeds a real "
                "external Figure 1 teaser/method/framework overview. "
                "Figure 1 communicates the problem, mechanism and flow at "
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
                "force an irrelevant cross-benchmark matrix or a universal house style. "
                "A reader skimming one should see the answer without reconstructing it: "
                "name the method as ours, put it where the eye lands, bold the winning "
                "number in each column, and say in the caption what the table shows "
                "rather than what it contains. A row the reader has to identify as "
                "yours, or a column where they have to work out who won, has buried "
                "the result the paper spent its whole budget earning. Round every "
                "number to the precision its evidence supports: "
                "0.6946666666666667 is a machine dump, and it reads as a paper "
                "nobody proofread."
            ),
            evidence_hint=(
                "paper/main.tex tables + canonical result artifacts: an ours row that "
                "reads as ours, a marked best value per column, a caption that states "
                "the finding"
            ),
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
                "actual evidence. That argument is not always the selected idea: "
                "work done to make an experiment possible can outgrow it, and a "
                "campaign that measured why a published benchmark number "
                "reproduces as anything from 6 to 76 percent depending on two "
                "implicit protocol steps holds a contribution whatever its method "
                "does next. Take the by-product when it is already measured, "
                "replicated, and explains something the field is currently getting "
                "wrong — that is not drift, because drift abandons a question for "
                "an unmeasured one. The selection contract binds what you may "
                "claim, not what you are allowed to notice. "
                "Result sign, failure to beat a baseline, modest effect "
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
                "and do not reopen settled stages. A limitation naming a gap against "
                "accepted work is not a paragraph, it is the next experiment: a campaign "
                "able to write that accepted papers here evaluate several model families "
                "and a dozen benchmarks while it evaluated one has already located its "
                "own highest-value remaining run, and has chosen to concede it. Close "
                "that gap, or say why it is fundamental rather than merely unaffordable. "
                "Conceding the objection that decides the review and then spending the "
                "rest of the budget on prose is the most expensive mistake available at "
                "this stage. A "
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
                "A fourth question decides none of those and gets skipped anyway: "
                "whether the manuscript is finished. Open the accepted same-area "
                "papers already on disk and compare what they carry — the length "
                "of the argument, the references, the figures — before calling a "
                "campaign done. One campaign here declared itself complete with a "
                "3,114-word manuscript carrying twelve citations and four figures, "
                "on a mechanism result strong enough to deserve a real paper. "
                "Being out of experiments is not the same as being finished, and "
                "what remains at that point needs no compute at all. "
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


# Stages whose checklist already carries paper-facing items, taken from the
# checklists rather than restated, so adding a paper question to a stage also
# stops the reminder that nobody is asking one.
_PAPER_FACING_STAGES = frozenset(
    stage
    for stage, items in STAGE_CHECKLISTS.items()
    if any(item.id.startswith(("draft.", "review.", "submission.")) for item in items)
)


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
        from .contamination_check import contamination_issues
        from .publication_scale import publication_scale_issues

        target = resolve_research_target_level(state_root or project_root)
        issues.extend(
            f"[publication_scale] {issue}"
            for issue in publication_scale_issues(
                project_root,
                research_target_level=target,
            )
        )
        issues.extend(
            f"[contamination] {issue}"
            for issue in contamination_issues(project_root)
        )
    if normalized in {"review", "submission"}:
        from .artifact_freshness import artifact_freshness_issues

        issues.extend(
            f"[artifact_freshness] {issue}"
            for issue in artifact_freshness_issues(project_root)
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


def iteration_assessment(
    *,
    stage: str,
    scope: str,
    project_root: Path,
    state_root: Path,
    mission: Any,
    outcome: Any,
) -> IterationAssessment | None:
    """Turn a trusted final-result shortfall into one bounded next cycle.

    Only the project-closing ``final_submission`` envelope carries the charter
    this hook evaluates. Bounded nodes still close against their own acceptance
    checks. The supervisor supplies the cycle ceiling; this vertical supplies
    the domain judgment and refuses to optimize against contaminated or stale
    evidence.
    """
    _ = (stage, mission)
    if str(scope or "").strip().lower().replace("-", "_") != "final_submission":
        return None

    from ...core.research_contract import (
        ACCEPTED_SIGNIFICANCE,
        normalize_research_result,
        research_completion_issue,
        resolve_research_target_level,
    )

    target = resolve_research_target_level(state_root)
    raw_result = getattr(outcome, "research_result", None)
    issue = research_completion_issue(
        raw_result,
        research_target_level=target,
    )
    if not issue:
        return None

    result = normalize_research_result(raw_result)
    if result is None:
        return IterationAssessment(
            shortfall=issue,
            blocking_issues=(
                "the final Reviewer did not provide a valid structured research "
                "result, so the claimed shortfall cannot be measured safely",
            ),
        )

    shortfall_type = "optimization"
    next_cycle = (
        "improve the method against the same reproduced baseline and measure the "
        "chartered endpoint again"
    )
    actual = result["significance_status"]
    if issue.startswith(("correctness_", "statement_fidelity_")):
        shortfall_type = "implementation"
        next_cycle = (
            "repair the implementation and independently verify that it performs "
            "the stated method before comparing scores again"
        )
    elif issue.startswith(("novelty_", "survey_novelty_")):
        shortfall_type = "data"
        next_cycle = (
            "collect the missing comparison evidence needed to resolve novelty "
            "against the same charter"
        )
    elif issue.startswith(("significance_", "survey_significance_")):
        shortfall_type = "scale"
        next_cycle = (
            "buy enough repeats, evaluation units, or representative systems to "
            "lift the evidence to the chartered significance level"
        )
    elif issue.startswith("missing_research_evidence"):
        shortfall_type = "evaluator"
        next_cycle = (
            "produce a reproducible evaluator result with independent evidence "
            "before making another completion claim"
        )

    if issue.startswith(("significance_", "survey_significance_")):
        levels = ("exploratory", "publishable", "doctoral")
        try:
            distance = max(1, levels.index(str(target)) - levels.index(actual))
            amount = (
                f"significance={actual!r} is {distance} declared level(s) below "
                f"the chartered {target!r} level"
            )
        except ValueError:
            amount = f"significance={actual!r} does not clear target={target!r}"
    elif issue.startswith("result_class_"):
        accepted = sorted(ACCEPTED_SIGNIFICANCE.get(str(target), ()))
        amount = (
            f"result_class={result['result_class']!r} supplies 0 of the 1 "
            f"required qualifying terminal results at target={target!r}; "
            f"accepted significance levels are {', '.join(accepted) or 'none'}"
        )
    else:
        amount = f"one required charter gate remains unmet: {issue}"

    measured_detail = next(
        (
            " ".join(str(text).split())[:500]
            for text in [*result["evidence"], *result["limitations"]]
            if any(character.isdigit() for character in str(text))
        ),
        "",
    )
    if measured_detail:
        amount += f"; measured evidence: {measured_detail}"

    from .artifact_freshness import artifact_freshness_issues
    from .contamination_check import contamination_issues

    integrity_issues = tuple(
        [f"contamination: {text}" for text in contamination_issues(project_root)]
        + [
            f"artifact freshness: {text}"
            for text in artifact_freshness_issues(project_root)
        ]
    )
    if integrity_issues:
        return IterationAssessment(
            shortfall=amount,
            blocking_issues=integrity_issues,
        )

    objective = (
        "Close the measured charter shortfall without narrowing the original claim.\n"
        f"What fell short: {issue}.\n"
        f"By how much: {amount}.\n"
        f"Shortfall type: {shortfall_type}.\n"
        f"What this cycle buys: {next_cycle}.\n"
        "Keep the baseline, endpoint, and evaluation scale fixed unless a change is "
        "explicitly part of the diagnosed fix; then rerun the blocking comparison."
    )
    return IterationAssessment(shortfall=amount, objective=objective)



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
    "You are trying to win, not trying to be safe. The goal of the campaign is a "
    "method that beats the strongest baseline the field would put against it, and "
    "every round should be spent making that more likely. Caution has a price and "
    "it is usually invisible: seven campaigns here each measured carefully, "
    "reported honestly, and produced nothing anyone would cite, because none of "
    "them was ever trying to succeed — only trying not to overclaim. When you do "
    "win, say so plainly and immediately, in the first sentence, with the number: "
    "burying a real win under qualifications is as much a misreport as inventing "
    "one, and it is the more common failure here. "
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
    "what you are claiming, not a smaller claim. Keep the campaign chasing that fix "
    "for as long as the budget allows: a strong result is normally reached after "
    "many rounds of better engineering and larger runs, not on the first honest "
    "attempt, and the campaign that stops early is the one that ships a bounded "
    "negative. Watch where the missions are actually going, because that ratio is "
    "the campaign's real strategy: across seven campaigns 68 percent of missions "
    "repaired self-built measurement code and 6 percent made the method better, "
    "which is why none of them reached a result worth publishing. A method almost "
    "never wins in its first form. If several rounds have passed and no mission "
    "has proposed a stronger version of the idea itself — a better objective, a "
    "better estimator, a better selection rule, more capacity, more data — the "
    "campaign is maintaining infrastructure rather than doing research, and that "
    "is the intervention to make. "
    "Your job at this altitude is to notice when a run is not worth "
    "improving because it was broken — a number nowhere near what this model and "
    "benchmark are known to do, a control that detects nothing, an evaluation too "
    "small to resolve the claim — and send it back to be rebuilt properly instead "
    "of letting the campaign interpret it. "
    "A boundary or mechanism finding is worth writing when it is genuinely "
    "the interesting thing you found and you can show it at real scale, not when it is "
    "what is left after giving up. "
    "What gets a reviewer excited is narrow and worth aiming at: explaining something the field assumed it already understood, a connection between two areas nobody had joined, a principled method where the principle does the work, or a result that contradicts what everyone expected. None of those is a bigger number. What loses a reviewer is equally narrow: a problem never shown to be real, an increment with no new idea, and above all a claim the results do not support. Say the thing you found and let it be judged. "
    "Apply requirements proportionally to the actual claim and contribution shape; "
    "mark inapplicable items instead of manufacturing work. Rigour apparatus is "
    "proportional too, and it is not free: seeds, repeats, content hashes, "
    "provenance ledgers and schema validators cost the hours the method needed. "
    "Buy them where the answer is genuinely in doubt — repeats when the margin is "
    "near the noise, a hash when two artifacts are actually at risk of being "
    "confused. A campaign that hashes every file while its evaluation cannot "
    "detect its own positive control has bought certainty about which bytes it "
    "measured and none about whether the measurement means anything. When the gap "
    "is enormous or the instrument is broken, more seeds answer nothing. "
    "Reuse certified upstream "
    "evidence and do not reopen it without a concrete contradiction. Default to advance "
    "with explicit limitations and a small number of high-value next actions. Stop for "
    "fabricated evidence, invalid measurement, or a headline claim the data "
    "contradicts — those cost the paper everything and are the reason the bold claim "
    "has to be a real one. "
)

_REVIEWER_RESEARCH_JUDGEMENT = (
    "A round can be done and the paper still be unpublishable, and that is the case this campaign keeps landing in: each mission closes correctly, the manuscript stays where it was, and nothing ever says the programme cannot reach the bar. When the local work is sound but the evidence or the visual argument still could not survive the venue -- one model where accepted work spans several, no ablation that rules out the obvious alternative, claims a reader has to take on trust -- accept the round and set `plan_signal` to `reconsider`, naming what is missing and the work that would supply it. Manufacturing a local failure to force that conversation is the wrong tool, and staying silent is how a campaign spends a week being correct about nothing that matters. Read the whole run before judging the number. A result far off what this "
    "model and benchmark are known to do is a defect report, not a finding: say "
    "so, name the suspect setting, and send it back to be rebuilt rather than "
    "scoring it. The settings that silently destroy a result are ordinary and "
    "few people check them — a generation cap shorter than the answer needs, so "
    "the model is cut off before it can be right; a protocol step the published "
    "number assumes and this run skipped, like actually executing the tool in "
    "tool-integrated reasoning; RL or SFT training whose sequences are far "
    "shorter than what the task requires at inference, which teaches the model "
    "to stop early; a scorer that cannot recognise a correct answer in the form "
    "the model writes it; an evaluation too small to see the declared margin. "
    "Those are examples, not a list to tick: ask each time which single setting, "
    "if wrong, would produce exactly the number in front of you, then go and "
    "look at it. Training that leaves every variant below its own untrained "
    "starting checkpoint is the same kind of report: the pipeline degraded the "
    "model, and ranking the variants against each other hides it. Ask for the "
    "untrained checkpoint under the identical protocol in every table where a "
    "trained method appears. "
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
    "threshold nobody can argue with or fail. Name too the run that would "
    "convince a reader who wants the claim to be false: the field's standard "
    "split at the field's standard size, repeats enough to put the margin "
    "outside their own spread, and the baselines a referee would ask for "
    "unprompted. Buy that measurement early, so there is a number to improve "
    "for the rest of the campaign. Scale is part of the argument, not a cost "
    "to minimize: a win of three examples on 120, or one on 48, is not a small "
    "result but no result, and three campaigns have already filed one. When "
    "the convincing run will not fit at once, stage it and buy it in pieces; "
    "shrinking it into a run nobody can believe spends the whole budget on "
    "nothing.\n"
    "Short of the baseline is a gap with a "
    "size, and the campaign's job is to close it: each round names what the "
    "shortfall is made of, buys the implementation, optimization, data, scale or "
    "evaluator fix that addresses it, and measures again. Papers are won this "
    "way, over many rounds, and an early miss is the normal starting position "
    "rather than a verdict on the idea. A protocol is worth only the evidence it "
    "ends up governing: one campaign wrote an 808-word protocol section against "
    "704 words of introduction, method and results combined, and a results "
    "section reporting no measured value at all. Specification is unbounded and "
    "costs nothing, measurement is bounded and expensive, and a campaign under "
    "deadline drifts toward the free one while the artifact still reads as "
    "rigour. When the protocol is longer than the science it protects, stop "
    "specifying and buy the cheapest measurement that would make any of it real. "
    "Keep only research-stage route "
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
    "instead of the paper. When a mission will sit for hours on external "
    "compute, queue beside it the work that does not need its result: the "
    "baseline to reproduce, the analysis to write against the agreed schema, "
    "the section the paper already owes. Campaigns can run two missions at once "
    "and have been running one, so eighteen hours of GPU wait across five rounds "
    "bought nothing else — and wall-clock is most of what a paper costs. Say how, "
    "or none of it happens: a task can be claimed alongside another only when it "
    "is `parallel_safe` with a concrete `owns_paths` list AND every running task "
    "is too. One unmarked long run switches parallelism off for the whole "
    "campaign, which is why six of seven campaigns queued nothing beside their "
    "GPU work for a day. Mark the long run with the directories it writes, mark "
    "the writing task with `paper`, keep both lists free of globs, absolute "
    "paths and commas inside one entry, and make sure they do not overlap.\n"
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
    "The frontier is live throughout the campaign, not a packet read once at "
    "selection. Re-search current papers and official implementations whenever "
    "a headline result lands, a baseline wins, the thesis changes, or a paper "
    "claim is written. Ask what current belief the result overturns, what "
    "assumption the field has left untested, and whether the selected idea has "
    "become another safe selector, allocator or local score improvement. "
    "Literature is not decoration for Related Work: use it to make the method "
    "less obvious, the experiment harder to dismiss, and the programme bolder. "
    "If the campaign has retreated from the idea it selected, treat that as a "
    "plan-level failure and reopen the technical direction with what the new "
    "evidence taught you. "
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


def _literature_ledger_block(project_root: object) -> str:
    """Verified papers the campaign found and then left out of the paper.

    run-05 searched harder than either campaign that ended with a real
    bibliography -- sixty-one searches against forty-one -- and wrote thirteen
    papers into the ledger with full author lists. Seven reached the
    manuscript, all of them stripped to a bare title; the Engineer deleted the
    note "author metadata to be filled from the source ledger" without filling
    it. Six verified papers were never cited at all. The reference count read
    as an ordinary thin bibliography, so nothing said the reading had already
    been done and lost on the way to the page.

    Only counts and one example are stated. Which papers belong in the related
    work is the Agent's judgement. Fail-soft: any error yields no block.
    """
    try:
        import json
        import re
        from pathlib import Path as _Path

        root = _Path(str(project_root)).resolve()
        payload = json.loads(
            (root / "research" / "LITERATURE_GROUNDING.json").read_text(
                encoding="utf-8"
            )
        )
        papers = [p for p in payload.get("papers") or [] if isinstance(p, dict)]
        if not papers:
            return ""
        bib = ""
        for name in ("references.bib", "refs.bib", "bibliography.bib"):
            path = root / "paper" / name
            if path.exists():
                bib += path.read_text(encoding="utf-8", errors="ignore")
        if not bib:
            return ""

        # Punctuation dropped and runs of space collapsed on both sides, so a
        # colon in "SAEBench: A Comprehensive..." cannot hide a real citation.
        flattened_bib = " ".join(re.sub(r"[^a-z]+", " ", bib.lower()).split())

        def _cited(paper: dict) -> bool:
            arxiv = str(paper.get("arxiv_id") or "").strip()
            if arxiv and arxiv in bib:
                return True
            words = re.findall(r"[a-z]+", str(paper.get("title") or "").lower())
            return bool(words) and " ".join(words[:6]) in flattened_bib

        missing = [p for p in papers if not _cited(p)]
        named = sum(1 for p in papers if p.get("authors"))
        if not missing:
            return ""
        example = str(missing[0].get("title") or "").strip()[:70]
        uncited = (
            f"{len(missing)} of them are not cited anywhere in the bibliography"
            if len(missing) > 1
            else "one of them is not cited anywhere in the bibliography"
        )
        return (
            f"\nLITERATURE LEDGER: research/LITERATURE_GROUNDING.json holds "
            f"{len(papers)} verified papers, {named} with full author lists. "
            f"{uncited}, among them {example!r}.\n"
        )
    except Exception:  # noqa: BLE001
        return ""


def _manuscript_high_water_block(project_root: object) -> str:
    """What this draft used to be, when it is now smaller.

    Campaigns rebuild the manuscript around each new experiment revision rather
    than extending it, and the accumulated prose and figure includes go with
    it. In one night run-02 went from 7,830 words and five figures to 4,523 and
    one, run-03 from 4,230 to 1,460, run-06 from 3,640 to 1,515, and run-07
    dropped every figure it had. Each rebuild reads like ordinary progress from
    the inside, and nothing on disk remembers the larger draft: main.aux is
    rewritten by the next compile, and the campaigns are not under git.

    So the peak is recorded here, and stated only while the draft sits below
    it. Rebuilding can be right -- run-03's rewrite followed a result that
    genuinely replaced its claim -- so nothing is scored or refused. The
    campaign is only told what it had, which it otherwise has no way to know.
    """
    try:
        import json
        import time
        from pathlib import Path as _Path

        root = _Path(str(project_root)).resolve()
        words, figures = _manuscript_size(root)
        if not words:
            return ""
        record = root / "paper" / ".manuscript_peak.json"
        try:
            peak = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            peak = {}
        peak_words = int(peak.get("words") or 0)
        peak_figures = int(peak.get("figures") or 0)
        if words >= peak_words and figures >= peak_figures:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(
                json.dumps(
                    {
                        "words": max(words, peak_words),
                        "figures": max(figures, peak_figures),
                        "at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            return ""
        return (
            f"\nEARLIER DRAFT: this manuscript has been {peak_words:,} words "
            f"with {peak_figures} figure(s); it is now {words:,} with "
            f"{figures}.\n"
        )
    except Exception:  # noqa: BLE001
        return ""


def _manuscript_size(root: object) -> tuple[int, int]:
    """Body-text word count and figure includes, cut at the appendix.

    The exemplar side of this comparison has always been cut at its reference
    list, and the draft side was not, so everything after \\appendix counted as
    body. A campaign satisfies that by moving the paper into the appendix, and
    one did: run-04 read as 10,185 words and eleven figures next to exemplars
    running 15,673 and 16,221, while its body held 1,481 words and four figures
    and its appendix held 8,145 and seven. The comparison was telling it it had
    nearly arrived.
    """
    import re
    from pathlib import Path as _Path

    from .academic_language_review import _latex_to_plain_text, _word_count

    try:
        tex = (_Path(str(root)) / "paper" / "main.tex").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return 0, 0
    body = re.split(r"\\appendix\b", tex, 1)[0]
    return _word_count(_latex_to_plain_text(body)), len(
        re.findall(r"\\includegraphics", body)
    )


def _manuscript_scale_block(project_root: object) -> str:
    """This draft's length beside the accepted papers the campaign chose.

    Every campaign picked same-area accepted papers as its standard and pulled
    the full text to disk. Nothing ever compared the two. run-01's manuscript
    is a fifth the length of the two ICLR papers it named, and read from the
    inside like a finished short paper: sections present, citations present,
    figures present. A fixed word quota was deliberately never added because it
    only teaches a draft to pad; the campaign's own exemplars are a standard it
    already agreed to, and the gap is a fact rather than a target.

    Both sides are measured as body text, cut at the reference list, so the
    comparison is like for like. Fail-soft: any error yields no block.
    """
    try:
        import json
        import re
        from pathlib import Path as _Path

        from .academic_language_review import _latex_to_plain_text, _word_count
        from .argument_organization import ARGUMENT_ORGANIZATION_PATH

        root = _Path(str(project_root)).resolve()
        draft, drawn = _manuscript_size(root)
        if not draft:
            return ""
        payload = json.loads(
            (root / ARGUMENT_ORGANIZATION_PATH).read_text(encoding="utf-8")
        )
        lengths: list[int] = []
        figures: list[int] = []
        for exemplar in payload.get("exemplars") or []:
            if not isinstance(exemplar, dict):
                continue
            extract = root / str(exemplar.get("text_extract") or "")
            if not extract.is_file():
                continue
            text = extract.read_text(encoding="utf-8", errors="ignore")
            body = re.split(r"\n\s*(?:references|bibliography)\s*\n", text, 1,
                            re.IGNORECASE)[0]
            lengths.append(_word_count(body))
            # The highest figure number the paper refers to is how many it
            # carries, and it survives text extraction when the images do not.
            numbered = [int(n) for n in re.findall(r"\bFigure\s+(\d{1,2})\b", body)]
            if numbered:
                figures.append(max(numbered))
        if not lengths:
            return ""
        lengths.sort()
        # A median needs enough samples to mean anything; with two exemplars
        # the range is the whole truth and quoting a "typical" would inflate it.
        span = (
            f"{lengths[0]:,}-{lengths[-1]:,}, typically "
            f"{lengths[len(lengths) // 2]:,}"
            if len(lengths) > 2
            else f"{lengths[0]:,} and {lengths[-1]:,}"
        )
        figure_note = (
            f" with {min(figures)}-{max(figures)} figures" if figures else ""
        )
        return (
            f"\nMANUSCRIPT SCALE: {draft:,} words, {drawn} figure(s). The "
            f"accepted papers this campaign chose run {span} words"
            f"{figure_note}.\n"
        )
    except Exception:  # noqa: BLE001
        return ""


def _paper_notes_block(project_root: object) -> str:
    """Structural facts that are true but are not reasons to fail a gate.

    A figure drawn and left out, or a reference with nobody's name on it, tells
    the campaign something worth knowing without meaning the draft stopped
    being a draft. Blocking on them would have made nine unrelated fixtures
    "not yet a paper" for owning a spare image, which is the sign of a fact
    wearing a verdict's clothes. Fail-soft: any error yields no block.
    """
    try:
        from pathlib import Path as _Path

        from .paper_structural_minimums import validate_paper_structural_minimums

        report = validate_paper_structural_minimums(
            _Path(str(project_root)).resolve()
        )
        # Only what reading the paper cannot show. A missing figure or a
        # sixteen-decimal number is visible to anyone who opens the file, and
        # listing those here is the host doing the reading -- which can only
        # ever catch the faults someone thought of first. Work that was paid
        # for and left outside the manuscript is different: it is invisible in
        # the artifact, because it is not in it.
        lines = [note.detail for note in report.notes]
        if not lines:
            return ""
        return "\nPAPER NOTES: " + " ".join(f"{line}." for line in lines) + "\n"
    except Exception as exc:  # noqa: BLE001 — prompt building must not break
        # An empty block already means "nothing structural to report", so a
        # validator that exploded must not borrow that meaning: the campaign
        # would read the same silence as a clean bill of health for a
        # manuscript nobody managed to check. Stay fail-soft, fail loud.
        log.exception(
            "paper-notes: structural fact collection failed for %r", project_root
        )
        return (
            "\nPAPER NOTES: structural fact collection FAILED "
            f"({type(exc).__name__}: {exc}). Nothing about this manuscript was "
            "checked -- treat its structure as UNVERIFIED, not confirmed good.\n"
        )


def _framework_maintenance_share_block(project_root: object) -> str:
    """How much of this campaign's work went to the framework, not the paper.

    Repairing Argus from inside a campaign is legitimate and has found real
    defects -- one campaign independently derived a parser fix the same night
    it was made upstream. But the missions come out of the same budget as the
    research, and the two campaigns whose manuscripts had not changed a word in
    hours were the two spending the most on it: 6 of run-04's 13 missions in a
    day, 8 of run-05's 24. run-04 ran seven research missions in twenty-four
    hours.

    The count is stated and nothing is scored. Whether the framework or the
    paper is the better use of the next mission is the Manager's call; it just
    should not be made without knowing the ratio. Fail-soft: any error, or no
    maintenance at all, yields no block.
    """
    try:
        import json
        from pathlib import Path as _Path

        root = _Path(str(project_root)).resolve()
        backlogs = sorted(
            root.glob("state/projects/*/backlog.jsonl"),
            key=lambda path: path.stat().st_mtime,
        )
        if not backlogs:
            return ""
        done = maintenance = 0
        for line in backlogs[-1].read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("status") or "") != "done":
                continue
            done += 1
            tags = " ".join(str(tag) for tag in row.get("tags") or ())
            if "self_maintenance" in tags or "framework_maintenance" in tags:
                maintenance += 1
        if not maintenance:
            return ""
        return (
            f"\nFRAMEWORK SHARE: {maintenance} of this campaign's {done} "
            f"finished missions repaired Argus itself rather than advancing "
            f"this paper. Both come out of the same budget.\n"
        )
    except Exception:  # noqa: BLE001
        return ""


def _campaign_stage(root: object) -> str:
    """The stage this campaign is actually in.

    The pipeline state lives under the session directory, not the worktree, so
    reading it from the worktree silently yields the default first stage --
    which would make a campaign at `submission` look like one at `research`.
    """
    from pathlib import Path as _Path

    from ...skills.stage_machine import current_stage

    base = _Path(str(root)).resolve()
    candidates = sorted(
        base.glob("state/projects/*/.argus/PIPELINE_STATE.json"),
        key=lambda path: path.stat().st_mtime,
    )
    for state in reversed(candidates):
        stage = str(current_stage(state.parent.parent) or "").strip().lower()
        if stage:
            return stage
    return str(current_stage(base) or "").strip().lower()


def _unasked_manuscript_block(project_root: object) -> str:
    """Ask the reviewer's question while the stage checklist is not asking it.

    Only draft, review and submission carry paper-facing checklist items.
    Campaigns write the paper long before they declare those stages: run-07
    holds a 6,863-word, twenty-page manuscript with no figures at all and
    numbers printed to sixteen decimal places, while its stage is `benchmark`
    and every one of its last hundred and seventy reviews asked whether a
    measurement packet had the right JSON files in it. Nobody had asked whether
    the paper was a paper.

    The question is asked here, and only the question. Enumerating what is
    wrong with the manuscript would be the host doing the reading, and it can
    only ever find the faults someone thought of in advance; the reviewing
    Agent can open the file. Silent once the stage asks this itself, and silent
    before there is a manuscript to ask about. Fail-soft: any error is silence.
    """
    try:
        from pathlib import Path as _Path

        root = _Path(str(project_root)).resolve()
        words, figures = _manuscript_size(root)
        if not words:
            return ""
        stage = _campaign_stage(root)
        if stage in _PAPER_FACING_STAGES:
            return ""
        exemplars = root / "paper" / "style_ref" / "exemplars"
        beside = (
            f" beside the accepted papers whose full text is in "
            f"{exemplars.relative_to(root)}"
            if exemplars.is_dir()
            else ""
        )
        return (
            f"\nTHE PAPER IS ALREADY REAL: paper/main.tex holds {words:,} words "
            f"and {figures} figure include(s) while this campaign's stage is "
            f"{stage or 'unset'!r}, whose checklist asks nothing about the "
            f"manuscript. Open it and judge it as the reviewer who will decide "
            f"it{beside}. What would get it rejected is work, not a note.\n"
        )
    except Exception:  # noqa: BLE001
        return ""


def search_altitude_context(project_root: object) -> str:
    """Everything a role should have in view before it judges its own work.

    Two facts the campaign wrote down itself and then never reopened: what it
    promised at selection, and which accepted papers it said it would learn
    from. Both are rendered; neither is scored.
    """
    return (
        _selection_contract_block(project_root)
        + _accepted_papers_block(project_root)
        + _literature_ledger_block(project_root)
        + _manuscript_scale_block(project_root)
        + _paper_notes_block(project_root)
        + _unasked_manuscript_block(project_root)
        + _framework_maintenance_share_block(project_root)
        + _manuscript_high_water_block(project_root)
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
            + "\nA margin filed here is a plan, not a verdict: it was written "
            "before anyone knew the effect size. Decide on the measured effect "
            "against the baseline and its interval -- a number under a self-set "
            "line still counts if it separates, and one over it does not if it "
            "cannot.\n"
            "If the claim has moved since, say so and why: drift you argue for "
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
            + "\nCount what they carry before deciding your own draft is done: "
            "how many references, how many figures, and what the first figure "
            "is asked to do. Seven campaigns here filed nine to eighteen "
            "references and none to six figures against papers accepted with "
            "several times that, which a reviewer reads as a related-work "
            "section nobody wrote.\n"
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
    "iteration_assessment",
    "completion_gate",
    "PAPER_MISSION",
]
