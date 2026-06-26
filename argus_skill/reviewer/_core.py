"""Reviewer sub-agent: graded "done / continue / blocked" verdict.

Provenance: vendored from ``ArgusBot/agent_cli/reviewer.py``. The
substantive change is decoupling: the original took a ``AgentCliRunner``
directly; this version takes any ``RunnerBackend`` (see
``argus_skill.core.ports``) so it works with codex, claude-code, or the
in-memory test stub equally well.

Public surface kept identical: ``Reviewer.evaluate(...) -> ReviewDecision``,
``parse_decision_text(text) -> ReviewDecision | None``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import CheckResult, ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from ..engineer.checks import summarize_checks
from ..skills.role_context import format_role_context, load_builtin_skill_text
from ._parsing import _find_decision_in_messages

log = logging.getLogger(__name__)


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False


SCHEMA_PATH = str(Path(__file__).with_name("reviewer_schema.json"))
_REVIEWER_ROLE_SKILL = "argus-reviewer-role.md"
_REVIEWER_ENGINEER_HANDOFF_SKILL = "reviewer-engineer-handoff.md"
_ACADEMIC_PAPER_REVIEW_SKILL = "academic-paper-peer-review-benchmark.md"
_WIKI_CURATOR_SKILL = "wiki-curator.md"
_REVIEWER_ROLE_FALLBACK = """# Argus Reviewer Role

The Reviewer is argus-skill's evidence gate. Decide done/continue/blocked from
concrete artifacts and checks, and turn failures into concise engineer
next_action instructions.
"""
_REVIEWER_ENGINEER_HANDOFF_FALLBACK = """# Reviewer-to-engineer handoff

When validation fails, your `next_action` is the engineer's next prompt. The
engineer may be a smaller model, so convert logs into a concise repair brief:
name failed commands, exit codes, issue codes, exact paths, ordered fixes, and
the command that proves completion. Do not paste raw logs wholesale.
"""
_ACADEMIC_PAPER_REVIEW_FALLBACK = """# Academic paper peer-review benchmark

Use for nearly complete EMNLP/ACL paper tasks. Simulate a strict reviewer:
score contribution, claim-evidence alignment, experiment integrity, benchmark
quality, literature/citations, reproducibility, writing, format/layout, and the
strongest reviewer objection. Any remaining major actionable reviewer objection
means `continue`, not `done`.
"""
_WIKI_CURATOR_FALLBACK = """# Wiki Curator

