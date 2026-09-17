"""Research-owned role prompts and explicit stage context loading."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .library_preparation import STAGE_PLAYBOOK_PATHS
from .notes import RESEARCH_NOTES_FILENAME, read_research_notes

_NOTES_STAGES = frozenset({"idea", "experiment", "paper"})
_CONTEXT_CHAR_LIMIT = 32_000

# Stages whose work actually touches compute: sizing an idea, then building
# and running experiments. Paper/review prose does not need it.
_COMPUTE_STAGES = frozenset({"idea", "experiment"})
_HARDWARE_CACHE_SECONDS = 60.0
# One nvidia-smi reading feeds both the static inventory and the live usage
# lines, so the two never describe different moments.
_hardware_cache: tuple[float, list[tuple[str, str, float, float]]] | None = None
# Local checkpoint inventory: scanning a few cache directories is cheap, but
# not so cheap that every prompt render should redo it.
# Prompt budget: the largest checkpoints are the ones a claim about scale
# needs; beyond this many the list stops informing and starts crowding.


def _query_local_gpus() -> list[tuple[str, str, float, float]]:
    """``(index, name, total_gb, used_gb)`` per GPU, cached briefly.

    Fail-soft to an empty list on machines without GPUs or ``nvidia-smi``.
    """
    global _hardware_cache
    now = time.monotonic()
    if _hardware_cache is not None and now - _hardware_cache[0] < _HARDWARE_CACHE_SECONDS:
        return _hardware_cache[1]
    rows: list[tuple[str, str, float, float]] = []
    if shutil.which("nvidia-smi") is not None:
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            proc = None
        for row in (proc.stdout if proc is not None else "").strip().splitlines():
            parts = [part.strip() for part in row.split(",")]
            if len(parts) != 4:
                continue
            index, name, total_mib, used_mib = parts
            try:
                total_gb = int(total_mib) / 1024
                used_gb = int(used_mib) / 1024
            except ValueError:
                continue
            rows.append((index, name, total_gb, used_gb))
    _hardware_cache = (now, rows)
    return rows


def local_hardware_block() -> str:
    """Describe the compute this machine actually has, so ideas and
    experiments are sized to it.

    Purely informational — it never blocks anything. Only the inventory is
    listed here — device names, total memory, CPU count — because this block
    sits in the cacheable prompt prefix; the memory free and in use right now
    is rendered by ``local_hardware_usage_block`` for the per-turn tail.
    Fail-soft to an empty string on machines without GPUs or ``nvidia-smi``.
    """
    gpus = _query_local_gpus()
    if not gpus:
        return ""
    cpu_count = os.cpu_count() or 0
    cpu_line = f"- {cpu_count} CPU cores" if cpu_count else ""
    gpu_lines = [
        f"- GPU {index}: {name}, {total_gb:.0f} GB memory"
        for index, name, total_gb, _used_gb in gpus
    ]
    return (
        "## Compute available on this machine\n"
        + "\n".join(line for line in (*gpu_lines, cpu_line) if line)
        + "\n\n"
        "Experiments run locally on this hardware. Size the work to it rather "
        "than assuming a small machine: real training and evaluation runs on "
        "these GPUs are expected, several GPUs can be used at once when a run "
        "benefits, and batch sizes, model scale, and evaluation sets should "
        "use the memory that is actually free. Prefer the GPUs with the most "
        "free memory and leave others' running jobs undisturbed. The inventory "
        "uses physical GPU indices; a configuration's cuda:0 names the first "
        "device visible to that process. Keep any assigned CUDA_VISIBLE_DEVICES "
        "mask. When selecting an authorized free physical GPU yourself, include "
        "the corresponding mask in the command you actually launch and use its "
        "logical device index in the configuration. A mask written only in a "
        "reproduction example does not configure the running process. Confirm "
        "the actual mapping before a long run and retain it with the run command."
    )


def local_hardware_usage_block() -> str:
    """The numbers that change from turn to turn: memory free and in use per GPU.

    Rendered apart from the inventory so the inventory can sit in the
    cacheable prompt prefix while these lines ride in the per-turn tail.
    """
    gpus = _query_local_gpus()
    if not gpus:
        return ""
    return "Memory in use right now, by physical GPU index:\n" + "\n".join(
        f"- GPU {index}: {max(total_gb - used_gb, 0.0):.0f} GB free, "
        f"{used_gb:.0f} GB in use by running jobs"
        for index, _name, total_gb, used_gb in gpus
    )


def _hardware_block_for_stage(stage: str, project_root: Path | None = None) -> str:
    """The static compute inventory for a compute stage.

    The list of every model checkpoint cached on the host used to follow the
    GPU lines. It grew with the cache (five thousand characters of Qwen
    snapshots and dataset names on the trial host), was repeated in every
    Planner and Engineer call of the idea and experiment stages, and nothing
    in the pipeline read it back; the roles inspect the cache themselves when
    an experiment actually needs a checkpoint.
    """
    del project_root
    if stage not in _COMPUTE_STAGES:
        return ""
    return local_hardware_block()


def _hardware_usage_for_stage(stage: str) -> str:
    """The live GPU memory numbers for a compute stage."""
    if stage not in _COMPUTE_STAGES:
        return ""
    return local_hardware_usage_block()


def research_runtime_context(stage: str, project_root: Path | None = None) -> str:
    """Live resource facts for a Reviewer's delta, outside its policy hash."""
    return "\n\n".join(
        block
        for block in (
            _hardware_block_for_stage(stage, project_root),
            _hardware_usage_for_stage(stage),
        )
        if block
    )


