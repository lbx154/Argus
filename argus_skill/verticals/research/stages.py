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
from ...skills.venue_profiles import VenueProfile, resolve_venue_profile

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
                "The canonical literature ledger covers the claims the project "
                "actually depends on: the nearest competing methods, the relevant "
                "lineage/classic anchors, contradictory or negative evidence, and "
                "the unresolved frontier. Each retained source has a verifiable "
                "primary URL and a project-relevant implication. Judge connected "
                "claim coverage, not a fixed paper or query count."
            ),
            evidence_hint=(
                "research/LITERATURE_GROUNDING.json (canonical); "
                "research/LIT_MATRIX.tsv is generated with "
                "`python -m argus_skill.verticals.research.literature_ledger sync`"
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
                "The project states why its proposed thesis would matter to the "
                "target community, what evidence could falsify it, and whether it is "
                "worth the experiment budget. A paper-shaped deliverable is not itself "
                "a reason to continue."
            ),
            evidence_hint="research/RESEARCH_BRIEF.md and the primary sources it cites",
        ),
        ChecklistItem(
            id="research.signal_derisk",
            statement=(
                "Before leaving research, the locked idea survives the cheapest REAL "
                "falsification probe that tests its binding premise on this machine. "
                "The Planner authors the evidence contract for the research shape: a "
                "comparative method may use measured baseline/proposed deltas; a "
                "systems or architecture idea may test fidelity plus the claimed "
                "resource/stability signal; theoretical or survey work uses its own "
                "decisive counterexample/coverage test. Prefer <=10 minutes / <=$1 "
                "when faithful, but do not substitute a toy proxy merely to meet that "
                "budget. Preserve commands and raw outputs. Store the outcome without "
                "turning it into a mechanical routing decision; the Planner reads it "
                "and decides what it changes. A passed wiring-only smoke does not prove "
                "the thesis. "
                "`argus_skill.skills.signal_derisk validate` is available only for "
                "the default scalar-comparison shape and never decides quality."
            ),
            evidence_hint=(
                "Planner-authored research.signal_derisk evidence paths; for the "
                "default scalar shape use research/SIGNAL_DERISK.json + raw log"
            ),
        ),
    ),
    "plan": _checklist(
        ChecklistItem(
            id="plan.experiment",
            statement=(
                "Experiment plan states the hypothesis, the proposed method, the "
                "baselines (including the strongest feasible prior work), the "
                "ablations, the metrics, the success threshold, and the compute / "
                "API budget."
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
                "those records. Never invent an evaluator, gold label, participant, "
                "visit, or task merely to satisfy this item."
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
            id="run.environment_preflight",
            statement=(
                "Each pilot/full/ablation launch has a fresh, run-specific "
                "Environment Readiness Gate transcript. Verify only resources that "
                "run actually uses (environment, data/evaluator, storage, GPU/model/API "
                "as applicable); an applicable failed or missing preflight means the "
                "run is uncertified."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt (per run)",
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
                "not the sole final evidence."
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
                "`jq -r .score experiments/runs/<id>/results/<family>/scored_rows.jsonl"
                " | sort -u | wc -l` should be > 1 per file with >3 rows"
            ),
        ),
        ChecklistItem(
            id="run.method_diagnosis_recall",
            statement=(
                "Before calling an underperforming idea a scientific failure, audit "
                "whether the implementation is faithful and competitive: compare "
                "against trusted reference behavior, inspect actual executed knobs "
                "and loaded checkpoint identity/capability when relevant, inspect "
                "evaluator semantics, diagnose optimization/tuning/capacity/data "
                "limits, and test concrete plausible repairs when their information "
                "gain justifies the cost. Classify the outcome as misconfigured, "
                "under-engineered, genuine method failure, or infeasible. Do not use "
                "a fixed retry count, generic extra scale, or passing unit tests as a "
                "substitute for this diagnosis."
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
            id="analysis.gaps",
            statement=(
                "Known evidence gaps are explicitly enumerated with a planned "
                "supplement, ablation, or claim downgrade — no missing evidence "
                "is silently absorbed."
            ),
            evidence_hint="paper/main.tex limitations + Reviewer notes + raw results",
        ),
        ChecklistItem(
            id="analysis.thesis",
            statement=(
                "The evidence supports one defensible, venue-relevant thesis. Internal "
                "records preserve all valid outcomes, but the proposed paper is a "
                "selective argument: claim-critical contrary evidence remains visible; "
                "misconfigured runs, exploratory dead ends, and secondary diagnostics "
                "are kept in audit artifacts or an appendix rather than dumped into "
                "the main narrative. If the original method claim failed and no "
                "standalone insight remains, return to research/plan instead of drafting."
            ),
            evidence_hint="paper/main.tex + canonical raw evidence + Reviewer judgment",
        ),
    ),
    "draft": _checklist(
        ChecklistItem(
            id="draft.tex",
            statement=(
                "paper/main.tex uses the selected venue's official structure and tells "
                "one coherent argument. The title, abstract, introduction, method, and "
                "experiments all serve the same thesis; the paper does not introduce "
                "a method as its contribution and then make that method's failure the "
                "main conclusion without an independently valuable insight."
            ),
            evidence_hint="paper/main.tex + research/VENUE_PROFILE.json + research/NARRATIVE_REPORT.md",
        ),
        ChecklistItem(
            id="draft.pdf",
            statement=(
                "paper/main.pdf compiles cleanly: no '??' citations, no undefined "
                "references, no material overflow, and no LaTeX errors. Its body and "
                "back matter obey the selected venue's actual page and format rules; "
                "do not pad a weak argument to fill a historical page quota."
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
            evidence_hint="paper/references.bib + verification log",
        ),
        ChecklistItem(
            id="draft.figures",
            statement=(
                "The paper's figures are clear, readable at final size, visually "
                "coherent, and attractive enough for the venue. Use a good-enough "
                "standard: minor stylistic imperfections are not blockers, and do "
                "not request repeated regeneration unless a figure is unreadable, "
                "factually wrong, visibly broken, or seriously harms the paper."
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
                "Paper prose contains no local paths (/root/, /home/), no Argus / "
                "Codex / daemon route names, no capability vault references, no "
                "device IDs, no API keys — the manuscript is publication-clean."
            ),
            evidence_hint="grep main.tex for '/root/', 'CUDA_VISIBLE_DEVICES', 'argus-skill', 'codex', 'OPENAI_API_KEY'",
        ),
        ChecklistItem(
            id="review.placeholders",
            statement=(
                "No PLACEHOLDER / TODO / TBD / FIXME / UNVERIFIED markers in the "
                "paper body, captions, or tables."
            ),
            evidence_hint="grep -nE 'PLACEHOLDER|TODO|TBD|FIXME|UNVERIFIED' paper/main.tex",
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
                "output: the Abstract states problem, gap, method, evidence, and "
                "implication (no result-first opening, no validator-checklist "
                "phrasing); the Introduction grounds the gap in cited prior work, "
                "then gives the method insight, a quantified result preview, and a "
                "contribution roadmap before Related Work; the Method/Setup lets an "
                "outside reviewer identify the evaluated system, baselines, task "
                "source, metrics, evaluated model/backend, and budget; every "
                "headline claim is tied to reported evidence; no unsupported hype, "
                "template LLM openings, experiment-report narration, or repeated "
                "not-X-but-Y caveats. Limitations bound the thesis instead of becoming "
                "the paper's central message. The "
                "model-backed reviewer (academic_language_review) is advisory "
                "input — this checklist, judged by the reviewer agent, is the "
                "source of truth."
            ),
            evidence_hint="paper/main.tex Abstract/Introduction/Method + paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)",
        ),
        ChecklistItem(
            id="review.publication_value",
            statement=(
                "As a venue reviewer, identify the strongest accept argument before "
                "passing. A valid experiment, transparent failure report, or complete "
                "artifact bundle is not enough: the manuscript must deliver a clear "
                "insight, capable method/system, theorem, or genuinely surprising and "
                "decision-relevant boundary. A weak result cannot be rescued by "
                "renaming it a diagnostic."
            ),
            evidence_hint="paper/main.tex + paper/main.pdf + canonical evidence",
        ),
    ),
    "submission": _checklist(
        ChecklistItem(
            id="submission.upstream",
            statement=(
                "All upstream stage checklists (research → review) are themselves "
                "marked done by a prior reviewer round — submission readiness is "
                "not a way to retro-fix missing evidence."
            ),
            evidence_hint="research/PIPELINE_STATE.json shows every stage status=done",
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
        "live-search domain-appropriate CCF-A conferences whose deadline has not "
        "passed, record the official CCF/CFP/deadline evidence in "
        "`research/VENUE_SELECTION.md`, and write `research/VENUE_PROFILE.json` "
        f"from the selected official author kit. {instruction}"
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
        "the same thesis;",
        f"Required venue end matter: {venue.draft_section_tail()}. The title, "
        "abstract, introduction, method, and experiments all serve the same thesis;",
    )
    if venue.key == "FRONTIERS_SLEEP":
        replacements = {
            (
                "paper/main.tex uses the official Frontiers in Sleep journal-article "
                "sections and tells one coherent argument."
            ): (
                "paper/main.tex uses the Frontiers in Sleep Hypothesis and "
                "Theory sections in a coherent order: one-paragraph Abstract, "
                "Introduction, subject-relevant evidence and theory subsections, "
                "discriminating tests or proposed study, Discussion, Conclusion, "
                "required declarations, and References. The article tells one "
                "coherent argument."
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
                "Paper prose contains no local paths (/root/, /home/), no Argus / "
                "Codex / daemon route names, no capability vault references, no "
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
completion_gate = "full_paper"

# Research proceeds through strict stage gates, but evidence reuse within those
# stages is proportional: once a Reviewer certifies a source or artifact, later
# bounded missions verify only the new claim/delta unless a concrete conflict
# reopens it. This keeps scientific integrity without repeatedly rebuilding the
# same provenance tree.
WORKFLOW_MODE = "proportional"

# Scientific implementation and experiment claims always require a fresh,
# independent Reviewer; an Engineer verifier cannot waive this review.
REQUIRE_INDEPENDENT_REVIEW = True

_REVIEWER_ENGINEERING_AUDIT = (
    "For experiment claims, inspect the relevant implementation and raw rows once, "
    "then reuse that reviewed evidence until a dependency changes. Distinguish the "
    "method result from infrastructure or evaluator failure.\n"
)


def role_banner(role: str = "engineer") -> str:
    """Add the research-specific engineering contract to Reviewer prompts."""
    return _REVIEWER_ENGINEERING_AUDIT if role == "reviewer" else ""


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
    "REQUIRE_INDEPENDENT_REVIEW",
    "role_banner",
    "completion_gate",
]
