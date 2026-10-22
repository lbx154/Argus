"""Reviewer sub-agent: one judgment per candidate, handed on by its baton line.

Provenance: vendored from ``ArgusBot/agent_cli/reviewer.py``. The
substantive change is decoupling: the original took a ``AgentCliRunner``
directly; this version takes any ``RunnerBackend`` (see
``argus.core.ports``) so it works with any supported agent CLI or the
in-memory test stub equally well.

``Reviewer.evaluate(...) -> ReviewDecision`` receives a native tool action:
the review and the line closing it that names the next holder, never a reply
the host reads for a verdict.
"""
from __future__ import annotations

import logging
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from ..core.mission_view import say, uses_cjk
from ..core.models import (
    READING_ROLE_UNUSED_COPILOT_TOOLS,
    ReviewDecision,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.stop_kinds import (
    StopKind,
    normalize_stop_kind,
    stop_kind_clause,
    stop_kind_from_external_interrupt,
)
from ..core.workspace_boundary import role_workspace_boundary
from ..core.workspace_snapshot import WorkspaceSnapshots
from .tools import review_action_tools

log = logging.getLogger(__name__)

class _ReviewUsage(TypedDict):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int


def _parallel_final_review_passes(
    runner: RunnerBackend,
    config: "ReviewerConfig",
    *,
    current_work: str = "",
) -> ReviewDecision | None:
    """Obtain current specialist evidence; the integrated review still runs."""
    workdir = Path(config.artifact_root or config.working_dir or ".").resolve()
    state_root = Path(config.vertical_state_root or workdir).resolve()
    vertical = str(config.active_vertical or "").strip().lower()
    if not vertical:
        from ..skills.vertical_select import resolve_vertical

        vertical = resolve_vertical(state_root)
    if vertical != "research":
        return None
    from ..core.pipeline_state import read_pipeline_state
    from ..skills.stage_machine import current_stage

    verdict = str(read_pipeline_state(state_root).get("current_verdict") or "")
    if (
        current_stage(state_root) != "review"
        or verdict
        not in {
            "",
            "continue",
            "in_progress",
            "mapped_stage_requires_current_review",
        }
    ):
        return None
    comparison_mode = bool(config.narrative_snapshot_root)
    comparison = None
    if comparison_mode:
        from ..core.manuscript_narrative_runtime import snapshot_after_edit

        try:
            comparison = snapshot_after_edit(
                workdir,
                config.narrative_snapshot_root or "",
            )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            return ReviewDecision(
                status="continue",
                reason=f"ScientificLoss shadow pass unavailable: {exc}",
                next_action=(
                    "Restore a readable immutable pre-edit snapshot, then rerun the "
                    "semantic-loss comparison."
                ),
            )
        from ..core.manuscript_narrative_runtime import rendered_pdf_freshness

        pdf_is_current, pdf_status = rendered_pdf_freshness(workdir)
        if not pdf_is_current:
            return ReviewDecision(
                status="continue",
                reason=f"ColdRead shadow pass unavailable: {pdf_status}",
                next_action="Compile the current manuscript before the PDF-only cold read.",
            )

    from ._paper_pass_cache import paper_pass_key, pdf_facts, same_pdf

    pdf_seen = pdf_facts(workdir)
    pdf_only_visual = (workdir / "paper" / "main.pdf").is_file()
    common = (
        "Read the current paper. Do not edit files. Start from "
        "paper/main.tex and its rendered output, then follow only direct references "
        "needed for this assigned pass. Return a concise pass/fail assessment with "
        "evidence-backed strengths, verified progress, and specific blocking findings "
        "with constructive repairs. For each material finding, explain the opportunity, "
        "a feasible change, and its decisive validation or success criterion. Credit "
        "resolved findings; separate useful high-impact improvements from speculative "
        "stretch ideas. Encourage Engineer through concrete progress and promising "
        "directions while keeping the assessment honest."
    )
    if comparison is None:
        # Compatibility for direct callers that have not entered the narrative-edit
        # operation. Production Review missions always provide an immutable baseline.
        prompts = {
            "Scientific": (
                common
                + " Check the complete thesis, novelty, claim-to-code fidelity, positive "
                "controls, strongest same-information baselines, evidence, citations, and "
                "whether every necessary experiment and section is present."
            ),
            "Visual": (
                common
                + " Inspect every rendered page and every included figure and table at "
                "publication scale. Reject visible overlap, clipping, overflow, connector "
                "penetration, wrong arrows, unreadable labels, malformed tables, misleading "
                "plots, abnormal whitespace, broken float placement, or inconsistent "
                "typography."
            ),
            "Language": (
                common
                + " Check academic language and argument flow. Identify exact revisions for "
                "confident, precise prose without defensive boilerplate, experiment "
                "chronology, internal workflow language, repeated caveats, or integrity "
                "self-praise."
            ),
        }
    else:
        from ..roles.prompts import ChecklistMode, resolve_role_prompt
        from ..roles.prompts.reviewer import (
            COLD_READ,
            SCIENCE_LOSS_CHECK,
            evaluate_request,
        )

        science_policy = resolve_role_prompt(
            evaluate_request(
                state_root,
                altitude_root=workdir,
                vertical="research",
                stage="review",
                checklist_mode=ChecklistMode.NONE,
                operation=SCIENCE_LOSS_CHECK,
            )
        ).role_banner
        cold_policy = resolve_role_prompt(
            evaluate_request(
                state_root,
                altitude_root=workdir,
                vertical="research",
                stage="review",
                checklist_mode=ChecklistMode.NONE,
                operation=COLD_READ,
            )
        ).role_banner
        prompts = {
            "ScientificLoss": (
                science_policy
                + "\n\nCompare these immutable trees:\n"
                f"- before: `{comparison.before_paper}`\n"
                f"- after: `{comparison.after_paper}`\n"
                "Use the live project only for a direct claim-critical dispute. Return "
                "pass/fail and identify every loss by fact, prior carrier, and required "
                "replacement carrier."
            ),
            "Visual": (
                common
                + " Inspect every rendered page and every included figure and table at "
                "publication scale. Reject visible overlap, clipping, overflow, connector "
                "penetration, wrong arrows, unreadable labels, malformed tables, misleading "
                "plots, abnormal whitespace, broken float placement, or inconsistent "
                "typography."
            ),
            "ColdRead": (
                cold_policy
                + "\n\nOpen `paper/main.pdf` and return a concise pass/fail cold-read "
                "assessment with page-locatable findings."
            ),
        }

    # These two passes judge the rendered paper. Isolating both makes their
    # entire evidence boundary explicit, so reuse never hides changed code or
    # raw evidence from the integrated scientific Reviewer.
    if pdf_only_visual:
        prompts["Visual"] = (
            "Read only paper/main.pdf and its host-rendered derivatives in this "
            "isolated workspace. Open every image in paper/pages/page-*.png; "
            "these are the actual PDF pages, rendered by the host. paper/main.txt "
            "contains text extracted from the same PDF for navigation. Reading PDF "
            "binary bytes or extracted text alone is not visual inspection. "
            "Inspect every rendered page and every included figure and table at "
            "publication scale. Reject visible overlap, clipping, overflow, connector "
            "penetration, wrong arrows, unreadable labels, malformed tables, visually "
            "misleading plots, abnormal whitespace, broken float placement, or "
            "inconsistent typography. Also judge composition, visual hierarchy, "
            "meaningful grouping, information density, spacing, and whether the "
            "mechanism reads immediately. Reject an unfinished collection of text "
            "boxes even when its labels are individually legible. Return a concise "
            "natural-language assessment with page "
            "locations. The integrated Reviewer separately checks scientific claims "
            "against source code and raw evidence; do not launch other reviewers."
        )
    prompts["Visual"] += (
        "\n\nA coherent, readable composition with professional academic styling "
        "should stand. Request a repair only for a concrete remaining defect in "
        "scientific meaning, readability, rendering or presentation; give its page "
        "and the affected detail. A preferred palette, font or alternate arrangement "
        "alone must not reopen the design search or delay scientific review. "
        "Judge the current render and do not repeat a concern it has already resolved. "
        "Respect the paper's actual template and column layout; natural whitespace "
        "or intentional typographic hierarchy is not a defect by itself. Normal "
        "top-of-page or bottom-of-page floats may occur between the two pages of a "
        "continuing sentence; that alone is not broken reading order. Ordinary "
        "trailing whitespace on the final references or appendix page is optional "
        "polish, not grounds to fail the paper. Require a specific comprehension "
        "or rendering problem before requesting forced float or page placement."
    )
    if "ColdRead" in prompts:
        prompts["ColdRead"] += (
            "\n\nThe host supplies paper/main.txt and paper/pages/page-*.png, "
            "derived only from the current paper/main.pdf. Use the extracted text "
            "and rendered pages to read the paper; the file viewer may expose the "
            "PDF itself only as compressed binary bytes."
        )
    if current_work.strip() or config.review_policy_context.strip():
        from ..core.model_visible_text import sanitize_model_visible_text

        source_context = sanitize_model_visible_text("\n\n".join(
            block for block in (
                config.review_policy_context.strip(),
                current_work.strip(),
            ) if block
        ))
        for label in ("Scientific", "Language", "ScientificLoss"):
            if label in prompts:
                prompts[label] += (
                    "\n\n## Current revision context\n"
                    "Use this current revision context to locate the changed work and distinguish "
                    "retained failed attempts from their replacements. The Engineer's "
                    "account is unreviewed; task liveness and completed manifests do not "
                    "establish scientific validity. Verify claim-critical results against "
                    "their actual inputs, executed version and raw outputs. Credit "
                    "verified repairs and identify evidence still pending without "
                    "presenting it as a completed experiment. Do not request a duplicate "
                    "run of a healthy job or restore obsolete results merely because "
                    "their files are complete.\n\n"
                    + source_context
                )
    prompts = {
        label: prompt
        + "\n\nPut the complete assessment and all required repairs in your final response. "
        "Intermediate progress messages are not forwarded to the integrated Reviewer."
        for label, prompt in prompts.items()
    }
    findings: dict[str, str] = {}
    if comparison is not None and comparison.identical:
        prompts.pop("ScientificLoss", None)
        findings["ScientificLoss"] = (
            "No semantic-loss call was needed: the host verified identical before/after "
            "manuscript source and rendered bytes. This proves no edit-induced loss, "
            "not scientific correctness; independently judge the current work."
        )
    pass_keys = {
        label: paper_pass_key(
            pdf=pdf_seen, prompt=prompt, config=config, runner=runner
        )
        for label, prompt in prompts.items()
        if label in {"Visual", "ColdRead"} and pdf_seen
    }
    for label, key in pass_keys.items():
        if not key:
            continue
        previous = config.paper_pass_cache.get(label)
        if previous is not None and previous[0] == key:
            findings[label] = previous[1]
            del prompts[label]
    if not prompts:
        return ReviewDecision(
            status="continue",
            reason="\n\n".join(f"{label}: {text}" for label, text in findings.items()),
            next_action="Adjudicate these current assessments in the integrated review.",
        )

    import threading
    from concurrent.futures import ThreadPoolExecutor

    fork = getattr(runner, "fork", None)
    if not callable(fork):
        return ReviewDecision(
            status="blocked",
            reason="The reviewer backend cannot create independent parallel calls.",
            next_action="Use a reviewer backend that supports independent calls.",
            backend_unavailable=True,
            backend_stop_kind="backend_unavailable",
        )
    from ..adapters.agent_cli_backend import AgentCliBackend

    if isinstance(runner, AgentCliBackend):
        interrupt_lock = threading.Lock()
        interrupt_reason = ""
        source_interrupt = runner._default_interrupt_reason_provider

        def shared_interrupt() -> str | None:
            nonlocal interrupt_reason
            with interrupt_lock:
                if not interrupt_reason and source_interrupt is not None:
                    interrupt_reason = str(source_interrupt() or "")
                return interrupt_reason or None

        pass_runners = {
            label: fork(interrupt_reason_provider=shared_interrupt)
            for label in prompts
        }
    else:
        pass_runners = {label: fork() for label in prompts}
    if (
        any(backend is runner for backend in pass_runners.values())
        or len({id(backend) for backend in pass_runners.values()}) != len(prompts)
    ):
        return ReviewDecision(
            status="blocked",
            reason="The reviewer backend returned shared instances for parallel calls.",
            next_action="Use a reviewer backend that supports independent calls.",
            backend_unavailable=True,
            backend_stop_kind="backend_unavailable",
        )

    workspace_stack = ExitStack()
    working_dirs = {label: workdir for label in prompts}
    pdf_only_passes = [
        label for label in ("Visual", "ColdRead")
        if label in prompts and (label == "ColdRead" or pdf_only_visual)
    ]
    source_pdf_passes = [
        label for label in ("Scientific", "Language")
        if label in prompts and pdf_only_visual
    ]
    if pdf_only_passes or source_pdf_passes:
        from ..core.manuscript_narrative_runtime import isolated_pdf_workspace

        try:
            # Every pass must see the same current PDF, including source-aware
            # readers whose native viewer cannot render a PDF. Project preview
            # directories may still contain pages from a much earlier draft.
            pdf_workspace = workspace_stack.enter_context(
                isolated_pdf_workspace(workdir, readable=True)
            )
            copied_as_seen = same_pdf(workdir, pdf_workspace, pdf_seen)
            for label in pdf_only_passes:
                working_dirs[label] = pdf_workspace
                if not copied_as_seen:
                    pass_keys.pop(label, None)
            for label in source_pdf_passes:
                prompts[label] += (
                    "\n\n## Current rendered paper supplied by the host\n"
                    "The host copied the current PDF for this review. "
                    f"Read its extracted text at `{pdf_workspace / 'paper/main.txt'}` "
                    f"and page images at `{pdf_workspace / 'paper/pages'}/page-*.png`. "
                    "These derivatives were generated from that same current PDF. "
                    "Use them for rendered-paper claims; do not substitute project "
                    "preview directories or images left by an earlier build. Keep "
                    "reading current source, code and raw evidence in the project "
                    "for this pass. Compare the current source with these PDF-derived "
                    "inputs before alleging a stale PDF or requesting a rebuild. "
                    "Old preview images alone establish no defect in the current paper."
                )
        except (OSError, RuntimeError) as exc:
            workspace_stack.close()
            return ReviewDecision(
                status="blocked",
                reason=f"Rendered paper review inputs are unavailable: {exc}",
                next_action="Restore PDF page rendering before running the paper review.",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
            )

    def inspect(label: str) -> Any:
        return gateway_run_exec(
            pass_runners[label],
            prompt=prompts[label],
            options=RunnerOptions(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                **_engineer_access(config),
                skip_git_repo_check=config.skip_git_repo_check,
                extra_args=list(config.extra_args) if config.extra_args else None,
                working_dir=str(working_dirs[label]),
                workspace_boundary=role_workspace_boundary(
                    working_dir=working_dirs[label],
                    own_dirs=[workdir, config.vertical_state_root, *config.add_dirs],
                ),
            ),
            run_label=f"reviewer-{label.lower()}",
        )

    try:
        with ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="argus-final-review",
        ) as pool:
            futures = {
                label: pool.submit(inspect, label)
                for label in prompts
            }
            results = {}
            errors = {}
            for label in prompts:
                try:
                    results[label] = futures[label].result()
                except Exception as exc:  # provider boundary
                    errors[label] = exc
    finally:
        workspace_stack.close()

    def total(field: str) -> int:
        return sum(int(getattr(result, field, 0) or 0) for result in results.values())

    usage = _ReviewUsage(
        input_tokens=total("input_tokens"), cached_input_tokens=total("cached_input_tokens"),
        output_tokens=total("output_tokens"), reasoning_output_tokens=total("reasoning_output_tokens"),
    )
    premium_requests = sum(
        float(getattr(result, "premium_requests", 0.0) or 0.0)
        for result in results.values()
    )
    stop_priority = {
        "operator_abort": 0,
        "operator_pause": 1,
        "daemon_shutdown": 2,
        "budget_exhausted": 3,
        "cost_unreconciled": 4,
        "provider_cooldown": 5,
        "provider_fence": 6,
        "permanent_error": 7,
        "transient_error": 8,
        "backend_unavailable": 9,
    }
    failures: list[tuple[int, str, StopKind, str, int | None, str]] = []
    for label, error in errors.items():
        stop_kind = normalize_stop_kind(getattr(error, "stop_kind", None))
        if stop_kind is None and getattr(error, "login_required", False):
            stop_kind = "permanent_error"
        stop_kind = stop_kind or "backend_unavailable"
        failures.append((
            stop_priority[stop_kind],
            label,
            stop_kind,
            str(error),
            None,
            f"{label} review provider raised {type(error).__name__}: {error}",
        ))
    for label, result in results.items():
        fatal = str(getattr(result, "fatal_error", "") or "").strip()
        exit_code = int(getattr(result, "exit_code", 0) or 0)
        if not fatal and exit_code == 0:
            continue
        stop_kind = (
            normalize_stop_kind(getattr(result, "stop_kind", None))
            or "backend_unavailable"
        )
        failures.append((
            stop_priority[stop_kind],
            label,
            stop_kind,
            fatal,
            exit_code,
            (
                f"{label} review failed before producing an assessment"
                + (f": {fatal}" if fatal else f" (exit={exit_code})")
            ),
        ))
    if failures:
        _, _, stop_kind, fatal, failed_exit_code, reason = min(failures)
        return ReviewDecision(
            status="blocked",
            reason=reason,
            next_action="Resolve the failed review provider call before retrying.",
            backend_unavailable=True,
            backend_fatal_error=fatal,
            backend_exit_code=failed_exit_code,
            backend_stop_kind=stop_kind,
            premium_requests=premium_requests,
            **usage,
        )
    findings.update({
        label: "\n".join((results[label].agent_messages or [])[-1:]).strip()
        for label in prompts
    })
    empty = next((label for label, text in findings.items() if not text), "")
    if empty:
        return ReviewDecision(
            status="blocked",
            reason=f"{empty} review returned no assessment",
            next_action="Retry the missing review pass.",
            backend_unavailable=True,
            backend_stop_kind="backend_unavailable",
            premium_requests=premium_requests,
            **usage,
        )
    # A provider may have run while another process updated the rendered paper.
    # Keep its result for adjudication, but never reuse it under the new bytes.
    if pdf_seen and pdf_facts(workdir) == pdf_seen:
        for label, key in pass_keys.items():
            if key:
                config.paper_pass_cache[label] = (key, findings[label])
    return ReviewDecision(
        status="continue",
        reason="\n\n".join(f"{label}: {text}" for label, text in findings.items()),
        next_action=(
            "Apply the scientific, visual, and reader-facing findings to the paper, "
            "recompile it, then request the integrated final review."
        ),
        premium_requests=premium_requests,
        **usage,
    )