def active_context_paths(stage: str) -> tuple[str, ...]:
    """Return the only normal cross-stage context path for ``stage``."""
    normalized = str(stage or "").strip().lower()
    if normalized in _NOTES_STAGES:
        return (RESEARCH_NOTES_FILENAME,)
    if normalized == "review":
        return ("paper/REVIEW.md",)
    return ()


def _stage_playbook_block(stage: str) -> str:
    playbook = STAGE_PLAYBOOK_PATHS.get(stage)
    if not playbook:
        return ""
    resolved = Path(__file__).resolve().parent / "skills" / playbook
    return (
        "## Authoritative stage playbook\n"
        f"Playbook: `{playbook}`. Open `{resolved}` before acting. It is "
        f"the single workflow playbook for `{stage}`. Other Skills are optional "
        "tools: they cannot redefine the stage, its completion bar, the research "
        "notes in RESEARCH_NOTES.md, or the files the project shows."
    )


def active_research_context(stage: str, project_root: Path | None) -> str:
    if project_root is None:
        return ""
    paths = active_context_paths(stage)
    if not paths:
        return ""
    relative = paths[0]
    if relative == RESEARCH_NOTES_FILENAME:
        text = read_research_notes(project_root)
    else:
        try:
            text = (Path(project_root) / relative).read_text(encoding="utf-8")
        except OSError:
            text = ""
    if not text.strip():
        return (
            "## Active research context\n"
            f"The only normal cross-stage context for `{stage}` is `{relative}`, "
            "and it is currently absent or empty. Do not substitute historical "
            "research files or search the project for an older version of it."
        )
    if len(text) > _CONTEXT_CHAR_LIMIT:
        text = text[:_CONTEXT_CHAR_LIMIT].rstrip() + "\n[context truncated]"
    return (
        "## Active research context\n"
        f"Loaded only from `{relative}`:\n\n{text.strip()}\n\n"
        "Treat this as the current upstream summary, not as permission to crawl "
        "historical files. Open an older file only if this document explicitly "
        "names it for a concrete dispute."
    )


def academic_paper_review_block() -> str:
    return (
        "## Integrated final paper review\n"
        "Act as the independent post-repair Reviewer required by the Review playbook. "
        "Judge the current complete paper rather than Engineer or Planner confidence. "
        "Follow direct claim-critical references to executed code, explicit "
        "configuration, raw rows, the real evaluator, positive controls, strong "
        "same-information baselines, citations, "
        "and primary sources. Use the host's current independent page-by-page and cold-read "
        "assessments when supplied; do not launch duplicate passes or repeat their whole-paper "
        "inspection. Resolve a concrete contradiction with a targeted check. When no current "
        "assessment is supplied, inspect every rendered page, figure, and table at publication "
        "size yourself; that inspection is the assessment, and the absence of host-side "
        "passes is never by itself a reason to withhold `done` or to wait for the host. "
        "Perform full conference peer review of contribution, novelty, methodology, "
        "experimental design, results, conclusions, scientific correctness and importance, rendered layout, visual "
        "quality, academic argument and language, and whether the paper follows the "
        "venue's rules. Read the latest paper/REVIEW.md to close resolved concerns; "
        "do not load the research notes or crawl other old reports or history. "
        "Write a natural review with evidence and constructive, actionable guidance; "
        "no fixed fields or review template is required. Engineer implements scientific "
        "repairs, including method changes and new experiments, directly inside this "
        "final Review and returns for re-review. Update your own paper/REVIEW.md with "
        "the current natural-language review; do not edit the manuscript, code, figures, "
        "or experiment evidence, change stage state, reopen selection, or move backward. "
        + paper_reviewer_standard()
        + " For each required "
        "narrative repair, identify its location, the concrete obstacle to understanding "
        "or inference, and the smallest repair goal. Calling prose report-like, "
        "unacademic, or less fluent is insufficient by itself. Close resolved findings; "
        "request another revision only for a remaining or newly introduced defect."
    )


def paper_writing_standard() -> str:
    """The one writing standard every paper-facing prompt shares.

    It deliberately fixes no quota. The selected venue's strong accepted papers
    are the reference, and the claim decides how long, how numerical, and how
    hedged each passage should be.
    """
    return (
        "The standard is a strong accepted paper at the selected venue, the kind the "
        "exemplar skill has you read; there is no house quota for sentences, words, "
        "numbers, or caption format. Let the claim decide the form. "
        "Apply 'Plan the manuscript length' in research-paper-playbook.md: "
        "for a full-length paper, target nearly all permitted body space under "
        "the exact track's official counting rules, not total PDF pages. "
        "Respect explicit short-paper and partial-edit requests. Actively develop "
        "principle-level analysis toward that target: mechanisms, assumptions, "
        "derivations, and design tradeoffs. Experiments support the argument; do not write "
        "an experiment report. Record the target and actual body extent "
        "in existing research notes. The abstract is as "
        "long and as numerical as the venue's norm and the claim require: a large "
        "speedup is stated as a speedup, a narrow margin is stated with its "
        "uncertainty, and a mechanism finding may need no number at all. In prose, "
        "give a number the precision the comparison needs, usually two or three "
        "significant digits, and keep full precision in tables; a paragraph that has "
        "become a list of numbers has stopped arguing. A caption tells the reader what "
        "to see: a number when the number is the point, a pattern when the pattern is "
        "the point. Say plainly what the evidence establishes, state each limit once "
        "where it matters, and hedge a sentence only when the evidence for that "
        "sentence is uncertain. Think in evidence roles (headline, mechanism, control, "
        "scope, completeness) while deciding what goes where. Keep internal task "
        "routing, review status and process bookkeeping out of the scientific account. "
        "Preserve established scientific terminology in its correct domain sense, "
        "including `certified bounds`, `communication gates`, `communication rounds`, "
        "`numerical artifacts`, `mechanisms` and `controls`. Explain their scientific "
        "meaning when needed; do not rename legitimate terms to satisfy a word list. "
        "The paper argues the claim as stated in METHOD.md and is written only when "
        "that claim is supported: lead with the strongest supported result, no "
        "defensive writing, and limitations are one honest paragraph rather than the "
        "framing. A restricted-case or negative-result paper exists only after the "
        "operator has changed the claim; unfinished development is never a finding."
    )


