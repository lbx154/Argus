"""``LifeSupervisor`` — owns the outer process, runs missions back-to-back.

Per the rubber-duck critique:

- Supervisor (not observer): we OWN the outer loop and call
  ``MissionExecutor.execute(...)`` once per backlog item. We never try
  to push ``/run`` into a finished single-mission daemon.
- Single inbox owner: we don't tail any JsonlCommandBus. The optional
  ``user_inbox`` callable lets a host process feed user-provided
  high-priority objectives into the supervisor's own queue without two
  consumers racing on the same offset file.
- Bounded autonomy: ``LifeBudget`` enforces a per-mission preflight cap
  AND a daily cap. Defaults are generous enough for long polish runs
  (max 6 autonomous missions in one supervisor run, $30/mission,
  $180/day).
- Memory injection is a separate channel (``prelude_context``) — the
  objective string passed to the executor is unmodified, so skill
  matching, mission-id hashing, and reviewer prompts are unaffected.
- Idle = sleep, not spin. We poll every 5 seconds when there's nothing
  to do.

The supervisor is intentionally **synchronous**: one mission at a
time, no thread pool. That matches "an agent with continuity" — the
agent is doing one thing, then the next, like a person.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from ..core.ports import EventSink
from ..core.pricing import price_for, usd_for_tokens
from .memory import (
    BacklogItem,
    Journal,
    JournalEntry,
)

log = logging.getLogger(__name__)

_price_for = price_for


class _MemoryView(Protocol):
    @property
    def backlog(self) -> Any: ...

    @property
    def journal(self) -> Any: ...

    def render_prelude(self, *, objective: str) -> str: ...


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@dataclass
class LifeBudget:
    """Layered cost / iteration limits.

    Enforcement points:

    1. **Preflight per-mission cap**: before starting a backlog item we
       refuse if ``item.max_cost_usd > per_mission_cap_usd`` OR if
       ``daily_remaining < per_mission_cap_usd``. Either condition
       pauses the supervisor with a journal entry — we do not silently
       trim caps.
    2. **Daily cap**: cumulative ``cost_usd`` from journal entries
       whose timestamp is ≥ start-of-current-day-local. The supervisor
       refreshes this number on each loop tick so a long-running
       supervisor honours UTC day rollover.
    3. **Iteration cap**: hard count of autonomous missions completed
       in this supervisor run (NOT cumulative across restarts). Once
       reached, supervisor exits cleanly even if backlog is non-empty.

    Field semantics:

    - ``per_mission_cap_usd``: the highest a single mission is allowed
      to cost (sum of engineer + reviewer + scientist tokens × prices).
    - ``daily_cap_usd``: ceiling on summed cost of mission entries in
      ``journal.jsonl`` whose timestamp falls in the current local day.
    - ``max_missions``: hard cap on missions run by THIS supervisor
      process (resets per ``LifeSupervisor`` instance).
    """

    per_mission_cap_usd: float = 30.0
    daily_cap_usd: float = 180.0
    max_missions: int = 6

    def remaining_today(self, journal: Journal, *, now: float | None = None) -> float:
        """USD remaining in today's budget."""
        now = now if now is not None else time.time()
        # Local day start.
        local = time.localtime(now)
        day_start = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        spent = journal.total_cost_since(day_start)
        return max(0.0, float(self.daily_cap_usd) - float(spent))

    def can_start(
        self,
        *,
        item: BacklogItem,
        journal: Journal,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Return ``(allowed, reason)``. ``reason`` is empty when allowed.

        We do NOT refuse a mission just because ``item.max_cost_usd``
        exceeds ``per_mission_cap_usd`` — that's a daemon-vs-item
        misconfiguration and a 7×24 product should keep working. The
        per-mission cap is enforced inside the supervisor by clamping
        the effective per-mission budget (see
        ``LifeSupervisor._effective_per_mission_cap``); this method
        only blocks on the *daily* budget envelope, which is the real
        bottom line.
        """
        remain = self.remaining_today(journal, now=now)
        # Use the smaller of (operator-requested mission budget, our
        # per-mission cap) when comparing to daily remaining — same
        # number the supervisor will actually permit.
        effective_cap = min(item.max_cost_usd, self.per_mission_cap_usd)
        if remain < effective_cap:
            return False, (
                f"daily budget remaining ${remain:.2f} < "
                f"effective mission cap ${effective_cap:.2f}"
            )
        return True, ""


# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------

def _normalize_planner_text(text: str) -> str:
    """Normalize planner task text for duplicate detection."""
    normalized = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _sanitize_planner_task_text(text: str) -> str:
    """Remove stale host-specific entry paths from planner-generated missions."""
    value = str(text)
    command_replacements = {
        (
            "PYTHONPATH=/home/argustest/argus-skill "
            "/home/argustest/miniconda3/bin/python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
        (
            "PYTHONPATH=/home/argustest/argus-skill "
            "python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
        (
            "/home/argustest/miniconda3/bin/python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
    }
    for old, new in command_replacements.items():
        value = value.replace(old, new)
    path_replacements = {
        "`/home/argustest/research.md`": "the operator-provided research playbook if present",
        "/home/argustest/research.md": "the operator-provided research playbook if present",
        "`/home/argustest/argus-skill`": "the active Argus source/package",
        "/home/argustest/argus-skill": "the active Argus source/package",
        (
            "`/root/Auto-claude-code-research-in-sleep/skills/"
            "paper-illustration-image2/SKILL.md`"
        ): "`argus_builtin_skills/paper-illustration-image2.md`",
        (
            "/root/Auto-claude-code-research-in-sleep/skills/"
            "paper-illustration-image2/SKILL.md"
        ): "argus_builtin_skills/paper-illustration-image2.md",
    }
    for old, new in path_replacements.items():
        value = value.replace(old, new)
    return value


def _planner_task_signature(title: str, objective: str) -> tuple[str, str]:
    return (_normalize_planner_text(title), _normalize_planner_text(objective))


_PLANNER_DEDUP_STATUSES = {"pending", "running", "done"}
_PLANNER_RECENT_HISTORY_WINDOW = 20
_PLANNER_RECENT_FAILURE_STATUS = "no_progress"
_FOLLOWUP_CRITIC_MIN_IMPACT_SCORE = 5
_PLANNER_SCOPE_BOUNDED = "bounded"
_PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"
_FULL_EMNLP_GATE_COMMAND = (
    "python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root ."
)
_FULL_SCALE_EVIDENCE_GATE_COMMAND = (
    "python -m argus_skill.skills.pipeline_contracts "
    "validate-full-scale-evidence --project-root ."
)
_REFRESH_MANIFEST_COMMAND = (
    "python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root ."
)
_REFRESH_ARTIFACT_FRESHNESS_COMMAND = (
    "python -m argus_skill.skills.pipeline_contracts "
    "refresh-artifact-freshness --project-root ."
)
_WRITE_VALIDATION_PRIORITY_POLICY_COMMAND = (
    "python -m argus_skill.skills.pipeline_contracts "
    "write-validation-priority-policy --project-root ."
)
_REPAIR_EMNLP_CONTRACT_ARTIFACTS_COMMAND = (
    "python -m argus_skill.skills.pipeline_contracts "
    "repair-emnlp-contract-artifacts --project-root ."
)
_PLANNER_GATE_CONTEXT_MAX_ISSUES = 24
_PLANNER_GATE_CONTEXT_MAX_CHARS = 6000
_EMNLP_BOOTSTRAP_GATE_CODES = {
    "missing_pipeline_state",
    "missing_literature_grounding",
    "missing_idea_provenance",
    "missing_code_reuse_plan",
}
_EMNLP_FULL_SCALE_GATE_CODES = {
    "missing_full_scale_experiment_run",
    "missing_baseline_condition_run",
    "incomplete_full_scale_experiment_run",
    "underpowered_pilot",
    "pilot_pdf_without_full_scale_evidence",
}
_EMNLP_DOWNSTREAM_PACKAGE_CODES = {
    "missing_stage_artifact",
    "missing_submission_assurance",
    "missing_style_exemplar",
    "missing_paper_draft_report_json",
    "missing_layout_review",
    "missing_academic_language_review",
    "missing_claim_graph",
    "missing_image2_figures_manifest",
    "missing_figure_table_style_guide",
}
_EMNLP_MANIFEST_FRESHNESS_GATE_CODES = {
    "artifact_stale_vs_inputs",
    "artifact_modified_after_freshness_recorded",
    "artifact_digest_mismatch",
    "artifact_freshness_missing_required_input",
    "missing_required_artifact_freshness_record",
    "unknown_generated_artifact_source",
    "generated_artifact_without_canonical_source",
    "generated_artifact_missing_sources",
    "invalid_artifact_manifest_entry",
}
_EMNLP_CITATION_GATE_CODES = {
    "appendix_before_references",
    "claim_graph_uncited_citation_key",
    "citation_command_dumping",
    "citation_paragraph_dumping",
    "insufficient_rendered_reference_pages",
    "insufficient_unique_citations",
    "insufficient_verified_bibliography_entries",
    "missing_bib_source",
    "missing_bibliography_command",
    "pdf_unresolved_reference_marker",
    "placeholder_bibtex_author_others",
    "rendered_placeholder_reference_authors",
    "unresolved_latex_references",
    "unverified_bib_entry",
}
_EMNLP_VALIDATION_POLICY_GATE_CODES = {
    "missing_validation_priority_policy",
    "invalid_validation_priority_policy_json",
    "invalid_validation_priority_policy_schema_version",
    "missing_validation_priority_order",
    "incomplete_validation_priority_order",
    "missing_validation_failure_routing",
    "missing_validation_failure_route",
    "validation_failure_route_missing_prefixes",
    "validation_failure_route_missing_repair_mode",
    "validation_failure_route_bad_repair_mode",
    "missing_validation_reset_policy",
}
_EMNLP_IMAGE2_GATE_CODES = {
    "conceptual_body_figure_not_image2",
    "image2_conceptual_figure_not_included_in_main_tex",
    "missing_image2_conceptual_figure",
    "missing_image2_figures_manifest",
    "missing_image2_generation_provenance",
    "missing_image2_inspect_path",
    "missing_image2_review_path",
    "missing_image2_sidecar_path",
    "mismatched_image2_sidecar_prompt_sha256",
}
_EMNLP_REVIEW_GATE_CODES = {
    "academic_language_review_not_pass",
    "academic_language_review_needs_revision",
    "academic_language_review_has_blockers",
    "failed_academic_language_required_check",
    "low_academic_language_review_score",
    "low_academic_language_section_score",
    "missing_academic_language_review",
    "stale_academic_language_review_source",
    "academic_language_evidence_quote_not_found",
    "academic_language_evidence_boilerplate_quote",
    "pass_academic_language_review_with_model_revise",
    "pass_academic_language_review_with_model_blocking_issues",
    "pass_academic_language_review_with_model_major_issues",
    "pass_academic_language_review_with_model_revision_directives",
    "pass_academic_language_review_with_model_failed_checks",
    "pass_academic_language_review_with_model_low_sections",
    "layout_review_not_pass",
    "layout_review_needs_revision",
    "layout_review_has_blockers",
    "layout_review_not_visual",
    "low_layout_review_score",
    "missing_layout_review",
    "missing_layout_review_vision_payload",
    "stale_layout_review_artifact",
    "incomplete_layout_review_snapshot_coverage",
    "pass_layout_review_with_vision_revise",
    "pass_layout_review_with_vision_blocking_issues",
    "pass_layout_review_with_vision_major_issues",
    "pass_layout_review_with_vision_revision_directives",
    "pass_layout_review_with_vision_low_criteria",
}
_EMNLP_FIGURE_TABLE_FORMAT_CODES = {
    "body_figure_not_referenced",
    "body_float_missing_from_style_guide",
    "figure_table_style_guide_not_pass",
    "float_inventory_label_not_in_body",
    "missing_figure_table_style_guide",
    "missing_figure_table_style_guide_rule",
    "missing_float_inventory_target_section",
    "missing_paired_significance_table",
    "missing_research_md_table_style",
    "severe_overfull_hbox",
    "table_caption_missing_number",
    "too_few_figure_table_style_floats",
}
_EMNLP_CONTENT_SUFFICIENCY_CODES = {
    "missing_main_content_pages",
    "appendix_before_page_9",
    "references_before_full_body",
    "rendered_main_body_underfilled",
    "underlength_emnlp_paper",
}
_EMNLP_BODY_OVER_BUDGET_CODES = {
    "conclusion_after_page_8",
    "overlength_emnlp_paper",
}
_EMNLP_SUBMISSION_ASSURANCE_CODES = {
    "draft_not_submission_quality",
    "draft_self_reports_not_submission_quality",
    "missing_submission_assurance",
    "submission_not_ready_verdict",
    "submission_stage_not_successful",
}
_EMNLP_CITATION_GATE_PREFIXES = (
    "bibliography_",
    "bibtex_",
    "citation_",
    "reference_",
    "rendered_reference_",
    "unresolved_citation",
)
_EMNLP_IMAGE2_GATE_PREFIXES = ("image2_",)
_EMNLP_FIGURE_TABLE_FORMAT_PREFIXES = (
    "body_figure_",
    "body_float_",
    "float_",
    "invalid_float_",
    "missing_float_",
    "table_",
)
_EMNLP_REVIEW_GATE_PREFIXES = (
    "academic_",
    "failed_academic_",
    "layout_",
    "low_academic_",
    "low_layout_",
    "stale_academic_",
    "stale_layout_",
)
_EMNLP_MANIFEST_FRESHNESS_GATE_PREFIXES = (
    "artifact_",
    "freshness_",
    "generated_artifact_",
    "invalid_artifact_",
    "manifest_",
)
_EMNLP_DOWNSTREAM_PATH_PREFIXES = (
    "paper/",
    "research/NARRATIVE_REPORT",
    "research/CLAIM",
    "research/result",
    "research/results",
)
_EMNLP_DOWNSTREAM_PATH_NAMES = {
    "paper/main.tex",
    "paper/main.pdf",
    "paper/RESULTS_REPORT.md",
    "paper/SUBMISSION_ASSURANCE.json",
    "paper/CLAIM_GRAPH.json",
    "paper/ARTIFACT_MANIFEST.json",
    "paper/ARTIFACT_FRESHNESS.json",
}
_OPEN_ENDED_OBJECTIVE_MARKERS = (
    "open-ended",
    "self-improvement",
    "ongoing",
    "always-on",
    "always on",
    "7x24",
    "7×24",
    "24/7",
    "perpetual",
    "never-ending",
    "never ending",
)
_PAPER_LONG_HORIZON_OBJECTIVE_SUBSTRINGS = (
    "academic paper",
    "camera-ready",
    "citation",
    "latex",
    "long paper",
    "main.pdf",
    "main.tex",
    "make_paper",
    "manuscript",
    "paper/",
    "paper draft",
    "publication-ready",
    "submission-ready",
    "validate-full-emnlp",
    "正文",
    "论文",
    "投稿",
)
_PAPER_LONG_HORIZON_OBJECTIVE_WORDS = {"acl", "emnlp"}


@dataclass(frozen=True)
class _EmnlpFinalizationRepairTask:
    """A deterministic planner fallback for the final 30% paper repair loop."""

    title: str
    impact_area: str
    target_label: str
    target_issues: tuple[Any, ...]
    skill_files: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    narrow_commands: tuple[str, ...]
    repair_focus: str


def _objective_is_open_ended(objective: str) -> bool:
    normalized = _normalize_planner_text(objective)
    return any(marker in normalized for marker in _OPEN_ENDED_OBJECTIVE_MARKERS)


def _objective_requires_full_emnlp_gate(objective: str) -> bool:
    raw = str(objective or "").casefold()
    normalized = _normalize_planner_text(objective)
    venue_marker = "emnlp" in normalized or "acl" in normalized
    final_marker = any(
        marker in raw
        for marker in (
            "submission",
            "submit",
            "submission-ready",
            "camera-ready",
            "paper",
            "long paper",
            "投稿",
            "论文",
            "投递",
        )
    )
    return venue_marker and final_marker


def _objective_is_paper_long_horizon(objective: str) -> bool:
    normalized = _normalize_planner_text(objective)
    if any(marker in normalized for marker in _PAPER_LONG_HORIZON_OBJECTIVE_SUBSTRINGS):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    return bool(tokens & _PAPER_LONG_HORIZON_OBJECTIVE_WORDS)


def _emnlp_issue_paths(issues: list[Any]) -> set[str]:
    return {str(getattr(issue, "path", "") or "") for issue in issues}


def _has_emnlp_downstream_package_gap(
    issue_codes: set[str],
    issue_paths: set[str],
) -> bool:
    if issue_codes & _EMNLP_DOWNSTREAM_PACKAGE_CODES:
        return True
    return any(
        path in _EMNLP_DOWNSTREAM_PATH_NAMES
        or path.startswith(_EMNLP_DOWNSTREAM_PATH_PREFIXES)
        for path in issue_paths
    )


def _planner_emnlp_stage_hints(issues: list[Any]) -> str:
    """Return compact stage-routing hints for final-gate issue snapshots."""
    if not issues:
        return ""
    issue_codes = {str(getattr(issue, "code", "") or "") for issue in issues}
    issue_paths = _emnlp_issue_paths(issues)
    hints: list[str] = []

    if issue_codes & _EMNLP_BOOTSTRAP_GATE_CODES:
        hints.append(
            "- stage route: bootstrap literature grounding, idea provenance, "
            "code-reuse plan, and PIPELINE_STATE before benchmark execution or paper polish."
        )
    if issue_codes & _EMNLP_FULL_SCALE_GATE_CODES:
        hints.append(
            "- stage route: complete or collect the full-scale evidence matrix before "
            "analysis, narrative, drafting, reviews, or submission assurance."
        )
    elif _has_emnlp_downstream_package_gap(issue_codes, issue_paths):
        hints.append(
            "- stage route: full-scale evidence is not currently a final-gate blocker; "
            "collect completed runs, then build analysis/narrative/draft/review/"
            "submission artifacts from current evidence instead of relaunching duplicate benchmarks."
        )
    if issue_codes & _EMNLP_MANIFEST_FRESHNESS_GATE_CODES:
        hints.append(
            "- stage route: regenerate stale generated artifacts from current upstream "
            "inputs or refresh manifest/freshness records with canonical source links; "
            f"after regeneration, run `{_REFRESH_MANIFEST_COMMAND}` and "
            f"`{_REFRESH_ARTIFACT_FRESHNESS_COMMAND}`; do not hand-edit only "
            "the readiness JSON."
        )
    if issue_codes & _EMNLP_VALIDATION_POLICY_GATE_CODES:
        hints.append(
            "- stage route: rebuild the failure-routing contract with "
            f"`{_WRITE_VALIDATION_PRIORITY_POLICY_COMMAND}` before final "
            "review/assurance loops; do not hand-write partial routes."
        )
    if (
        issue_codes & _EMNLP_MANIFEST_FRESHNESS_GATE_CODES
        and issue_codes & _EMNLP_VALIDATION_POLICY_GATE_CODES
    ):
        hints.append(
            "- stage route: when manifest, freshness, and validation-route drift appear "
            "together after content regeneration, prefer the combined repair helper "
            f"`{_REPAIR_EMNLP_CONTRACT_ARTIFACTS_COMMAND}`; then inspect remaining "
            "content/evidence issues as real blockers."
        )
    if issue_codes & _EMNLP_CITATION_GATE_CODES:
        hints.append(
            "- stage route: repair bibliography sources and rendered citation placement "
            "in the drafting/format-preflight skills before final assurance."
        )
    if issue_codes & _EMNLP_IMAGE2_GATE_CODES:
        hints.append(
            "- stage route: image-2 issues belong to results-analysis/figures; use the "
            "Argus image tool, keep the exact accepted raster in main.tex, and repair "
            "prompt/output/sidecar/inspect/review/provenance hashes instead of "
            "wrapping a local redraw in image-2 metadata."
        )
    if issue_codes & _EMNLP_FIGURE_TABLE_FORMAT_CODES:
        hints.append(
            "- stage route: figure/table/format failures belong to format preflight and "
            "drafting; every body float needs a style-guide entry, target section, "
            "text reference, readable placement, and a caption with a numerical or "
            "evidence-backed takeaway."
        )
    if issue_codes & _EMNLP_BODY_OVER_BUDGET_CODES:
        hints.append(
            "- stage route: overlength body or Conclusion-after-page-8 failures are "
            "page-budget reflow blockers; stop adding prose, inspect the rendered PDF "
            "page map, and compress/move duplicated body or appendix-like material "
            "until Conclusion lands on page 8 and References/Appendix begin on page 9."
        )
    if issue_codes & _EMNLP_CONTENT_SUFFICIENCY_CODES:
        hints.append(
            "- stage route: treat short or underfilled PDFs as evidence/analysis/structure "
            "blockers first; add supported analyses, failure studies, or claim downgrades "
            "before cosmetic layout edits."
        )
    if issue_codes & _EMNLP_REVIEW_GATE_CODES:
        hints.append(
            "- stage route: academic-language and visual-layout review issues are "
            "downstream; stabilize main.tex/main.pdf first, then rerun the model-backed "
            "`academic_language_review` and vision `paper_layout_review` tools. Do not "
            "hand-edit stale review JSON to PASS."
        )
    if issue_codes & _EMNLP_SUBMISSION_ASSURANCE_CODES:
        hints.append(
            "- stage route: submission assurance is last; write PASS/WARN only after "
            "evidence, paper format, claim graph, image-2, academic-language review, "
            "layout review, manifest, freshness, and final gate blockers are genuinely "
            "cleared."
        )

    if not hints:
        return ""
    return "Automatic stage route hints:\n" + "\n".join(hints)


def _emnlp_issue_matches(
    issue: Any,
    *,
    codes: set[str] | frozenset[str] = frozenset(),
    prefixes: tuple[str, ...] = (),
    contains: tuple[str, ...] = (),
) -> bool:
    code = str(getattr(issue, "code", "") or "")
    return (
        code in codes
        or any(code.startswith(prefix) for prefix in prefixes)
        or any(token in code for token in contains)
    )


def _emnlp_matching_issues(
    issues: list[Any],
    *,
    codes: set[str] | frozenset[str] = frozenset(),
    prefixes: tuple[str, ...] = (),
    contains: tuple[str, ...] = (),
) -> tuple[Any, ...]:
    return tuple(
        issue
        for issue in issues
        if _emnlp_issue_matches(
            issue,
            codes=codes,
            prefixes=prefixes,
            contains=contains,
        )
    )


def _emnlp_issue_summary(issues: list[Any] | tuple[Any, ...], *, limit: int = 10) -> str:
    snippets: list[str] = []
    for issue in issues[:limit]:
        snippets.append(
            f"{getattr(issue, 'code', '')} at {getattr(issue, 'path', '')}"
        )
    if len(issues) > limit:
        snippets.append(f"... {len(issues) - limit} more")
    return "; ".join(snippets)


def _emnlp_top_issue_counts(issues: list[Any] | tuple[Any, ...], *, limit: int = 8) -> str:
    counts = Counter(str(getattr(issue, "code", "") or "") for issue in issues)
    return ", ".join(f"{code}={count}" for code, count in counts.most_common(limit))


def _select_emnlp_finalization_repair_task(
    issues: list[Any],
) -> _EmnlpFinalizationRepairTask | None:
    """Select the next narrow repair lane from a failing EMNLP final gate.

    This is deliberately deterministic. The planner is still free to produce
    richer work, but when it refuses, emits broad JSON, or keeps asking for
    "make the paper good", the supervisor can hand the engineer a bounded
    finalization lane with concrete issue codes and validators.
    """
    if not issues:
        return None

    bootstrap = _emnlp_matching_issues(issues, codes=_EMNLP_BOOTSTRAP_GATE_CODES)
    if bootstrap:
        return _EmnlpFinalizationRepairTask(
            title="Bootstrap the grounded EMNLP research pipeline",
            impact_area="discovery",
            target_label="bootstrap grounding and provenance",
            target_issues=bootstrap,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "auto-research-pipeline.md",
                "research-brief-to-experiment-plan.md",
            ),
            allowed_paths=(
                "research/",
                "benchmarks/",
                "experiments/",
                "code/",
                "paper/VALIDATION_PRIORITY_POLICY.json",
            ),
            narrow_commands=(
                "python -m argus_skill.skills.pipeline_contracts validate-grounding --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-idea-provenance --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-code-reuse --project-root .",
            ),
            repair_focus=(
                "Create or repair the literature-grounded brief, idea provenance, "
                "code-reuse plan, benchmark provenance, and PIPELINE_STATE before "
                "paper drafting. Do not polish paper/main.tex while bootstrap "
                "blockers remain."
            ),
        )

    full_scale = _emnlp_matching_issues(issues, codes=_EMNLP_FULL_SCALE_GATE_CODES)
    if full_scale:
        return _EmnlpFinalizationRepairTask(
            title="Complete the full-scale EMNLP evidence gate",
            impact_area="requirement_gap",
            target_label="full-scale experiment evidence",
            target_issues=full_scale,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "agent-research-benchmark-runner.md",
                "research-results-analysis-and-figures.md",
            ),
            allowed_paths=("experiments/", "benchmarks/", "research/", "code/", "paper/RESULTS_REPORT.md"),
            narrow_commands=(
                _FULL_SCALE_EVIDENCE_GATE_COMMAND,
                "python -m argus_skill.skills.pipeline_contracts validate-claim-graph --project-root .",
            ),
            repair_focus=(
                "Run or collect the required non-pilot benchmark rows for every "
                "method/baseline condition. Only after this gate passes should the "
                "paper claim final EMNLP-ready results."
            ),
        )

    over_budget = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_BODY_OVER_BUDGET_CODES,
        contains=("overlength", "page budget", "page 8"),
    )
    if over_budget:
        return _EmnlpFinalizationRepairTask(
            title="Rebalance EMNLP body page budget",
            impact_area="requirement_gap",
            target_label="body overlength, conclusion placement, and page-budget reflow",
            target_issues=over_budget,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "emnlp-paper-drafting.md",
                "emnlp-format-preflight.md",
                "paper-review-revision-loop.md",
            ),
            allowed_paths=(
                "paper/main.tex",
                "paper/main.pdf",
                "paper/PAGE_BUDGET.md",
                "paper/PAPER_DRAFT_REPORT.json",
                "paper/PAPER_QUALITY_CALIBRATION.json",
                "paper/FORMAT_PREFLIGHT.md",
                "paper/FIGURE_TABLE_STYLE_GUIDE.json",
                "code/",
            ),
            narrow_commands=(
                "python -m argus_skill.skills.pipeline_contracts validate-paper-contract --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-paper-format --project-root .",
            ),
            repair_focus=(
                "The paper is over the eight-page main-body budget, so do not keep "
                "expanding prose. Rebuild or compile, inspect the rendered page map, "
                "then trim, merge, or relocate duplicated body paragraphs, oversized "
                "tables, and redundant review/appendix-like material until the main "
                "Conclusion appears on page 8 and References/Appendix begin on page "
                "9 or later. Preserve the normal-paper floors for Abstract and "
                "Introduction, keep result numbers tied to their exact benchmark/"
                "method rows, and do not introduce new paragraphs unless they replace "
                "missing evidence-backed content. References and appendices still "
                "have no total-page maximum after the body boundary."
            ),
        )

    content = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_CONTENT_SUFFICIENCY_CODES | _EMNLP_SUBMISSION_ASSURANCE_CODES,
        prefixes=("rendered_pdf_",),
        contains=("underfilled", "underlength", "content_pages"),
    )
    if content:
        return _EmnlpFinalizationRepairTask(
            title="Expand evidence-backed EMNLP content to final-paper length",
            impact_area="requirement_gap",
            target_label="content sufficiency, page budget, and draft readiness",
            target_issues=content,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "emnlp-paper-drafting.md",
                "research-results-analysis-and-figures.md",
                "claims-evidence-audit.md",
            ),
            allowed_paths=(
                "paper/main.tex",
                "paper/PAGE_BUDGET.md",
                "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md",
                "paper/RESULTS_REPORT.md",
                "paper/EVIDENCE_GAPS.json",
                "paper/CLAIM_GRAPH.json",
                "paper/PAPER_DRAFT_REPORT.json",
                "paper/PAPER_QUALITY_CALIBRATION.json",
                "research/",
                "experiments/",
                "benchmarks/",
                "code/",
            ),
            narrow_commands=(
                _FULL_SCALE_EVIDENCE_GATE_COMMAND,
                "python -m argus_skill.skills.pipeline_contracts validate-paper-contract --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-paper-quality-contracts --project-root .",
            ),
            repair_focus=(
                "Treat short pages and draft-not-ready verdicts as content/evidence "
                "sufficiency gaps, not cosmetic padding or float shuffling. Make a "
                "section-level expansion plan against the page budget; if evidence is "
                "thin, run the missing experiments, ablations, robustness slices, or "
                "failure studies first; if evidence exists, expand Introduction/Related "
                "Work, Method/Setup, Results/Analysis, and Failure Cases from those "
                "artifacts until references and appendix material start on page 9 or later. "
                "Do not impose any total-page maximum on references or appendices."
            ),
        )

    citations = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_CITATION_GATE_CODES,
        prefixes=_EMNLP_CITATION_GATE_PREFIXES,
        contains=("citation", "reference", "bibliograph", "bibtex"),
    )
    if citations:
        return _EmnlpFinalizationRepairTask(
            title="Repair verified EMNLP citations and references",
            impact_area="integration",
            target_label="citation, BibTeX, and rendered reference hygiene",
            target_issues=citations,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "emnlp-paper-drafting.md",
                "emnlp-format-preflight.md",
                "claims-evidence-audit.md",
            ),
            allowed_paths=("paper/main.tex", "paper/*.bib", "paper/CLAIM_GRAPH.json", "research.md"),
            narrow_commands=(
                "python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-claim-graph --project-root .",
            ),
            repair_focus=(
                "Use verified BibTeX metadata, distribute citations adjacent to "
                "the claims they support, and keep claim-graph citation keys in "
                "sync with paper/main.tex. Do not dump citations into one paragraph."
            ),
        )

    image2 = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_IMAGE2_GATE_CODES,
        prefixes=_EMNLP_IMAGE2_GATE_PREFIXES,
        contains=("image2", "image-2", "conceptual_figure"),
    )
    if image2:
        return _EmnlpFinalizationRepairTask(
            title="Repair the image-2 overview figure contract",
            impact_area="integration",
            target_label="image-2 conceptual figure provenance and inclusion",
            target_issues=image2,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "research-results-analysis-and-figures.md",
                "emnlp-paper-drafting.md",
            ),
            allowed_paths=("paper/figures/", "paper/main.tex", "code/generate_image_2.py"),
            narrow_commands=(
                "python -m argus_skill.skills.pipeline_contracts validate-image2-figures --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-figure-table-style --project-root .",
            ),
            repair_focus=(
                "Generate or select the accepted raster through image-2, include "
                "that exact output in main.tex, and repair prompt/sidecar/inspect/"
                "review/provenance hashes instead of relabeling a local redraw."
            ),
        )

    figure_format = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_FIGURE_TABLE_FORMAT_CODES,
        prefixes=_EMNLP_FIGURE_TABLE_FORMAT_PREFIXES,
        contains=("overfull", "caption", "float", "figure", "table"),
    )
    if figure_format:
        return _EmnlpFinalizationRepairTask(
            title="Repair EMNLP LaTeX floats, captions, and page layout",
            impact_area="integration",
            target_label="figure/table style, captions, labels, and overfull boxes",
            target_issues=figure_format,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "emnlp-format-preflight.md",
                "emnlp-paper-drafting.md",
                "research-results-analysis-and-figures.md",
            ),
            allowed_paths=(
                "paper/main.tex",
                "paper/FIGURE_TABLE_STYLE_GUIDE.json",
                "paper/figures/",
                "paper/tables/",
            ),
            narrow_commands=(
                "python -m argus_skill.skills.pipeline_contracts validate-figure-table-style --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .",
            ),
            repair_focus=(
                "Fix float inventory, text references, numerical captions, table "
                "widths, and overfull boxes without padding or weakening evidence. "
                "Compile the PDF after every layout-affecting change."
            ),
        )

    reviews = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_REVIEW_GATE_CODES,
        prefixes=_EMNLP_REVIEW_GATE_PREFIXES,
    )
    if reviews:
        return _EmnlpFinalizationRepairTask(
            title="Refresh EMNLP academic-language and visual-layout reviews",
            impact_area="integration",
            target_label="model academic-language review and vision layout review",
            target_issues=reviews,
            skill_files=(
                "emnlp-paper-skill-router.md",
                "emnlp-academic-language-review.md",
                "paper-review-revision-loop.md",
            ),
            allowed_paths=("paper/main.tex", "paper/main.pdf", "paper/ACADEMIC_LANGUAGE_REVIEW.json", "paper/LAYOUT_REVIEW.json"),
            narrow_commands=(
                "python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write",
                "python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write",
                "python -m argus_skill.skills.pipeline_contracts validate-academic-language-review --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-layout-review --project-root .",
            ),
            repair_focus=(
                "Stabilize main.tex/main.pdf first, then rerun the model-backed "
                "language reviewer and vision layout reviewer from current sources. "
                "Never hand-edit stale review JSON into PASS."
            ),
        )

    contract_artifacts = _emnlp_matching_issues(
        issues,
        codes=_EMNLP_MANIFEST_FRESHNESS_GATE_CODES | _EMNLP_VALIDATION_POLICY_GATE_CODES,
        prefixes=_EMNLP_MANIFEST_FRESHNESS_GATE_PREFIXES,
    )
    if contract_artifacts:
        return _EmnlpFinalizationRepairTask(
            title="Regenerate EMNLP manifest, freshness, and routing artifacts",
            impact_area="integration",
            target_label="manifest, freshness, and validation routing drift",
            target_issues=contract_artifacts,
            skill_files=("emnlp-paper-skill-router.md", "auto-research-pipeline.md"),
            allowed_paths=(
                "paper/ARTIFACT_MANIFEST.json",
                "paper/ARTIFACT_FRESHNESS.json",
                "paper/VALIDATION_PRIORITY_POLICY.json",
            ),
            narrow_commands=(
                _REPAIR_EMNLP_CONTRACT_ARTIFACTS_COMMAND,
                "python -m argus_skill.skills.pipeline_contracts validate-manifest --project-root .",
                "python -m argus_skill.skills.pipeline_contracts validate-validation-priority --project-root .",
            ),
            repair_focus=(
                "Regenerate machine contracts from current source artifacts instead "
                "of hand-editing readiness JSON. If content blockers remain after "
                "this quick repair, route to those real blockers next."
            ),
        )

    downstream = tuple(issues[: min(len(issues), 12)])
    return _EmnlpFinalizationRepairTask(
        title="Repair current EMNLP final-gate blockers",
        impact_area="requirement_gap",
        target_label="highest-priority remaining final-gate blockers",
        target_issues=downstream,
        skill_files=("emnlp-paper-skill-router.md", "emnlp-paper-drafting.md"),
        allowed_paths=("paper/", "research/", "experiments/", "benchmarks/", "code/"),
        narrow_commands=(_FULL_EMNLP_GATE_COMMAND,),
        repair_focus=(
            "Group the gate TSV by issue code, repair the earliest upstream blocker "
            "class first, and avoid whole-paper rewrites unless the selected issue "
            "class requires a structural reset."
        ),
    )


def _build_emnlp_finalization_objective(
    task: _EmnlpFinalizationRepairTask,
    *,
    planner_error: str = "",
    raw_text: str = "",
) -> str:
    target_codes = _emnlp_top_issue_counts(task.target_issues)
    target_summary = _emnlp_issue_summary(task.target_issues)
    skill_list = ", ".join(f"`argus_builtin_skills/{name}`" for name in task.skill_files)
    path_list = ", ".join(f"`{path}`" for path in task.allowed_paths)
    command_list = "; ".join(f"`{command}`" for command in task.narrow_commands)
    error_note = (
        f"Planner failure context: {planner_error}. "
        if planner_error
        else ""
    )
    raw_note = f" Planner raw output: {raw_text[:300]}" if raw_text else ""
    return (
        "paper_optimization_task. Use the automatic validate-full-emnlp "
        "finalization route, not a blind whole-paper rewrite or a cosmetic micro-patch. "
        f"{error_note}"
        f"Target repair lane: {task.target_label}. Target issue codes: {target_codes}. "
        f"First target issues: {target_summary}. "
        f"Read AGENTS.md and these routed skills first: {skill_list}. "
        f"Allowed primary edit surface for this mission: {path_list}; touch other files "
        "only when the target validator proves they are upstream inputs. "
        f"Repair focus: {task.repair_focus} "
        f"Start by running `{_FULL_EMNLP_GATE_COMMAND}` or inspecting its fresh TSV "
        "output, then repair only this target lane until its issue count decreases "
        "or the lane is genuinely blocked by missing upstream evidence. "
        f"Run the narrow validator(s): {command_list}. "
        f"After modifying generated paper artifacts, run `{_REFRESH_MANIFEST_COMMAND}` "
        f"and `{_REFRESH_ARTIFACT_FRESHNESS_COMMAND}` when applicable, then rerun "
        f"`{_FULL_EMNLP_GATE_COMMAND}`. "
        "Acceptance requires reporting the before/after count for the target issue "
        "codes and either clearing them or recording the exact upstream blocker; "
        "do not declare submission readiness unless the exact final gate exits 0."
        f"{raw_note}"
    )


def _emnlp_finalization_task_spec_from_issues(
    issues: list[Any],
    *,
    planner_error: str = "",
    raw_text: str = "",
) -> Any | None:
    selected = _select_emnlp_finalization_repair_task(issues)
    if selected is None:
        return None
    from ..critic import TaskSpec

    return TaskSpec(
        title=selected.title,
        objective=_build_emnlp_finalization_objective(
            selected,
            planner_error=planner_error,
            raw_text=raw_text,
        ),
        impact_score=5,
        impact_area=selected.impact_area,
        evidence=(
            f"automatic validate-full-emnlp finalization route selected "
            f"{selected.target_label}: {_emnlp_issue_summary(selected.target_issues, limit=8)}"
        ),
        scope=_PLANNER_SCOPE_BOUNDED,
    )


def _is_emnlp_finalization_objective(text: str) -> bool:
    normalized = _normalize_planner_text(text)
    return (
        "paper_optimization_task" in normalized
        and "validate-full-emnlp" in normalized
        and "target issue codes" in normalized
    )


def _planner_tasks_need_emnlp_finalization_override(
    tasks: list[Any],
    issues: list[Any],
) -> bool:
    if not tasks or not issues:
        return False
    selected = _select_emnlp_finalization_repair_task(issues)
    if selected is None:
        return False
    target_codes = {
        str(getattr(issue, "code", "") or "")
        for issue in selected.target_issues
    }
    combined = _normalize_planner_text(
        "\n".join(
            "\n".join((
                str(getattr(task, "title", "") or ""),
                str(getattr(task, "objective", "") or ""),
                str(getattr(task, "evidence", "") or ""),
            ))
            for task in tasks
        )
    )
    if "validate-full-emnlp" not in combined:
        return True
    if "target issue codes" in combined and any(code in combined for code in target_codes):
        return False
    if any(code in combined for code in target_codes):
        return False
    broad_markers = (
        "build the evidence-backed emnlp paper package",
        "repair current emnlp final-gate blockers",
        "make the paper",
        "polish the paper",
        "paper package",
    )
    return any(marker in combined for marker in broad_markers)


def _backlog_item_requires_full_scale_evidence_precondition(
    item: BacklogItem,
) -> bool:
    """Return true for downstream tasks explicitly gated on full-scale evidence.

    This intentionally looks for hard start preconditions, not ordinary
    acceptance criteria such as "run validate-full-scale-evidence before
    stopping." Evidence-building tasks must remain runnable while the gate is
    red.
    """
    text = _normalize_planner_text(f"{item.title}\n{item.objective}")
    if "validate-full-scale-evidence" not in text:
        return False
    start_gate = any(
        marker in text
        for marker in (
            "start only after",
            "only start after",
            "must start only after",
            "do not start until",
            "do not begin until",
            "wait until",
        )
    )
    success_gate = any(
        marker in text
        for marker in (
            "exits 0",
            "exit 0",
            "returns 0",
            "passes",
            "passed",
            "is green",
            "succeeds",
        )
    )
    downstream = any(
        word in text
        for word in (
            "paper",
            "draft",
            "manuscript",
            "submission",
            "assurance",
            "review",
            "narrative",
            "analysis",
            "package",
        )
    )
    return start_gate and success_gate and downstream


def _text_has_full_emnlp_gate_success(text: str) -> bool:
    normalized = _normalize_planner_text(text)
    if "validate-full-emnlp" not in normalized:
        return False
    gate = r"validate-full-emnlp"
    zero_exit = (
        r"(?:exit(?:ed|s)?(?:\s+with\s+code)?\s*0|"
        r"return\s*code\s*0|returncode\s*0|status\s*[=:]?\s*0)"
    )
    success_word = r"(?:pass(?:ed|es)?|succeed(?:ed|s)?)"
    patterns = (
        rf"{gate}.{{0,240}}{zero_exit}",
        rf"{zero_exit}.{{0,240}}{gate}",
        rf"{gate}.{{0,120}}{success_word}",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _entry_task_signature(entry: JournalEntry) -> tuple[str, str] | None:
    extra = getattr(entry, "extra", {}) or {}
    signature = extra.get("planner_task_signature")
    title = ""
    objective = ""
    if isinstance(signature, dict):
        title = str(signature.get("title", "") or "")
        objective = str(signature.get("objective", "") or "")
    elif isinstance(signature, (list, tuple)) and len(signature) >= 2:
        title = str(signature[0] or "")
        objective = str(signature[1] or "")
    else:
        title = str(extra.get("title") or entry.title or "")
        objective = str(extra.get("objective") or "")
    normalized_title = _normalize_planner_text(title)
    normalized_objective = _normalize_planner_text(objective)
    if not normalized_title and not normalized_objective:
        return None
    return normalized_title, normalized_objective


def _is_recent_no_progress_failure(entry: JournalEntry) -> bool:
    if entry.kind != "mission_failed":
        return False
    extra = getattr(entry, "extra", {}) or {}
    terminal_status = str(
        extra.get("terminal_status")
        or extra.get("status")
        or extra.get("failure_status")
        or ""
    ).strip().casefold()
    return terminal_status == _PLANNER_RECENT_FAILURE_STATUS


class _CostTrackingSink:
    """Wraps an ``EventSink`` to accumulate token counts.

    The mission engine emits ``round.main.completed`` and
    ``round.review.completed`` events that already carry per-call
    ``input_tokens`` / ``output_tokens`` (Phase-2 instrumentation). We
    fold them into running totals and forward every event downstream
    unchanged.
    """

    def __init__(
        self,
        downstream: EventSink,
        *,
        engineer_model: str,
        reviewer_model: str,
        on_phase_change: Any = None,  # Callable[[str, dict], None] | None
    ) -> None:
        self.downstream = downstream
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self.engineer_input_tokens = 0
        self.engineer_output_tokens = 0
        self.reviewer_input_tokens = 0
        self.reviewer_output_tokens = 0
        self._on_phase_change = on_phase_change
        self._reviewer_notified = False
        self._engineer_round_count = 0
        self.engineer_cached_input_tokens = 0
        self.reviewer_cached_input_tokens = 0
        self._cumulative_usage_baselines: dict[
            tuple[str, str], tuple[int, int, int]
        ] = {}

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "round.main.completed":
                in_tok, cached_tok, out_tok = self._usage_delta(
                    event,
                    layer="engineer",
                )
                self.engineer_input_tokens += in_tok
                self.engineer_cached_input_tokens += cached_tok
                self.engineer_output_tokens += out_tok
                self._engineer_round_count += 1
            elif kind == "round.review.started":
                if not self._reviewer_notified and self._on_phase_change:
                    self._reviewer_notified = True
                    try:
                        self._on_phase_change("reviewer", {
                            "round_index": event.get("round_index", 0),
                            "status": "started",
                            "engineer_rounds": self._engineer_round_count,
                        })
                    except Exception:  # noqa: BLE001
                        log.debug("phase change callback failed", exc_info=True)
            elif kind == "round.review.completed":
                in_tok, cached_tok, out_tok = self._usage_delta(
                    event,
                    layer="reviewer",
                )
                self.reviewer_input_tokens += in_tok
                self.reviewer_cached_input_tokens += cached_tok
                self.reviewer_output_tokens += out_tok
        except Exception:  # noqa: BLE001
            log.debug("cost-tracking sink ignored malformed event", exc_info=True)
        # Always forward.
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream event sink raised; continuing")

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Forward stream lines when the downstream sink supports them."""
        try:
            handler = getattr(self.downstream, "handle_stream_line", None)
            if handler is not None:
                handler(stream, line)
        except Exception:  # noqa: BLE001
            log.exception("downstream stream handler raised; continuing")

    def close(self) -> None:
        try:
            closer = getattr(self.downstream, "close", None)
            if closer is not None:
                closer()
        except Exception:  # noqa: BLE001
            log.exception("downstream close raised; continuing")

    def total_usd(self) -> float:
        return self.engineer_usd() + self.reviewer_usd()

    def engineer_usd(self) -> float:
        return usd_for_tokens(
            self.engineer_model,
            self.engineer_input_tokens,
            self.engineer_cached_input_tokens,
            self.engineer_output_tokens,
            price_lookup=price_for,
        )

    def reviewer_usd(self) -> float:
        return usd_for_tokens(
            self.reviewer_model,
            self.reviewer_input_tokens,
            self.reviewer_cached_input_tokens,
            self.reviewer_output_tokens,
            price_lookup=price_for,
        )

    def total_input_tokens(self) -> int:
        return self.engineer_input_tokens + self.reviewer_input_tokens

    def total_output_tokens(self) -> int:
        return self.engineer_output_tokens + self.reviewer_output_tokens

    def _usage_delta(
        self,
        event: dict[str, Any],
        *,
        layer: str,
    ) -> tuple[int, int, int]:
        raw = (
            int(event.get("input_tokens", 0) or 0),
            int(event.get("cached_input_tokens", 0) or 0),
            int(event.get("output_tokens", 0) or 0),
        )
        if str(event.get("usage_scope") or "delta").lower() != "cumulative":
            return raw

        session_id = str(
            event.get("session_id")
            or event.get("thread_id")
            or event.get("actor")
            or "__global__"
        )
        key = (layer, session_id)
        previous = self._cumulative_usage_baselines.get(key)
        self._cumulative_usage_baselines[key] = raw
        if previous is None:
            return raw
        delta = (
            raw[0] - previous[0],
            raw[1] - previous[1],
            raw[2] - previous[2],
        )
        if any(value < 0 for value in delta):
            log.debug(
                "cumulative usage decreased; treating current event as fresh delta "
                "(layer=%s, session_id=%s, previous=%s, current=%s)",
                layer,
                session_id,
                previous,
                raw,
            )
            return raw
        return delta


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

@dataclass
class LifeSupervisorConfig:
    """Knobs for one ``LifeSupervisor`` run."""

    budget: LifeBudget = field(default_factory=LifeBudget)
    poll_interval_seconds: float = 5.0
    # The real repository worktree for this project. When present, the
    # supervisor should run engineer / planner work there instead of in
    # the life metadata directory.
    project_worktree: Path | None = None
    # Highest-level kill switch — the supervisor checks this between
    # missions. The CLI sets it on SIGTERM/SIGINT.
    stop_event: threading.Event | None = None
    # Optional callable consulted at the start of every mission; should
    # return one pending operator nudge per call (or ``None`` when the
    # bus is empty). The supervisor splices each message into the
    # prelude_context so the engineer sees it as live operator
    # guidance. The default ``None`` disables the bus.
    user_inbox: Any = None  # Callable[[], str | None] | None
    # Runtime context injected into the prelude of every mission so
    # the agent knows its own backend, models, and budget constraints.
    # Set by the REPL / daemon worker; empty string disables injection.
    runtime_context: str = ""
    # Defaults for tasks generated by the continuous planner. Manual backlog
    # items already use the BacklogItem defaults; keep planner-generated work
    # equally capable instead of cutting it off after one local polish cycle.
    planner_task_iteration_max_cycles: int = 6
    planner_task_iteration_budget_usd: float = 30.0
    # --- Continuous improvement mode -----------------------------------
    # When enabled, the supervisor does not exit when the backlog is
    # empty. Instead it invokes the critic-as-planner to inspect the
    # project and generate the next batch of tasks. The supervisor
    # only stops when the planner declares the project done, or when
    # budget / stop_event fires.
    continuous: bool = False
    continuous_objective: str = ""
    # Optional callback returning ``(enabled, objective)`` — the
    # supervisor calls it each iteration to hot-reload from disk or
    # elsewhere. When ``None``, the static ``continuous`` /
    # ``continuous_objective`` fields are used unchanged.
    continuous_config_provider: Any = None  # Callable[[], tuple[bool, str]] | None
    # Optional callback consulted immediately before each continuous
    # planner cycle. Return a non-empty stop reason to let the host
    # process defer planning and yield control, e.g. for daemon handoff.
    planner_cycle_gate: Any = None  # Callable[[], str] | None
    # Optional context injected into the planner prompt. The daemon uses
    # this to tell L4 that runtime source changed without making another
    # agent call.
    planner_runtime_context_provider: Any = None  # Callable[[], str] | None
    # Optional handler invoked only when the planner verdict explicitly
    # requests a daemon restart. Return True when the host is yielding.
    planner_restart_handler: Any = None  # Callable[[str], bool] | None
    # Optional mission-boundary hook. The host may use this to perform
    # process-level actions that are only safe between missions (for example
    # blue/green handoff after the agent modifies its own daemon/runtime
    # architecture). Return a non-empty stop reason to end this drain pass.
    post_mission_hook: Any = None  # Callable[[dict[str, Any]], str] | None
    # Optional runtime directory for mission telemetry. When set, the
    # supervisor starts a daemon-owned heartbeat around runner.execute()
    # so long-running shell experiments still show process/artifact progress.
    telemetry_dir: Path | None = None
    telemetry_interval_seconds: float = 10.0


# ----- thin protocol describing what we need from a MissionExecutor --------

class _MissionRunner(Protocol):
    """Structural type for the MissionExecutor we drive.

    We keep this loose so tests can substitute a fake without dragging
    ArgusBot in. Real callers pass an ``argus_skill.daemon.mission_executor.MissionExecutor``.
    """

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> Any:  # MissionOutcome
        raise NotImplementedError


class LifeSupervisor:
    """Cross-mission scheduler.

    Public API:

    - :meth:`run` — drive missions until backlog is exhausted, the
      iteration cap is hit, the budget is tripped, or ``stop_event``
      is set. Returns a summary dict (mission count, costs, statuses).

    - :meth:`tick` — process a single backlog item if available; useful
      for tests and CLI ``life next``.

    Memory wiring:

    - Before each mission, we render ``LifeMemory.render_prelude(...)``
      using the live objective and forward it as ``prelude_context``.
    - After each mission, we append a ``mission_complete`` /
      ``mission_failed`` journal entry so the next mission can recall it.
    """

    def __init__(
        self,
        *,
        memory: _MemoryView,
        runner: _MissionRunner,
        sink: EventSink,
        config: LifeSupervisorConfig | None = None,
        engineer_model: str = "gpt-5.4",
        reviewer_model: str = "gpt-5.4",
        critic_runner: Any | None = None,
    ) -> None:
        self.memory = memory
        self.runner = runner
        self.sink = sink
        self.config = config or LifeSupervisorConfig()
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        # critic_runner: any RunnerBackend (codex / memory). When None
        # the iteration loop is effectively disabled — items still go
        # ``done`` after the first successful mission. Wired by the
        # life worker / REPL to the same backend the engineer uses.
        self.critic_runner = critic_runner
        self._missions_started = 0
        self._planning_cycles = 0
        self._reap_orphans_on_startup()

    def _reap_orphans_on_startup(self) -> None:
        """Recover items left ``running`` by a crashed process.

        Items are reset to ``pending`` (up to 3 retries) so they resume
        automatically after a daemon restart. Items that keep crashing
        are marked ``failed`` to prevent poison-pill loops.
        """
        try:
            reaped = self.memory.backlog.reap_orphans()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: orphan reaper failed")
            return
        for it in reaped:
            requeued = it.status == "pending"
            if requeued:
                kind = "mission_requeued"
                title = f"recovered after restart: {it.title}"
                summary = (
                    f"item_id={it.id} "
                    f"retry={it.orphan_retries}/3 "
                    f"will resume automatically"
                )
            else:
                kind = "mission_orphaned"
                title = f"orphaned (max retries): {it.title}"
                summary = (
                    f"item_id={it.id} "
                    f"retries={it.orphan_retries} "
                    f"err={it.last_error}"
                )
            entry = JournalEntry.new(
                kind=kind,
                title=title,
                summary=summary,
                tags=list(it.tags) + ["life", "orphan"],
            )
            try:
                self.memory.journal.append(entry)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to journal orphan %s", it.id)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            self._emit({
                "type": "life.mission.orphaned",
                "item_id": it.id,
                "title": it.title,
                "started_ts": it.started_ts,
                "error": it.last_error,
            })

    @staticmethod
    def _safe_mode_enabled() -> bool:
        return os.environ.get("ARGUS_SKILL_SAFE_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _configured_worktree(self) -> Path | None:
        configured = getattr(self.config, "project_worktree", None)
        if configured is not None:
            return Path(configured).expanduser()
        memory_worktree = getattr(self.memory, "project_worktree", None)
        if memory_worktree is not None:
            return Path(memory_worktree).expanduser()
        return None

    def _project_workdir(self) -> Path:
        configured = self._configured_worktree()
        if configured is not None:
            return configured
        env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
        if env_workdir:
            return Path(env_workdir).expanduser()
        project_root = getattr(self.memory, "project_root", None)
        if project_root:
            return Path(project_root)
        project = getattr(self.memory, "project", None)
        if project is not None:
            root = getattr(project, "root", None)
            if root:
                return Path(root)
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return Path.cwd()

    def _full_scale_evidence_precondition_reason(
        self,
        item: BacklogItem,
    ) -> str:
        if not _backlog_item_requires_full_scale_evidence_precondition(item):
            return ""
        root = self._project_workdir()
        try:
            from ..skills.pipeline_contracts import validate_full_scale_experiment_evidence

            issues = validate_full_scale_experiment_evidence(root)
        except Exception as exc:  # noqa: BLE001
            return (
                "deferred precondition could not be verified: "
                f"`{_FULL_SCALE_EVIDENCE_GATE_COMMAND}` raised "
                f"{type(exc).__name__}: {exc}; repair the evidence gate before "
                "starting downstream paper/package work"
            )
        if not issues:
            return ""
        first = "; ".join(
            f"{getattr(issue, 'code', 'issue')} at {getattr(issue, 'path', '?')}"
            for issue in issues[:4]
        )
        return (
            "deferred precondition unmet: "
            f"`{_FULL_SCALE_EVIDENCE_GATE_COMMAND}` still reports "
            f"{len(issues)} issue(s)"
            + (f" ({first})" if first else "")
            + "; complete or collect the full-scale evidence matrix before "
            "starting downstream paper/package work"
        )

    def _block_unmet_backlog_preconditions(
        self,
        item: BacklogItem,
    ) -> dict[str, Any] | None:
        reason = self._full_scale_evidence_precondition_reason(item)
        if not reason:
            return None
        marked = self.memory.backlog.mark_failed(item.id, error=reason)
        if marked is None:
            return {"status": "claim_lost", "item_id": item.id}
        entry = JournalEntry.new(
            kind="mission_failed",
            title=item.title,
            summary=f"status=precondition_blocked; rounds=0; reason={reason}",
            tags=list(item.tags) + ["life", "precondition"],
            extra={
                "item_id": item.id,
                "objective": item.objective,
                "terminal_status": "precondition_blocked",
                "failure_reason": reason,
                "precondition": "validate-full-scale-evidence",
                "agent_layer": "supervisor",
            },
        )
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        self._emit_status(f"precondition block: {item.title}")
        self._emit({
            "type": "life.mission.precondition_blocked",
            "item_id": item.id,
            "title": item.title,
            "reason": reason,
            "precondition": "validate-full-scale-evidence",
        })
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")
        return {
            "success": False,
            "status": "precondition_blocked",
            "item_id": item.id,
            "reason": reason,
        }

    def _planner_workdir(self) -> Path:
        configured = self._configured_worktree()
        if configured is not None:
            return configured
        env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
        if env_workdir:
            return Path(env_workdir).expanduser()
        project_root = getattr(self.memory, "project_root", None)
        if project_root:
            return Path(project_root)
        project = getattr(self.memory, "project", None)
        if project is not None:
            root = getattr(project, "root", None)
            if root:
                return Path(root)
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return Path.cwd()

    def _critic_config(self):
        from ..critic import CriticConfig

        safe_mode = self._safe_mode_enabled()
        return CriticConfig(
            model=self.reviewer_model,
            working_dir=str(self._planner_workdir()),
            skip_git_repo_check=True,
            full_auto=safe_mode,
            dangerous_yolo=not safe_mode,
        )

    # ------------------------------------------------------------------
    # Public driving methods
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Drive missions until a stop condition. Returns a summary."""
        results: list[dict[str, Any]] = []
        stopped_by: str = ""
        while True:
            # Hot-reload continuous config from provider (disk, etc.)
            self._reload_continuous_config()
            stop_reason = self._maybe_stop()
            if stop_reason:
                if stop_reason != "__silent_stop__":
                    self._emit_status(stop_reason)
                stopped_by = stop_reason
                break
            # Early auto-stop: if this is an EMNLP project and the gate
            # already passes, stop immediately — don't run any more ticks
            # or planner cycles.  This prevents the planner from inventing
            # new work (lint, refactor, etc.) after the paper is done.
            if (
                self.config.continuous
                and self.config.continuous_objective
                and _objective_requires_full_emnlp_gate(
                    self.config.continuous_objective
                )
                and self._journal_has_full_emnlp_gate_success()
            ):
                self._emit_status(
                    "auto-stop: EMNLP gate passes, project complete"
                )
                stopped_by = "project_done"
                break
            try:
                outcome = self.tick()
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                log.exception("life supervisor: tick raised")
                recovered = self._fail_running_items_after_supervisor_error(err)
                self._emit({
                    "type": "life.supervisor.error",
                    "error": err,
                    "recovered_item_ids": recovered,
                })
                results.append({
                    "success": False,
                    "status": "supervisor_error",
                    "reason": err,
                    "recovered_item_ids": recovered,
                })
                stopped_by = "supervisor_error"
                break
            if outcome is None:
                # Backlog empty — continuous mode: ask planner for more
                if self.config.continuous and self.config.continuous_objective:
                    gate_reason = self._planner_cycle_gate_reason()
                    if gate_reason:
                        self._emit({
                            "type": "life.planner.deferred",
                            "reason": gate_reason,
                            "agent_layer": "planner",
                        })
                        self._emit_status(gate_reason)
                        stopped_by = gate_reason
                        break
                    # Auto-stop: if the EMNLP gate already passes, the
                    # project is done — don't ask the planner to invent
                    # more work.
                    if (
                        _objective_requires_full_emnlp_gate(
                            self.config.continuous_objective
                        )
                        and self._journal_has_full_emnlp_gate_success()
                    ):
                        self._emit_status(
                            "planner: project done — EMNLP gate passes"
                        )
                        stopped_by = "project_done"
                        break
                    planned = self._plan_next_work()
                    if planned == "daemon_handoff":
                        stopped_by = "daemon_handoff"
                        break
                    if planned == "planner_retry":
                        stopped_by = "planner_retry"
                        break
                    if planned is True:
                        continue  # new items in backlog, loop around
                    if planned is False:
                        self._emit_status("planner: project done")
                        stopped_by = "project_done"
                        break
                    stopped_by = "planner_error"
                    break
                # Non-continuous: sleep then re-check (so user-added
                # items via the file get picked up). Sleep is bounded
                # by the stop_event so a Ctrl-C shuts us down quickly.
                if self._wait_idle():
                    self._emit_status("stop requested while idle")
                    stopped_by = "stop_requested"
                    break
                # Re-check: if backlog still empty, exit cleanly so
                # `life run --once` semantics work in tests.
                if self.memory.backlog.next_pending() is None:
                    self._emit_status("backlog empty; exiting")
                    stopped_by = "backlog_empty"
                    break
                continue
            results.append(outcome)
            # Auth failure flagged by _run_one: propagate immediately
            if outcome.get("auth_failure"):
                stopped_by = "auth_failure"
                break
            post_mission_stop = self._post_mission_hook(outcome)
            if post_mission_stop:
                self._emit({
                    "type": "life.post_mission.stop",
                    "reason": post_mission_stop,
                    "item_id": outcome.get("item_id"),
                    "status": outcome.get("status"),
                })
                self._emit_status(post_mission_stop)
                stopped_by = post_mission_stop
                break
            # Stop conditions that ``tick`` signals via the result dict
            # (budget pause leaves the item PENDING on purpose so a
            # later supervisor run can retry — but for THIS run we must
            # not spin on the same blocked item).
            if outcome.get("status") in {"budget_pause", "iteration_cap"}:
                stopped_by = outcome.get("status", "")
                break
        return {
            "missions_started": self._missions_started,
            "planning_cycles": self._planning_cycles,
            "results": results,
            "stopped_by": stopped_by,
        }

    def _fail_running_items_after_supervisor_error(self, error: str) -> list[str]:
        """Best-effort cleanup when an unexpected supervisor error escapes.

        ``_run_one`` normally finalizes its claimed item, but this guard
        prevents a bug outside that narrow try/except from leaving durable
        ``running`` rows forever.
        """
        try:
            items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog after error")
            return []

        recovered: list[str] = []
        for item in items:
            if getattr(item, "status", "") != "running":
                continue
            item_id = str(getattr(item, "id", "") or "")
            if not item_id:
                continue
            title = str(getattr(item, "title", "") or "running mission")
            objective = str(getattr(item, "objective", "") or "")
            failure_reason = f"supervisor error: {error}"
            try:
                self.memory.backlog.mark_failed(item_id, error=failure_reason)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to mark running item failed: %s", item_id)
                continue
            recovered.append(item_id)
            entry = JournalEntry.new(
                kind="mission_failed",
                title=title,
                summary=f"status=supervisor_error; rounds=0; exc={error}",
                tags=list(getattr(item, "tags", []) or []) + ["life"],
                extra={
                    "item_id": item_id,
                    "objective": objective,
                    "terminal_status": "supervisor_error",
                    "failure_reason": failure_reason,
                    "agent_layer": "supervisor",
                },
            )
            try:
                self.memory.journal.append(entry)
                self._inject_cumulative_cost(entry)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to journal supervisor error")
            self._emit({
                "type": "life.mission.completed",
                "item_id": item_id,
                "success": False,
                "status": "supervisor_error",
                "rounds": 0,
                "cost_usd": 0.0,
                "journal_entry_id": entry.id,
            })
        return recovered

    def _planner_cycle_gate_reason(self) -> str:
        gate = self.config.planner_cycle_gate
        if gate is None:
            return ""
        try:
            reason = gate()
        except Exception:  # noqa: BLE001
            log.exception("planner cycle gate raised; continuing with planner")
            return ""
        return str(reason or "").strip()

    def _planner_runtime_context(self) -> str:
        provider = self.config.planner_runtime_context_provider
        if provider is None:
            return ""
        try:
            context = provider()
        except Exception:  # noqa: BLE001
            log.exception("planner runtime context provider raised; continuing")
            return ""
        return str(context or "").strip()

    def _planner_project_context(self) -> str:
        """Return cheap project-state context that keeps planner work grounded."""
        parts = [self._planner_runtime_context(), self._planner_emnlp_gate_context()]
        return "\n\n".join(part for part in parts if part.strip())

    def _planner_emnlp_gate_context(self) -> str:
        """Summarize the current EMNLP final gate for paper-oriented projects.

        This is advisory planner context, not completion evidence. Final
        readiness still requires a mission to run the exact gate command and
        record the passing output.
        """
        root = self._planner_workdir()
        if not self._planner_should_include_emnlp_gate_context(root):
            return ""
        try:
            from ..skills.pipeline_contracts import validate_full_emnlp_readiness

            issues = validate_full_emnlp_readiness(root)
        except Exception as exc:  # noqa: BLE001
            return (
                "Automatic EMNLP final gate snapshot:\n"
                f"- unable to evaluate validate-full-emnlp context: {type(exc).__name__}: {exc}\n"
                "- planner must inspect the gate manually before declaring readiness."
            )

        if not issues:
            return (
                "Automatic EMNLP final gate snapshot:\n"
                "- validate-full-emnlp currently reports no contract issues.\n"
                "- The project is ready for submission. You may declare project_done=true."
            )

        counts = Counter(issue.code for issue in issues)
        lines = [
            "Automatic EMNLP final gate snapshot:",
            f"- current validate-full-emnlp blockers: {len(issues)} issue(s), "
            f"{len(counts)} distinct code(s).",
            "- highest-frequency blockers: "
            + ", ".join(f"{code}={count}" for code, count in counts.most_common(8)),
        ]
        stage_hints = _planner_emnlp_stage_hints(issues)
        if stage_hints:
            lines.extend(stage_hints.splitlines())
        repair_route = _select_emnlp_finalization_repair_task(issues)
        if repair_route is not None:
            lines.append(
                "- recommended finalization repair route: "
                f"{repair_route.title} "
                f"({repair_route.target_label}; "
                f"{_emnlp_top_issue_counts(repair_route.target_issues, limit=6)})."
            )
        lines.append("- first blocking issues:")
        for issue in issues[:_PLANNER_GATE_CONTEXT_MAX_ISSUES]:
            message = issue.message.replace("\n", " ").strip()
            lines.append(f"  - {issue.code}\t{issue.path}\t{message}")
        if len(issues) > _PLANNER_GATE_CONTEXT_MAX_ISSUES:
            lines.append(
                f"  - ... {len(issues) - _PLANNER_GATE_CONTEXT_MAX_ISSUES} more issue(s) omitted"
            )
        lines.append(
            "- planner should queue the smallest high-impact repair task that moves "
            "these blockers toward the exact final gate; this snapshot is not a PASS."
        )
        text = "\n".join(lines)
        if len(text) > _PLANNER_GATE_CONTEXT_MAX_CHARS:
            return text[:_PLANNER_GATE_CONTEXT_MAX_CHARS].rstrip() + "\n- ... snapshot truncated"
        return text

    def _fallback_emnlp_gate_task_for_planner_error(self, verdict: Any) -> Any | None:
        """Turn a planner formatting/refusal failure into useful EMNLP repair work."""
        planner_error = str(getattr(verdict, "error", "") or "planner error")

        def unavailable(reason: str, detail: str = "") -> None:
            payload = {
                "type": "life.planner.fallback_unavailable",
                "cycle": self._planning_cycles,
                "planner_error": planner_error,
                "reason": reason,
            }
            if detail:
                payload["detail"] = detail[:500]
            self._emit(payload)

        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            unavailable("stop_event_set")
            return None
        root = self._planner_workdir()
        if not self._planner_should_include_emnlp_gate_context(root):
            unavailable("not_emnlp_project", str(root))
            return None
        try:
            from ..skills.pipeline_contracts import validate_full_emnlp_readiness

            issues = validate_full_emnlp_readiness(root)
        except Exception as exc:  # noqa: BLE001
            unavailable("validate_full_emnlp_failed", f"{type(exc).__name__}: {exc}")
            return None
        if not issues:
            unavailable("final_gate_has_no_issues")
            return None

        first_issues = "; ".join(
            f"{issue.code} at {issue.path}" for issue in issues[:8]
        )
        raw_text = str(getattr(verdict, "raw_text", "") or "").strip()
        task = _emnlp_finalization_task_spec_from_issues(
            issues,
            planner_error=(
                "Planner backend failed to return usable JSON; "
                f"{planner_error}"
            ),
            raw_text=raw_text,
        )
        if task is None:
            unavailable("no_finalization_task_selected")
            return None
        evidence = (
            f"{planner_error}; automatic validate-full-emnlp snapshot reports "
            f"{len(issues)} issue(s), including {first_issues}."
        )
        return replace(
            verdict,
            project_done=False,
            reason=(
                f"planner failed with {planner_error}; queued fallback from "
                "automatic EMNLP final-gate snapshot"
            ),
            new_tasks=[replace(task, evidence=evidence)],
            error="",
        )

    def _automatic_emnlp_finalization_task_for_current_gate(
        self,
    ) -> tuple[Any | None, list[Any]]:
        """Return a deterministic EMNLP repair task for the current gate, if any."""
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return None, []
        root = self._planner_workdir()
        if not self._planner_should_include_emnlp_gate_context(root):
            return None, []
        try:
            from ..skills.pipeline_contracts import validate_full_emnlp_readiness

            issues = validate_full_emnlp_readiness(root)
        except Exception:  # noqa: BLE001
            return None, []
        if not issues:
            return None, []
        return _emnlp_finalization_task_spec_from_issues(issues), issues

    def _planner_should_include_emnlp_gate_context(self, root: Path) -> bool:
        objective = self.config.continuous_objective or ""
        if _objective_requires_full_emnlp_gate(objective):
            return True
        if "validate-full-emnlp" in str(objective).casefold():
            return True
        if (root / "argus_builtin_skills" / "emnlp-paper-skill-router.md").exists():
            return True
        for filename in ("AGENTS.md", "agent.md"):
            path = root / filename
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:120_000]
            except OSError:
                continue
            normalized = _normalize_planner_text(text)
            if "validate-full-emnlp" in normalized:
                return True
            if ("emnlp" in normalized or "acl" in normalized) and "paper" in normalized:
                return True
        return False

    def _recent_no_progress_failures(self) -> dict[tuple[str, str], JournalEntry]:
        """Return recent failed task signatures quarantined from replanning."""
        try:
            recent_entries = self.memory.journal.tail(_PLANNER_RECENT_HISTORY_WINDOW)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to read recent journal for planner")
            return {}
        matches: dict[tuple[str, str], JournalEntry] = {}
        for entry in reversed(recent_entries):
            if not _is_recent_no_progress_failure(entry):
                continue
            signature = _entry_task_signature(entry)
            if signature is None or signature in matches:
                continue
            matches[signature] = entry
        return matches

    def _handle_planner_restart(self, reason: str) -> bool:
        handler = self.config.planner_restart_handler
        if handler is None:
            return False
        try:
            return bool(handler(reason))
        except Exception:  # noqa: BLE001
            log.exception("planner restart handler raised; continuing")
            return False

    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        hook = self.config.post_mission_hook
        if hook is None:
            return ""
        try:
            return str(hook(outcome) or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("post mission hook raised; continuing")
            return ""

    def tick(self) -> dict[str, Any] | None:
        """Process at most one backlog item. Returns its result dict or
        ``None`` if nothing was eligible to run."""
        item = self.memory.backlog.next_pending()
        if item is None:
            return None

        precondition_block = self._block_unmet_backlog_preconditions(item)
        if precondition_block is not None:
            return precondition_block

        ok, reason = self.config.budget.can_start(
            item=item, journal=self.memory.journal
        )
        if not ok:
            # Don't fail the item — it'll be retried next supervisor
            # run when the daily cap rolls over. Just journal it and
            # signal the caller to exit cleanly.
            self._emit_status(f"budget block: {reason}")
            entry = JournalEntry.new(
                kind="budget_pause",
                title=f"paused before '{item.title}'",
                summary=reason,
                tags=["budget"],
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return {"status": "budget_pause", "item_id": item.id, "reason": reason}

        if not self.config.continuous and self._missions_started >= self.config.budget.max_missions:
            # Only narrate the cap when there's actually pending work
            # being held back. If the backlog is empty (or the user
            # asked for ``--once`` and we just ran their one mission),
            # this message is just noise.
            try:
                more_pending = self.memory.backlog.next_pending() is not None
            except Exception:  # noqa: BLE001
                more_pending = False
            if more_pending:
                self._emit_status(
                    f"max-missions cap reached ({self.config.budget.max_missions})"
                )
            return {"status": "iteration_cap", "item_id": item.id}

        return self._run_one(item)

    # ------------------------------------------------------------------
    # One mission
    # ------------------------------------------------------------------

    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        prelude = self.memory.render_prelude(objective=item.objective)
        item_metadata = self._render_backlog_item_metadata(item)
        if item_metadata:
            prelude = item_metadata + "\n---\n\n" + prelude if prelude else item_metadata
        # Inject runtime context (backend, models, budget) so the agent
        # knows its own environment. Placed before operator nudges so
        # nudges can override if needed.
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
        # Drain any pending operator nudges from the inbox bus and
        # splice them in front of the prelude as live operator
        # guidance. Each round in the engineer loop will see this as
        # `Operator message history`.
        nudges = self._drain_user_inbox()
        if nudges:
            prelude = (
                "## Operator messages (live nudges, most recent last)\n"
                + "\n".join(f"- {m}" for m in nudges)
                + "\n\n---\n\n"
                + prelude
            )
        # Atomic claim: flip pending → running in one rewrite. If the
        # head moved between the budget peek and now (concurrent writer
        # or user `/rm`), bail; the next tick will re-evaluate.
        claimed = self.memory.backlog.claim_next()
        if claimed is None or claimed.id != item.id:
            if claimed is not None:
                # Roll back so the next tick sees it again. running →
                # pending is a legal transition (only terminal states
                # are sealed).
                try:
                    self.memory.backlog.update(claimed.id, status="pending")
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: claim rollback failed")
            return {"status": "claim_lost", "item_id": item.id}
        item = claimed
        self._missions_started += 1

        self._emit({
            "type": "life.mission.started",
            "item_id": item.id,
            "title": item.title,
            "missions_started": self._missions_started,
        })
        # Notify: mission starting (engineer layer)
        try:
            start_entry = JournalEntry.new(
                kind="mission_started",
                title=item.title,
                summary=f"objective={item.objective[:200]}",
                tags=list(item.tags) + ["life"],
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "engineer",
                },
            )
            self.memory.journal.append(start_entry)
            self._inject_cumulative_cost(start_entry)
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(start_entry)
        except Exception:  # noqa: BLE001
            log.debug("mission_started notify failed; non-critical")

        # Phase-change callback: notifies Telegram when reviewer starts
        def _phase_cb(layer: str, info: dict[str, Any]) -> None:
            try:
                self._emit({
                    "type": "life.phase.started",
                    "item_id": item.id,
                    "agent_layer": layer,
                    "round_index": info.get("round_index", 0),
                })
                from .notify import dispatch_journal_entry
                entry = JournalEntry.new(
                    kind="phase_change",
                    title=item.title,
                    summary=f"round {info.get('round_index', '?')}: {layer} 开始评审",
                    tags=["life", "phase"],
                    extra={
                        "item_id": item.id,
                        "objective": item.objective,
                        "agent_layer": layer,
                        "engineer_rounds": info.get("engineer_rounds", 0),
                    },
                )
                # Don't journal phase changes — just notify.
                # Pass in-flight cost so cumulative includes current mission.
                self._inject_cumulative_cost(
                    entry, in_flight_usd=cost_sink.total_usd(),
                )
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.debug("phase_change notify failed; non-critical")

        cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
        )

        telemetry_monitor: Any = None
        if self.config.telemetry_dir is not None:
            try:
                from .telemetry import MissionTelemetryMonitor
                telemetry_monitor = MissionTelemetryMonitor(
                    life_dir=self.config.telemetry_dir,
                    workdir=self._project_workdir(),
                    item_id=item.id,
                    title=item.title,
                    interval_seconds=self.config.telemetry_interval_seconds,
                    stop_event=self.config.stop_event,
                )
                telemetry_monitor.start()
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to start telemetry monitor")

        outcome: Any = None
        exc_str: str | None = None
        t0 = time.time()
        try:
            outcome = self.runner.execute(
                objective=item.objective,
                sink=cost_sink,
                prelude_context=prelude,
            )
        except Exception as exc:  # noqa: BLE001
            exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
        finally:
            if telemetry_monitor is not None:
                try:
                    telemetry_monitor.stop()
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: failed to stop telemetry monitor")
        elapsed = time.time() - t0

        success = bool(getattr(outcome, "success", False)) if outcome else False
        status = str(getattr(outcome, "status", "error") if outcome else "error")
        rounds = int(getattr(outcome, "rounds", 0) or 0)
        stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        usd = cost_sink.total_usd()

        # Emit per-layer completion notifications with actual costs
        try:
            from .notify import dispatch_journal_entry
            eng_usd = cost_sink.engineer_usd()
            rev_usd = cost_sink.reviewer_usd()
            # L1 engineer completed
            eng_done = JournalEntry.new(
                kind="phase_change",
                title=item.title,
                summary=f"工程师完成: {rounds}轮, ${eng_usd:.4f}",
                tags=["life", "phase"],
                cost_usd=eng_usd,
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "engineer",
                    "phase_status": "completed",
                    "rounds": rounds,
                    "input_tokens": cost_sink.engineer_input_tokens,
                    "output_tokens": cost_sink.engineer_output_tokens,
                },
            )
            self._inject_cumulative_cost(eng_done, in_flight_usd=usd)
            dispatch_journal_entry(eng_done)
            # L2 reviewer completed (only if reviewer was actually used)
            if cost_sink.reviewer_input_tokens > 0:
                rev_done = JournalEntry.new(
                    kind="phase_change",
                    title=item.title,
                    summary=f"审查员完成: ${rev_usd:.4f}",
                    tags=["life", "phase"],
                    cost_usd=rev_usd,
                    extra={
                        "item_id": item.id,
                        "objective": item.objective,
                        "agent_layer": "reviewer",
                        "phase_status": "completed",
                        "input_tokens": cost_sink.reviewer_input_tokens,
                        "output_tokens": cost_sink.reviewer_output_tokens,
                    },
                )
                self._inject_cumulative_cost(rev_done, in_flight_usd=usd)
                dispatch_journal_entry(rev_done)
        except Exception:  # noqa: BLE001
            log.debug("layer completion notify failed; non-critical")

        # Auth failure: the codex backend detected an expired/invalid
        # token. Stop this drain pass so we do not immediately continue
        # with stale credentials, but do not signal the daemon's global
        # stop_event. A 7x24 worker should stay alive so it can recover
        # after credentials are refreshed, and transient provider errors
        # should not kill the supervising process.
        auth_failure = bool(getattr(outcome, "auth_failure", False))
        if auth_failure:
            self._emit({
                "type": "life.auth_failure",
                "item_id": item.id,
                "text": (
                    "⚠️  codex authentication failed — run `codex login` "
                    "to refresh credentials if this persists; the daemon "
                    "will keep polling."
                ),
            })

        # ---- iteration loop: should we requeue for another polish cycle?
        # Trigger on `success` (mission marked done) AND on `max_rounds`
        # (engineer ran out of rounds without reviewer-confirmed done).
        # The latter is critical for a 7×24 product: when the engineer
        # built a perfectly correct artifact but reviewer kept demanding
        # more verbatim evidence, we don't want the whole mission to die
        # — let the critic sub-agent inspect the work and either certify
        # it as done or ask for a *concrete* next round.
        iteration_outcome: dict[str, Any] | None = None
        salvage_mode = (not success) and status == "max_rounds" and item.iterate
        # Chat fast-path: when the runner short-circuited a conversational
        # input, there is no artifact to polish — skip the critic loop
        # entirely. Otherwise the critic would try to "improve" a
        # one-line greeting reply, costing another LLM call for no gain.
        chat_mode = bool(getattr(outcome, "chat_mode", False))
        if not chat_mode and (success or salvage_mode):
            iteration_outcome = self._maybe_iterate(
                item=item,
                outcome=outcome,
                cycle_cost_usd=usd,
                salvage_mode=salvage_mode,
            )
        # If the critic accepted the salvage attempt, treat the mission
        # as successful so it transitions to ``done`` not ``failed``.
        if salvage_mode and iteration_outcome and iteration_outcome.get("salvaged"):
            success = True
            status = "done"
            stop_reason = iteration_outcome.get("stop_reason") or stop_reason

        iteration_bonus_usd = 0.0
        if iteration_outcome:
            iteration_bonus_usd = float(iteration_outcome.get("critic_cost_usd", 0.0) or 0.0)
            usd += iteration_bonus_usd

        # Update backlog row.
        if iteration_outcome and iteration_outcome.get("requeued"):
            # Item is back to ``pending``; do not mark_done. The next
            # tick will pick it up and re-execute with the polished
            # objective.
            pass
        elif success:
            self.memory.backlog.mark_done(item.id)
        else:
            err = exc_str or stop_reason or "unspecified failure"
            self.memory.backlog.mark_failed(item.id, error=err)

        # Journal entry.
        if iteration_outcome and iteration_outcome.get("requeued"):
            kind = "mission_iterated"
        else:
            kind = "mission_complete" if success else "mission_failed"
        summary_parts = [
            f"status={status}",
            f"rounds={rounds}",
            f"elapsed={elapsed:.1f}s",
            f"tokens_in={cost_sink.total_input_tokens()}",
            f"tokens_out={cost_sink.total_output_tokens()}",
            f"cost_usd=${usd:.4f}",
        ]
        if iteration_outcome:
            summary_parts.append(
                f"iter={iteration_outcome.get('cycles_done', 0)}/{item.iteration_max_cycles}"
            )
            if iteration_outcome.get("requeued"):
                summary_parts.append(
                    f"improvements={iteration_outcome.get('improvement_count', 0)}"
                )
            elif iteration_outcome.get("stop_reason"):
                summary_parts.append(f"iter_stop={iteration_outcome['stop_reason']}")
        if stop_reason:
            summary_parts.append(f"reason={stop_reason}")
        if exc_str:
            summary_parts.append(f"exc={exc_str}")
        entry = JournalEntry.new(
            kind=kind,
            title=item.title,
            summary="; ".join(summary_parts),
            tags=list(item.tags) + ["life"],
            cost_usd=usd,
            extra={
                "item_id": item.id,
                "objective": item.objective,
                "planner_task_signature": {
                    "title": _normalize_planner_text(item.title),
                    "objective": _normalize_planner_text(item.objective),
                }
                if kind == "mission_failed"
                else {},
                "terminal_status": status if kind == "mission_failed" else "",
                "stop_reason": (stop_reason or err) if kind == "mission_failed" else "",
                "failure_reason": err if kind == "mission_failed" else "",
                "agent_layer": "critic" if iteration_outcome and iteration_outcome.get("requeued") else "engineer",
                "engineer_model": self.engineer_model,
                "reviewer_model": self.reviewer_model,
                "input_tokens": cost_sink.total_input_tokens(),
                "output_tokens": cost_sink.total_output_tokens(),
                "matched_skill": str(getattr(outcome, "matched_skill_name", "") or ""),
                "skill_distilled": bool(getattr(outcome, "skill_distilled", False)),
                "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
                "completion_summary": self._completion_evidence_from_outcome(outcome),
                "iteration": iteration_outcome or {},
            },
        )
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")

        self._emit({
            "type": "life.mission.completed",
            "item_id": item.id,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
            "iteration": iteration_outcome or None,
        })

        return {
            "item_id": item.id,
            "title": item.title,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
            "iteration": iteration_outcome,
            "auth_failure": auth_failure,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _drain_user_inbox(self, *, max_messages: int = 10) -> list[str]:
        """Pull all pending operator nudges from the configured inbox.

        Returns up to ``max_messages`` lines (oldest-first). Empty list
        if no inbox is configured or nothing is pending. Any exception
        from the user-supplied callable is swallowed — a flaky bus
        must never break a mission.
        """
        cb = getattr(self.config, "user_inbox", None)
        if cb is None:
            return []
        out: list[str] = []
        for _ in range(max(1, int(max_messages))):
            try:
                msg = cb()
            except Exception:  # noqa: BLE001
                log.exception("user_inbox callable raised; ignoring")
                break
            if not msg:
                break
            text = str(msg).strip()
            if text:
                out.append(text)
        if out:
            self._emit({
                "type": "life.inbox.drained",
                "count": len(out),
                "messages": out,
            })
        return out

    def _maybe_stop(self) -> str:
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return "stop_event signalled"
        # In continuous mode, max_missions is not a hard cap — the
        # planner generates new work indefinitely until it declares
        # the project done. Only daily budget is enforced.
        if not self.config.continuous:
            if self._missions_started >= self.config.budget.max_missions:
                # Suppress the cap message when there's no held-back work.
                # Treats "you asked for one mission, you got one" as silent
                # success rather than a noisy guardrail trip.
                try:
                    more_pending = self.memory.backlog.next_pending() is not None
                except Exception:  # noqa: BLE001
                    more_pending = False
                if more_pending:
                    return f"max-missions cap reached ({self.config.budget.max_missions})"
                return "__silent_stop__"
        if self.config.budget.remaining_today(self.memory.journal) <= 0:
            return "daily budget exhausted"
        return ""

    def _wait_idle(self) -> bool:
        """Sleep ``poll_interval_seconds`` honouring stop_event.

        Returns True if stop_event fired during the wait."""
        ev = self.config.stop_event
        if ev is None:
            time.sleep(self.config.poll_interval_seconds)
            return False
        return ev.wait(self.config.poll_interval_seconds)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: event sink raised")

    def _emit_status(self, text: str) -> None:
        self._emit({"type": "life.status", "text": text})

    def _planner_task_tags(self, task: Any) -> list[str]:
        scope = self._normalize_planner_scope(getattr(task, "scope", ""))
        return ["planner", f"scope:{scope}"]

    @staticmethod
    def _normalize_planner_scope(scope: object) -> str:
        normalized = str(scope or _PLANNER_SCOPE_BOUNDED).strip().lower().replace("-", "_")
        if normalized == _PLANNER_SCOPE_FINAL_SUBMISSION:
            return _PLANNER_SCOPE_FINAL_SUBMISSION
        return _PLANNER_SCOPE_BOUNDED

    @staticmethod
    def _planner_scope_from_item(item: BacklogItem) -> str:
        for tag in item.tags:
            normalized = str(tag).strip().lower().replace("-", "_")
            if normalized in {
                f"scope:{_PLANNER_SCOPE_FINAL_SUBMISSION}",
                f"planner_scope:{_PLANNER_SCOPE_FINAL_SUBMISSION}",
            }:
                return _PLANNER_SCOPE_FINAL_SUBMISSION
            if normalized in {
                f"scope:{_PLANNER_SCOPE_BOUNDED}",
                f"planner_scope:{_PLANNER_SCOPE_BOUNDED}",
            }:
                return _PLANNER_SCOPE_BOUNDED
        return ""

    def _render_backlog_item_metadata(self, item: BacklogItem) -> str:
        scope = self._planner_scope_from_item(item)
        if not scope and not item.tags:
            return ""
        paper_context = "\n".join((
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "objective", "") or ""),
            str(getattr(self.config, "continuous_objective", "") or ""),
        ))
        is_paper_long_horizon = (
            _objective_is_paper_long_horizon(paper_context)
            or _objective_requires_full_emnlp_gate(paper_context)
        )
        lines = ["## Backlog item metadata"]
        if scope:
            lines.append(f"- planner_scope: {scope}")
        if item.tags:
            lines.append("- tags: " + ", ".join(item.tags))
        if scope == _PLANNER_SCOPE_FINAL_SUBMISSION:
            lines.append(
                f"- final_submission_gate: `{_FULL_EMNLP_GATE_COMMAND}` must exit 0 "
                "before this item can be marked done."
            )
        elif scope == _PLANNER_SCOPE_BOUNDED:
            if is_paper_long_horizon:
                lines.append(
                    "- paper_optimization_task: this is a bounded mission, but it is "
                    "part of a long-horizon paper/submission objective. First satisfy "
                    "the named acceptance criteria, then continue through adjacent "
                    "paper blockers while budget allows; do not mark done only because "
                    "one narrow check passed if `validate-research-md-format` or "
                    "`validate-full-emnlp` still reports addressable manuscript, "
                    "evidence, review, layout, figure/table, citation, manifest, or "
                    "assurance blockers. Full-gate success is required only for "
                    "`final_submission`, but fresh validator evidence or an exact "
                    "blocker list is required here."
                )
            else:
                lines.append(
                    "- bounded_task: judge this item against its own acceptance criteria; "
                    "do not require the project-final EMNLP gate unless the objective "
                    "explicitly asks for it."
                )
        return "\n".join(lines)

    def _objective_with_item_scope_context(
        self,
        item: BacklogItem,
        objective: str,
    ) -> str:
        metadata = self._render_backlog_item_metadata(item)
        if not metadata:
            return objective
        return f"{metadata}\n\nOriginal operator objective:\n{objective.strip()}"

    @staticmethod
    def _completion_evidence_from_outcome(outcome: Any) -> str:
        for attr in ("final_message", "completion_summary_markdown", "stop_reason"):
            value = getattr(outcome, attr, "") or ""
            if value:
                return str(value)[:4000]
        return ""

    def _journal_has_full_emnlp_gate_success(self) -> bool:
        # First check: run the live validator directly. If it passes now,
        # that's stronger evidence than any journal entry.
        try:
            import subprocess
            workdir = self._project_workdir()
            result = subprocess.run(
                [sys.executable, "-m", "argus_skill.skills.pipeline_contracts",
                 "validate-full-emnlp", "--project-root", str(workdir)],
                capture_output=True, text=True, timeout=120,
                cwd=str(workdir),
            )
            if result.returncode == 0:
                return True
        except Exception:  # noqa: BLE001
            pass

        # Fallback: check journal for historical evidence
        try:
            entries = self.memory.journal.tail(50)
        except Exception:  # noqa: BLE001
            return False
        for entry in entries:
            if getattr(entry, "kind", "") != "mission_complete":
                continue
            extra = getattr(entry, "extra", {}) or {}
            chunks = [
                str(getattr(entry, "summary", "") or ""),
            ]
            if isinstance(extra, dict):
                for key in ("completion_summary", "verification_summary"):
                    value = extra.get(key)
                    if value:
                        chunks.append(str(value))
            if _text_has_full_emnlp_gate_success("\n".join(chunks)):
                return True
        return False

    # ------------------------------------------------------------------
    # Iteration loop
    # ------------------------------------------------------------------

    def _maybe_iterate(
        self,
        *,
        item: BacklogItem,
        outcome: Any,
        cycle_cost_usd: float,
        salvage_mode: bool = False,
    ) -> dict[str, Any] | None:
        """Critic iteration is disabled. Engineer does the work, reviewer
        verifies. No separate critic polish cycle."""
        return {
            "cycles_done": int(item.iteration_cycles_done),
            "cost_so_far_usd": float(item.iteration_cost_usd),
            "requeued": False,
            "stop_reason": "critic layer removed — reviewer handles verification",
        }

        cycles_done = int(item.iteration_cycles_done)
        cycles_max = int(item.iteration_max_cycles)
        cost_so_far = float(item.iteration_cost_usd) + max(0.0, float(cycle_cost_usd))
        budget = float(item.iteration_budget_usd)
        remaining_budget = max(0.0, budget - cost_so_far)

        if cycles_done >= cycles_max:
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": f"cycle ceiling {cycles_max} reached",
            }
        if remaining_budget <= 0.0:
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": (
                    f"iteration budget exhausted (${cost_so_far:.2f}/${budget:.2f})"
                ),
            }

        # Pull the reviewer's accepted completion summary.
        latest = ""
        for attr in ("final_message", "completion_summary_markdown", "stop_reason"):
            v = getattr(outcome, attr, "") or ""
            if v:
                latest = str(v)
                break
        original = item.original_objective or item.objective
        critic_original = self._objective_with_item_scope_context(item, original)

        # Notify: critic layer starting
        try:
            self._emit({
                "type": "life.phase.started",
                "item_id": item.id,
                "agent_layer": "critic",
                "iteration_cycle": cycles_done + 1,
                "iteration_max": cycles_max,
            })
            from .notify import dispatch_journal_entry
            critic_start = JournalEntry.new(
                kind="phase_change",
                title=item.title,
                summary=f"迭代 {cycles_done + 1}/{cycles_max}: 评审员开始评估",
                tags=["life", "phase"],
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "critic",
                    "iteration_cycle": cycles_done + 1,
                    "iteration_max": cycles_max,
                },
            )
            self._inject_cumulative_cost(
                critic_start, in_flight_usd=cycle_cost_usd,
            )
            dispatch_journal_entry(critic_start)
        except Exception:  # noqa: BLE001
            log.debug("critic phase_change notify failed; non-critical")

        try:
            from ..critic import (
                Critic,
                render_iteration_objective,
            )
            critic = Critic(self.critic_runner)
            # Enable streaming so critic output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                verdict = critic.evaluate(
                    original_objective=critic_original,
                    latest_completion_summary=latest,
                    cycles_done=cycles_done,
                    cycles_max=cycles_max,
                    budget_remaining_usd=remaining_budget,
                    journal_tail=self._render_recent_journal_for_critic(item.id),
                    config=self._critic_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: critic raised; finalizing as done")
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": f"critic error: {type(exc).__name__}",
            }

        critic_cost_usd = usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.cached_input_tokens,
            verdict.output_tokens,
            price_lookup=price_for,
        )
        cost_so_far += critic_cost_usd
        remaining_budget = max(0.0, budget - cost_so_far)
        raw_improvements = list(verdict.improvements)
        value_gate_min_score = (
            _FOLLOWUP_CRITIC_MIN_IMPACT_SCORE
            if cycles_done >= 1
            else 4
        )
        valuable_improvements = [
            imp
            for imp in raw_improvements
            if int(getattr(imp, "impact_score", 0) or 0) >= value_gate_min_score
        ]
        dropped_low_value_count = len(raw_improvements) - len(valuable_improvements)
        effective_stop = bool(verdict.stop) or not valuable_improvements
        effective_reason = verdict.reason
        if not bool(verdict.stop) and not valuable_improvements:
            effective_reason = (
                f"critic improvements below impact gate "
                f"({value_gate_min_score}/5); handing control back to planner"
            )

        # Notify: critic layer completed with cost
        try:
            from .notify import dispatch_journal_entry
            critic_done = JournalEntry.new(
                kind="phase_change",
                title=item.title,
                summary=(
                    f"评审员完成: {'停止迭代' if effective_stop else f'{len(valuable_improvements)}项高价值改进'}"
                    f", ${critic_cost_usd:.4f}"
                ),
                tags=["life", "phase"],
                cost_usd=critic_cost_usd,
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "critic",
                    "phase_status": "completed",
                    "stop": effective_stop,
                    "improvement_count": len(valuable_improvements),
                    "raw_improvement_count": len(raw_improvements),
                    "dropped_low_value_count": dropped_low_value_count,
                    "value_gate_min_score": value_gate_min_score,
                    "reason": effective_reason,
                    "input_tokens": verdict.input_tokens,
                    "cached_input_tokens": verdict.cached_input_tokens,
                    "output_tokens": verdict.output_tokens,
                },
            )
            self._inject_cumulative_cost(
                critic_done, in_flight_usd=cycle_cost_usd + critic_cost_usd,
            )
            dispatch_journal_entry(critic_done)
        except Exception:  # noqa: BLE001
            log.debug("critic completion notify failed; non-critical")

        self._emit({
            "type": "life.iteration.critic",
            "item_id": item.id,
            "stop": effective_stop,
            "improvement_count": len(valuable_improvements),
            "raw_improvement_count": len(raw_improvements),
            "dropped_low_value_count": dropped_low_value_count,
            "value_gate_min_score": value_gate_min_score,
            "reason": effective_reason,
            "input_tokens": verdict.input_tokens,
            "cached_input_tokens": verdict.cached_input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": critic_cost_usd,
        })

        if effective_stop:
            # Salvage path: the engineer hit max_rounds without a `done`
            # verdict, but the critic — looking at journal evidence —
            # decided no further work is needed. Promote the mission to
            # ``done`` so the operator isn't woken up by a false-failure.
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": effective_reason or "critic stopped",
                "salvaged": bool(salvage_mode),
                "critic_cost_usd": critic_cost_usd,
            }

        if remaining_budget <= 0.0:
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": (
                    f"iteration budget exhausted (${cost_so_far:.2f}/${budget:.2f})"
                ),
                "critic_cost_usd": critic_cost_usd,
            }

        new_objective = render_iteration_objective(
            original_objective=original,
            cycles_done=cycles_done,
            improvements=valuable_improvements,
        )
        try:
            self.memory.backlog.requeue_for_iteration(
                item.id,
                new_objective=new_objective,
                cost_delta_usd=cycle_cost_usd + critic_cost_usd,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: requeue_for_iteration failed")
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": f"requeue failed: {type(exc).__name__}",
            }
        self._emit({
            "type": "life.iteration.continued",
            "item_id": item.id,
            "cycles_done": cycles_done + 1,
            "cycles_max": cycles_max,
            "cost_so_far_usd": cost_so_far,
            "budget_usd": budget,
            "improvements": [
                {
                    "title": imp.title,
                    "acceptance": imp.acceptance,
                    "impact_score": getattr(imp, "impact_score", 0),
                    "impact_area": getattr(imp, "impact_area", ""),
                    "evidence": getattr(imp, "evidence", ""),
                }
                for imp in valuable_improvements
            ],
            "critic_cost_usd": critic_cost_usd,
        })
        return {
            "cycles_done": cycles_done + 1,
            "cost_so_far_usd": cost_so_far,
            "requeued": True,
            "improvement_count": len(valuable_improvements),
            "improvements": [
                {
                    "title": imp.title,
                    "acceptance": imp.acceptance,
                    "impact_score": getattr(imp, "impact_score", 0),
                    "impact_area": getattr(imp, "impact_area", ""),
                    "evidence": getattr(imp, "evidence", ""),
                }
                for imp in valuable_improvements
            ],
            "critic_cost_usd": critic_cost_usd,
        }

    def _render_recent_journal_for_critic(self, item_id: str) -> str:
        """A tiny tail of journal entries for the current item, plain text."""
        try:
            entries = self.memory.journal.tail(6)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            extra = getattr(e, "extra", None) or {}
            if isinstance(extra, dict) and extra.get("item_id") == item_id:
                lines.append(f"- {e.kind}: {e.summary}")
        return "\n".join(lines[-3:])

    def _inject_cumulative_cost(
        self, entry: Any, *, in_flight_usd: float = 0.0,
    ) -> None:
        """Stamp ``cumulative_cost_usd`` onto ``entry.extra``.

        ``in_flight_usd`` is an optional cost from the *current* mission
        that hasn't been journaled yet (e.g. during phase-change
        notifications that fire mid-execution).
        """
        try:
            cumul = self.memory.journal.total_cost_since(0) + max(0.0, in_flight_usd)
            extra = getattr(entry, "extra", None)
            if extra is None:
                entry.extra = {"cumulative_cost_usd": round(cumul, 2)}
            else:
                extra["cumulative_cost_usd"] = round(cumul, 2)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Hot-reload continuous config
    # ------------------------------------------------------------------

    def _reload_continuous_config(self) -> None:
        """Update ``self.config.continuous`` from the config provider.

        Called at the top of every ``run()`` iteration so that changes
        from the REPL (written to disk) take effect within seconds even
        when the supervisor is in a long continuous run.
        """
        provider = self.config.continuous_config_provider
        if provider is None:
            return
        try:
            enabled, objective = provider()
            self.config.continuous = enabled
            if objective:
                self.config.continuous_objective = objective
        except Exception:  # noqa: BLE001
            log.debug("continuous config provider raised; keeping current values")

    # ------------------------------------------------------------------
    # Planner — continuous improvement mode
    # ------------------------------------------------------------------

    def _plan_next_work(self) -> bool | None | str:
        """Call the critic-as-planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and
        ``"daemon_handoff"`` if the planner asked the host to restart,
        and ``None`` when the planner fails and should be retried later.
        """
        if self.critic_runner is None:
            self._emit_status("planner error: no critic runner wired; retry later")
            entry = JournalEntry.new(
                kind="planner_error",
                title="planner unavailable",
                summary="no critic runner wired",
                tags=["life", "planner"],
                extra={"agent_layer": "planner", "error": "no critic runner wired"},
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return None

        self._planning_cycles += 1
        self._emit({
            "type": "life.planner.start",
            "cycle": self._planning_cycles,
            "objective": self.config.continuous_objective[:200],
        })

        journal_tail = self._render_journal_for_planner()
        remaining = self.config.budget.remaining_today(self.memory.journal)

        try:
            from ..critic import Critic

            critic = Critic(self.critic_runner)
            # Enable streaming so planner output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                verdict = critic.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,
                    budget_remaining_usd=remaining,
                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary=self._planner_project_context(),
                    config=self._critic_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            self._emit({
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": f"{type(exc).__name__}: {exc}",
            })
            entry = JournalEntry.new(
                kind="planner_error",
                title=f"planner cycle #{self._planning_cycles}",
                summary=f"{type(exc).__name__}: {exc}",
                tags=["life", "planner"],
                extra={
                    "agent_layer": "planner",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return None

        planner_cost_usd = usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.cached_input_tokens,
            verdict.output_tokens,
            price_lookup=price_for,
        )

        if verdict.error:
            fallback_verdict = self._fallback_emnlp_gate_task_for_planner_error(verdict)
            if fallback_verdict is not None:
                verdict = fallback_verdict
            else:
                self._emit({
                    "type": "life.planner.error",
                    "cycle": self._planning_cycles,
                    "error": verdict.error,
                    "raw_text": verdict.raw_text,
                })
                self._emit_status(f"planner error: {verdict.error}; retry later")
                entry = JournalEntry.new(
                    kind="planner_error",
                    title=f"planner cycle #{self._planning_cycles}",
                    summary=f"{verdict.error}: {verdict.reason}",
                    tags=["life", "planner"],
                    extra={
                        "agent_layer": "planner",
                        "error": verdict.error,
                        "reason": verdict.reason,
                        "raw_text": verdict.raw_text,
                    },
                )
                self.memory.journal.append(entry)
                self._inject_cumulative_cost(entry)
                try:
                    from .notify import dispatch_journal_entry
                    dispatch_journal_entry(entry)
                except Exception:  # noqa: BLE001
                    log.exception("notify dispatch failed; continuing")
                return None

        if not verdict.project_done:
            automatic_task, gate_issues = self._automatic_emnlp_finalization_task_for_current_gate()
            if (
                automatic_task is not None
                and _planner_tasks_need_emnlp_finalization_override(
                    verdict.new_tasks,
                    gate_issues,
                )
            ):
                verdict = replace(
                    verdict,
                    reason=(
                        "planner task was too broad for the current EMNLP final-gate "
                        "snapshot; queued deterministic finalization repair"
                    ),
                    new_tasks=[automatic_task],
                )

        if (
            verdict.project_done
            and _objective_requires_full_emnlp_gate(self.config.continuous_objective)
            and not self._journal_has_full_emnlp_gate_success()
        ):
            from ..critic import TaskSpec

            verdict = replace(
                verdict,
                project_done=False,
                reason=(
                    "full EMNLP readiness gate is required before project_done; "
                    "queueing final submission proof"
                ),
                new_tasks=[
                    TaskSpec(
                        title="Prove EMNLP submission readiness",
                        objective=(
                            "Project-final task. Scope: final_submission. "
                            "Run the full EMNLP readiness gate and fix every blocker "
                            "until it passes. Acceptance requires verbatim output showing "
                            f"`{_FULL_EMNLP_GATE_COMMAND}` exits 0, plus "
                            "paper/SUBMISSION_ASSURANCE.json with PASS or accepted WARN "
                            "and no hard blockers. Do not declare done based only on "
                            "validate-pipeline, validate-manifest, a pilot run, or an "
                            "underlength draft; if the gate fails, inspect the reported "
                            "blockers and repair experiments, baselines, ablations, paper "
                            "contract, assurance, manifest, or submission state as needed."
                        ),
                        impact_score=5,
                        impact_area="requirement_gap",
                        evidence=(
                            "Planner attempted project_done without journal evidence "
                            "that validate-full-emnlp exited 0."
                        ),
                        scope=_PLANNER_SCOPE_FINAL_SUBMISSION,
                    )
                ],
            )

        if verdict.project_done and _objective_is_open_ended(self.config.continuous_objective):
            self._emit({
                "type": "life.planner.verdict",
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
                "open_ended_objective": True,
            })
            self._emit_status(
                "planner: project done — continuing later for open-ended objective"
            )
            entry = JournalEntry.new(
                kind="planner_retry",
                title="planner suggests continuing",
                summary=(
                    f"project_done=true; continuing later for open-ended objective: "
                    f"{verdict.reason}"
                ),
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "open_ended_objective": True,
                    "restart_daemon": verdict.restart_daemon,
                    "restart_reason": verdict.restart_reason,
                    "reason": verdict.reason,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return "planner_retry"

        if verdict.project_done:
            self._emit({
                "type": "life.planner.verdict",
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
            })
            self._emit_status(
                f"planner: project done — {verdict.reason}"
            )
            entry = JournalEntry.new(
                kind="planner_done",
                title="planner declares project done",
                summary=verdict.reason,
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "restart_daemon": verdict.restart_daemon,
                    "restart_reason": verdict.restart_reason,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            if verdict.restart_daemon and self._handle_planner_restart(
                verdict.restart_reason
            ):
                self._emit_status("daemon_handoff")
                return "daemon_handoff"
            return False

        if verdict.restart_daemon and not verdict.new_tasks:
            restart_reason = verdict.restart_reason or verdict.reason
            self._emit({
                "type": "life.planner.verdict",
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": 0,
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": True,
                "restart_reason": restart_reason,
            })
            entry = JournalEntry.new(
                kind="planner_cycle",
                title=f"planner cycle #{self._planning_cycles}",
                summary=f"planner requested daemon restart: {restart_reason}",
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "objective": self.config.continuous_objective[:200],
                    "proposed_tasks": 0,
                    "enqueued_tasks": 0,
                    "skipped_duplicate_tasks": 0,
                    "enqueued_titles": [],
                    "skipped_duplicate_titles": [],
                    "restart_daemon": True,
                    "restart_reason": restart_reason,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            if self._handle_planner_restart(restart_reason):
                self._emit_status("daemon_handoff")
                return "daemon_handoff"
            self._emit_status("planner requested daemon restart but host did not restart")
            return None

        if not verdict.new_tasks:
            self._emit({
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": "planner produced no tasks",
                "raw_text": verdict.raw_text,
            })
            self._emit_status("planner error: produced no tasks; retry later")
            entry = JournalEntry.new(
                kind="planner_error",
                title=f"planner cycle #{self._planning_cycles}",
                summary=verdict.reason or "planner produced no tasks",
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "error": "planner produced no tasks",
                    "reason": verdict.reason,
                    "raw_text": verdict.raw_text,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return None

        try:
            existing_items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            existing_items = []

        seen_signatures: dict[tuple[str, str], BacklogItem] = {}
        for existing in existing_items:
            if existing.status not in _PLANNER_DEDUP_STATUSES:
                continue
            if (
                existing.status == "done"
                and _is_emnlp_finalization_objective(existing.objective)
            ):
                continue
            signature = _planner_task_signature(existing.title, existing.objective)
            if existing.status in {"pending", "running"}:
                seen_signatures[signature] = existing
            elif signature not in seen_signatures:
                seen_signatures[signature] = existing

        recent_failures = self._recent_no_progress_failures()
        added_titles: list[str] = []
        skipped_duplicate_titles: list[str] = []
        skipped_recent_failure_titles: list[str] = []
        added_impact_scores: list[int] = []

        # Add new tasks to the backlog.
        for task in verdict.new_tasks:
            sanitized_title = _sanitize_planner_task_text(task.title)
            sanitized_objective = _sanitize_planner_task_text(task.objective)
            sanitized_evidence = _sanitize_planner_task_text(task.evidence)
            if (
                sanitized_title != task.title
                or sanitized_objective != task.objective
                or sanitized_evidence != task.evidence
            ):
                task = replace(
                    task,
                    title=sanitized_title,
                    objective=sanitized_objective,
                    evidence=sanitized_evidence,
                )
            signature = _planner_task_signature(task.title, task.objective)
            duplicate_item = seen_signatures.get(signature)
            if duplicate_item is not None:
                skipped_duplicate_titles.append(task.title)
                duplicate_reason = (
                    "duplicate completed task"
                    if duplicate_item.status == "done"
                    else "duplicate pending/running task"
                )
                self._emit({
                    "type": "life.planner.task_skipped",
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": duplicate_item.id,
                    "matched_status": duplicate_item.status,
                    "reason": duplicate_reason,
                })
                continue
            recent_failure = recent_failures.get(signature)
            if recent_failure is not None:
                skipped_recent_failure_titles.append(task.title)
                failure_extra = getattr(recent_failure, "extra", {}) or {}
                failure_signature = _entry_task_signature(recent_failure)
                self._emit({
                    "type": "life.planner.task_skipped",
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": failure_extra.get("item_id"),
                    "matched_title": recent_failure.title,
                    "matched_status": failure_extra.get("terminal_status") or failure_extra.get("status"),
                    "matched_stop_reason": failure_extra.get("stop_reason") or failure_extra.get("failure_reason"),
                    "matched_signature": (
                        {
                            "title": failure_signature[0],
                            "objective": failure_signature[1],
                        }
                        if failure_signature is not None
                        else None
                    ),
                    "skip_category": "recent_no_progress_failure",
                    "reason": "recent no_progress failure",
                })
                continue
            item = BacklogItem.new(
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=self._planner_task_tags(task),
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                iteration_budget_usd=self._item_iteration_budget(),
            )
            self.memory.backlog.add(item)
            seen_signatures[signature] = item
            added_titles.append(item.title)
            added_impact_scores.append(task.impact_score)
            self._emit({
                "type": "life.planner.task_added",
                "item_id": item.id,
                "title": item.title,
                "impact_score": task.impact_score,
                "impact_area": task.impact_area,
            })

        summary_parts = [
            f"proposed {len(verdict.new_tasks)} task(s)",
            (
                "enqueued "
                f"{len(added_titles)} task(s): "
                + (", ".join(added_titles) if added_titles else "(none)")
            ),
        ]
        if skipped_duplicate_titles:
            summary_parts.append(
                "skipped "
                f"{len(skipped_duplicate_titles)} duplicate(s): "
                + ", ".join(skipped_duplicate_titles)
            )
        if skipped_recent_failure_titles:
            summary_parts.append(
                "quarantined "
                f"{len(skipped_recent_failure_titles)} recent no_progress repeat(s): "
                + ", ".join(skipped_recent_failure_titles)
            )

        entry = JournalEntry.new(
            kind="planner_cycle",
            title=f"planner cycle #{self._planning_cycles}",
            summary="; ".join(summary_parts),
            tags=["life", "planner"],
            cost_usd=planner_cost_usd,
            extra={
                "agent_layer": "planner",
                "objective": self.config.continuous_objective[:200],
                "proposed_tasks": len(verdict.new_tasks),
                "enqueued_tasks": len(added_titles),
                "skipped_duplicate_tasks": len(skipped_duplicate_titles),
                "skipped_recent_failure_tasks": len(skipped_recent_failure_titles),
                "enqueued_titles": added_titles,
                "enqueued_impact_scores": added_impact_scores,
                "skipped_duplicate_titles": skipped_duplicate_titles,
                "skipped_recent_failure_titles": skipped_recent_failure_titles,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
            },
        )
        self._emit({
            "type": "life.planner.verdict",
            "cycle": self._planning_cycles,
            "project_done": verdict.project_done,
            "reason": verdict.reason,
            "task_count": len(verdict.new_tasks),
            "enqueued_tasks": len(added_titles),
            "skipped_duplicate_tasks": len(skipped_duplicate_titles),
            "skipped_recent_failure_tasks": len(skipped_recent_failure_titles),
            "enqueued_titles": added_titles,
            "enqueued_impact_scores": added_impact_scores,
            "skipped_duplicate_titles": skipped_duplicate_titles,
            "skipped_recent_failure_titles": skipped_recent_failure_titles,
            "input_tokens": verdict.input_tokens,
            "cached_input_tokens": verdict.cached_input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": planner_cost_usd,
            "restart_daemon": verdict.restart_daemon,
            "restart_reason": verdict.restart_reason,
        })
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")
        if verdict.restart_daemon and self._handle_planner_restart(
            verdict.restart_reason
        ):
            self._emit_status("daemon_handoff")
            return "daemon_handoff"
        return True

    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

    def _item_iteration_budget(self) -> float:
        """Default iteration budget for planner-generated tasks."""
        try:
            return max(0.0, float(self.config.planner_task_iteration_budget_usd))
        except (TypeError, ValueError):
            return 30.0

    def _render_journal_for_planner(self) -> str:
        """Render recent journal entries for the planner's context."""
        try:
            entries = self.memory.journal.tail(20)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            from datetime import datetime
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            line = f"- [{ts}] {e.kind}: {e.title} — {e.summary}"
            extra = getattr(e, "extra", {}) or {}
            if isinstance(extra, dict):
                evidence = str(extra.get("completion_summary") or "")
                if "validate-full-emnlp" in evidence:
                    line += f" | evidence: {evidence[:500]}"
            lines.append(line)
        return "\n".join(lines) or "(empty)"