def _persist_research_review(decision: ReviewDecision, config: "ReviewerConfig") -> None:
    """Overwrite the one research review file at the Review stage."""
    workdir = Path(config.working_dir or ".").expanduser().resolve()
    artifact_root = Path(
        config.artifact_root or workdir
    ).expanduser().resolve()
    state_root = Path(config.vertical_state_root or workdir).expanduser().resolve()
    vertical = str(config.active_vertical or "").strip().lower()
    if not vertical:
        from ..skills.vertical_select import resolve_vertical

        vertical = resolve_vertical(state_root)
    if vertical != "research":
        return
    from ..skills.stage_machine import current_stage

    if current_stage(state_root) != "review" and not decision.venue_review_required:
        return
    issues = ""
    if decision.venue_review is not None:
        from ..core.venue_review import UNSELECTED_VENUE

        issues = "\n".join(f"- {issue}" for issue in decision.venue_review["blocking_issues"])
    text = (
        "# Authoritative review\n\n"
        + (
            "## Selected-venue assessment\n"
            f"Venue: {decision.venue_review['venue'] or UNSELECTED_VENUE}\n\n"
            f"Recommendation: {decision.venue_review['recommendation']}\n\n"
            if decision.venue_review is not None else f"**Judgment:** {decision.status}\n\n"
        )
        +
        "## Scientific, visual, and language assessment\n"
        f"{decision.reason or 'Not assessed.'}\n\n"
    )
    # Natural reviews already carry strengths, concerns, and revision guidance.
    # Preserve the complete review once; add only details it does not repeat.
    for heading, content in (
        ("Reject-level issues", issues),
        ("Next action", str(decision.next_action or "").strip()),
    ):
        if content and not all(
            line.strip().removeprefix("- ") in text
            for line in content.splitlines() if line.strip()
        ):
            text += f"## {heading}\n{content}\n\n"
    path = artifact_root / "paper" / "REVIEW.md"
    from ..manager.source_writeback import atomic_write

    atomic_write(path, text)

    from ..core.pipeline_state import read_pipeline_state, write_pipeline_state

    payload = read_pipeline_state(state_root)
    payload["current_verdict"] = str(decision.status)
    payload["next_action"] = str(decision.next_action or "none")
    write_pipeline_state(state_root, payload)


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    active_vertical: str = ""
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    working_dir: str | None = None
    # The Engineer's: the Reviewer reads what the work it judges was built from.
    add_dirs: list[str] = field(default_factory=list)
    artifact_root: str | None = None
    # The task's snapshots, its candidate just taken: the Reviewer starts from
    # the diff from baseline to candidate.
    snapshots: WorkspaceSnapshots | None = None
    vertical_state_root: str | None = None
    narrative_snapshot_root: str | None = None
    review_policy_context: str = ""
    # Host memory only: project/agent-writable files cannot forge reuse.
    # One latest entry per PDF-only pass; cached calls incur zero new usage.
    paper_pass_cache: dict[str, tuple[str, str]] = field(
        default_factory=dict, repr=False, compare=False
    )
    # Standalone reviews discover persisted policy; mission callers supply theirs.
    workflow_mode: str | None = None
    # Host-gathered for a mission's Reviewer (``round_reviewer.mission_grounding``):
    # the workspace's files as recorded when work on the objective began, the
    # files its packet names with their recorded state, and the acceptances
    # recorded for its item. What "impossible here" and an acceptance must match.
    mission_grounding: dict[str, Any] | None = field(default=None, repr=False, compare=False)