def paper_reviewer_standard() -> str:
    """How the Reviewer applies the writing standard: as a venue reviewer, not a checker."""
    return (
        "Judge the writing as a reviewer at the selected venue would: would this be "
        "accepted, and what would a careful reader object to? Do not enforce an "
        "abstract length, sentence count, number density, or caption format; a longer "
        "or shorter abstract, more or fewer numbers, and a headline figure that recurs "
        "across sections are all fine when they serve the argument at that venue. "
        "Object when a claim outruns its evidence, when a reader cannot recover the "
        "central finding, when a number's meaning is unclear from its context, when "
        "prose recites a result matrix instead of arguing, when hedging or limitation "
        "lists stand in for a clear statement, or when internal task routing, review "
        "status or process bookkeeping replaces the scientific account. Judge terms "
        "by their scientific meaning, not a banned-word list; preserve legitimate "
        "scientific terminology such as `certified bounds`, `communication gates`, "
        "`communication rounds`, `numerical artifacts`, `mechanisms` and `controls`. "
        "Do not ask for more hedging than the evidence requires, and do not "
        "ask for a number where a plain statement is clearer. "
        "Apply the manuscript-length policy in research-paper-playbook.md: "
        "compare counted body extent with the full-paper writing target, "
        "not total PDF pages. Judge the depth of principle-level analysis and "
        "request expansion of terse mechanisms, derivations, and design tradeoffs. "
        "Experiments should support the argument, "
        "not turn it into an experiment report. Put actionable expansion requests "
        "in the existing paper/REVIEW.md, "
        "respecting explicit short-paper and partial-edit requests."
    )


def _paper_narrative_packaging_block() -> str:
    return (
        "## Paper writing standard\n"
        + paper_writing_standard()
        + " Keep the complete scientific evidence: complete definitions and matrices "
        "live in Methods, tables, or the Appendix, and prose selects the comparisons "
        "that change the current inference and explains why. A headline number may "
        "recur in the abstract, introduction, results, caption, and conclusion when it "
        "does each location's job; do not copy a flat method-by-dataset-by-metric "
        "recital across sections. Translate any workflow or evidence-bookkeeping "
        "language into the scientific question, the result, the "
        "alternative explanation resolved, and the resulting inference. The "
        "manuscript's Method section and claims follow METHOD.md; every deviation "
        "is named there first, and the experimental-setup section reproduces the "
        "reused code and hyperparameters the host derives next to the card, and "
        "states the evaluation substrate exactly: the model and environment that "
        "ran, or that the evaluation is simulated; a simulation is never described "
        "as a benchmark."
    )


def _method_card_engineer_block(stage: str, operation: str) -> str:
    """The method card, the pinned reference and the spec suite come first.

    One project shipped a simplified method that the Engineer had defined in
    passing, tested with outcome-shaped tests, and had certified from its own
    account. The card fixes the statement, the reference fixes the baseline,
    and the host-run spec gives the Reviewer evidence nobody wrote for it.
    """
    scientific_revision = stage == "review" and operation != "narrative_edit"
    if stage != "experiment" and not scientific_revision:
        return ""
    return (
        "## Method card and executable spec\n"
        "First round: write METHOD.md once from the selected route (path under "
        "'Evidence considered' in RESEARCH_NOTES.md; quote it) per "
        "engineer/method-card.md: statement, Components (Component | The idea "
        "prescribes | Notes), Protocol, falsifier. Clone the official or strongest "
        "public implementation at a pinned revision into third_party/ and extend it: "
        "the code diff is the idea diff (engineer/delta-on-reference.md). Before "
        "any claim-bearing run write tests/spec (engineer/executable-spec.md): oracle "
        "(one function per equation), differential tests, one knockout per "
        "component, claim-shaped tests, each tagged "
        "@pytest.mark.component('<name as in the card>'). Put '# @component <name>' "
        "above each component entry point, '# @simplified' and '# @reuses' likewise "
        "(engineer/write-for-review.md); the host builds the Reviewer's packet from "
        "these anchors. The host runs tests/spec after every round "
        "and derives status, reused code, hyperparameters and history from code, "
        "markers, configs and git: maintain no tables by hand; write '# why: ...' "
        "beside each chosen config value; edit METHOD.md only when the method changes. "
        "Stand-ins (mock model, fake environment, oracle policy, synthetic data where "
        "the route names real data) belong in tests/spec only: a claim-bearing run "
        "exercises the real system the route names; if it cannot run here, name the "
        "deviation in METHOD.md and say so, never report a simulation as the benchmark. "
        "When a round produces a claim-bearing number, write .argus/claim_attainment.json: "
        "one entry per clause of the claim with clause, obtained, met (yes/no/partial/"
        "untested) and source {path, field} of the number; the host reads that field and "
        "shows the value beside your words to the Reviewer and the Planner. A clause you "
        "cannot meet is a negative result to state, never to reword."
    )