If a project wiki exists, run the curator pass at mission close: backfill
sources from literature artifacts, lift new sources into scratch pages, and
regenerate/validate query indexes.
"""


def _load_reviewer_engineer_handoff_skill() -> str:
    return load_builtin_skill_text(
        _REVIEWER_ENGINEER_HANDOFF_SKILL, _REVIEWER_ENGINEER_HANDOFF_FALLBACK
    )


def _load_academic_paper_review_skill() -> str:
    return load_builtin_skill_text(
        _ACADEMIC_PAPER_REVIEW_SKILL, _ACADEMIC_PAPER_REVIEW_FALLBACK
    )


def _load_wiki_curator_skill_if_present() -> str | None:
    """Return wiki-curator skill text when the current project has a wiki.

    The adaptive reviewer matcher has empirically missed this skill for
    diagnostic/debugging objectives, so wiki-curator is fixed context whenever
    `.autors/*/wiki/` exists in the current project.
    """
    autors = Path.cwd() / ".autors"
    if not autors.exists():
        return None
    from ..wiki.bootstrap import is_initialized_wiki
    if not any(
        is_initialized_wiki(p / "wiki") for p in autors.iterdir() if p.is_dir()
    ):
        return None
    return load_builtin_skill_text(_WIKI_CURATOR_SKILL, _WIKI_CURATOR_FALLBACK)


def _format_academic_paper_review_skill_block(*, include: bool) -> str:
    if not include:
        return ""
    skill = _load_academic_paper_review_skill()
    return (
        "Academic-paper peer review benchmark skill "
        "(apply only to near-complete academic paper scopes):\n"
        f"{skill}\n\n"
    )


def _verification_directive() -> str:
    """Trust-first verification stance for the reviewer prompt.

    Root-cause fix (operator directive 2026-06-26): the previous unconditional
    instruction told the reviewer to re-run the engineer's commands itself and
    use *its own* output as ground truth. On a trusted-scorer task that meant
    the reviewer re-ran the official scorer EVERY round to re-confirm a number
    the engineer had already obtained from that same frozen scorer — burning the
    whole round, adding zero value, and treating the engineer as a suspect even
    though it has no reward signal to game and does not control the scorer.

    The new stance (global, all verticals): TRUST an honest, internally
    consistent self-report; spend a verification command ONLY when the evidence
    is missing or self-contradictory (the cheap anti-fabrication floor that
    still stops a faked number). Reinvest the saved round in the two things the
    engineer cannot do for itself — judging the novelty/quality of the idea and
    giving high-altitude strategic direction.
    """
    return (
        "**Trust the engineer by default; verify only on doubt.** The engineer "
        "has no reward signal it could game, and on measured tasks its numbers "
        "come from a TRUSTED external scorer it does not control — so an honest, "
        "internally consistent self-report is the NORMAL case, not a suspect "
        "one. When the engineer's summary already SHOWS the verification output "
        "(the scorer's RESULT line, pytest output, a file listing) and it is "
        "internally consistent, TRUST IT — do NOT reflexively re-run the same "
        "commands just to re-confirm an honest result. That redundant re-run "
        "burns the entire round and is the #1 reviewer anti-pattern to avoid.\n"
        "**You have shell access** — spend a verification command ONLY when the "
        "evidence is actually MISSING from the summary, or the claimed result "
        "is self-contradictory / implausible / contradicted by an acceptance "
        "check. That cheap floor is what stops a fabricated number; it does NOT "
        "require re-running every honest one.\n"
        "**Reinvest the round in what the engineer cannot do for itself:** judge "
        "whether THIS round's idea was genuinely novel and well-motivated or a "
        "tired re-tweak of a direction that already lost, and give HIGH-ALTITUDE "
        "direction — your `next_action` should name a concrete, clearly-"
        "different next approach (a different SOTA technique, a different "
        "hardware feature, the specific profiled bottleneck to attack), not "
        "`re-run and paste the output`.\n\n"
    )






class Reviewer:
    """One reviewer call per round. Stateless across rounds."""

    def __init__(self, runner: RunnerBackend, *, skill_store: Any | None = None) -> None:
        self.runner = runner
        self.schema_path = SCHEMA_PATH
        # Optional: when wired, the reviewer runs the same role-mission skill
        # matcher every other role uses, surfacing adaptive reviewer skills
        # (e.g. stage-specific review playbooks) plus cross-role engineer
        # references on top of the fixed role/handoff context. ``None`` keeps
        # the legacy fixed-context-only behaviour.
        self.skill_store = skill_store
        from ..skills.missions import ReviewerMission
        self.mission = ReviewerMission(skill_store)

    def evaluate(
        self,
        *,
        objective: str,
        original_objective: str | None = None,
        operator_messages: list[str] | None = None,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        checks: list[CheckResult],
        config: ReviewerConfig,
        planner_review_instruction: str = "",
        active_skill_id: str | None = None,
        engineer_reasoning_summary: str = "",
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        background_context: str = "",
        escalate_hint: str = "",
    ) -> ReviewDecision:
        # Defense-in-depth (root-cause guard for the 2026-06-25 incident): if the
        # reviewer output-schema file is missing, codex aborts with exit 1
        # ("Failed to read output schema file ...") and the round renders NO
        # verdict. Detect it up front and fail loud as a backend-unavailable
        # block, instead of building a prompt and handing codex a path it cannot
        # read. This catches a moved schema / a stale import-time path held by a
        # long-lived daemon whose on-disk tree moved underneath it.
        if self.schema_path and not Path(self.schema_path).exists():
            reason = (
                "Reviewer output-schema file is missing at "
                f"{self.schema_path}; the reviewer backend cannot start. This is "
                "an environment/packaging fault (e.g. the schema was moved or a "
                "running process holds a stale import-time path), not a verdict."
            )
            return ReviewDecision(
                status="blocked",
                confidence=0.0,
                reason=reason,
                next_action=(
                    "Restore the reviewer schema at that path, or restart the "
                    "daemon on code whose schema path matches disk; do not treat "
                    "this as evidence about the engineer's work."
                ),
                round_summary_markdown=f"# Review Summary\n\n- {reason}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
                backend_unavailable=True,
            )
        prompt = self._build_prompt(
            objective=objective,
            original_objective=original_objective or objective,
            operator_messages=operator_messages or [],
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            checks=checks,
            active_skill_id=active_skill_id,
            engineer_reasoning_summary=engineer_reasoning_summary,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
            scope=scope,
            prior_checkpoint=prior_checkpoint,
            background_context=background_context,
            escalate_hint=escalate_hint,
        )
        try:
            result = self.runner.run_exec(
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=config.model,
                    reasoning_effort=config.reasoning_effort,
                    dangerous_yolo=config.dangerous_yolo,
                    full_auto=config.full_auto,
                    skip_git_repo_check=config.skip_git_repo_check,
                    extra_args=list(config.extra_args) if config.extra_args else None,
                    output_schema_path=self.schema_path,
                ),
                run_label="reviewer",
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"Reviewer runner raised {type(exc).__name__}: {exc}"
            log.exception("reviewer runner raised")
            return ReviewDecision(
                status="blocked",
                confidence=0.0,
                reason=msg,
                next_action="Resolve the reviewer runner failure before retrying.",
                round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
                backend_unavailable=True,
            )
        rev_in = int(getattr(result, "input_tokens", 0) or 0)
        rev_cached = int(getattr(result, "cached_input_tokens", 0) or 0)
        rev_out = int(getattr(result, "output_tokens", 0) or 0)
        if not result.agent_messages:
            fatal = str(getattr(result, "fatal_error", "") or "").strip()
            if fatal or result.exit_code != 0:
                reason = (
                    "Reviewer backend returned no output "
                    f"(exit={result.exit_code}"
                    + (f", fatal_error={fatal}" if fatal else "")
                    + ")."
                )
                return ReviewDecision(
                    status="blocked",
                    confidence=0.0,
                    reason=reason,
                    next_action=(
                        "Reviewer backend died before any verdict — do NOT treat "
                        "this as evidence the engineer completed or failed the "
                        "task. The supervised loop retries on a fresh session and "
                        "escalates to the operator if it keeps failing."
                    ),
                    round_summary_markdown=f"# Review Summary\n\n- {reason}\n",
                    completion_summary_markdown="",
                    failure_cause="environmental",
                    backend_unavailable=True,
                    input_tokens=rev_in,
                    cached_input_tokens=rev_cached,
                    output_tokens=rev_out,
                )
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason=f"Reviewer returned empty output. exit={result.exit_code}",
                next_action="Continue implementation and provide concrete completed work.",
                round_summary_markdown="# Review Summary\n\n- Reviewer returned empty output.\n",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
            )
        parsed = _find_decision_in_messages(result.agent_messages)
        if parsed is None:
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason="Reviewer output was not valid JSON.",
                next_action="Continue implementation and include clear completion evidence.",
                round_summary_markdown="# Review Summary\n\n- Reviewer output was not valid JSON.\n",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
            )
        # Phase-2 instrumentation: cost-tracking sinks (e.g. LifeSupervisor's
        # _CostTrackingSink) read these fields off ``round.review.completed``
        # events. If we don't propagate them every iteration budget enforcement
        # silently breaks and the journal shows ``cost_usd=$0.0000``.
        parsed.input_tokens = rev_in
        parsed.cached_input_tokens = rev_cached
        parsed.output_tokens = rev_out
        # The L2 reviewer's verdict is authoritative — the harness must not
        # second-guess it with keyword heuristics on the engineer's summary.
        # If a generic role-acknowledgment turn slips through, that is a
        # reviewer-prompt concern (the reviewer is told to demand concrete
        # evidence and verify when it is missing/contradictory), not a harness
        # post-filter.
        return parsed

    def _build_prompt(
        self,
        *,
        objective: str,
        original_objective: str = "",
        operator_messages: list[str],
        planner_review_instruction: str,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        checks: list[CheckResult],
        active_skill_id: str | None = None,
        engineer_reasoning_summary: str = "",
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        background_context: str = "",
        escalate_hint: str = "",
    ) -> str:
        error_text = main_error or "none"
        check_text = summarize_checks(checks)
        reviewer_role_context = format_role_context(
            "Argus reviewer role skill",
            _REVIEWER_ROLE_SKILL,
            _REVIEWER_ROLE_FALLBACK,
        )
        handoff_skill = _load_reviewer_engineer_handoff_skill()
        # Role-mission matcher (same primitive engineer/planner use). It
        # surfaces ADAPTIVE reviewer skills (stage-specific review playbooks)
        # plus cross-role engineer references on top of the fixed
        # role/handoff/academic blocks above. The three fixed reviewer skills
        # are excluded by ReviewerMission so the matcher never re-injects what
        # is already hard-wired into this prompt.
        matched_review_skill_block = ""
        if self.skill_store is not None:
            from ..skills.harness_overlay import resolve_project_root as _rpr
            from ..skills.venue_profiles import venue_excluded_skill_files

            review_match = self.mission.match(
                objective, extra_exclude=venue_excluded_skill_files(_rpr())
            )
            if review_match.block:
                matched_review_skill_block = (
                    "Matched reviewer skill(s) for this objective "
                    "(read first; apply the relevant one(s)):\n"
                    f"{review_match.block}\n\n"
                )
        from ..skills.harness_overlay import resolve_project_root
        from ..skills.stage_checklists import (
            CANONICAL_STAGE_ORDER,
            current_stage,
            format_full_pipeline_checklist,
            format_stage_checklist,
        )
        from ..skills.vertical_select import resolve_vertical
        from ..verticals._base import (
            load_vertical,
            vertical_completion_gate,
            vertical_role_banner,
            vertical_search_altitude,
        )

        _proot = resolve_project_root()
        stage = current_stage(_proot)
        import os as _os
        _measured = _os.environ.get("ARGUS_SKILL_MEASURED_MODE", "").strip().lower() in ("1", "true", "yes", "on")
        # Vertical-native prompt framing: resolve the active vertical and let it
        # supply the top-of-prompt role banner. The rollback / final-submission
        # framing below applies ONLY to a paper vertical (completion_gate ==
        # "full_emnlp"); for any other vertical (e.g. speedrun) those blocks are
        # suppressed and the vertical's banner is prepended so the reviewer judges
        # only that vertical's metric instead of paper-pipeline artifacts.
        _vmod = load_vertical(resolve_vertical(_proot))
        _full_emnlp = vertical_completion_gate(_vmod) == "full_emnlp"
        optimize_banner = vertical_role_banner(_vmod, "reviewer")
        # Live search-altitude facts (NO verdict) so the reviewer can SEE the
        # floor history when judging forward_progress — i.e. distinguish "this
        # round advanced a declared structural line" from "Nth single-knob
        # nibble at a floor that has not moved in N attempts". Empty for
        # verticals that do not surface it.
        search_altitude_block = vertical_search_altitude(_vmod, _proot)
        # Structured scope only. The planner threads scope=final_submission as
        # a backlog tag all the way here; we no longer sniff the objective
        # prose for "scope: final_submission" markers. Normalize the same way
        # the planner does (lower + hyphen→underscore) so callers that pass
        # "final-submission" still match.
        scope_normalized = (scope or "").strip().lower().replace("-", "_")
        is_final_submission = scope_normalized == "final_submission"
        if _measured:
            stage_checklist = (
                "## MEASURED-BENCHMARK MODE — TRUST the scorer, judge the IDEA\n"
                "This task has a TRUSTED, FROZEN scorer and the engineer has NO reward signal "
                "and does not control that scorer, so the RESULT line it pastes is the normal, "
                "honest case — not something you must re-derive. Your verdict depends on ONE "
                "thing: did this round's MEASURED score improve over the engineer's previous "
                "best?\n\n"
                "TRUST the engineer's reported RESULT (correct + cand_ms/score). Do NOT re-run "
                "the scorer yourself to 'confirm' an honest, internally consistent number — the "
                "engineer already self-supervises correctness by running the scorer every round, "
                "and re-running it just to re-confirm burns the whole round for zero added value. "
                "Spend a check ONLY if NO RESULT line was pasted, or the reported result is "
                "self-contradictory. Otherwise your job this round is JUDGMENT + DIRECTION, not "
                "re-measurement.\n\n"
                "- `continue` if the score improved (tell the engineer to lock it in and "
                "explore the NEXT mechanism), OR the engineer can still try a clearly-different "
                "unexplored mechanism. First JUDGE the idea: was this round's mechanism genuinely "
                "novel, or a tired re-tweak of a direction that already lost? Then your "
                "`next_action` MUST name a CONCRETE new direction — a "
                "different library/SOTA approach, a different hardware technique, or the specific "
                "profiled bottleneck to attack. PUSH mechanism diversity; do NOT ask for more "
                "tweaking of a direction that already lost, and do NOT ask the engineer to "
                "re-run and paste a result it already showed.\n"
                "- `blocked` ONLY if several consecutive rounds measured no improvement AND the "
                "engineer has genuinely run out of distinct mechanisms (real plateau), or there "
                "is an operator-only blocker.\n"
                "- `done` is rare for open-ended optimization — only if the score is at/above the "
                "known ceiling.\n\n"
                "Do NOT demand GROUND_TRUTH / gate / marker / status / evidence / provenance "
                "files — the scorer's number is the only evidence, and the harness ignores those "
                "files. Do NOT judge bookkeeping or artifact hygiene. A round that MEASURED a "
                "real number — even a worse one — made forward progress by ruling out a mechanism. "
                "This block OVERRIDES the generic 'demand evidence / re-run the commands' decision "
                "rules below: here the scorer IS the evidence and re-running an honest result is "
                "waste."
            )
        elif is_final_submission or stage == "submission":
            stage_checklist = format_full_pipeline_checklist(role="reviewer", project_root=_proot)
        else:
            stage_checklist = format_stage_checklist(stage, role="reviewer", project_root=_proot)

        # Academic peer-review benchmark skill: advisory rubric for reviewing
        # a near-complete manuscript. Gate it on the structured stage/scope
        # signal — final_submission, or the paper-writing stages (review /
        # submission) — instead of keyword-sniffing the objective/evidence
        # for tokens like "main.pdf". `draft` is excluded so mid-production
        # drafting isn't held to final peer-review standards prematurely.
        paper_review_skill_block = _format_academic_paper_review_skill_block(
            include=is_final_submission or stage in {"review", "submission"},
        )
        wiki_curator_text = _load_wiki_curator_skill_if_present()
        wiki_curator_skill_block = (
            "## Wiki curator (fixed when a wiki exists -- run as part of this verdict)\n\n"
            f"{wiki_curator_text}\n\n"
            if wiki_curator_text
            else ""
        )

        # Always-on project-venv reminder for the reviewer too: a round
        # summary that says "I skipped X because the package is missing"
        # is never acceptable — the engineer must `./.venv/bin/pip install`
        # and retry. Inject the canonical skill body so both roles read
        # the same source of truth.
        venv_skill_block = ""
        try:
            from ..skills.builtins import iter_builtin_skill_texts
            for fname, body in iter_builtin_skill_texts():
                if fname == "project-venv-package-management.md":
                    venv_skill_block = (
                        "## Project venv (any missing package is the engineer's job to install)\n"
                        + body
                    )
                    break
        except Exception:  # noqa: BLE001
            pass

        # Stage-rollback instruction. When the reviewer notices that an
        # upstream stage's evidence is missing or unreliable while
        # working a later stage, demoting current_stage back to the
        # earlier stage is the correct move — the agent should not be
        # asked to repair upstream defects through the current stage's
        # acceptance criteria. The instruction lives here (not in the
        # individual checklist items) so it applies uniformly.
        stage_idx = (
            CANONICAL_STAGE_ORDER.index(stage)
            if stage in CANONICAL_STAGE_ORDER
            else 0
        )
        earlier_stages = ", ".join(CANONICAL_STAGE_ORDER[:stage_idx]) or "(none)"
        rollback_block = (
            "## Stage rollback\n"
            f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
            "If you discover that an *earlier* stage's evidence is missing, "
            "stale, or unreliable (e.g. while in `run` you notice the "
            "`benchmark` evaluator is a stub; while in `draft` you notice "
            "that `research/INFRA_CHOICE.md` was never locked in; while in "
            "`analysis` you notice the `run.score_variance` rows are all "
            "identical) — do NOT try to patch the gap from inside the "
            "current stage. Reply `continue` and tell the engineer to roll "
            "back the pipeline state machine by calling:\n\n"
            "    python -c \"from argus_skill.skills.stage_checklists import rollback_stage; "
            "rollback_stage('.', target_stage='<earlier-stage>', "
            "reason='<one-sentence reason>')\"\n\n"
            "then complete the earlier stage's checklist before re-advancing."
        )
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        shared_context_block = _format_engineer_shared_context(
            skill_used=active_skill_id,
            engineer_reasoning_summary=engineer_reasoning_summary,
            prev_review_summary=prev_review_summary,
        )
        # v12 phase-4: when callers (e.g. harbor_adapter) collect richer
        # post-round evidence (engineer self-report verbatim, runtime probe,
        # official verifier output with "ground truth, trust this" framing),
        # they pass it as ``raw_evidence``. We append it after the
        # acceptance-check section so the reviewer always has the strongest
        # signal grounded in actual container state, not just the
        # engineer's prose. Empty string → legacy v3 behaviour.
        evidence_block = (
            f"\nRaw verification evidence:\n{raw_evidence.rstrip()}\n"
            if raw_evidence.strip()
            else ""
        )
        # Background-subagent context (rendered by the engineer/runner from the
        # live ``.argus_subagents`` registry). Present only when this mission has
        # in-flight subagents. A SUPERVISED subagent advancing on its own is NOT
        # by itself the engineer's forward progress, so we steer next_action away
        # from "poll again" toward independent work (or an explicit cadence
        # yield) without forcing a forward_progress value.
        background_block = ""
        if background_context.strip():
            background_block = (
                f"\n{background_context.strip()}\n\n"
                "Reviewer note on the above: these are SUPERVISED subagents with "
                "their own independent supervisor, so their autonomous progress is "
                "NOT by itself the engineer's forward progress. If the engineer only "
                "re-polled a healthy self-watched subagent this round, steer "
                "`next_action` to advance independent work that does not depend on "
                "it — or, if nothing else can proceed, to yield with "
                "`WAIT_FOR_SUBAGENT: <task_id>` — rather than prescribing another "
                "poll.\n"
            )
        # Curated working-memory: show the reviewer the checkpoint it authored
        # last round so it can do deliberate CRUD (add/update/delete) rather
        # than re-deriving memory from scratch. The engineer's HANDOFF proposal
        # arrives inside ``main_summary``; the reviewer validates it against
        # checks/artifacts and emits the next canonical checkpoint.
        from ..engineer.checkpoint import CheckpointState as _CheckpointState
        prior_cp = _CheckpointState.from_dict(prior_checkpoint or {})
        if prior_cp.is_empty():
            prior_checkpoint_block = (
                "## Curated working memory (checkpoint)\n"
                "No prior checkpoint yet — author the first one from the "
                "engineer's HANDOFF and the verified facts of this round.\n\n"
            )
        else:
            _cp_done = "\n".join(f"  - {d}" for d in prior_cp.done) or "  - (none)"
            _cp_fail = (
                "\n".join(f"  - {d}" for d in prior_cp.tried_and_failed)
                or "  - (none)"
            )
            _cp_maturing = (
                "\n".join(f"  - {d}" for d in prior_cp.maturing)
                or "  - (none)"
            )
            _cp_facts = (
                "\n".join(f"  - {d}" for d in prior_cp.env_facts)
                or "  - (none)"
            )
            _al = prior_cp.active_line if isinstance(prior_cp.active_line, dict) else {}
            _cp_active = (
                f"{_al.get('desc', '')} | code saved at "
                f"{_al.get('branch_or_path', '?')} | developed "
                f"{_al.get('rounds_active', 0)} round(s) | next: {_al.get('note', '')}"
                if _al else "(none yet)"
            )
            prior_checkpoint_block = (
                "## Curated working memory (checkpoint) — PRIOR\n"
                f"goal: {prior_cp.goal or '(unset)'}\n"
                f"done:\n{_cp_done}\n"
                f"tried_and_failed:\n{_cp_fail}\n"
                f"maturing (tried, not yet succeeding — NOT dead ends):\n{_cp_maturing}\n"
                "active_line (a bold/structural line being matured ABOVE the floor "
                "— BUILD ON IT; do NOT restore the floor while it is alive): "
                f"{_cp_active}\n"
                f"open_blocker: {prior_cp.open_blocker or '(none)'}\n"
                f"next_step: {prior_cp.next_step or '(none)'}\n"
                f"env_facts:\n{_cp_facts}\n\n"
            )
        # Anti-livelock escalation directive (supplied by the round loop once a
        # mission passes the soft round limit): tell the reviewer to escalate an
        # unresolvable EXTERNAL blocker to `blocked` instead of looping `continue`.
        escalate_block = ""
        if escalate_hint:
            escalate_block = (
                "## Escalation directive (operator harness — IMPORTANT)\n"
                f"{escalate_hint}\n\n"
            )
        # Final-submission completion contract. This block replaces the
        # retired hardcoded EMNLP validators: instead of the supervisor
        # running ``validate_full_emnlp_readiness`` and friends, the reviewer
        # is the single source of truth for whether the *whole project* is
        # ready to submit. It only fires for final_submission missions.
        final_submission_block = ""
        if is_final_submission:
            final_submission_block = (
                "## FINAL SUBMISSION CONTRACT (scope = final_submission)\n"
                "This mission's scope is `final_submission`: you are certifying\n"
                "that the ENTIRE research pipeline is complete and ready to\n"
                "submit, not just one bounded task. You are the single source of\n"
                "truth for project completion — there is no separate validator.\n\n"
                "You MUST:\n"
                "1. Set `scope` to `final_submission` in your JSON response.\n"
                "2. Populate `checklist` with ONE entry per item in the full\n"
                "   pipeline checklist above. Each entry needs `item` (the\n"
                "   checklist item text), `satisfied` (true/false), and\n"
                "   `evidence` (concrete proof you verified yourself: command\n"
                "   output, file contents, query rows). `evidence` must be\n"
                "   non-empty for every item you mark `satisfied: true`.\n"
                "3. Choose `status: done` ONLY when EVERY checklist item is\n"
                "   `satisfied: true` with concrete evidence. If even one item\n"
                "   is unmet or lacks evidence, choose `status: continue` and\n"
                "   list every unmet item with the exact repair steps in\n"
                "   `next_action`.\n"
                "Do not certify on the engineer's word alone — re-run the\n"
                "verification commands yourself and cite your own output.\n\n"
            )
        if not _full_emnlp:
            # non-paper vertical: no paper stages to roll back to, and no
            # final-submission certification — judge only the vertical's metric.
            rollback_block = ""
            final_submission_block = ""
        from ..skills.ground_truth import ground_truth_mandate

        return (
            ground_truth_mandate("reviewer")
            + optimize_banner
            + search_altitude_block
            + "You are the reviewer sub-agent for an argus-skill autoloop run.\n"
            "Decide whether the objective is fully complete.\n\n"
            + _verification_directive()
            + "**Never mark `done` on a generic role acknowledgment** (e.g. the\n"
            "engineer merely says it will act as the primary agent / take\n"
            "ownership) without concrete execution evidence — actual command\n"
            "output, file diffs, or query results — that you have verified.\n\n"
            "Return valid JSON matching the provided schema.\n"
            "Do not wrap the response in markdown fences.\n\n"
            f"{reviewer_role_context}"
            "Reviewer-to-engineer handoff skill:\n"
            f"{handoff_skill}\n\n"
            f"{paper_review_skill_block}"
            f"{wiki_curator_skill_block}"
            f"{matched_review_skill_block}"
            f"{stage_checklist}\n\n"
            f"{final_submission_block}"
            f"{rollback_block}\n\n"
            f"{venv_skill_block}\n\n"
            f"{prior_checkpoint_block}"
            f"{escalate_block}"
            "**Length constraints:**\n"
            "- Be thorough in `round_summary_markdown` — include all relevant details\n"
            "- Use brief bullet points, not lengthy explanations\n"
            "- `next_action` must contain ALL specific issues, failure details, and repair steps\n"
            "  the engineer needs — do NOT summarize away critical information\n\n"
            "Required JSON keys:\n"
            "- status\n"
            "- confidence\n"
            "- reason\n"
            "- next_action\n"
            "- round_summary_markdown\n"
            "- completion_summary_markdown\n"
            "- planner_report (object: forward_progress, headline, blocker,\n"
            "  recommended_next, evidence_files[{path, why}])\n"
            "- checkpoint (object: goal, done[], tried_and_failed[],\n"
            "  maturing[], active_line{desc, branch_or_path, rounds_active, note},\n"
            "  open_blocker, next_step, env_facts[]). env_facts =\n"
            "  durable environment/infra facts the successor must NOT re-derive\n"
            "  (paths, access endpoints, versions, what's ephemeral vs\n"
            "  persistent); carry prior env_facts forward and add any newly\n"
            "  established this round, dropping the least load-bearing to stay\n"
            "  within the cap.\n"
            "Optional JSON keys (REQUIRED when scope is final_submission):\n"
            "- scope (`bounded` or `final_submission`)\n"
            "- checklist (array of {item, satisfied, evidence})\n"
            "Optional JSON keys (emit on any non-`done` verdict — see\n"
            "SKILL-EVOLUTION SIGNAL below):\n"
            "- failure_cause (`skill_gap` | `execution_mistake` |\n"
            "  `ambiguous_objective` | `environmental` | `method_failure` |\n"
            "  `unknown`)\n"
            "- mission_lesson (string; non-empty ONLY when failure_cause is\n"
            "  `skill_gap`)\n"
            "Always-emit JSON key (judge on EVERY verdict — see PROCESS\n"
            "SELF-DISTILLATION below):\n"
            "- process_lesson (string; empty when nothing reusable)\n"
            "Optional JSON key (emit on ANY verdict — success OR failure — see\n"
            "SKILL MEMORY below):\n"
            "- skill_ops (array of {op, name, content, why}; omit or [] when this\n"
            "  round warrants no skill change)\n\n"
            "Planner report rules (this object is the ONLY thing the project\n"
            "planner reads about this mission — keep it a clean, structured,\n"
            "self-contained briefing, NOT raw logs or terminal output):\n"
            "- `forward_progress`: boolean. TRUE only if this mission actually\n"
            "  moved the project closer to the operator goal. Set FALSE when\n"
            "  the mission merely passed through an allowed blocked / rollback /\n"
            "  not-launched / gate-blocked escape path, or only renamed/\n"
            "  refreshed artifacts while the real blocker remains — even if\n"
            "  `status` is `done`. FOR AN OPTIMIZATION / SPEEDRUN / METRIC mission:\n"
            "  forward_progress is TRUE whenever the round produced a NEW\n"
            "  verifier-measured data point that advances the active line —\n"
            "  INCLUDING a structural / optimizer / architecture / precision change\n"
            "  that REGRESSED vs the current best but was honestly measured and\n"
            "  reverted (it co-tuned or ruled out a real direction; a measured-and-\n"
            "  reverted bold experiment is GOOD process, NOT failure — do not punish\n"
            "  it, the verified global-best floor is never lost). Reserve FALSE for\n"
            "  rounds that produced NO new measured evidence (crash / NaN /\n"
            "  no-measurement / pure no-op / rename / refresh / escape path), or a\n"
            "  stalled single-knob nibble that neither beat the floor nor advanced a\n"
            "  declared structural line. CONSULT the Search-altitude facts above when\n"
            "  they are present: if they show the FLOOR unchanged across many\n"
            "  attempts AND the last deltas are all within run-to-run noise\n"
            "  (~0.001-0.002) AND this candidate merely RE-COMBINES levers already\n"
            "  tried (see the attempt-name token frequency), that is the stalled-\n"
            "  nibble case → forward_progress=FALSE (so the stall guards can act);\n"
            "  do NOT score it TRUE just because it was 'measured'. CONVERSELY, a\n"
            "  round that produced a genuine MEASURED DIAGNOSIS artifact (a step\n"
            "  profile, a train-vs-val curve read, a within-attempt ablation) is\n"
            "  forward_progress=TRUE even with no new scored candidate — diagnosis\n"
            "  the next candidate needs is real progress, not a wasted round.\n"
            "- `headline`: one or two plain sentences stating what actually\n"
            "  changed or was proven this mission. No ANSI codes, no banners,\n"
            "  no command dumps.\n"
            "- `blocker`: the single most important unresolved blocker the\n"
            "  planner must address next, with its root cause and the owning\n"
            "  stage if known (e.g. `plan-level method defect: all conditions\n"
            "  emit identical outputs; owning stage = plan`). Empty string if\n"
            "  nothing blocks forward progress.\n"
            "- `recommended_next`: the concrete next mission focus you advise\n"
            "  (e.g. `pivot the method` / `roll back to plan and redesign\n"
            "  condition separation`), or empty string if the project is done.\n"
            "  Do NOT recommend re-running an equivalent task that leaves the\n"
            "  blocker in place.\n"
            "- `evidence_files`: array of {path, why} (≤8) — the SPECIFIC files\n"
            "  the planner must OPEN to understand/diagnose what happened. Use\n"
            "  ABSOLUTE paths or project-root-relative paths the planner can open\n"
            "  from the project root. For a failed / no-progress / surprising run\n"
            "  this is REQUIRED and must point at the real evidence, not just a\n"
            "  generated summary: the run dir's `status.json` / `progress.jsonl`,\n"
            "  any supervisor handoff/verdict, the training/eval SOURCE script,\n"
            "  the DATA-PROVENANCE file (which dataset/rows the run consumed and\n"
            "  where they came from), the reward/metric diagnostics, and any\n"
            "  mechanical `*_NO_GO.md`. `why` says in one phrase what the planner\n"
            "  will learn by reading it. Empty array only when nothing on disk\n"
            "  would help the planner (e.g. a trivially-done doc task).\n"
            "- RUN-HEALTH AUTHORITY: a mechanical health-gate / `*_NO_GO.md` /\n"
            "  `status.json state=failed` that was produced by a METRIC THRESHOLD\n"
            "  (e.g. one tail step's `clipped_ratio`, a short-window reward dip) is\n"
            "  ADVISORY, NOT the verdict. Judge run health from the METRIC TREND\n"
            "  and the supervisor's handoff. Do NOT set `forward_progress=false`\n"
            "  or recommend a relaunch SOLELY because a mechanical terminal gate\n"
            "  tripped — only a real failure (crash/OOM/NaN/timeout/collapsing\n"
            "  trend) or genuinely-unusable evidence justifies that.\n"
            "- GRADUATION POLICY (stop the smoke-thrash): a smoke / micro-run\n"
            "  (tiny `max_steps`, `num_generations=2`, a handful of rows) only\n"
            "  validates that the harness WIRING runs — it is never paper\n"
            "  evidence. Once a smoke proves the pipeline executes, the next\n"
            "  mission must be EITHER (a) launch the real pilot/full training\n"
            "  (scale steps/data/generations up), OR (b) diagnose a NAMED\n"
            "  root-cause hypothesis from the evidence_files. Recommending yet\n"
            "  another equivalent micro-smoke with only a threshold/flag tweak is\n"
            "  `forward_progress=false` unless it explicitly tests a named\n"
            "  hypothesis.\n"
            "- NO-GO ATTRIBUTION (never falsify an IDEA on a misconfigured run):\n"
            "  when an RL / post-training method UNDERPERFORMS a baseline (a no-go\n"
            "  / negative delta), an underperformance result retires or pivots\n"
            "  away from the METHOD/IDEA only AFTER you confirm the EXECUTED run\n"
            "  was a fair, well-configured run — read its manifest + rollout\n"
            "  diagnostics, do not just trust that it matched a (possibly\n"
            "  underpowered) plan. Consult the matched method-diagnosis skill\n"
            "  (for RL/post-training, `rl-training-collapse-diagnosis`) as the\n"
            "  AUTHORITY for the collapse signatures and sane-regime thresholds —\n"
            "  apply ITS criteria rather than re-deriving them here. Label the\n"
            "  outcome `misconfigured_run`, `method_failure`, or\n"
            "  `infeasible_under_budget`. If the skill's signatures show the run\n"
            "  was misconfigured (e.g. truncated rollouts, zero reward variance,\n"
            "  sub-RL-scale knobs, or a health-gate gamed by terse/`answer_only`\n"
            "  rollouts that suppress needed reasoning), it is `misconfigured_run`:\n"
            "  roll back and re-run with the correction the skill names, do NOT\n"
            "  record the idea as dead. Only after ONE corrected, sane-regime run\n"
            "  STILL loses may you treat it as `method_failure`. Do not demand\n"
            "  endless reruns: once a fair run exists, or the sane regime is\n"
            "  unreachable within budget (`infeasible_under_budget`), let the\n"
            "  verdict stand.\n\n"
            "- SKILL-EVOLUTION SIGNAL (you decide whether a failure should\n"
            "  teach a skill — do NOT leave this to a status heuristic): on any\n"
            "  non-`done` verdict, set `failure_cause` to one of `skill_gap`,\n"
            "  `execution_mistake`, `ambiguous_objective`, `environmental`,\n"
            "  `method_failure`, or `unknown`. Use `skill_gap` when the failure\n"
            "  was a FIXABLE knowledge/configuration gap that a future mission\n"
            "  could avoid if it knew better — e.g. an RL run that lost because\n"
            "  of underpowered/wrong HYPERPARAMETERS, truncated rollouts, a\n"
            "  reward/length-gate gamed by `answer_only`, a wrong model/base vs\n"
            "  instruct, or a missing methodology step (this is the\n"
            "  `misconfigured_run` case above). Use `method_failure` ONLY when\n"
            "  the IDEA itself is genuinely dead after a fair, sane-regime run —\n"
            "  there is NO reusable fix, so do not emit a lesson. For\n"
            "  `skill_gap` ONLY, also emit `mission_lesson`: a concise, GENERAL,\n"
            "  reusable paragraph (not a transcript) stating the corrected\n"
            "  approach and the concrete regime/threshold to use next time (e.g.\n"
            "  'For 14B RLVR on reasoning tasks, set max_completion_length>=N,\n"
            "  steps>=M, num_generations>=K; never cap rollouts below the\n"
            "  task's reasoning length or per-group reward variance collapses\n"
            "  to zero'). Leave `mission_lesson` empty for every other cause.\n\n"
            "- PROCESS SELF-DISTILLATION (you decide, on EVERY verdict — success\n"
            "  OR failure — once per mission): SEPARATE from failure_cause/\n"
            "  mission_lesson (which are about the research METHOD), judge whether\n"
            "  the agent's PROCESS this mission — HOW it worked, not whether it\n"
            "  won — yielded a reusable lesson for FUTURE missions: wasted or\n"
            "  repeated rounds, a stall, the same wall re-hit, an INCENTIVE\n"
            "  CONTRADICTION (a prompt/role exhorted X while a gate, counter, or\n"
            "  trigger rewarded the opposite), a checklist that misfired, or a\n"
            "  workaround that WORKED and should become standard. Emit\n"
            "  `process_lesson`: a concise, GENERAL one-liner naming the recurring\n"
            "  PROCESS pattern + the corrected process (NOT this mission's\n"
            "  content/numbers). This distills the agent's PROCESS ONLY; you may\n"
            "  NEVER propose changing the outcome/metric/verifier/validity test —\n"
            "  those are FROZEN. Empty string when this mission's process had\n"
            "  nothing reusable. Most missions: empty. Emit only a real, general\n"
            "  process improvement, not a restatement of what happened.\n\n"
            "- SKILL MEMORY (you own the skill library; emit `skill_ops` on ANY\n"
            "  verdict, success OR failure). You may:\n"
            "  * create — a NEW capability playbook the engineer lacked. Set\n"
            "    op=create, content=the full skill markdown. Write it for a\n"
            "    FAMILY of tasks, NOT this single mission: title a CAPABILITY\n"
            "    (e.g. 'Recover benchmark official surface', not 'fix run X');\n"
            "    use angle-bracket placeholders (<path>, <N>) for anything that\n"
            "    varies; never hardcode this mission's paths/ids/numbers. Include\n"
            "    When-to-use, When-NOT-to-use, How-to-solve (+common pitfalls).\n"
            "  * update — fold a lesson into an EXISTING matched skill. Set\n"
            "    op=update, name=its name, content=the FULL revised markdown.\n"
            "  * archive / delete — retire a matched skill you found WRONG, harmful,\n"
            "    or mis-scoped (it steered the engineer wrong). Set op=archive,\n"
            "    name=its name, why=one clause. This is your direct authority.\n"
            "  create/update are PROPOSALS: a Manager generality-gate rejects any\n"
            "  skill that only fits THIS task, so keep them genuinely reusable or\n"
            "  they will be thrown away. Most rounds need NO skill_ops — emit it\n"
            "  ONLY for a real, reusable capability change. Never propose a skill\n"
            "  for a one-off environmental blocker (that is not a skill gap).\n\n"
            "Checkpoint rules (you are the MEMORY AUDITOR; this object becomes\n"
            "the engineer's ENTIRE working memory next round — the raw session\n"
            "is dropped, so a fresh engineer sees ONLY this):\n"
            "- Author the next checkpoint from (a) the PRIOR checkpoint above\n"
            "  and (b) the engineer's `HANDOFF:` block in its summary. The\n"
            "  engineer PROPOSES; you VALIDATE and curate. Never copy a claim\n"
            "  you cannot back with checks/artifacts.\n"
            "- This is curated working memory, NOT a log. It is hard-capped\n"
            "  (done ≤ 8, tried_and_failed ≤ 6, maturing ≤ 5, short strings).\n"
            "  The cap forces you to FORGET: keep only items that change what\n"
            "  the next session does; delete resolved blockers, stale plans,\n"
            "  and low-value\n"
            "  detail. Deletion is correct, not lossy — ground truth stays on\n"
            "  disk and is re-summonable.\n"
            "- `goal`: the mission's end goal in one line (carry it forward).\n"
            "- `done`: only VERIFIED accomplishments, each with the proof\n"
            "  (command/file). Never list an unverified self-report. If a check\n"
            "  is `[FAIL]`, the checked objective is NOT done.\n"
            "- `tried_and_failed`: GENUINE dead ends with the reason, so the\n"
            "  successor does not repeat them (prevents a Sisyphus loop). Keep\n"
            "  the ones tied to the current blocker; drop irrelevant ones. Do\n"
            "  NOT dump a promising approach here just because it failed on its\n"
            "  FIRST round — if it is worth refining, it belongs in `maturing`.\n"
            "- `maturing`: directions/approaches that were TRIED and did NOT yet\n"
            "  succeed but are NOT dead ends. An early attempt at a new approach\n"
            "  often underperforms a tuned baseline until it is refined, so\n"
            "  these merit further development before being abandoned. Each\n"
            "  item: the direction + the SPECIFIC next refinement to try. It is\n"
            "  YOUR research judgment when a maturing direction has had a fair\n"
            "  window and still fails — only THEN demote it to\n"
            "  `tried_and_failed`. Because this is the engineer's entire working\n"
            "  memory, carrying a maturing direction forward is what lets it be\n"
            "  refined over several rounds instead of re-discovered much later.\n"
            "- `active_line`: the ONE bold/structural direction currently being\n"
            "  matured on a retained branch that may sit ABOVE the global-best\n"
            "  floor. Object: `desc` (what it is), `branch_or_path` (where its\n"
            "  train.py is saved so the next engineer checks it out and BUILDS ON\n"
            "  it), `rounds_active` (increment each round it keeps developing),\n"
            "  `note` (the specific next refinement). THE DEFAULT, INVERTED: when\n"
            "  this round's candidate was MEASURED but not promoted yet is within\n"
            "  run-to-run noise (~0.001-0.002) of the floor OR advances a declared\n"
            "  structural direction, you OPEN or CONTINUE an `active_line` from it\n"
            "  (save its train.py to a named branch, carry it here) — you do NOT\n"
            "  discard it and send the next candidate back to the global-best\n"
            "  floor. Snapping the next candidate's base back to the global-best\n"
            "  floor is reserved for when the active line has had a FAIR window and\n"
            "  is genuinely dead (then demote it to `tried_and_failed` and clear\n"
            "  `active_line`), or for a deliberate fresh regime change. The\n"
            "  global-best floor is never lost — it stays recoverable on disk — so\n"
            "  developing an above-floor active line risks nothing.\n"
            "- `open_blocker`: the single most important unresolved blocker +\n"
            "  root cause. Do NOT delete it unless it is resolved (move to\n"
            "  `done`) or replaced by a more specific one.\n"
            "- `next_step`: the most useful next concrete action. If an\n"
            "  `active_line` is alive, `next_step` MUST be to CONTINUE developing\n"
            "  it from its saved branch (the named refinement) — NOT 'restore the\n"
            "  global-best floor and tweak it'. Authoring 'restore the floor + one\n"
            "  knob' round after round while the floor never moves is the greedy\n"
            "  rut the active_line exists to break.\n"
            "- Carry forward load-bearing prior items the engineer did not\n"
            "  mention — your job is to PRESERVE valuable memory across the\n"
            "  session boundary, only dropping what is genuinely low-value.\n\n"
            "Decision rules:\n"
            "1) Choose `done` ONLY when the main agent's last summary contains\n"
            "   CONCRETE EVIDENCE that the work succeeded: actual command output,\n"
            "   test results, file inspections with shown contents, query\n"
            "   results, or other artifact you can read. A bare assertion such\n"
            "   as `I implemented X` or `Verified that file Y exists` WITHOUT\n"
            "   showing the actual command + output is NOT evidence.\n"
            "1a) Symmetric stop rule: if the agent's last summary DOES include\n"
            "   verbatim command output (e.g. pytest pass count, script stdout,\n"
            "   `ls -l` showing the artifact) AND that output directly\n"
            "   satisfies the operator request, choose `done`. Do NOT demand\n"
            "   yet another round whose only purpose is re-running the same\n"
            "   commands and re-printing the same outputs. That wastes rounds\n"
            "   and tokens. Anti-pattern: agent shows test_accuracy=0.98 + the\n"
            "   classification report → reviewer says `continue` asking for\n"
            "   `run the script and paste the output` even though the agent\n"
            "   just did exactly that. WRONG. Choose `done`.\n"
            "2) Default to `continue` whenever the agent's claims are not backed\n"
            "   by concrete artifacts in the summary. Better to spend another\n"
            "   round verifying than declare premature `done`. The agent has no\n"
            "   ground-truth signal — your job is to demand evidence. But once\n"
            "   the evidence is in front of you (rule 1a), stop.\n"
            "2a) Acceptance-check failures override all self-report. If any\n"
            "   check in `Acceptance check results` is `[FAIL]`, choose\n"
            "   `continue` even if the main agent claims success. Your\n"
            "   `next_action` is the only repair prompt the engineer receives,\n"
            "   so include the failed command, exit code,\n"
            "   concrete issue codes/paths/messages, likely root cause, ordered\n"
            "   repair steps, and exact rerun command. Include full check output\n"
            "   so the engineer has all the context needed to fix the issue.\n"
            "   For any acceptance command that emits structured issues, list\n"
            "   every issue with its code, path, and message, then provide\n"
            "   concrete repair instructions.\n"
            "3) When `continue`, `next_action` must be a concrete instruction.\n"
            "   If the round genuinely LACKS the evidence to judge, ask for the\n"
            "   SPECIFIC verification command (e.g. `run pytest -xvs and paste\n"
            "   the full output`, `cat the produced file and show first 50\n"
            "   lines`). But when the evidence is already in hand and honest, do\n"
            "   NOT spend `next_action` re-requesting it — point the engineer at\n"
            "   the specific NEXT work or unexplored direction instead.\n"
            "4) Use `blocked` ONLY when additional user input is strictly\n"
            "   required to make ANY further progress (e.g. missing\n"
            "   credentials, ambiguous spec the user must clarify, hardware\n"
            "   the agent cannot access). A failing test, a runtime error,\n"
            "   incorrect output, or any other condition the agent COULD\n"
            "   attempt to fix on its own is NOT `blocked` — it is\n"
            "   `continue` with a concrete next_action telling the agent\n"
            "   what to debug. When in doubt, prefer `continue` over\n"
            "   `blocked`. Example: tests still failing → `continue`, NOT\n"
            "   `blocked`. Example: file not yet created → `continue`,\n"
            "   NOT `blocked`.\n"
            "5) `round_summary_markdown` summarizes this round's completed work,\n"
            "   evidence shown, and remaining gaps.\n"
            "6) If status is not `done`, `completion_summary_markdown` should be\n"
            "   a short placeholder or empty note.\n"
            "7) If status is `done`, `completion_summary_markdown` must quote\n"
            "   the concrete evidence (command + output) that establishes\n"
            "   success. No evidence → not done.\n"
            "8) Spec adherence: when the operator's request specifies CONCRETE\n"
            "   STRUCTURAL CONSTRAINTS — exact file paths, module/package\n"
            "   names, framework choice (e.g. `pytest` vs `unittest`),\n"
            "   API signatures, return-type contracts, count of test cases,\n"
            "   directory layout — the produced artifacts MUST match those\n"
            "   constraints unless the agent explicitly justified the\n"
            "   deviation in its summary AND the deviation is materially\n"
            "   equivalent. Any unjustified structural deviation → `continue`\n"
            "   with `next_action` naming the deviation (e.g. `the request\n"
            "   asked for tracker.py + pytest tests, but the agent built an\n"
            "   expense_tracker/ package using unittest. Either restructure\n"
            "   to match the spec or justify the deviation in the summary`).\n"
            "   Functional correctness alone is NOT sufficient when the\n"
            "   operator gave a precise structural contract. This rule\n"
            "   protects users who rely on exact paths/frameworks for\n"
            "   downstream tooling.\n"
            "9) Final-submission scope: ONLY when the Objective metadata says\n"
            "   `planner_scope: final_submission` or `Task scope: final_submission`,\n"
            "   choose `done` only if every item on the **full pipeline checklist**\n"
            "   injected above is satisfied (research → submission). A passing\n"
            "   single-stage checklist, a pilot run, or an underlength draft is NOT\n"
            "   enough for final submission. For positive paper objectives, do not\n"
            "   accept a negative-result pivot or a baseline-only win: the proposed\n"
            "   contribution must have a structured X-Y-Z-W paper_contribution claim\n"
            "   and beat the strongest nontrivial baseline on the declared metric\n"
            "   with statistical support. For `planner_scope: bounded` or absent\n"
            "   scope metadata, do not require the full pipeline checklist; judge\n"
            "   the bounded task by its own acceptance criteria and the relevant\n"
            "   per-stage checklist items.\n\n"
            "Original operator request (immutable anchor):\n"
            f"{(original_objective or objective).strip()}\n\n"
            "Current mission objective (may include planner/prelude context):\n"
            f"{objective}\n\n"
            "Operator message history (source of truth for user instructions):\n"
            f"{operator_text}\n\n"
            "Planner guidance for this review:\n"
            f"{planner_review_instruction or 'none'}\n\n"
            f"Round: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n"
            f"{shared_context_block}"
            f"{background_block}"
            f"Main agent fatal error: {error_text}\n\n"
            "Main agent last summary:\n"
            f"{main_summary}\n\n"
            "Acceptance check results (include full details in next_action so engineer can act on them):\n"
            f"{check_text}\n"
            f"{evidence_block}"
        )


_MAX_SHARED_CTX_CHARS = 30000


def _format_engineer_shared_context(
    *,
    skill_used: str | None,
    engineer_reasoning_summary: str,
    prev_review_summary: str,
) -> str:
    """Render the read-only shared context block injected into reviewer prompts.

    Keep this renderer stable because the same block is consumed across
    engineer/reviewer round boundaries.
    """
    skill = (skill_used or "").strip()
    reasoning = (engineer_reasoning_summary or "").strip()
    prev = (prev_review_summary or "").strip()
    if not skill and not reasoning and not prev:
        return ""
    parts = ["Shared read-only context (do NOT modify; advisory only):"]
    if skill:
        parts.append(f"- skill_used: {skill}")
    if reasoning:
        if len(reasoning) > _MAX_SHARED_CTX_CHARS:
            reasoning = reasoning[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in reasoning.splitlines())
        parts.append("- engineer_reasoning_summary:\n" + indented)
    if prev:
        if len(prev) > _MAX_SHARED_CTX_CHARS:
            prev = prev[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in prev.splitlines())
        parts.append("- previous_review_summary:\n" + indented)
    return "\n".join(parts) + "\n\n"