def _engineer_access(config: ReviewerConfig) -> dict[str, Any]:
    """The Engineer's access, so the Reviewer can run and fetch what it judges.

    Worktree isolation stays the Engineer's: it hides the paths the Reviewer's
    verdict tools and prompt-named records live on.
    """
    return {
        "full_auto": config.full_auto,
        "dangerous_yolo": config.dangerous_yolo,
        "sandbox_mode": config.sandbox_mode,
    }


def _changed_during_review(snapshots: WorkspaceSnapshots | None) -> list[str]:
    """Workspace paths that differ from the candidate the Reviewer was given.

    Compared against the round's own ``candidate`` snapshot; with no snapshots,
    or one that cannot be taken or compared, nothing is known and nothing is said.
    """
    if snapshots is None or not snapshots.take("reviewed"):
        return []
    return [change.path for change in snapshots.changes("candidate", "reviewed") or []]


def _load_wiki_curator_skill_if_present(
    working_dir: str | Path | None = None,
) -> str | None:
    """Compatibility wrapper for the prompt-module helper."""
    from ..roles.prompts.reviewer import _load_wiki_curator_skill_if_present

    return _load_wiki_curator_skill_if_present(working_dir)


def _verification_directive() -> str:
    """Compatibility wrapper for the prompt-module helper."""
    from ..roles.prompts.reviewer import _verification_directive

    return _verification_directive()