def _method_card_reviewer_block(stage: str) -> str:
    if stage not in {"experiment", "review"}:
        return ""
    return (
        "## Method card first\n"
        "Start from Claim attainment (the Engineer's per-clause statement with the values "
        "the host read from the files it points to) and the host log of what the Engineer "
        "ran this round; choose the one link most likely not to hold the claim and read "
        "only there. A clause marked not met, partial or untested is a negative result to "
        "iterate on, never a claim to narrow; a stated value the host resolves differently, "
        "a headline number from a run shorter than its protocol allows, or a metric named "
        "differently from the protocol's is where you open the script. Then the review packet "
        "(anchors with code excerpts, test outcomes, config changes, files changed, Run "
        "reality), METHOD.md, the derived method-card status in Raw verification evidence "
        "(proven, contradicted, partial, untested, unchecked; reused code; hyperparameter "
        "changes), tests/spec, code, and the Engineer's account last. Per component report MATCHES, CONTRADICTS, NOT_IMPLEMENTED or "
        "INSUFFICIENT_EVIDENCE with file:line (reviewer/claim-to-code-trace.md). "
        "Required repairs, returned as continue naming the smallest fix: a component "
        "without a '# @component' anchor or a knockout that fails in its absence, an "
        "untested or contradicted component, a failing or unexplained-skip test, tests "
        "collected last round but missing now, a hyperparameter change without a "
        "'# why' or a card note, code that contradicts the card. The claim is fixed; "
        "never accept claim drift: a result that narrows the claim, or a negative "
        "result with fewer than three diagnosed attempts, is a repair request. A "
        "result produced through a stand-in listed under Run reality is "
        "NOT_IMPLEMENTED whatever the tests say, unless METHOD.md Deviations names it "
        "and the paper calls the evaluation simulated. Run reality also dates each "
        "result file against the last code edit and names functions fed random "
        "tensors: a one-minute run or random keys is not the protocol's evaluation, "
        "whatever the results file lists. Settled evidence stays settled: do not re-read "
        "what the packet already shows; ask at most two questions, each answerable by an "
        "artifact. Do not ask for tools or re-run anything yourself."
    )


def _method_card_planner_block(stage: str) -> str:
    if stage != "experiment":
        return ""
    return (
        "## Method card, reference and spec first\n"
        "The first Experiment task is the method card (METHOD.md), the pinned "
        "reference clone under third_party/ and the tests/spec suite, by the same "
        "Engineer who implements; never a separate 'define the method' task. Every "
        "implementation TASK_OBJECTIVE follows engineer/implementation-brief.md "
        "(claim verbatim, components from METHOD.md with file:Symbol entry points, "
        "interfaces, tests/spec ids, data and scale, commands, environment, "
        "definition of done, out of scope); acceptance is executable checks, not "
        "adjectives; one task is one brief. Claim-bearing tasks copy the route's "
        "protocol (datasets, baselines, seeds, scale) verbatim into acceptance. When a "
        "task is re-issued after an infrastructure failure or a provider failure its "
        "brief and acceptance stay verbatim: repair the infrastructure, do not lower "
        "the bar. When the route hosts a model or an environment, the claim-bearing task "
        "depends on a stand-up task whose acceptance is the official example running "
        "end to end here (engineer/framework-stand-up-pilot.md); a benchmark run "
        "through a stand-in is not a result. Read Claim attainment before deciding the "
        "stage: a clause not met, partial or untested keeps Experiment open for another "
        "iteration on the implementation; advancing to Paper on the clauses that happened "
        "to pass is claim drift, whatever the margin over a baseline."
    )


def _planner_fragment(stage: str, project_root: Path | None) -> str:
    # The research notes and the GPU memory in use change between cycles;
    # ``render_role_prompt_context`` carries them, after this static policy.
    return "\n\n".join(
        block
        for block in (
            _stage_playbook_block(stage),
            _hardware_block_for_stage(stage, project_root),
            _method_card_planner_block(stage),
            _attainment_block_for_planner(stage, project_root),
            (
                "## Post-result experiment scale assessment\n"
                "After Reviewer accepts the current experiment, apply the "
                "'Planner scale assessment after a passing experiment' section of "
                "research-experiment-playbook.md before leaving Experiment. "
                "Assess actual training and evaluation coverage against the operator "
                "objective, not just the run's acceptance checks. If insufficient, "
                "keep the stage and assign an incremental scale-up, preferring "
                "existing benchmarks and reusing valid code, configurations, and results. "
                "Apply the playbook's cost-aware benchmark policy: small custom "
                "benchmarks are allowed with no API calls or only a small amount "
                "within the authorized budget; reassess total cost when expanding. "
                "Respect stricter project-specific restrictions. "
                "If sufficient, explain why in the existing plan and REASON, "
                "then return ADVANCE_TO_STAGE=paper with the paper task. "
                "Do not schedule a separate inspection mission or repeat an "
                "unchanged assessment; perform this judgment in your normal "
                "planning turn."
                if stage == "experiment"
                else ""
            ),
            (
                "## Method figure through PPT Master\n"
                "The task that draws the method or architecture figure has executable "
                "acceptance: `paper/figures/<name>.pptx` (native PPT Master source), "
                "`<name>.pdf` and `<name>.png` written from it by `" + _PPTX_EXPORT_CLI + "`, "
                "the PDF included by the manuscript, and `" + _FIGURE_LINT_CLI + "` reporting "
                "no method-figure defect. A matplotlib or TeX-compiled diagram does not satisfy it; "
                "re-issue the task, do not accept the substitute."
                if stage in {"paper", "review"}
                else ""
            ),
            (
                "## Planner responsibility\n"
                f"Plan only the highest-value unresolved work in `{stage or '(unknown)'}` "
                "under the stage playbook. Keep repairs in the current stage, avoid "
                "ceremonial tasks, and leave stage transitions to Manager. The claim in "
                "METHOD.md is fixed: the Planner never rewrites the claim and never "
                "schedules a negative-result or restricted-case paper. After a failed "
                "comparison schedule the next undiagnosed rung of the playbook's "
                "diagnosis ladder with its evidence, not another variant of the same "
                "objective under a new title; after three diagnosed attempts raise the "
                "operator question with the evidence instead of narrowing the claim. "
                "In `paper`, schedule "
                "writing; a run belongs there only for a specific evidence gap the "
                "manuscript exposed. Retire superseded pending tasks with RETIRE_TASK."
            ),
        )
        if block
    )


