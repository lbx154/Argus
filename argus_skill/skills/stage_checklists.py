"""Stage-aware checklists that the L2 reviewer verifies for each project.

This module replaces the historical CLI-based ``validate-*`` toolbelt that
was injected into engineer / reviewer / critic prompts. Validators turned
into gates the agent kept trying to defeat; checklists are written for a
human-level reviewer to read, ground in artifacts, and rule on.

The validator functions under :mod:`argus_skill.skills.pipeline_contracts`
are intentionally still importable — the :mod:`argus_skill.life.supervisor`
harness uses them for project-done detection. They are no longer exposed
on the CLI or in any agent prompt; the only thing the agent sees is the
markdown checklist for the current stage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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


@dataclass(frozen=True)
class ChecklistItem:
    """One verifiable item on a stage checklist."""

    id: str
    statement: str
    evidence_hint: str


def _checklist(*items: ChecklistItem) -> tuple[ChecklistItem, ...]:
    return tuple(items)


STAGE_CHECKLISTS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": _checklist(
        ChecklistItem(
            id="research.literature",
            statement=(
                "Literature grounding lists at least 10 recent high-quality papers "
                "and at least 3 classic anchor papers, with verifiable venue/URL "
                "and a paper-relevant summary for each."
            ),
            evidence_hint="research/LITERATURE_GROUNDING.json, research/LIT_MATRIX.tsv",
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
            id="research.rejection",
            statement=(
                "At least one mediocre / already-done idea is explicitly rejected "
                "with reasoning, not silently skipped."
            ),
            evidence_hint="research/IDEA_REJECTION_LOG.md",
        ),
        ChecklistItem(
            id="research.go_no_go",
            statement=(
                "A GO / NO-GO verdict is written for whether this thesis is worth "
                "the experiment budget, with pivot criteria if conditions fail."
            ),
            evidence_hint="research/GO_NO_GO.md",
        ),
        ChecklistItem(
            id="research.references",
            statement=(
                "Reference repos that will be reused or compared against are "
                "shallow-cloned locally with origin URL and commit recorded."
            ),
            evidence_hint="code/references/<repo>/.git/config + a notes file",
        ),
        ChecklistItem(
            id="research.infra_shortlist",
            statement=(
                "If the project will involve gradient-based training or "
                "large-scale inference, an initial training-infra and "
                "inference-infra shortlist is recorded. Candidates must be "
                "actively maintained open-source frameworks (last release "
                "or commit in 2026 or later); self-written training/inference "
                "loops are forbidden. The shortlist must (a) anchor against "
                "the bundled `argus_builtin_skills/training-infrastructure-guide.md`, "
                "(b) add at least one candidate the agent independently "
                "discovered (with URL + last-commit date + paper/citation), "
                "and (c) note any candidate from the bundled guide that is no "
                "longer maintained and must be excluded."
            ),
            evidence_hint="research/INFRA_SHORTLIST.md",
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
                "Benchmark-and-baseline plan names at least 3 independent real "
                "benchmark families (not 3 splits of the same dataset) with URL, "
                "license, task count, and capability tested for each."
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
                "training-infra and inference-infra choice is locked in (one "
                "framework per axis, picked from research.infra_shortlist) with "
                "an explicit rationale tying each choice to the project's domain "
                "(e.g. diffusion RL post-training vs LLM SFT vs agent RL) and to "
                "the resource budget. The chosen frameworks must be 2026+-active, "
                "open-source, and explicitly NOT custom training/inference loops. "
                "Cite the chosen project's repo URL, last release/commit date, "
                "and (if from a paper) the paper. Record both the final choice "
                "and any explicitly-rejected alternative with a one-line reason."
            ),
            evidence_hint="research/INFRA_CHOICE.md + research/EXPERIMENT_PLAN.md `## Infra` section",
        ),
    ),
    "benchmark": _checklist(
        ChecklistItem(
            id="benchmark.tasks",
            statement=(
                "Each selected benchmark family has runnable task files prepared "
                "with their official gold answers (not hand-written placeholders) "
                "and a deterministic loader."
            ),
            evidence_hint="benchmarks/<family>/tasks.jsonl + loader",
        ),
        ChecklistItem(
            id="benchmark.provenance",
            statement=(
                "Benchmark provenance lists ≥3 independent real benchmark families "
                "with version/date, license, split, task count, evaluation harness, "
                "and an execution-readiness status."
            ),
            evidence_hint="experiments/BENCHMARK_PROVENANCE.md and .json",
        ),
        ChecklistItem(
            id="benchmark.smoke",
            statement=(
                "A small smoke run produced at least one real scored row per "
                "benchmark family so the eval harness is known to work end-to-end."
            ),
            evidence_hint="experiments/**/smoke/*.jsonl",
        ),
    ),
    "run": _checklist(
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
                "Proposed method, the strongest feasible literature baseline, and "
                "all planned ablations have completed on every selected benchmark "
                "family — not just one slice."
            ),
            evidence_hint="experiments/**/scored.jsonl across all method × family cells",
        ),
        ChecklistItem(
            id="run.scale",
            statement=(
                "Results are full-scale evidence, not pilot or synthetic — every "
                "row labelled as final is from a real benchmark execution."
            ),
            evidence_hint="experiments/**/manifest.json declares scale=full",
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
            evidence_hint="paper/CLAIM_GRAPH.json + result_to_claim.tsv",
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
            evidence_hint="paper/EVIDENCE_GAPS.json",
        ),
    ),
    "draft": _checklist(
        ChecklistItem(
            id="draft.tex",
            statement=(
                "paper/main.tex exists with the EMNLP/ACL long-paper sections in "
                "the standard order (Abstract, Introduction, Related Work, Method, "
                "Experimental Setup, Results, Analysis / Ablation, Failure Cases, "
                "Conclusion, Limitations, Ethics, Reproducibility appendix)."
            ),
            evidence_hint="paper/main.tex",
        ),
        ChecklistItem(
            id="draft.pdf",
            statement=(
                "paper/main.pdf compiles cleanly: no '??' citations, no undefined "
                "references, no Overfull \\hbox > 5pt, no LaTeX errors, body is "
                "7.5-8.0 pages, References starts on page 9 or later."
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
                "At least one core conceptual figure is generated via image-2 with "
                "preserved prompt, sidecar, sha256, and inspect/review JSON; data "
                "figures are generated from raw results, not hand-drawn."
            ),
            evidence_hint="paper/figures/*.png + .json sidecars + paper/figures/IMAGE2_FIGURES.json",
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
                "Tables follow the style guide (footnotesize, tabcolsep 3-4pt, "
                "arraystretch 1.15, bold winning values) and the body has one "
                "main cross-benchmark results table (table*) covering every "
                "family × method cell."
            ),
            evidence_hint="paper/main.tex table* envs + caption",
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
            id="submission.assurance",
            statement=(
                "Submission assurance memo states explicit PASS / BLOCKED with "
                "named blockers and is current against paper/main.tex and the "
                "latest results."
            ),
            evidence_hint="paper/SUBMISSION_ASSURANCE.md and .json",
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
                "The compiled PDF uses the anonymous EMNLP/ACL author block (no "
                "real author names, affiliations, or self-deanonymizing strings)."
            ),
            evidence_hint="grep paper/main.tex for 'Anonymous EMNLP Submission' + acl review mode",
        ),
    ),
}


def list_stages() -> tuple[str, ...]:
    """Return the canonical stage order (research → submission)."""

    return CANONICAL_STAGE_ORDER


def get_stage_checklist(stage: str) -> tuple[ChecklistItem, ...]:
    """Return the checklist items for ``stage``; empty tuple if unknown."""

    return STAGE_CHECKLISTS.get(_normalize_stage(stage), ())


def _normalize_stage(stage: str | None) -> str:
    if not stage:
        return ""
    return str(stage).strip().lower()


def current_stage(project_root: Path | str = ".") -> str:
    """Read ``research/PIPELINE_STATE.json`` and return the current stage.

    Falls back to ``"research"`` if the file is missing / unreadable / does
    not name a known stage.
    """

    root = Path(project_root)
    state_path = root / "research" / "PIPELINE_STATE.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "research"
    stage = _normalize_stage(payload.get("current_stage") if isinstance(payload, dict) else None)
    if stage in STAGE_CHECKLISTS:
        return stage
    return "research"


def _render_items(items: Iterable[ChecklistItem]) -> str:
    lines: list[str] = []
    for item in items:
        lines.append(f"- [ ] **{item.id}** — {item.statement}")
        lines.append(f"      _evidence to look at:_ `{item.evidence_hint}`")
    return "\n".join(lines)


def format_stage_checklist(stage: str, *, role: str = "engineer") -> str:
    """Render the checklist for ``stage`` as prompt-injectable markdown.

    ``role`` controls the framing line at the top:

    * ``engineer`` — "produce evidence the reviewer can tick off"
    * ``reviewer`` — "tick these items off based on artifacts you read"
    * ``critic`` / ``planner`` — "use this to decide whether more rounds add value"

    An unknown stage or role still renders a usable block (empty body
    with a one-line note) so the caller does not need to special-case.
    """

    stage_norm = _normalize_stage(stage)
    items = STAGE_CHECKLISTS.get(stage_norm, ())
    role_norm = (role or "engineer").strip().lower()
    if not items:
        return (
            f"## Stage checklist ({stage_norm or 'unknown'})\n"
            "No checklist is defined for this stage. Treat the stage's normal "
            "artifacts (research/, experiments/, paper/) as the source of truth."
        )

    if role_norm == "reviewer":
        framing = (
            "You are the L2 reviewer. Verify each item by reading the cited "
            "evidence. Reply `continue` if any item is unmet; reply `done` only "
            "when every item is satisfied. Do not run any `validate-*` shell "
            "command — there isn't one. Read the artifacts yourself."
        )
    elif role_norm in ("critic", "planner"):
        framing = (
            "Use this checklist to judge whether another engineer round on this "
            "stage adds real value. The reviewer will rule against this list, so "
            "additional polish that does not move an unchecked item to checked "
            "is wasted budget."
        )
    else:
        framing = (
            "The L2 reviewer will tick these items against your artifacts. "
            "Produce the evidence each item names; do not look for a "
            "`validate-*` CLI — the agent surface has none. The reviewer "
            "reads files directly."
        )

    return (
        f"## Stage checklist ({stage_norm})\n"
        f"{framing}\n\n"
        f"{_render_items(items)}"
    )


def format_full_pipeline_checklist(*, role: str = "reviewer") -> str:
    """Render every stage's checklist concatenated, for final submission review."""

    role_norm = (role or "reviewer").strip().lower()
    if role_norm == "reviewer":
        header = (
            "## Full pipeline checklist (final submission gate)\n"
            "Verify every stage's items end-to-end. Reply `done` only when every "
            "item across every stage is satisfied. There is no `validate-*` "
            "command to run — read the artifacts directly."
        )
    else:
        header = (
            "## Full pipeline checklist (final submission gate)\n"
            "Every item below must be true before the project can be marked done."
        )

    blocks = [header]
    for stage in CANONICAL_STAGE_ORDER:
        items = STAGE_CHECKLISTS.get(stage, ())
        if not items:
            continue
        blocks.append(f"### {stage}\n{_render_items(items)}")
    return "\n\n".join(blocks)