def _review_grounding(config: Any, *, task_parts: tuple[str, ...]) -> Any:
    """What the Reviewer's "impossible here" quotes and acceptances must match.

    The task as given (never the Engineer's account); the workspace for a
    quoted environment file still as it was when work on the objective began,
    or still as the Planner recorded it when the packet named it;
    and the acceptances recorded for this item: the Manager's, and the
    operator's choices on decision cards. Nothing the Reviewer was merely shown
    as text (OperatorContext included) is read as an acceptance.
    """
    from ..core.autonomy import operator_questions_can_be_asked
    from .tools import ReviewGrounding

    roots = tuple(dict.fromkeys(
        str(root) for root in (
            getattr(config, "working_dir", None), getattr(config, "artifact_root", None),
            getattr(config, "vertical_state_root", None),
        ) if root
    ))
    mission = getattr(config, "mission_grounding", None) or {}
    baseline = mission.get("baseline")
    return ReviewGrounding(
        task_text="\n".join(str(part or "") for part in task_parts),
        roots=roots,
        # The operator accepts a risk only while a question can reach them;
        # otherwise the Manager does (``core.autonomy``). The mission's own
        # answer wins: it knows the project's question policy.
        operator_available=bool(mission.get(
            "operator_questions_allowed", operator_questions_can_be_asked(),
        )),
        baseline=(
            {str(path): facts for path, facts in baseline.items() if isinstance(facts, list)}
            if isinstance(baseline, dict) else None
        ),
        # A ref without recorded ``file_facts`` (one from before them included)
        # is not a packet source; it counts only as an unchanged workspace file.
        packet_refs=tuple(
            (str(ref.get("ref") or ""), ref["content_hash"])
            for ref in mission.get("packet_refs") or ()
            if isinstance(ref, dict) and isinstance(ref.get("content_hash"), list)
        ),
        accepted_risks=tuple(dict(row) for row in mission.get("accepted_risks") or () if isinstance(row, dict)),
        operator_decisions=tuple(
            dict(card) for card in mission.get("operator_decisions") or () if isinstance(card, dict)
        ),
    )