def _narrative_editor_block() -> str:
    return (
        "## Fresh-context Narrative Editor\n"
        "Keep the current manuscript as the starting point. Inspect it and the latest "
        "actionable Reviewer findings supplied for this round; edit only a located "
        "problem that impairs reader understanding or the argument. Use the current "
        "paper, the evidence roles in the research notes (`RESEARCH_NOTES.md`), the venue drafting skill, and "
        "`engineer/references/paper-writing-craft.md` for how the repair should read. Do not "
        "search review history or internal diagnostic reports, or copy reviewer-response "
        "wording into the manuscript. Preserve clear content, structure, and wording. "
        "Prefer adding a missing explanation or adjusting local sentence order; explain "
        "why a local repair is insufficient before reorganizing a section or the paper. "
        "If no concrete problem needs repair, report that no manuscript change is needed "
        "and return to Reviewer without editing. Preserve every number, comparison "
        "direction, claim scope, adverse result, material uncertainty, decisive control, "
        "and the complete method/result coverage. Within the affected passage, clarify "
        "what the evidence establishes using only supported inferences; keep other "
        "evidence in its existing carrier. "
        "You may propose moving unique content in your closing note, but you may not "
        "unilaterally remove it or change its scientific meaning. Keep the abstract's "
        "claims and evidence; its length and shape follow the venue and the claim, not a "
        "quota. Compile when "
        "manuscript inputs changed or the rendered PDF is missing or stale; reuse a "
        "current PDF when no input changed."
    )


def _engineer_notes_stage(stage: str, operation: str) -> str:
    """The stage whose research notes the Engineer reads for ``operation``."""
    return "paper" if operation == "narrative_edit" else stage


def _engineer_compute_stage(stage: str, operation: str) -> str:
    """The stage whose compute inventory the Engineer sees for ``operation``."""
    scientific_revision = stage == "review" and operation != "narrative_edit"
    return "experiment" if scientific_revision else stage


_FIGURE_LINT_CLI = "python -m argus.verticals.research.figure_lint"
# The export step of Method D/B: PPT Master reads the PPTX, the browser renders
# the slide. No PowerPoint or LibreOffice on the machine is needed, so no
# Engineer has a reason to compile a TikZ look-alike beside a companion PPTX.
_PPTX_EXPORT_CLI = "figure_spec_scripts/pptx_export.py --pptx paper/figures/<name>.pptx"


def _engineer_figure_block(stage: str, operation: str) -> str:
    if stage != "paper" or operation == "narrative_edit":
        return ""
    return (
        "## Data figures\n"
        "Draw data figures through the shared paper_chart_style helper (vector PDF, "
        "TrueType fonts, colorblind palette, ours highlighted, sized for the float), or "
        "through the ECharts route of the same skill (echarts_figure.py, browser-rendered "
        "vector); one route per paper. "
        "Show uncertainty wherever runs were repeated, keep legends clear of titles "
        "and data at final size, and never substitute a sentinel value for zero or a "
        "missing point on a log axis. The method figure is composed only through "
        "PPT Master (Method D; Method B fallback): author `paper/figures/<name>.pptx`, "
        "then `" + _PPTX_EXPORT_CLI + "` writes `<name>.pdf` and `<name>.png` from it "
        "(no Office needed); matplotlib patches and TeX-compiled drawings are not a route for it. "
        "`" + _FIGURE_LINT_CLI + "` reports font, raster, missing-file and method-figure "
        "defects; fix them before inspecting the export at final size."
    )


