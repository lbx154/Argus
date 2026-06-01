"""Stage-aware checklists that the L2 reviewer verifies for each project.

This module replaces the historical CLI-based ``validate-*`` toolbelt that
was injected into engineer / reviewer / critic prompts. Validators turned
into gates the agent kept trying to defeat; checklists are written for a
human-level reviewer to read, ground in artifacts, and rule on.

The legacy validator functions under :mod:`argus_skill.skills.pipeline_contracts`
remain importable for other call sites (tooling, tests), but the
:mod:`argus_skill.life.supervisor` harness no longer uses them for
project-done detection. Whole-project completion is now decided by the L2
reviewer's full-pipeline checklist verdict (a certified ``final_submission``
review), not by any hardcoded validator gate. The only thing the agent sees
is the markdown checklist for the current stage.
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
                "inference-infra shortlist is recorded. Each shortlisted "
                "framework must be (a) actively maintained (last release or "
                "default-branch commit in 2026 or later), (b) a real, "
                "non-trivial open-source repository — not a snippet, gist, "
                "or 'starter template'; framework selection is a *find*, "
                "not a *write*, exercise, and (c) verified by actually "
                "cloning the repo under `code/references/<repo>/` and "
                "reading its `README` / `docs/` / example scripts so the "
                "shortlist rationale reflects how the framework is meant "
                "to be used. **Critically, the README must be scanned for "
                "supersession / migration hints** — phrases like \"now "
                "supported by X\", \"moved to X\", \"superseded by X\", "
                "\"recommended\", \"upstreamed into X\", \"deprecated, use X\", "
                "\"this repo is archived\", or a top-of-readme note pointing "
                "at a successor project. If such a hint exists, the "
                "shortlist row must (i) add the named successor as its "
                "own candidate, (ii) compare the two in the rationale, "
                "and (iii) usually pick the successor unless there is a "
                "concrete reason to stay on the older repo (e.g. the "
                "successor does not yet support the specific algorithm "
                "this project needs). The shortlist must anchor against "
                "the bundled `argus_builtin_skills/engineer/training-"
                "infrastructure-guide.md`, add at least one candidate the "
                "agent independently discovered, and note any guide entry "
                "that turned out stale and must be excluded."
            ),
            evidence_hint=(
                "research/INFRA_SHORTLIST.md (cites URL + last-commit-date + "
                "paper if any + a 1-line note on whether the README points "
                "at a successor) plus the actual cloned repos under "
                "`code/references/`"
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
                "open-source, **actually cloned and readme-studied**, and "
                "explicitly NOT a self-written stub or starter template. Cite "
                "the chosen project's repo URL, last release/commit date, and "
                "(if from a paper) the paper. Record both the final choice and "
                "any explicitly-rejected alternative with a one-line reason."
            ),
            evidence_hint=(
                "research/INFRA_CHOICE.md + research/EXPERIMENT_PLAN.md "
                "`## Infra` section + the actually-cloned framework repo under "
                "`code/references/<chosen-framework>/`"
            ),
        ),
    ),
    "benchmark": _checklist(
        ChecklistItem(
            id="benchmark.environment_preflight",
            statement=(
                "Before the first real scoring call, the engineer ran the "
                "Environment Readiness Gate (`argus_builtin_skills/engineer/"
                "environment-readiness-gate.md`) and captured the verbatim "
                "output to `experiments/runs/<run_id>/preflight.txt`. The "
                "preflight must show: project `.venv` active (NOT the Argus "
                "framework venv), `CUDA_VISIBLE_DEVICES` matches the vault, "
                "every chosen-framework `import` succeeds, `torch.cuda.is_"
                "available()` is True, HF/Torch cache env vars point under "
                "`<project>/models/`, the base model weights are on disk, "
                "and any reward/scoring API route returns a non-empty test "
                "response. No preflight evidence ⇒ this item fails ⇒ no "
                "downstream benchmark item can be ticked."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt",
        ),
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
        ChecklistItem(
            id="benchmark.evaluator_authentic",
            statement=(
                "The benchmark evaluator is the *real* scoring backend for "
                "each family, not a stub. The reviewer must inspect the "
                "evaluator source code and confirm it (a) actually loads or "
                "downloads the generator output, (b) calls the official scoring "
                "model / API / metric backend (e.g. GenEval's CLIP + detector "
                "stack, TIFA's QA model, T2I-CompBench++'s official backend), "
                "and (c) does NOT short-circuit by returning a constant "
                "(`return 1.0`, `gold_oracle_exact_match`, `smoke_oracle`, or "
                "any other label that means 'we pretended'). If the evaluator "
                "is currently a stub, this item fails and the next mission "
                "must wire in the real scorer before any pilot/full run."
            ),
            evidence_hint="code/**/*.py — read every `evaluate_*` / `_evaluate_*` body",
        ),
    ),
    "run": _checklist(
        ChecklistItem(
            id="run.environment_preflight",
            statement=(
                "Every pilot / full / ablation launch is preceded by a fresh "
                "Environment Readiness Gate run (`argus_builtin_skills/"
                "engineer/environment-readiness-gate.md`). The verbatim "
                "preflight output is captured to `experiments/runs/<run_id>/"
                "preflight.txt` for THAT run_id — not reused from an earlier "
                "run. Required signals: project `.venv` active, "
                "`CUDA_VISIBLE_DEVICES` matches the vault, framework imports "
                "succeed, `torch.cuda.is_available()` True, HF/Torch caches "
                "rooted under `<project>/models/`, base model weights present, "
                "API routes test-called. A run launched without a fresh "
                "preflight is treated as wasted budget and rolled back."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt (per run)",
        ),
        ChecklistItem(
            id="run.model_instruct_not_base",
            statement=(
                "Every method/baseline that must follow prompts, format answers, "
                "or do reasoning RL/eval runs on an INSTRUCTION-TUNED checkpoint "
                "(`-Instruct`/`-Chat`/`-IT`), not the same-size base/pretrained "
                "model — e.g. `Qwen3.5-9B-Instruct`, not `Qwen3.5-9B-base`. A base "
                "checkpoint has no instruction-following prior, so near-chance or "
                "format-collapsed outputs read as a dead method when the real "
                "cause is the wrong model. The manifest's declared model id and "
                "the preflight weights path must name the instruct variant. A base "
                "model is acceptable only when the experiment is explicitly about "
                "base-model behaviour and says so; otherwise fail this item."
            ),
            evidence_hint="experiments/<run>/manifest.json model id ends in an instruct/chat variant",
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
        ChecklistItem(
            id="run.score_variance",
            statement=(
                "Scored rows reflect a real scoring distribution. The reviewer "
                "must spot-check at least one `scored_rows.jsonl` per benchmark "
                "family and confirm that the `score` column varies across rows. "
                "A file with >3 rows whose scores are all identical (e.g. all "
                "1.0 or all 0.0) is treated as stub evidence — fail this item "
                "and require the engineer to wire in the real scorer (see "
                "`benchmark.evaluator_authentic`) before declaring the run "
                "stage done."
            ),
            evidence_hint=(
                "`jq -r .score experiments/runs/<id>/results/<family>/scored_rows.jsonl"
                " | sort -u | wc -l` should be > 1 per file with >3 rows"
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
        ChecklistItem(
            id="review.language",
            statement=(
                "Academic prose reads like a real EMNLP paper, not generic agent "
                "output: the Abstract states problem, gap, method, evidence, and "
                "implication (no result-first opening, no validator-checklist "
                "phrasing); the Introduction grounds the gap in cited prior work, "
                "then gives the method insight, a quantified result preview, and a "
                "contribution roadmap before Related Work; the Method/Setup lets an "
                "outside reviewer identify the evaluated system, baselines, task "
                "source, metrics, evaluated model/backend, and budget; every "
                "headline claim is tied to reported evidence; no unsupported hype, "
                "template LLM openings, or repeated not-X-but-Y caveats. The "
                "model-backed reviewer (academic_language_review) is advisory "
                "input — this checklist, judged by the reviewer agent, is the "
                "source of truth."
            ),
            evidence_hint="paper/main.tex Abstract/Introduction/Method + paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)",
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


def rollback_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    rolled_back_by: str = "reviewer",
) -> str:
    """Move the pipeline state machine **backward** to an earlier stage.

    Use this when reviewing stage N exposes a missing or unreliable
    upstream artifact (e.g. while in ``run`` the reviewer notices that
    the ``benchmark`` evaluator is a stub, or while in ``draft`` the
    reviewer notices that ``research/INFRA_CHOICE.md`` was never
    locked in). The next round will get the earlier stage's checklist
    and the agent will repair the upstream defect before being allowed
    to advance again.

    Behavior:
    - ``current_stage`` is set to ``target_stage``.
    - Every stage strictly between ``target_stage`` (exclusive) and the
      previous ``current_stage`` (inclusive) is downgraded from
      ``done``/``ready`` to ``pending`` so the planner does not skip
      back over them on the way up.
    - A ``rollback_history`` array is appended with the timestamp,
      previous stage, target stage, reason, and ``rolled_back_by`` so
      the journal carries an audit trail.

    Returns the rendered JSON file path written. Raises ``ValueError``
    if ``target_stage`` is unknown or not strictly earlier than the
    current stage.
    """

    import datetime as _dt

    root = Path(project_root)
    state_path = root / "research" / "PIPELINE_STATE.json"
    target = _normalize_stage(target_stage)
    if target not in STAGE_CHECKLISTS:
        raise ValueError(f"unknown stage {target_stage!r}")

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    previous = _normalize_stage(payload.get("current_stage") or "research")
    if previous not in STAGE_CHECKLISTS:
        previous = "research"
    if CANONICAL_STAGE_ORDER.index(target) >= CANONICAL_STAGE_ORDER.index(previous):
        raise ValueError(
            f"rollback target {target!r} must be strictly earlier than current "
            f"stage {previous!r}"
        )

    payload["current_stage"] = target

    stages = payload.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        payload["stages"] = stages

    # Downgrade every stage strictly after the rollback target back to
    # `pending`. Leave the target stage itself at whatever status it
    # currently has (the engineer may want to mark it `pending` again
    # via the next round, but we don't force that here).
    target_index = CANONICAL_STAGE_ORDER.index(target)
    for stage_name in CANONICAL_STAGE_ORDER[target_index + 1:]:
        stage_record = stages.get(stage_name)
        if not isinstance(stage_record, dict):
            stage_record = {}
            stages[stage_name] = stage_record
        status = str(stage_record.get("status") or "").lower()
        if status in {"done", "ready", "in_progress"}:
            stage_record["status"] = "pending"

    history = payload.get("rollback_history")
    if not isinstance(history, list):
        history = []
        payload["rollback_history"] = history
    history.append({
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "from_stage": previous,
        "to_stage": target,
        "reason": reason,
        "rolled_back_by": rolled_back_by,
    })

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(state_path)


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
