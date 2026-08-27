"""Planner prompt operations and structured context requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...core.model_visible_text import sanitize_model_visible_text
from ...core.role_decision import decision_footer_instruction
from ...planner.work_kind import planner_work_kind_guidance
from ..task_contract import native_shell_contract, native_shell_summary
from .types import ChecklistMode, RoleName, RolePromptRequest

CONTINUOUS = "continuous"
BOUNDED_DAG = "bounded_dag"
PLAN_PREVIEW = "plan_preview"
PARALLEL_DRAFT = "parallel_draft"

OPERATIONS = frozenset(
    {
        CONTINUOUS,
        BOUNDED_DAG,
        PLAN_PREVIEW,
        PARALLEL_DRAFT,
    }
)


_PLANNER_DECISION_FOOTER = decision_footer_instruction(
    "PROJECT_DONE=false\n"
    "REASON=why\n"
    "TASK_KEY=k1\n"
    "TASK_DEPS=\n"
    "TASK_TITLE=Launch the strongest untested attack on the core hypothesis\n"
    "TASK_OBJECTIVE=design and run the experiment whose outcome most changes what we believe, with success and failure criteria stated in advance"
)
_BOUNDED_DAG_FOOTER = decision_footer_instruction(
    "PLAN_REASON=why this is a coherent executable DAG\n"
    "TASK_KEY=k1\n"
    "TASK_DEPS=\n"
    "TASK_TITLE=Launch the strongest untested attack on the core hypothesis\n"
    "TASK_OBJECTIVE=design and run the experiment whose outcome most changes what we believe, with success and failure criteria stated in advance\n"
    "TASK_WORK_KIND=validation"
)

_PLANNER_CORE_CONTRACT = """
## Planner read-only delegation contract
Read the current state, then choose the next useful milestone. Do not implement it;
delegate implementation to Engineer. Do not edit project files; Engineer owns edits,
commands, tests, and iteration.

- Reuse Manager/completed decisions. Give Engineer a self-contained task with its
  decision, inputs, check; split only for dependencies or independent work.
- Add machinery only when evidence requires it; scale of evidence is not machinery.
- Follow the operator's requested actions and order. Existing artifacts or a usable
  alternative do not replace the first unmet requested action. Do not invent cleanup,
  docs, provenance, or rechecks.
  Optional hardening never keeps a finite objective alive after the requested result passes.
- For an external algorithm, check primary-source grounding. Wiki/Skills are
  starting context, not a boundary; use fresh paper/source/issue/hardware investigation
  only when decision-relevant. When related attempts repeatedly fail, revisit primary
  papers and official implementations. Performance claims need code-path evidence and
  timing/profiling or a controlled comparison.
- Set `project_done=true` only when the operator goal is complete. Bounded-direct
  Reviewer `done` closes it; review again only if requested or the verdict finds a gap.
  Integrity and reproducibility are admission constraints, not a routing command.
  Never emit a bare launch verdict; say what happened and the next action or Host rejects it.
- End with `PROJECT_DONE` and `REASON`; repeat one `TASK_*` block per task.
  `ADVANCE_TO_STAGE` is optional (omit to hold); any value must be Host-valid.
  Tasks require `TASK_TITLE`/`TASK_OBJECTIVE`;
  `TASK_SCOPE` is optional (default `bounded`). Use `TASK_KEY`/`TASK_DEPS` only
  for dependencies. Feedback work adds
  `TASK_HYPOTHESIS`, `TASK_GOAL_CONTRIBUTION`,
  `TASK_EXPECTED_REGRESSIONS`, `TASK_DECISION_RULE`; optional:
  `TASK_ACCEPTANCE_CHECK`, `TASK_PARALLEL_SAFE`, `TASK_OWNS_PATHS`,
  `TASK_VERTICAL`.
- External waits give `blocker_fingerprint`, `recheck_condition`, `recheck_token`;
  set `operator_action_required` only for the operator. Describe `wake_on`
  semantically; synonyms/combined sources are accepted. Include `watched_paths`
  for artifact wakes. Host chooses an observable event or bounded poll.
- Use the operator's language.
""" + _PLANNER_DECISION_FOOTER + """