def _research_learning_block(role: str, stage: str, operation: str) -> str:
    """Surveys and setups are vertical knowledge, not chat answers.

    The generic durable-learning contract keeps recommendations out of Skills
    unless a controlled comparison verified them, which is exactly what drops
    an infrastructure survey on the floor. In research the survey with its
    sources, versions and working commands is the reusable asset.
    """
    if role == "engineer":
        if operation == "narrative_edit" or stage not in {"idea", "experiment", "paper"}:
            return ""
        return (
            "## Durable research learning\n"
            "Surveys are learning too. When this round chooses training or inference "
            "infrastructure, write one project Engineer Skill per task class in the "
            "Durable learning directory, named "
            "`engineer/<task-class>-infrastructure-decision.md`, whose description "
            "begins `Surveyed <date>: <task class> on <hardware>; re-verify after "
            "<date> or when task class or hardware changes`. Body: `## Current` "
            "holding, in order, question and task-class card; survey date and "
            "hardware; sources (URL, access date, cached path); candidates including "
            "rejected; stand-up and profile; recipe and tuning rows; decision; what "
            "would change it; valid while; working commands; pitfalls verified by a "
            "run; then `## History` (one line per refresh). On refresh replace "
            "Current, append to History, keep the file under 32 KB. Read "
            "`training-infrastructure-guide.md` and the existing record first instead "
            "of duplicating. A dated record is durable once its sources and commands "
            "are recorded. Argus promotes reviewed project Skills into the shared "
            "research layer after the mission, so later projects start from this "
            "record instead of repeating it. Project-specific facts go to the Wiki "
            "when one is listed."
        )
    if role == "manager":
        return (
            "## Durable research learning\n"
            "When answering the operator needed a survey of infrastructure, "
            "frameworks, benchmarks, datasets or tools, retain that survey as a "
            "Manager Skill in the project skill directory named in the "
            "self-evolution section (question, candidates with sources and versions, "
            "decision and evidence), not only as a chat answer; later research "
            "missions read the shared layer it is promoted to."
        )
    return ""


def _reviewer_figure_block(stage: str, scope: str) -> str:
    if stage in {"paper", "review"} or scope == "final_submission":
        return (
            "## Figures at final size\n"
            "Inspect each data figure for uncertainty wherever runs were repeated, "
            "legends clear of titles and data, honest axes without sentinel "
            "substitutions, and a method figure that shows the mechanism rather than "
            "formula boxes; open `paper/figures/<name>.png`, the exporter's render at "
            "manuscript width, and read it as a reader would: bullet lists in three boxes "
            "are not a mechanism. `" + _FIGURE_LINT_CLI + "` lists font, raster, "
            "missing-file and method-figure defects to require as repairs. A method "
            "or architecture figure exported by matplotlib or TeX, or without a native PPT "
            "source of the same stem under paper/, is a required repair (return "
            "continue), not a limitation to note."
        )
    return ""


def _engineer_fragment(
    stage: str,
    project_root: Path | None,
    operation: str,
) -> str:
    narrative_edit = operation == "narrative_edit"
    scientific_revision = stage == "review" and not narrative_edit
    # The research notes supply evidence roles; current repair feedback arrives through
    # the normal round context. Do not preload REVIEW.md or historical reports.
    # The notes change between rounds, so ``render_role_prompt_context``
    # carries them after this static policy.
    handoff = (
        "Finish the method card, the reference clone and the spec suite before "
        "reporting the first round; afterwards, once this round's coherent "
        "scientific changes and directly coupled repairs are validated, save the "
        "current checkpoint and summarize the changes and their evidence. "
        if stage == "experiment"
        else "Once this "
        "round's coherent scientific changes and directly coupled repairs are validated, "
        "save the current checkpoint "
        "and summarize the changes and their evidence; you need not finish the whole paper "
        "before being reviewed. "
    )
    stage_policy = (
        "## Engineer responsibility\n"
        "Execute the current playbook directly. Use code, explicit configuration, raw "
        "outputs, figures, bibliography, manuscript source, and rendered output as work "
        "products. Do not create substitute summaries or process reports, and do not "
        "change stage state. The host runs independent preliminary paper reviews after your "
        "turn; do not duplicate those full-paper scientific, visual, or cold-read passes. "
        "The host also invokes the formal integrated Reviewer after you return. "
        + handoff
        + "Complete the relevant experiment and its coupled code, "
        "entry-point, analysis and presentation repairs together; do not trigger a "
        "whole-paper review after each small edit or preliminary test. "
        "Do not invoke or delegate an integrated/full-paper Reviewer inside the Engineer "
        "call, write the main paper/REVIEW.md, or give a delegate permission to write it. "
        "A bounded figure-design subtask may run in parallel in an isolated candidate "
        "workspace while you advance the science; only the lead integrates final figures "
        "and only the paper Reviewer owns the main paper/REVIEW.md."
    )
    narrative_packaging = (
        _paper_narrative_packaging_block()
        if stage == "paper" or narrative_edit
        else ""
    )
    return "\n\n".join(
        block
        for block in (
            _stage_playbook_block(stage),
            _hardware_block_for_stage(
                _engineer_compute_stage(stage, operation), project_root
            ),
            _engineer_figure_block(stage, operation),
            _method_card_engineer_block(stage, operation),
            _research_learning_block("engineer", stage, operation),
            narrative_packaging,
            (
                "## On-demand method figure\n"
                "Spend most of the effort on scientific contribution, methods and evidence. "
                "Aim for three informative figures and include at least two: a mechanism "
                "overview and the central result, with an ablation or diagnostic when useful. "
                "Reuse sound existing figures instead of adding decorative filler. "
                "Only when a method or architecture figure needs drawing, open "
                "engineer/paper-framework-figure-studio.md. Method D is the default: "
                "reference figures, an actual image design blueprint, and native editable "
                "PPT Master reconstruction. Method B is the fallback when D is unavailable "
                "or the task constraints rule it out: compose directly in native editable PPT. "
                "Both routes use PPT Master; an unavailable image API must not pause the task. "
                "Matplotlib patches, boxes and arrows are not a route for this figure: the "
                "lint reports such an export and the Reviewer returns it. Author the native "
                "`paper/figures/<name>.pptx`; `" + _PPTX_EXPORT_CLI + "` exports `<name>.pdf` "
                "and `<name>.png` from it, and the PDF's producer shows which route made it. "
                "ECharts can supply a data-grounded chart component when useful. "
                "There is no separate SVG workflow. Keep the framework itself as native "
                "PPT shapes, connectors, and text. Locate PPT Master with "
                "python -m argus.tools.ppt_master status. "
                "Reuse an existing suitable figure; do not invoke the component every "
                "round or for prose-only edits. Delegate bounded candidate design in parallel "
                "when a worker is available, using separate candidate directories, while "
                "you continue scientific repairs. Choose a good candidate from one batch; "
                "a dozen variants are optional, never a quota. After checking scientific "
                "accuracy, readability and visual quality, freeze the selected composition. "
                "Reopen it only for changed methods or data, misleading content or actual "
                "readability defects; aesthetic preferences alone must not restart it. "
                "The current Engineer grounds the "
                "drawing in code and manuscript and inspects the rendered composition "
                "at publication size. An operator-rejected figure needs a fresh composition; "
                "a successful export or a palette change does not make it suitable for reuse. "
                "Inspect actual figures from relevant accepted papers, borrow their visual "
                "grammar, and show the mechanism with meaningful objects instead of a wall "
                "of formula-filled boxes. Include "
                "the vector PDF after the Introduction, targeting page 2 or 3 in the "
                "compiled paper, and keep the canonical editable drawing source. "
                "Use restrained academic typography and thin strokes; oversized "
                "headings and a wall of colored cards do not establish visual quality."
                if (stage == "paper" and not narrative_edit) or scientific_revision else ""
            ),
            _narrative_editor_block() if narrative_edit else "",
            (
                "## Scientific revision within final Review\n"
                "The final Reviewer judges the full paper as a conference submission. "
                "Directly implement its scientific suggestions here: repair methods or "
                "evaluators, add fair baselines and controls, run the decisive experiments, "
                "and revise the supported claims and manuscript. Take constructive suggestions "
                "seriously and first turn them into method improvements and evidence. "
                "Do not default to weaker claims or extra caveats as a substitute for feasible "
                "experiments, and never rewrite the claim to match the code; aim to "
                "strengthen the contribution. If a real test disproves a claim, keep that "
                "result, work the playbook's diagnosis ladder, and raise the operator "
                "question with the evidence rather than narrowing the claim. "
                "Read the latest paper/REVIEW.md and confirm which concerns are now resolved. "
                "When the method, oracle, evaluator or accounting changes, identify the "
                "affected claims and results before choosing the next runs. Re-establish "
                "the affected claim's full comparison scope with the current variant; "
                "a focal diagnostic does not validate older panels under a new contract. "
                "Reuse unaffected evidence. Match baseline implementation maturity, "
                "batching and precision as well as information and resources when they "
                "affect the comparison. Test the manuscript's actual public entry point "
                "with its current configuration, using a supported small execution first "
                "when the full experiment is expensive. "
                "Preserve raw and adverse "
                "results. Use the existing resource and experiment controls at the scale "
                "the claim requires. Keep the current paper and stage; do not roll back "
                "to Idea, Experiment, or Paper, or wait for an earlier-stage mission. "
                "Return the actual changes and validation for independent re-review."
                if scientific_revision else ""
            ),
            stage_policy,
        )
        if block
    )