class Reviewer:
    """One independent judgment per round, with optional same-role resume."""

    def __init__(
        self,
        runner: RunnerBackend,
        *,
        skill_store: Any | None = None,
        memory_maintenance_enabled: bool = True,
    ) -> None:
        self.runner = runner
        self._last_prompt_block_stats: dict[str, dict[str, int]] = {}
        # Optional agent-native library roots. The Reviewer searches and reads
        # relevant Markdown itself; the runtime never injects Skill bodies.
        self.skill_store = skill_store
        self.memory_maintenance_enabled = memory_maintenance_enabled
        from ..skills.missions import ReviewerMission
        self.mission = ReviewerMission(skill_store)

    def evaluate(
        self,
        *,
        operation: str = "evaluate",
        objective: str,
        original_objective: str | None = None,
        operator_messages: list[str] | None = None,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        config: ReviewerConfig,
        round_max: int = 0,
        planner_review_instruction: str = "",
        active_skill_id: str | None = None,
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        checkpoint_path: str = "",
        background_context: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        preselected_skill_block: str | None = None,
        resume_thread_id: str | None = None,
        prior_static_fingerprint: str = "",
        previous_findings: str = "",
    ) -> ReviewDecision:
        # Resolve the Reviewer's own library contract once for both the prompt
        # fallback and a backend-native loader.
        review_libraries = self.mission.libraries()
        if preselected_skill_block is None:
            preselected_skill_block = review_libraries.block
        native_skill_paths = [
            str(path) for path in getattr(review_libraries, "native_paths", [])
        ]
        from ..core.pipeline_state import read_pipeline_state
        from ..core.venue_review import (
            enforce_venue_acceptance,
            paper_review_snapshot,
            requires_venue_review,
            selected_acceptance_minimum,
            selected_venue,
            venue_review_instruction,
        )
        from ..skills.builtins import vertical_skill_read_dirs
        from ..skills.vertical_select import resolve_vertical_if_decided

        artifact_root = Path(config.artifact_root or config.working_dir or ".")
        state_root = Path(config.vertical_state_root or config.working_dir or ".")
        vertical = config.active_vertical or resolve_vertical_if_decided(state_root) or ""
        venue_required = requires_venue_review(
            vertical=vertical,
            stage=str(read_pipeline_state(state_root).get("current_stage") or ""),
            scope=scope,
            operation=operation,
        )
        venue = selected_venue(state_root) if venue_required else ""
        acceptance_minimum = selected_acceptance_minimum(state_root) if venue_required else "weak_accept"
        venue_snapshot = paper_review_snapshot(artifact_root) if venue_required else None
        reviewed_manuscript_snapshot = None
        snapshot_root = config.artifact_root or config.working_dir
        if snapshot_root is not None:
            from ..core.manuscript_snapshot import manuscript_snapshot

            with suppress(OSError):  # an unreadable manuscript binds no review
                candidate_snapshot = manuscript_snapshot(snapshot_root)
                if candidate_snapshot["facts"]:
                    reviewed_manuscript_snapshot = candidate_snapshot
        # Split the prompt into a byte-stable STATIC preamble and per-round DELTA.
        # A matching same-role session receives only the new delta.
        static, delta_base = self._render(
            operation=operation,
            resumed=False,
            objective=objective,
            original_objective=original_objective or objective,
            operator_messages=operator_messages or [],
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            round_max=round_max,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            active_skill_id=active_skill_id,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
            scope=scope,
            prior_checkpoint=prior_checkpoint,
            checkpoint_path=checkpoint_path,
            background_context=background_context,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            preselected_skill_block=preselected_skill_block,
            working_dir=config.working_dir,
            vertical_state_root=config.vertical_state_root,
            vertical=config.active_vertical,
            workflow_mode=config.workflow_mode,
            snapshots=config.snapshots,
            previous_findings=previous_findings,
        )
        venue_policy = ""
        if venue_required:
            # A changed selected venue changes the static fingerprint, preventing
            # reuse of a Reviewer session calibrated to a different conference.
            venue_policy = "\n\n" + venue_review_instruction(venue, minimum=acceptance_minimum)
            static += venue_policy
        prompt_block_stats = {
            name: dict(stats)
            for name, stats in self._last_prompt_block_stats.items()
        }
        if venue_policy:
            extra_bytes = len(venue_policy.encode("utf-8"))
            extra_stats = {"chars": len(venue_policy), "bytes": extra_bytes, "estimated_tokens": (extra_bytes + 3) // 4}
            prompt_block_stats["venue_acceptance"] = extra_stats
            for name, amount in extra_stats.items():
                if "static_total" in prompt_block_stats:
                    prompt_block_stats["static_total"][name] = prompt_block_stats["static_total"].get(name, 0) + amount
        # The session resumes only under the very same static preamble.
        resume = (
            resume_thread_id
            if resume_thread_id and prior_static_fingerprint == static
            else None
        )
        from ..roles.prompts.reviewer import (
            _REEVALUATE_HEADER,
            assemble_reviewer_prompt,
        )

        prompt = assemble_reviewer_prompt(
            "" if resume else static,
            (_REEVALUATE_HEADER + delta_base) if resume else delta_base,
        )
        result: RunnerResult | None = None
        changed: list[str] = []

        def with_call_metadata(decision: ReviewDecision) -> ReviewDecision:
            if result is None:
                return decision
            # Blocking a verdict must not erase a completed provider call.
            decision.input_tokens = int(getattr(result, "input_tokens", 0) or 0)
            decision.cached_input_tokens = int(getattr(result, "cached_input_tokens", 0) or 0)
            decision.output_tokens = int(getattr(result, "output_tokens", 0) or 0)
            decision.reasoning_output_tokens = int(
                getattr(result, "reasoning_output_tokens", 0) or 0
            )
            decision.premium_requests = float(getattr(result, "premium_requests", 0.0) or 0.0)
            decision.thread_id = getattr(result, "thread_id", None)
            decision.static_fingerprint = static
            decision.session_resumed = bool(resume)
            decision.prompt_block_stats = prompt_block_stats
            decision.changed_during_review = changed
            return decision

        add_dirs = list(config.add_dirs)
        if str(getattr(self.runner, "backend", "")).lower() == "copilot":
            # Copilot denies a read outside its add-dirs, and this prompt names
            # files outside the workdir by absolute path: the project state
            # beside the Engineer log (handoffs, round checks, goal contract),
            # the checkpoint, and the active vertical's packaged playbooks.
            prompt_dirs = [
                Path(value).expanduser().resolve().parent
                for value in (engineer_log_path, checkpoint_path)
                if value
            ]
            if config.vertical_state_root:
                prompt_dirs.append(Path(config.vertical_state_root).expanduser().resolve())
            add_dirs += [
                *(str(path) for path in review_libraries.library_roots),
                *(str(path) for path in prompt_dirs if path.is_dir()),
                *vertical_skill_read_dirs(vertical),
            ]

        try:
            with review_action_tools(
                self.runner,
                RunnerOptions(
                    model=config.model,
                    reasoning_effort=config.reasoning_effort,
                    **_engineer_access(config),
                    skip_git_repo_check=config.skip_git_repo_check,
                    extra_args=list(config.extra_args) if config.extra_args else None,
                    add_dirs=add_dirs or None,
                    skill_paths=native_skill_paths,
                    working_dir=config.working_dir,
                    # Its own project, the Engineer's record and the libraries
                    # it reads stay open; other projects' data does not (SE-1).
                    workspace_boundary=role_workspace_boundary(
                        working_dir=config.working_dir,
                        own_dirs=[
                            *add_dirs, config.artifact_root, config.vertical_state_root,
                            *(Path(value).expanduser().parent
                              for value in (engineer_log_path, checkpoint_path) if value),
                        ],
                    ),
                    # Search is available for the rare turn that proposes a
                    # skill; ordinary review turns need not invoke it.
                    live_search=True,
                    # Sub-agent, workflow, session-store and semantic-search
                    # tools no Reviewer thread used stay out of every turn.
                    lean_tool_surface=True,
                    excluded_tools=READING_ROLE_UNUSED_COPILOT_TOOLS,
                ),
                venue=venue, venue_required=venue_required,
                grounding=_review_grounding(
                    config,
                    task_parts=(objective, original_objective or "", scope, planner_review_instruction),
                ),
            ) as (actions, options):
                result = gateway_run_exec(
                    self.runner, prompt=prompt, resume_thread_id=resume,
                    options=options, run_label="reviewer",
                )
                decision = actions.decision
            changed = _changed_during_review(config.snapshots)
            fatal = str(getattr(result, "fatal_error", "") or "").strip()
            stopped_by = (
                normalize_stop_kind(getattr(result, "stop_kind", None))
                or stop_kind_from_external_interrupt(fatal)
            )
            if fatal or result.exit_code != 0:
                # Plain words first, in the language of the task; the transport
                # details ride behind a "Technical record:" marker so a consumer
                # can set them aside.
                interrupted = "external interrupt" in fatal.lower() or "daemon stop" in fatal.lower()
                chinese = uses_cjk(objective)
                if interrupted:
                    sentence = say(
                        "review_interrupted", chinese,
                        why=stop_kind_clause(stopped_by, chinese=chinese, neutral=True),
                    )
                else:
                    sentence = (
                        "审阅者的会话在得出结论前就结束了，这一轮没有评审意见。"
                        if chinese
                        else "The Reviewer's session ended before it reached a conclusion, "
                        "so this round was not judged."
                    )
                reason = (
                    sentence
                    + (" 技术记录：" if chinese else " Technical record: ")
                    + f"exit={result.exit_code}"
                    + (f"; fatal_error={fatal}" if fatal else "")
                )
                return with_call_metadata(ReviewDecision(
                    status="blocked",
                    reason=reason,
                    next_action=(
                        "这一轮还没有得到判断；请把它当作尚未审阅，而不是对工作的反馈。"
                        if chinese
                        else "The check did not reach a conclusion; treat this round as "
                        "not yet judged rather than as feedback on the work."
                    ),
                    backend_unavailable=True,
                    backend_fatal_error=fatal,
                    backend_exit_code=result.exit_code,
                    backend_stop_kind=stopped_by or "backend_unavailable",
                ))
            if decision is None:
                return with_call_metadata(ReviewDecision(
                    status="blocked",
                    reason=(
                        "The Reviewer ended without submitting a review action. "
                        "Its reply was not interpreted as acceptance or rejection."
                    ),
                    next_action=(
                        "Ask the independent Reviewer to submit its judgment through "
                        "submit_review."
                    ),
                    backend_unavailable=True,
                    backend_stop_kind="backend_unavailable",
                ))
            decision = with_call_metadata(decision)
            decision.manuscript_snapshot = reviewed_manuscript_snapshot
            # Reviewer owns the scientific recommendation. The host enforces the
            # operator's explicit minimum and current-file binding, not prose or
            # research-result keyword heuristics.
            decision.reviewer_words = {
                "status": str(decision.status or ""),
                "reason": str(decision.reason or ""),
                "next_action": str(decision.next_action or ""),
            }
            if venue_required:
                enforce_venue_acceptance(
                    decision, venue=venue, before=venue_snapshot, artifact_root=artifact_root,
                    minimum=acceptance_minimum,
                )
                if decision.backend_unavailable:
                    return decision
            _persist_research_review(decision, config)
            return decision
        except Exception as exc:  # noqa: BLE001
            msg = f"Reviewer runner raised {type(exc).__name__}: {exc}"
            log.exception("reviewer runner raised")
            return with_call_metadata(ReviewDecision(
                status="blocked",
                reason=msg,
                next_action="Resolve the reviewer runner failure before retrying.",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
                backend_exit_code=result.exit_code if result is not None else None,
                backend_fatal_error=str(getattr(result, "fatal_error", "") or ""),
            ))

    def _render(
        self,
        *,
        operation: str = "evaluate",
        resumed: bool = False,
        objective: str,
        original_objective: str = "",
        operator_messages: list[str],
        planner_review_instruction: str,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        round_max: int = 0,
        active_skill_id: str | None = None,
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        checkpoint_path: str = "",
        background_context: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        preselected_skill_block: str | None = None,
        working_dir: str | Path | None = None,
        vertical_state_root: str | Path | None = None,
        vertical: str = "",
        workflow_mode: str | None = None,
        snapshots: WorkspaceSnapshots | None = None,
        previous_findings: str = "",
    ) -> tuple[str, str]:
        """F7: render the reviewer prompt as ``(static_preamble, round_delta)``.

        ``static_preamble`` is a byte-stable role/rubric prefix suitable for
        provider caching and same-role resume. Fresh calls receive both parts;
        resumed calls receive only the round delta.
        """
        from ..roles.prompts.reviewer import render_reviewer_prompt

        return render_reviewer_prompt(
            self,
            operation=operation,
            resumed=resumed,
            objective=objective,
            original_objective=original_objective,
            operator_messages=operator_messages,
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            round_max=round_max,
            active_skill_id=active_skill_id,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
            scope=scope,
            prior_checkpoint=prior_checkpoint,
            checkpoint_path=checkpoint_path,
            background_context=background_context,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            preselected_skill_block=preselected_skill_block,
            working_dir=working_dir,
            vertical_state_root=vertical_state_root,
            vertical=vertical,
            workflow_mode=workflow_mode,
            snapshots=snapshots,
            previous_findings=previous_findings,
        )

    def _build_prompt(self, **kwargs: Any) -> str:
        """Full reviewer prompt (static + round-1 delta). Kept for the unit tests
        and any non-resuming caller; ``evaluate`` uses ``_render`` directly."""
        static, delta = self._render(resumed=False, **kwargs)
        from ..roles.prompts.reviewer import assemble_reviewer_prompt

        return assemble_reviewer_prompt(static, delta)

    def _build_static_preamble(self, **kwargs: Any) -> str:
        """The byte-stable static preamble alone (for the fingerprint + resume)."""
        static, _ = self._render(resumed=False, **kwargs)
        return static

    def _build_round_delta(self, *, resumed: bool, **kwargs: Any) -> str:
        """This round's delta alone; ``resumed`` prepends the RE-EVALUATE header."""
        _, delta = self._render(resumed=resumed, **kwargs)
        return delta

    @property
    def last_prompt_block_stats(self) -> dict[str, dict[str, int]]:
        return {
            name: dict(stats)
            for name, stats in self._last_prompt_block_stats.items()
        }