The footer may optionally end with `PLAN_UPDATE=` followed by the complete
multi-line Markdown for RESEARCH_PLAN.md. Put it after every `TASK_*` block.
"""

_RESEARCH_PLAN_CONTRACT = """## Research plan (living document)
Planner owns `RESEARCH_PLAN.md` in the daemon state directory. If there is no
valid plan below, create it now from the Manager mission brief/OBJECTIVE.md and
journal. Update it whenever a hypothesis changes status, an experiment settles,
or the program changes direction. Return the complete replacement in the
optional final `PLAN_UPDATE` block; otherwise the file stays unchanged.

Keep it under ~300 lines and use exactly this section order: `# Research plan`
then an objective one-liner; `## Central hypotheses` (numbered, each marked
untested/supported/refuted/abandoned with a one-line evidence pointer);
`## Experiment program` (what runs next and why each is the highest-information
move, including at least one bold/high-variance bet); `## Established results`
with evidence refs; `## Dead ends` with what was tried and why abandoned; and
`## Next milestone` stating what would let the paper start. Never delete a Dead
ends entry. If the projection says it was truncated, prune repetition while
preserving those entries.

Current document:
"""

_EXTERNAL_TARGET_CONTRACT = (
    "## External-target optimization\n"
    "Operator success / external gate outranks public/reference baseline, current "
    "local incumbent, and secondary metrics. A material gate gap requires "
    "primary-score work or a proven enabler; runtime, kernels, serialization, "
    "calibration, documentation, and status copying are secondary. Public "
    "task-specific papers, discussions, and source are allowed when operator "
    "policy permits; only imported answers, labels, or predictions are forbidden, "
    "and Skills cannot narrow that policy. Before proposing work, index recorded "
    "experiment outcomes and reject semantic duplicates, including renamed variants. "
    "This external-target contract overrides incompatible vertical style mandates "
    "such as compulsory kernel invention, profiling, or task-specific-source bans. "
    "Validation, OOF, calibration, and blend selection must use models fitted without "
    "the scored row labels; a final all-train refit is test-only evidence. "
    "Every task needs "
    "`TASK_IMPACT_SCORE=1..5`, `TASK_IMPACT_AREA`, and `TASK_EVIDENCE`; reserve "
    "4-5 for direct target movement or a proven prerequisite. Controller "
    "gate/feedback files are live truth."
)


def _join_prompt_blocks(*blocks: str) -> str:
    """Join only applicable prompt modules with one stable separator."""
    rendered = [block.strip() for block in blocks if block and block.strip()]
    return sanitize_model_visible_text("\n\n".join(rendered) + "\n")


def continuous_request(
    project_root: Path | str,
    *,
    stage: str | None = None,
    operation: str = CONTINUOUS,
    include_search_altitude: bool = True,
    altitude_root: Path | str | None = None,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.PLANNER,
        altitude_root=altitude_root,
        operation=operation,
        project_root=project_root,
        stage=stage,
        checklist_mode=ChecklistMode.STAGE,
        include_search_altitude=include_search_altitude,
    )


def preview_request(project_root: Path | str) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.PLANNER,
        operation=PLAN_PREVIEW,
        project_root=project_root,
    )


def build_bounded_dag_prompt(
    objective: str,
    *,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
    require_independent_review: bool = False,
) -> str:
    verification = ""
    policy_root = state_root if state_root is not None else project_root
    if policy_root is not None:
        from ...core.verification_policy import policy_line, resolve_policy
        from .registry import resolve_role_prompt

        context = resolve_role_prompt(
            RolePromptRequest(
                role=RoleName.PLANNER,
                operation=BOUNDED_DAG,
                project_root=policy_root,
                altitude_root=project_root,
                checklist_mode=ChecklistMode.STAGE,
            )
        )
        if context.verification_stage_profiles:
            policy = resolve_policy(
                policy_root,
                stage=context.stage,
                vertical=context.vertical,
                stage_profiles=context.verification_stage_profiles,
            )
            verification = (
                "\n\nActive vertical context: "
                f"`{context.vertical}` stage `{context.stage or 'unset'}`; "
                f"{policy_line(policy)}. In explore/develop, acceptance means "
                "the feedback-producing experiment ran honestly, not that the "
                "hypothesis won; certification belongs to the final profile."
            )
    shell_contract = native_shell_contract()
    shell_block = "\n\n" + shell_contract if shell_contract else ""
    review_policy = (
        "\n\nManager review policy: independent review is required. Put "
        "`TASK_REQUIRE_INDEPENDENT_REVIEW=true` on each substantive work node. "
        "Host invokes Reviewer after Engineer; do not create a review-only task."
        if require_independent_review
        else ""
    )
    return sanitize_model_visible_text(
        "Plan the Manager handoff as a small executable DAG. Do not do the work."
        + verification
        + shell_block
        + review_policy
        + "\n\n"
        "Rules:\n"
        "- Default to one node. Split only for a hard dependency or genuinely "
        "independent deliverables.\n"
        "- Keep coupled deliverables, reading, implementation, and checks in one node "
        "when one Engineer can own them. Never create a review-only or validation-only "
        "task.\n"
        "- Each node should name the work, relevant files, and one decisive check. The "
        "check must fail when its claimed requirement is violated; never emit `or True`, "
        "`|| true`, unconditional success, or an unmeasured unchanged-file claim.\n"
        "- Preserve the requested outcome and order. Do not add planning documents, "
        "cleanup, Git ceremony, duplicate verification, or unrelated research.\n"
        "- Preserve every operator-named execution mechanism and role-owned Skill. "
        "Planner's Skill catalog differs from Engineer's, so never declare an "
        "Engineer Skill unavailable from Planner visibility, add a fallback, or "
        "replace the requested mechanism; delegate it unchanged.\n"
        "- Reuse existing grounding unless primary-source semantics are materially "
        "missing. Existing grounding never forbids fresh upstream research when it "
        "can change the plan. When related attempts repeatedly fail, revisit the "
        "source assumption.\n"
        "- Dependencies must reflect real handoffs. Independent nodes may run in parallel.\n"
        "- More than one mission runs at a time. A long job holds its slot for "
        "hours without holding the others, so a cycle that schedules only that "
        "job leaves the rest of the campaign idle for as long as it runs; "
        "schedule what does not need its result too.\n"
        "- The Host owns execution and enforces review policy. Set "
        "`require_independent_review:true` on the owned work node when the operator "
        "explicitly requests independent review or the task crosses an independent "
        "authority boundary; otherwise omit it.\n"
        "- End with `PLAN_REASON` and one repeated `TASK_*` block per task. Each "
        "task uses `TASK_KEY`, `TASK_DEPS` (same-batch keys only), `TASK_TITLE`, "
        "and `TASK_OBJECTIVE`; add "
        "`hypothesis`, `goal_contribution`, `expected_regressions`, and "
        "`decision_rule` for feedback-driven work; add `acceptance_check`, "
        "`non_goals`, `vertical`, `execution_workdir` "
        "(project-relative nested repository), " + planner_work_kind_guidance() + ", and "
        "`require_independent_review` when useful. Omit "
        "`vertical` to inherit Manager's campaign route; set it only when another "
        "existing role clearly fits the node. Use the operator objective's "
        "language. Keys must be unique and the graph acyclic.\n\n"
        + _BOUNDED_DAG_FOOTER
        + "\n\n"
        "Manager execution handoff:\n" + objective.strip()
    )


def build_bounded_dag_repair_prompt(
    objective: str,
    previous_output: str,
    validation_error: str,
    *,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
    require_independent_review: bool = False,
) -> str:
    """Request one complete replacement after a mechanically invalid DAG."""
    prior = sanitize_model_visible_text(str(previous_output or "")[-40_000:])
    error = sanitize_model_visible_text(str(validation_error or ""))
    return (
        build_bounded_dag_prompt(
            objective,
            project_root=project_root,
            state_root=state_root,
            require_independent_review=require_independent_review,
        )
        + "\n\nYour previous conclusion could not be read as an executable DAG. "
        "Send one complete corrected footer. Keep "
        "the intended deliverables and correct only the malformed action-footer "
        "fields.\n"
        + f"VALIDATION_ERROR={error}\n"
        + "PREVIOUS_ANSWER:\n"
        + prior
    )


def build_continuous_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    research_plan: str = "",
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    open_ended: bool = False,
    memory_maintenance_enabled: bool = True,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
) -> str:
    """Build the continuous Planner prompt from the unified role catalog."""
    from ...core.project import resolve_project_root
    from ...core.research_contract import resolve_research_target_level
    from ...skills.ground_truth import ground_truth_mandate
    from ...skills.vertical_select import (
        resolve_evidence_mode,
        resolve_workflow_mode,
    )
    from .registry import resolve_role_prompt

    cycle_line = f"This is planning cycle #{planning_cycle + 1}."
    _workspace = resolve_project_root(project_root)
    _proot = (
        resolve_project_root(state_root)
        if state_root is not None
        else _workspace
    )
    prompt_context = resolve_role_prompt(
        continuous_request(_proot, altitude_root=_workspace)
    )
    stage = prompt_context.stage
    stage_checklist = prompt_context.stage_checklist
    workflow_mode = resolve_workflow_mode(_proot)

    # Vertical-owned policy arrives through the prompt catalog; this module
    # contributes only role-wide planning behavior.
    optimize_banner = prompt_context.role_banner

    research_target_block = ""
    _research_target_level = resolve_research_target_level(_proot)
    if (
        _research_target_level is not None
        or prompt_context.verification_stage_profiles
    ):
        # The target is the PROJECT bar; the profile is THIS round's bar. A
        # publishable target does not mean every probe must already be
        # publishable — that reading is what kills seed ideas.
        from ...core.verification_policy import resolve_policy
        from ...skills.stage_machine import current_stage

        try:
            _stage = current_stage(_proot)
        except Exception:  # noqa: BLE001 - stage is advisory here
            _stage = ""
        _policy = resolve_policy(
            _proot,
            stage=_stage,
            target_level=_research_target_level,
            stage_profiles=prompt_context.verification_stage_profiles,
        )
        if _research_target_level is not None:
            research_target_block = (
                "## Manager-owned research target\n"
                f"Preserve `research_target_level={_research_target_level}` from "
                "`.argus/PIPELINE_STATE.json`; it sets `PROJECT_DONE`, not this "
                f"round (`{_policy.profile}`/{_policy.posture}). At "
                "`publishable`/`doctoral` original research needs a nontrivial "
                "technical core, verified originality, formal/causal grounding, "
                "and field-level significance. A literature review needs "
                "independently verified scope, coverage, synthesis, claims, and "
                "writing quality at that level; originality is not required. "
                "Known results, finite checks, and honest negative reports are "
                "progress, not done. At `exploratory`, an independently verified "
                "negative report may satisfy the objective."
            )
        else:
            research_target_block = (
                "## Vertical verification staging\n"
                f"This mission uses `{_policy.profile}` verification at stage "
                f"`{_stage or 'unset'}`. Plan only the evidence this stage needs; "
                "the vertical's final stage owns certification. Integrity and "
                "truth do not get weaker in earlier profiles."
            )

    standing_continuous_block = ""
    if open_ended:
        standing_continuous_block = (
            "## Standing continuous objective\n"
            "This campaign remains active until the operator stops it or a real "
            "external blocker requires waiting. Completing one increment is not "
            "project completion. Do not return `PROJECT_DONE=true`; after inspecting "
            "the latest certified result, delegate the next distinct high-value task. "
            "If no legal work can proceed, use `WAITING=true` with a concrete blocker "
            "and recheck condition instead of declaring completion.\n\n"
        )

    # Live search-altitude facts (NO verdict) so the planner can SEE the
    # floor / distance-to-target / how long it has been frozen / what it has
    # already recombined, instead of re-deriving it from attempts/ each
    # cycle. Empty for verticals that do not surface it.
    search_altitude_block = prompt_context.search_altitude

    _vstage_order = list(prompt_context.stage_order)
    if workflow_mode == "direct":
        stage_checklist = ""
        stage_gate_block = (
            "## Current workflow stage\n"
            "## Direct workflow — objective first\n"
            f"`workflow_mode=direct`; `{stage}` is semantic context, not a mandatory "
            "artifact phase. Treat it as semantic context, not a hard gate. This "
            "overrides the generic instruction to work only the "
            "active stage. Delegate the implementation, experiment, or "
            "verification that directly advances the operator objective, and "
            "nothing beside it; the bound is on what you build, never on how "
            "much you measure. Do not create, "
            "repair, or certify stage bundles, frontier snapshots, pipeline state, "
            "checkpoints, reports, or setup documents unless the operator explicitly "
            "requested that artifact or it is strictly necessary to execute the work. "
            "Use existing process artifacts as optional evidence; their absence must not "
            "displace substantive work."
        )
    else:
        stage_gate_block = (
            "## Current workflow stage\n"
            f"- current: `{stage}`\n"
            f"- sequence: {', '.join(_vstage_order) or '(none)'}\n"
            "Treat the stage as semantic context, not a hard gate. Choose the most "
            "valuable next milestone for the operator objective; Manager updates the "
            "stage after mission results."
        )

    # The Planner gets the same library paths as other roles and searches them
    # independently. No Skill content is selected or copied into this prompt.
    matched_planner_skill_block = ""
    if mission is not None:
        planner_libraries = mission.libraries()
        if planner_libraries.block:
            matched_planner_skill_block = planner_libraries.block + "\n\n"

    # ------------------------------------------------------------------
    # Shared declarative knowledge. Planner may maintain pages directly; task
    # history stays in events/handoffs and is intentionally not duplicated here.
    # ------------------------------------------------------------------
    wiki_block = ""
    autors_root = _workspace / ".autors"
    wiki_candidates = sorted(autors_root.glob("*/wiki")) if autors_root.exists() else []
    wiki_candidates = [
        wiki
        for wiki in wiki_candidates
        if (wiki / "INDEX.md").is_file() and (wiki / "pages").is_dir()
    ]
    if wiki_candidates:
        paths = "\n".join(f"- `{wiki.resolve()}`" for wiki in wiki_candidates)
        wiki_block = (
            "## Shared project Wiki\n"
            "Search these Wiki directories with your own file tools:\n"
            f"{paths}\n\n"
            "Start at INDEX.md and progressively read semantic pages as needed. "
            "Pages contain only title, description, and Markdown content. Edit "
            "pages and INDEX.md directly when planning establishes durable "
            "declarative knowledge; do not copy task history or procedures.\n"
        )

    host_policy_block = (
        "## Dynamic host policy\n"
        "- Planner owns task selection, decomposition, and impact priority. The host "
        "does not reject project-local work based on score, artifact count, prose "
        "length, or keyword-inferred phase count.\n"
        "- A reversible project-local archive/quarantine with provenance is "
        "ordinary Engineer work, not an external operator dependency. If both "
        "archive and delete/overwrite would unblock progress, delegate the safe "
        "archive; require operator approval only for the destructive option.\n"
        "- Once the plan is clear, explain it briefly and end with the actionable "
        "footer.\n\n"
    )

    objective_contract_block = (
        "## Immutable objective acceptance contract\n"
        "The operator's hard success criteria and explicit non-qualifying "
        "outcomes are acceptance constraints, not an optimization hint. The "
        "current-stage gate controls ordering but never lowers those criteria. "
        "Do not perform work whose acceptance can be satisfied entirely "
        "by an outcome the operator says does not count. Supporting searches, "
        "probes, computation, and literature work may be internal steps inside "
        "a qualifying implementation; they are not a successful outcome by "
        "themselves.\n\n"
    )
    # The block above states that the operator's hard criteria are binding, but
    # until the goal contract existed it never named any: the Planner was told
    # to honour constraints it was never shown. This adds the ones the Manager
    # recorded from what the operator actually said, and stays empty when there
    # are none rather than printing a heading with no rows.
    from ...core.project_contract import contract_briefing, load_contract_for_cwd

    goal_contract_block = contract_briefing(
        load_contract_for_cwd(_workspace),
        authoritative_objective=continuous_objective,
    )
    if goal_contract_block:
        objective_contract_block += goal_contract_block + "\n\n"

    external_target_block = ""
    if os.environ.get("ARGUS_SKILL_EXTERNAL_COMPLETION_GATE", "").strip():
        external_target_block = _EXTERNAL_TARGET_CONTRACT

    final_submission_scope_block = ""
    final_submission_scope_applies = (
        prompt_context.completion_gate == "certified"
        or _research_target_level is not None
    )
    if stage == "submission" and final_submission_scope_applies:
        final_submission_scope_block = (
            "## Final-submission task scope\n"
            "For a research submission or final independent certification task, "
            "the Planner structured task must emit `scope:\"final_submission\"` "
            "(legacy key-value: `TASK_SCOPE=final_submission`) so the successful "
            "Reviewer verdict can satisfy the final gate. Use `scope:\"bounded\"` "
            "for ordinary prerequisite work, and do not use final_submission for "
            "verticals without a final-submission or research-target gate."
        )

    planner_hygiene_block = (
        "## Runtime hygiene\n"
        "Use active project files, project-local skills, and "
        "`python -m argus_skill ...` or `ARGUS_SKILL_PYTHON`; do not copy stale "
        "host paths from history."
    )
    # Compile from structured state only: vertical/stage, target contract,
    # open-ended mode and available semantic libraries. Do not keyword-route
    # task prose to decide which policy fragments the Planner receives.
    return _join_prompt_blocks(
        ground_truth_mandate(
            "planner",
            workflow_mode=resolve_evidence_mode(_proot),
        ),
        optimize_banner,
        research_target_block,
        standing_continuous_block,
        _PLANNER_CORE_CONTRACT,
        native_shell_summary(),
        host_policy_block,
        objective_contract_block,
        external_target_block,
        final_submission_scope_block,
        stage_checklist,
        stage_gate_block,
        matched_planner_skill_block,
        wiki_block,
        search_altitude_block,
        "## Manager mission brief (authoritative)\n" + continuous_objective.strip(),
        "## Journal of completed work (most recent last)\n"
        + (journal_tail.strip() or "(no completed work yet — this is the first cycle)"),
        _RESEARCH_PLAN_CONTRACT
        + (
            research_plan.strip()
            or "(no plan yet — create RESEARCH_PLAN.md in this planning cycle)"
        ),
        "## Current reality (authoritative over the journal above)\n"
        + (runtime_change_summary.strip() or "(no additional runtime context)"),
        planner_hygiene_block,
        cycle_line,
        "Use only the focused read/search budget above, delegate the next concrete "
        "work or report a real "
        "blocker, then end with the Planner decision footer.",
    )


def build_continuous_resume_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    research_plan: str = "",
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
) -> str:
    """Render only the changing Planner delta for a resumable role session.

    The prior same-role turn already contains the immutable Planner contract,
    vertical policy, and tool boundary.  Repeating that large preamble on every
    cycle defeats provider prompt caching; this delta still carries the current
    stage/checklist, durable objective, journal, and fresh runtime facts.
    """
    from ...core.project import resolve_project_root
    from .registry import resolve_role_prompt

    workspace = resolve_project_root(project_root)
    state = resolve_project_root(state_root) if state_root is not None else workspace
    prompt_context = resolve_role_prompt(
        continuous_request(state, altitude_root=workspace)
    )
    skill_block = ""
    if mission is not None:
        try:
            libraries = mission.libraries()
            skill_block = str(getattr(libraries, "block", "") or "")
        except Exception:  # noqa: BLE001 - a resume delta must remain available
            skill_block = ""
    return _join_prompt_blocks(
        "## Continued Planner cycle\n"
        "You are resuming your own bounded Planner session. The original role "
        "contract remains binding; do not replay old exploration or re-author "
        "the static policy. Current state below supersedes stale session facts.",
        str(prompt_context.role_banner or ""),
        "## Current workflow stage\n"
        f"- current: `{prompt_context.stage}`\n"
        f"- sequence: {', '.join(prompt_context.stage_order) or '(none)'}\n"
        + str(prompt_context.stage_checklist or ""),
        skill_block,
        # Live vertical facts change between cycles, which is exactly what a
        # resume delta is for — the header above already promises that current
        # state supersedes stale session facts. Omitting them meant a resumed
        # Planner never saw its vertical's altitude at all: the search floor and
        # frozen count for a metric campaign, or the accepted papers pulled to
        # disk for a paper campaign. Each vertical still renders only its own.
        str(prompt_context.search_altitude or ""),
        "## Manager mission brief (authoritative)\n" + continuous_objective.strip(),
        "## Journal of completed work (most recent last)\n"
        + (journal_tail.strip() or "(no completed work yet — this is the first cycle)"),
        _RESEARCH_PLAN_CONTRACT
        + (
            research_plan.strip()
            or "(no plan yet — create RESEARCH_PLAN.md in this planning cycle)"
        ),
        "## Current reality (authoritative over the journal above)\n"
        + (runtime_change_summary.strip() or "(no additional runtime context)"),
        f"This is planning cycle #{planning_cycle + 1}.",
        "Inspect only what is needed to choose the next concrete task or a real "
        "blocker, then end with the Planner decision footer.",
    )


__all__ = [
    "BOUNDED_DAG",
    "CONTINUOUS",
    "OPERATIONS",
    "PARALLEL_DRAFT",
    "PLAN_PREVIEW",
    "build_bounded_dag_prompt",
    "build_continuous_prompt",
    "build_continuous_resume_prompt",
    "continuous_request",
    "preview_request",
]