def _reviewer_fragment(
    stage: str,
    scope: str,
    project_root: Path | None,
    operation: str,
) -> str:
    if operation == "cold_read":
        return (
            "## Rendered-PDF cold read\n"
            "Read only `paper/main.pdf` in the isolated working directory. Do not "
            "look for TeX, the research notes, REVIEW.md, code, evidence files, history, or "
            "internal diagnostics. Judge whether the PDF makes one central finding "
            "recoverable after the first page; whether sections advance rather than "
            "replay a flat matrix; whether headline, mechanism, control, scope, and "
            "completeness evidence have visible hierarchy; whether the scientific meaning "
            "of key comparisons is clear from the passage and necessary context; and "
            "whether figures and numerical captions "
            "answer a scientific question rather than resemble a dashboard. "
            + paper_reviewer_standard()
            + " Dense science and complete controls are not defects by themselves. "
            "Do not demand another explanation after "
            "each number when the context already supplies it. For each required repair, "
            "return a PDF location, a concrete obstacle to understanding or inference, "
            "and the smallest repair goal. A report-like tone or a preference for "
            "smoother wording alone is insufficient. Pass when no substantive "
            "reader-facing defect remains."
        )
    if operation == "science_loss_check":
        return (
            "## Scientific semantic-loss comparison\n"
            "Compare the immutable before/after manuscript snapshots named in the "
            "assignment. Judge scientific meaning and coverage, not sentence identity. "
            "Verify headline evidence, exact values and directions, claims and scope, "
            "complete methods/baselines/controls/result matrices, adverse or null "
            "findings, uncertainty, reproduction detail, the abstract's claims and "
            "evidence, and what each caption tells the reader. A move from prose to "
            "a clear table, Methods, Appendix, caption, or cross-reference is not loss. "
            "Any veto must name the exact lost reasoning step or its missing carrier. "
            "Do not edit either snapshot."
        )
    if stage == "review" or scope == "final_submission":
        policy = (
            academic_paper_review_block()
            + "\n\nFinal completion requires your explicit selected-venue "
            "recommendation at or above the operator's current completion standard, "
            "with no reject-level issues. A lower acceptance rating can recognize progress, "
            "but cannot finish a stronger requested goal. Borderline, "
            "reject, uncertainty, or missing evidence continues "
            "revision without a quality-round ceiling. Do not equate a repaired "
            "edit with a paper worthy of acceptance or inflate a rating to stop."
        )
    else:
        policy = (
            "## Reviewer responsibility\n"
            "Independently judge the current work against the stage playbook and direct "
            "evidence. Separate implementation defects from scientific evidence, name "
            "the smallest decisive repair, and do not change stage state."
        )
    return "\n\n".join(
        block
        # Live notes/REVIEW contents belong to the Reviewer's round delta, not
        # this static policy fragment used to decide whether a session resumes.
        for block in (
            _stage_playbook_block(stage),
            policy,
            _method_card_reviewer_block(stage),
            _reviewer_figure_block(stage, scope),
        )
        if block
    )


def _attainment_block_for_planner(stage: str, project_root: Path | None) -> str:
    """The Engineer's per-clause statement with host-read values, for the stage decision."""
    if project_root is None or stage not in {"experiment", "paper"}:
        return ""
    try:
        from .method_card import derive_method_card, render_claim_attainment

        lines = render_claim_attainment(derive_method_card(Path(project_root)))
    except Exception:  # noqa: BLE001 - derived context must never break a prompt
        return ""
    return "## Claim attainment\n" + "\n".join(lines) if lines else ""


def _derived_method_card_for_reviewer(stage: str, project_root: Path | None) -> str:
    """Host-derived component status, reused code and hyperparameter changes.

    Evidence for the Reviewer's reading order, not a gate; '' when the project
    has no METHOD.md or the derivation fails for any reason.
    """
    if project_root is None or stage not in {"experiment", "review"}:
        return ""
    try:
        from .method_card import render_for_reviewer

        return render_for_reviewer(Path(project_root))
    except Exception:  # noqa: BLE001 - derived context must never break a prompt
        return ""


def render_role_prompt_context(
    *,
    role: str,
    operation: str,
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    """Keep changing research evidence in the round delta, not the static policy.

    Everything here changes between turns of one campaign — the research
    notes, the GPU memory in use — so a role places it after the policy that
    ``render_role_prompt_fragment`` renders once per stage.
    """
    normalized_role = str(role or "").strip().lower()
    normalized_operation = str(operation or "").strip().lower()
    normalized_stage = str(stage or "").strip().lower()
    if normalized_role == "reviewer":
        if normalized_operation != "evaluate":
            return ""
        # The review packet: the host-derived method card (component status,
        # code anchors, files changed this round). Capped here as well as at
        # its source so a longer derivation can never crowd the Reviewer's
        # own evidence out of the prompt.
        packet = _derived_method_card_for_reviewer(normalized_stage, project_root)
        if packet:
            from .method_card import REVIEWER_MAX_LINES

            packet_lines = packet.splitlines()
            if not packet_lines[0].startswith("## Review packet"):
                packet_lines.insert(0, "## Review packet")
            packet = "\n".join(packet_lines[: REVIEWER_MAX_LINES + 1])
        blocks = (
            active_research_context(normalized_stage, project_root),
            research_runtime_context(normalized_stage, project_root),
            packet,
        )
    elif normalized_role == "planner":
        blocks = (
            active_research_context(normalized_stage, project_root),
            _hardware_usage_for_stage(normalized_stage),
        )
    elif normalized_role == "engineer":
        blocks = (
            active_research_context(
                _engineer_notes_stage(normalized_stage, normalized_operation),
                project_root,
            ),
            _hardware_usage_for_stage(
                _engineer_compute_stage(normalized_stage, normalized_operation)
            ),
        )
    elif normalized_role == "manager":
        blocks = (active_research_context(normalized_stage, project_root),)
    else:
        return ""
    # The date belongs in the per-round delta, not the static fragment: it
    # changes daily and remembered framework/engine/model names age with it.
    if (
        normalized_role in {"engineer", "reviewer", "planner"}
        and normalized_stage in {"idea", "experiment", "paper"}
    ):
        from datetime import date

        today_line = (
            f"Today is {date.today().isoformat()}. Treat remembered framework, "
            "engine and model names as dated hypotheses; verify against live sources."
        )
        blocks = (today_line,) + tuple(blocks)
    return "\n\n".join(block for block in blocks if block)


def render_role_prompt_fragment(
    *,
    role: str,
    operation: str,
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    """Render only policy owned by the Research vertical."""
    normalized_role = str(role or "").strip().lower()
    normalized_operation = str(operation or "").strip().lower()
    normalized_stage = str(stage or "").strip().lower()
    normalized_scope = str(scope or "").strip().lower().replace("-", "_")
    if normalized_role == "planner":
        return _planner_fragment(normalized_stage, project_root)
    if normalized_role == "engineer":
        return _engineer_fragment(
            normalized_stage,
            project_root,
            normalized_operation,
        )
    if normalized_role == "reviewer":
        return _reviewer_fragment(
            normalized_stage,
            normalized_scope,
            project_root,
            normalized_operation,
        )
    if normalized_role == "manager":
        # The research notes ride in ``render_role_prompt_context``.
        return (
            _stage_playbook_block(normalized_stage)
            + "\n\n## Forward-only stage authority\n"
            "Research stages never roll back. Hold the current stage and schedule "
            "repairs there, or advance when the stage's work is complete.\n\n"
            + _research_learning_block("manager", normalized_stage, normalized_operation)
        ).strip()
    return ""


__all__ = [
    "academic_paper_review_block",
    "paper_reviewer_standard",
    "paper_writing_standard",
    "active_research_context",
    "active_context_paths",
    "local_hardware_block",
    "local_hardware_usage_block",
    "research_runtime_context",
    "render_role_prompt_fragment",
    "render_role_prompt_context",
    "STAGE_PLAYBOOK_PATHS",
]
