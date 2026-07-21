"""Reviewer sub-agent: graded "done / continue / blocked" verdict.

Provenance: vendored from ``ArgusBot/agent_cli/reviewer.py``. The
substantive change is decoupling: the original took a ``AgentCliRunner``
directly; this version takes any ``RunnerBackend`` (see
``argus_skill.core.ports``) so it works with any supported agent CLI or the
in-memory test stub equally well.

Public surface kept identical: ``Reviewer.evaluate(...) -> ReviewDecision``,
``parse_decision_text(text) -> ReviewDecision | None``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..core.models import ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.stop_kinds import normalize_stop_kind
from ..core.task_contract import EFFECTIVE_TASK_CONTRACT
from ..engineer.checkpoint import shared_checkpoint_instructions
from ._parsing import _find_decision_in_messages

log = logging.getLogger(__name__)

# F7 anti-rubber-stamp guard. Prepended to the per-round DELTA only when the
# reviewer is RESUMING its own thread (the full static rubric is already in the
# thread). Resuming saves tokens; it must NEVER become deference to the prior
# verdict. The role/rubric/decision-rules from earlier in the thread still bind,
# but THIS round's artifacts (below) are the only evidence — re-verify against
# them. This preserves reviewer independence under HARD CONSTRAINT 3.
_REEVALUATE_HEADER = (
    "## NEW ROUND — RE-EVALUATE INDEPENDENTLY (resumed reviewer)\n"
    "You are resuming your OWN thread ONLY to avoid re-sending the static rubric "
    "— NOT to defer to your previous verdict. The role, rubric, and decision "
    "rules from earlier in this thread still bind, but THIS round's artifacts "
    "below are the ONLY evidence: re-verify against them from scratch. Your prior "
    "verdict is not a prior and must never be rubber-stamped; judge this round on "
    "its own artifacts, summary, and log audit.\n\n"
)


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    working_dir: str | None = None


SCHEMA_PATH = str(Path(__file__).with_name("reviewer_schema.json"))
RESEARCH_SCHEMA_PATH = str(Path(__file__).with_name("reviewer_research_schema.json"))
LEGACY_RESEARCH_SCHEMA_PATH = str(
    Path(__file__).with_name("reviewer_legacy_research_schema.json")
)


def _compact_schema_for_backend(
    schema_path: str,
    schema_contract: bytes,
) -> tuple[str, bytes]:
    """Return a content-addressed minified schema path for provider input.

    Keep the checked-in schema readable, but do not spend tokens on indentation
    and descriptive whitespace every review turn. Any parse/cache failure falls
    back to the authoritative original bytes/path.
    """
    try:
        payload = json.loads(schema_contract)
        compact = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(compact) >= len(schema_contract):
            return schema_path, schema_contract
        digest = hashlib.sha256(compact).hexdigest()[:20]
        cache_dir = Path(tempfile.gettempdir()) / "argus-skill-reviewer-schemas"
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        cached_path = cache_dir / f"{Path(schema_path).stem}-{digest}.json"
        if not cached_path.exists() or cached_path.read_bytes() != compact:
            fd, temp_name = tempfile.mkstemp(
                dir=cache_dir,
                prefix=f".{cached_path.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(compact)
                os.replace(temp_name, cached_path)
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        return str(cached_path), compact
    except (OSError, TypeError, ValueError):
        return schema_path, schema_contract


def _project_has_wiki(
    working_dir: str | Path | None = None,
) -> bool:
    project_root = Path(working_dir).expanduser() if working_dir else Path.cwd()
    autors = project_root / ".autors"
    if not autors.exists():
        return False
    from ..wiki.bootstrap import is_initialized_wiki
    return any(
        is_initialized_wiki(p / "wiki") for p in autors.iterdir() if p.is_dir()
    )


def _load_wiki_curator_skill_if_present(
    working_dir: str | Path | None = None,
) -> str | None:
    """Compatibility helper returning the compact wiki-curator contract."""
    if not _project_has_wiki(working_dir):
        return None
    return (
        "wiki-curator: directly maintain durable technique/conflict/pattern pages "
        "only when grounded in exact quotes from immutable wiki sources."
    )


def _direct_memory_edit_block(
    skill_store: Any,
    working_dir: str | Path | None,
) -> str:
    project_store = getattr(skill_store, "project", None)
    skill_dir_value = getattr(project_store, "skills_dir", None)
    if skill_dir_value is None:
        skill_dir_value = getattr(skill_store, "skills_dir", None)
    skill_dir = (
        Path(skill_dir_value).expanduser().resolve()
        if skill_dir_value is not None
        else None
    )
    project_root = (
        Path(working_dir).expanduser().resolve()
        if working_dir
        else Path.cwd().resolve()
    )
    wiki_roots: list[Path] = []
    try:
        from ..wiki.auto_hooks import discover_wikis

        wiki_roots = [path.resolve() for path in discover_wikis(project_root)]
    except Exception:  # noqa: BLE001
        wiki_roots = []
    if skill_dir is None and not wiki_roots:
        return ""
    wiki_lines = "\n".join(f"- {path}" for path in wiki_roots) or "- none"
    skill_line = str(skill_dir) if skill_dir is not None else "none"
    return (
        "## Direct reusable-memory maintenance\n"
        "You are an executable Reviewer with file and shell tools. If this round "
        "contains durable, reusable learning, edit the project memory directly "
        "BEFORE your final verdict. Do not describe a proposed edit in final JSON.\n"
        f"Project skill directory (project layer only): {skill_line}\n"
        "Project wiki directories:\n"
        f"{wiki_lines}\n"
        "Skill rules: inspect the existing Markdown first; create or edit only in "
        "the project skill directory; preserve valid frontmatter; increment "
        "`version` on an update; never modify a skill with `protected: true`; never "
        "write the shared/global skill layer.\n"
        "Wiki rules: directly edit durable pages under `pages/`; never rewrite "
        "immutable `sources/`; include exact source IDs/quotes for factual learning.\n"
        "If there is no durable reusable lesson, make no memory edit. The final "
        "handoff schema intentionally has no `skill_ops` or `wiki_ops`.\n\n"
    )


def _format_academic_paper_review_skill_block(*, include: bool) -> str:
    if not include:
        return ""
    return (
        "## Near-complete paper review\n"
        "Be a skeptical program-committee reviewer: require a clear contribution, "
        "credible comparisons, sufficient evidence/statistics, accurate citations, "
        "readable writing, and clean figures/layout. `done` requires the applicable "
        "final checklist with no critical blocker; do not reward polish without "
        "substantive evidence.\n\n"
    )


def _verification_directive() -> str:
    """Compact trust-first verification stance."""
    return (
        "**Trust the engineer by default; verify only on doubt.** If the summary "
        "shows internally consistent RESULT/test/file evidence, do NOT reflexively "
        "re-run it. Use shell checks only when evidence is MISSING, "
        "self-contradictory, implausible, or conflicts with an acceptance check. "
        "Spend the saved effort judging whether the work is genuinely novel/useful "
        "and naming the specific NEXT work or unexplored direction.\n\n"
    )


def _prompt_block_stats(blocks: Mapping[str, str]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for name, text in blocks.items():
        rendered = str(text or "")
        byte_count = len(rendered.encode("utf-8"))
        stats[str(name)] = {
            "chars": len(rendered),
            "bytes": byte_count,
            "estimated_tokens": (byte_count + 3) // 4,
        }
    return stats


def _reviewer_evidence_contract(
    workflow_mode: str,
    *,
    mandatory_engineering_audit: bool = False,
) -> str:
    """Small, non-contradictory evidence policy for the Reviewer."""
    mode = (workflow_mode or "").strip().lower()
    if mode == "direct":
        scope = "Verify only facts material to the requested deliverable; do not invent research scaffolding."
    elif mode == "proportional":
        scope = "Verify the new claim/delta and reuse previously certified evidence unless a concrete conflict exists."
    else:
        scope = (
            "Inspect relevant artifacts/logs and require the staged ground-truth record "
            "when the active checklist calls for it."
        )
    verification = (
        "Shown command/scorer output is only a lead: inspect the material source "
        "and raw artifacts with tools before deciding."
        if mandatory_engineering_audit
        else (
            "Consistent shown command/scorer output is evidence; re-run only on "
            "missing, stale, contradictory, or implausible facts."
        )
    )
    return (
        "## Evidence policy\n"
        "实事求是: never fabricate evidence or approve an unsupported material claim. "
        f"{scope} {verification}\n\n"
    )






def _engineer_log_audit_block(
    engineer_log_path: str,
    *,
    engineer_call_id: str = "",
    round_index: int,
    measured: bool,  # noqa: ARG001 — round_index kept for call-site symmetry with the other audit blocks
    compact: bool = False,
) -> str:
    """Reviewer prompt section for auditing the engineer's EXECUTION LOG.

    The reviewer normally sees ONLY the engineer's 4000-char final summary, so it
    cannot tell HOW the result was reached. This block
    points the reviewer at the mission's execution log (the per-project
    ``<life_dir>/events.jsonl``) and gives concrete grep recipes so it can audit
    PROCESS correctness: did the engineer hardcode the expected answer, skip a
    required step, use a cheat method (``use_attach``, fabricated metrics, a
    bypassed evaluator), or run commands that contradict the method it claims in
    the checklist?

    Back-compat contract: returns ``""`` when ``engineer_log_path`` is empty
    (memory backend / tests / unresolvable life_dir) — the prompt is then
    byte-for-byte identical to before this feature existed. The section is
    SUPPLEMENTARY to result-traceability, never a replacement.

    ``measured``: in MEASURED-BENCHMARK mode the reviewer is told to TRUST the
    frozen scorer and not re-run honest results. To avoid an incentive
    contradiction we soften this to a RED-FLAG-ONLY audit there (spend a grep
    only when the pasted RESULT is missing/implausible), and keep the full
    "audit by default when the evidence can't be independently verified" stance
    for paper/research mode.
    """
    path = (engineer_log_path or "").strip()
    if not path:
        return ""
    call_id = (engineer_call_id or "").strip()
    if compact:
        scope = (
            f"current engineer call id `{call_id}`"
            if call_id
            else "the current engineer round"
        )
        return (
            "## Engineer execution log (on-demand)\n"
            f"Log: `{path}`; scope: {scope}. Do not read or grep it routinely. "
            "Previously certified process evidence remains valid. Inspect this log "
            "only for a concrete contradiction, implausible result, missing material "
            "provenance, or suspected shortcut; otherwise spend the review judging "
            "the result and next research decision.\n\n"
        )
    progress_filter = '\'"type": "engineer.progress"\''
    if call_id:
        def shell_quote(value: str) -> str:
            return "'" + value.replace("'", "'\"'\"'") + "'"

        current_call_rows = (
            f"{shell_quote(sys.executable)} -I -m "
            "argus_skill.tools.event_log_query "
            f"--log {shell_quote(path)} --call-id {shell_quote(call_id)}"
        )
        audit_scope = (
            f"Current engineer call id: `{call_id}`. Scope every audit command "
            "to this id so prior rounds and this Reviewer's own prompt cannot "
            "pollute the evidence. The query parses top-level JSON fields and "
            "reads rolled logs in chronological order.\n"
        )
        progress_recipe = f"{current_call_rows} | tail -60"
        cheat_recipe = (
            f"{current_call_rows} | grep -nE 'use_attach|set_pose|teleport|hardcod|"
            "HARDCODE|TODO|FIXME|mock|monkeypatch|fake|dummy|placeholder|"
            "return 0\\.9|assert True|--skip|xfail'"
        )
        evaluator_recipe = (
            f"{current_call_rows} | grep -nE "
            "'pytest|check_success|scorer|evaluate|benchmark|metric'"
        )
        log_row_description = (
            "The call-scoped raw `agent.io.*` rows record the commands, tool "
            "results, and assistant messages produced by this invocation."
        )
    else:
        audit_scope = ""
        progress_recipe = f"grep {progress_filter} '{path}' | tail -60"
        cheat_recipe = (
            "grep -nE 'use_attach|set_pose|teleport|hardcod|HARDCODE|TODO|FIXME|"
            "mock|monkeypatch|fake|dummy|placeholder|return 0\\.9|assert True|"
            f"--skip|xfail' '{path}'"
        )
        evaluator_recipe = (
            "grep -nE 'pytest|check_success|scorer|evaluate|benchmark|metric' "
            f"'{path}'"
        )
        log_row_description = (
            "Each `engineer.progress` event's `text` field is what the engineer "
            "actually DID this round — a shell command it ran, a tool call, or a "
            "reasoning beat."
        )
    if measured:
        when_clause = (
            "MEASURED-BENCHMARK mode is active, so this is a RED-FLAG-ONLY check: "
            "you already TRUST the frozen scorer's pasted RESULT line and must NOT "
            "burn the round re-deriving an honest number. Grep the log ONLY when "
            "the engineer pasted NO RESULT line, the number is implausible / "
            "self-contradictory, or the score jumped suspiciously — then confirm "
            "the scorer was actually invoked and not bypassed/hardcoded. Otherwise "
            "skip this section.\n"
        )
    else:
        when_clause = (
            "Decide WHEN to dig: you do not need to read the log every round, but "
            "you SHOULD when the artifact is suspicious, the result is "
            "surprisingly good, a checklist item cannot be independently verified "
            "from the produced files, or the summary is thin on HOW the work was "
            "done. When the engineer's own summary already shows the verification "
            "output and it is internally consistent, a quick log skim is enough.\n"
        )
    return (
        "## Engineer execution-log audit (process correctness — SUPPLEMENTARY)\n"
        "This round's engineer EXECUTION LOG is on disk at:\n"
        f"  {path}\n"
        "It is the per-project event log (NOT in the git work-tree). "
        f"{log_row_description} You have shell access; you can grep it.\n"
        f"{audit_scope}\n"
        "Result-traceability (does the final artifact match the checklist?) tells "
        "you the OUTCOME is real. This log tells you the PROCESS was honest — the "
        "two are different, and an artifact can match the checklist while the "
        "process that produced it was faked. Use this to catch what the summary "
        "hides.\n\n"
        f"{when_clause}\n"
        "Grep recipes (substitute the path above):\n"
        "- See what the engineer ran this round (newest last):\n"
        f"    {progress_recipe}\n"
        "- Hunt for cheats / shortcuts that mask a real failure:\n"
        f"    {cheat_recipe}\n"
        "- Check the claimed evaluator/scorer was actually invoked (not bypassed "
        "or replaced by an inline constant):\n"
        f"    {evaluator_recipe}\n\n"
        "Red flags → even if the artifact traces to the checklist, return "
        "`continue` (or `blocked` if it needs the operator) and NAME the process "
        "defect in `reason` / `next_action`:\n"
        "- (a) HARDCODED the expected value/answer instead of computing it (e.g. "
        "writing the gold number straight into the output, an `assert True`, a "
        "constant return where a measurement belongs).\n"
        "- (b) SKIPPED a required step and wrote the result directly (the "
        "checklist says 'run X then measure', but no X command appears in the "
        "log).\n"
        "- (c) Used a WRONG or CHEATING method — a physics/sim override "
        "(`use_attach`, forced pose), a fabricated metric, or a bypassed/replaced "
        "real evaluator — to make a failing task look passed.\n"
        "- (d) Ran commands that CONTRADICT the method the checklist/summary "
        "claims (the prose says one approach; the log shows another).\n\n"
        "If the log is clean and the process matches the claim, say so briefly and "
        "judge on the result as usual — do NOT manufacture a process objection "
        "where there is none. This audit SUPPLEMENTS result-traceability; it does "
        "not replace it, and it never changes the frozen outcome/metric/verifier.\n\n"
    )


class Reviewer:
    """One reviewer call per round. Stateless across rounds."""

    def __init__(self, runner: RunnerBackend, *, skill_store: Any | None = None) -> None:
        self.runner = runner
        self.schema_path = SCHEMA_PATH
        self._last_prompt_block_stats: dict[str, dict[str, int]] = {}
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
        escalate_hint: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        preselected_skill_block: str | None = None,
        resume_thread_id: str | None = None,
        prior_static_fingerprint: str = "",
    ) -> ReviewDecision:
        schema_path = self.schema_path
        research_target_level = None
        research_target_required = False
        structured_result_required = False
        try:
            from ..core.research_contract import resolve_research_target_contract
            from ..skills.harness_overlay import resolve_project_root

            root = resolve_project_root(config.working_dir)
            target_contract = resolve_research_target_contract(root)
            research_target_level = target_contract.selected_level
            research_target_required = target_contract.required
            structured_result_required = research_target_required
            if structured_result_required and schema_path == SCHEMA_PATH:
                schema_path = RESEARCH_SCHEMA_PATH
        except Exception:  # noqa: BLE001 — default schema remains safe
            pass
        # Defense-in-depth (root-cause guard for the 2026-06-25 incident): if the
        # reviewer output-schema file is unavailable, codex aborts with exit 1
        # ("Failed to read output schema file ...") and the round renders NO
        # verdict. Detect it up front and fail loud as a backend-unavailable
        # block, instead of building a prompt and handing codex a path it cannot
        # read. This catches a moved schema / a stale import-time path held by a
        # long-lived daemon whose on-disk tree moved underneath it.
        schema_contract = b""
        try:
            if schema_path:
                schema_contract = Path(schema_path).read_bytes()
                schema_path, schema_contract = _compact_schema_for_backend(
                    schema_path,
                    schema_contract,
                )
        except OSError as exc:
            reason = (
                "Reviewer output-schema file is unavailable (missing or unreadable) at "
                f"{schema_path} ({type(exc).__name__}: {exc}); the reviewer backend "
                "cannot start. This is "
                "an environment/packaging fault (e.g. the schema was moved or a "
                "running process holds a stale import-time path), not a verdict."
            )
            return ReviewDecision(
                status="blocked",
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
                backend_stop_kind="backend_unavailable",
            )
        # Split the prompt into a byte-stable STATIC preamble and per-round DELTA
        # for provider prefix caching. Every call still sends both into a fresh
        # Reviewer session.
        common = dict(
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
            escalate_hint=escalate_hint,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            preselected_skill_block=preselected_skill_block,
            working_dir=config.working_dir,
        )
        static, delta_base = self._render(resumed=False, **common)
        prompt_block_stats = {
            name: dict(stats)
            for name, stats in self._last_prompt_block_stats.items()
        }
        if schema_contract:
            schema_bytes = len(schema_contract)
            prompt_block_stats["output_schema"] = {
                "chars": len(schema_contract.decode("utf-8", errors="replace")),
                "bytes": schema_bytes,
                "estimated_tokens": (schema_bytes + 3) // 4,
            }
        fingerprint_input = bytearray(static.encode("utf-8"))
        if schema_path:
            fingerprint_input.extend(b"\0output-schema\0")
            fingerprint_input.extend(schema_contract)
        new_fp = hashlib.sha256(fingerprint_input).hexdigest()
        # Autonomous reviews are deliberately one turn per provider session.
        # ``resume_thread_id`` / ``prior_static_fingerprint`` remain accepted for
        # source compatibility but are never used.
        _ = (resume_thread_id, prior_static_fingerprint)
        prompt = static + delta_base
        try:
            result = gateway_run_exec(
                self.runner,
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=config.model,
                    reasoning_effort=config.reasoning_effort,
                    dangerous_yolo=config.dangerous_yolo,
                    full_auto=config.full_auto,
                    skip_git_repo_check=config.skip_git_repo_check,
                    extra_args=list(config.extra_args) if config.extra_args else None,
                    output_schema_path=schema_path,
                    working_dir=config.working_dir,
                    # Search is available for the rare turn that proposes a
                    # skill; ordinary review turns need not invoke it.
                    live_search=True,
                ),
                run_label="reviewer",
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"Reviewer runner raised {type(exc).__name__}: {exc}"
            log.exception("reviewer runner raised")
            return ReviewDecision(
                status="blocked",
                reason=msg,
                next_action="Resolve the reviewer runner failure before retrying.",
                round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
            )
        rev_in = int(getattr(result, "input_tokens", 0) or 0)
        rev_cached = int(getattr(result, "cached_input_tokens", 0) or 0)
        rev_out = int(getattr(result, "output_tokens", 0) or 0)
        rev_reasoning_output_tokens = int(
            getattr(result, "reasoning_output_tokens", 0) or 0
        )
        # Copilot premium-request delta for this reviewer turn (0.0 off copilot).
        # copilot 下本轮 reviewer 的高级请求增量（非 copilot 时为 0.0）。
        rev_premium = float(getattr(result, "premium_requests", 0.0) or 0.0)
        # Preserve transport metadata for observability only; the supervised
        # loop never resumes this Reviewer thread.
        rev_tid = getattr(result, "thread_id", None)
        fatal = str(getattr(result, "fatal_error", "") or "").strip()
        backend_stop_kind = (
            normalize_stop_kind(getattr(result, "stop_kind", None))
            or "backend_unavailable"
        )
        if fatal or result.exit_code != 0:
            reason = (
                "Reviewer backend returned no complete verdict "
                f"(exit={result.exit_code}"
                + (f", fatal_error={fatal}" if fatal else "")
                + ")."
            )
            return ReviewDecision(
                status="blocked",
                reason=reason,
                next_action=(
                    "Reviewer backend ended before a complete verdict — do NOT "
                    "treat partial output as evidence about the engineer's work."
                ),
                round_summary_markdown=f"# Review Summary\n\n- {reason}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
                backend_unavailable=True,
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
                backend_fatal_error=fatal,
                backend_exit_code=result.exit_code,
                backend_stop_kind=backend_stop_kind,
            )
        if not result.agent_messages:
            return ReviewDecision(
                status="continue",
                reason=f"Reviewer returned empty output. exit={result.exit_code}",
                next_action="Continue implementation and provide concrete completed work.",
                round_summary_markdown="# Review Summary\n\n- Reviewer returned empty output.\n",
                progress_class="none",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
            )
        parsed = _find_decision_in_messages(
            result.agent_messages,
            allow_research_pause=structured_result_required,
        )
        if parsed is None:
            return ReviewDecision(
                status="continue",
                reason="Reviewer output was not valid JSON.",
                next_action="Continue implementation and include clear completion evidence.",
                round_summary_markdown="# Review Summary\n\n- Reviewer output was not valid JSON.\n",
                progress_class="none",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
            )
        # Phase-2 instrumentation: cost-tracking sinks (e.g. LifeSupervisor's
        # _CostTrackingSink) read these fields off ``round.review.completed``
        # events. If we don't propagate them every iteration budget enforcement
        # silently breaks and the journal shows ``cost_usd=$0.0000``.
        parsed.input_tokens = rev_in
        parsed.cached_input_tokens = rev_cached
        parsed.output_tokens = rev_out
        parsed.reasoning_output_tokens = rev_reasoning_output_tokens
        parsed.premium_requests = rev_premium
        parsed.prompt_block_stats = prompt_block_stats
        # Transport metadata remains useful in events even though the next
        # Reviewer call is always fresh.
        parsed.thread_id = rev_tid
        parsed.static_fingerprint = new_fp
        if (
            research_target_required
            and research_target_level is None
            and parsed.status == "done"
            and str(scope or "").strip().lower() != "bounded"
        ):
            parsed.status = "research_incomplete"
            parsed.achievement = None
            parsed.reason = (
                "Research completion gate held: the target-capable vertical has "
                "no persisted research_target_level. "
                + parsed.reason
            )[:5000]
            parsed.next_action = (
                "Restore the Manager-owned research target contract before "
                "claiming project completion."
            )
        # The L2 reviewer's verdict is authoritative — the harness must not
        # second-guess its scientific judgment from structured result labels or
        # keyword heuristics on the engineer's summary.
        # If a generic role-acknowledgment turn slips through, that is a
        # reviewer-prompt concern (the reviewer is told to demand concrete
        # evidence and verify when it is missing/contradictory), not a harness
        # post-filter.
        return parsed

    def _render(
        self,
        *,
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
        escalate_hint: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        preselected_skill_block: str | None = None,
        working_dir: str | Path | None = None,
    ) -> tuple[str, str]:
        """F7: render the reviewer prompt as ``(static_preamble, round_delta)``.

        ``static_preamble`` is a byte-stable role/rubric prefix suitable for
        provider prefix caching. Every Reviewer call is nevertheless a fresh
        session and receives ``static + delta`` in full.
        """
        error_text = main_error or "none"
        # Role-mission matcher (same primitive engineer/planner use). It
        # surfaces ADAPTIVE reviewer skills (stage-specific review playbooks)
        # plus cross-role engineer references on top of the fixed
        # role/handoff/academic blocks above. The three fixed reviewer skills
        # are excluded by ReviewerMission so the matcher never re-injects what
        # is already hard-wired into this prompt.
        from ..skills.harness_overlay import resolve_project_root
        from ..skills.vertical_select import (
            _persisted_vertical,
            resolve_evidence_mode,
            resolve_vertical,
        )
        from ..verticals._base import (
            load_vertical,
            vertical_completion_gate,
            vertical_requires_independent_review,
            vertical_role_banner,
            vertical_search_altitude,
        )

        _proot = resolve_project_root(working_dir)
        _active_vertical = resolve_vertical(_proot)
        _vmod = load_vertical(_active_vertical, project_root=_proot)
        _persisted = _persisted_vertical(_proot)
        _requires_engineering_audit = bool(
            _persisted is not None
            and vertical_requires_independent_review(
                load_vertical(_persisted, project_root=_proot)
            )
        )
        matched_review_skill_block = ""
        if preselected_skill_block is not None:
            if preselected_skill_block.strip():
                matched_review_skill_block = (
                    "Preselected mission skill context from the single matcher pass "
                    "(apply what is relevant; follow any on-demand read instruction "
                    "inside):\n"
                    f"{preselected_skill_block.strip()}\n\n"
                )
        elif self.skill_store is not None:
            from ..skills.venue_profiles import venue_excluded_skill_files

            review_match = self.mission.match(
                objective,
                extra_exclude=venue_excluded_skill_files(_proot),
            )
            if review_match.block:
                matched_review_skill_block = (
                    "Matched reviewer skill(s) for this objective "
                    "(read first; apply the relevant one(s)):\n"
                    f"{review_match.block}\n\n"
                )
        from ..skills.stage_checklists import (
            CANONICAL_STAGE_ORDER,
            current_stage,
            format_full_pipeline_checklist,
            format_stage_checklist,
        )
        stage = current_stage(_proot)
        import os as _os
        _measured = (
            not _requires_engineering_audit
            and _os.environ.get("ARGUS_SKILL_MEASURED_MODE", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        # Vertical-native prompt framing: resolve the active vertical and let it
        # supply the top-of-prompt role banner. The rollback / final-submission
        # framing below applies ONLY to a paper vertical (completion_gate ==
        # "full_paper"); for any other vertical (e.g. speedrun) those blocks are
        # suppressed and the vertical's banner is prepended so the reviewer judges
        # only that vertical's metric instead of paper-pipeline artifacts.
        _full_paper = vertical_completion_gate(_vmod) == "full_paper"
        optimize_banner = vertical_role_banner(_vmod, "reviewer")
        if (
            vertical_requires_independent_review(_vmod)
            and not _requires_engineering_audit
        ):
            optimize_banner = ""
        research_result_instruction = ""
        from ..core.research_contract import resolve_research_target_level

        _research_target_level = resolve_research_target_level(_proot)
        if _research_target_level is not None:
            _bounded_research_contract = (
                "This is a structured `bounded` backlog item. `done` certifies "
                "only this item's explicit objective and acceptance criteria; it "
                "does NOT certify the persisted project-level research target. "
                "Still emit an honest `research_result`, but do not require "
                "verified novelty, publishable significance, or an original "
                "terminal theorem unless this bounded objective explicitly asks "
                "for them. A verification probe may therefore finish with a "
                "correctly classified novelty-unverified result. If this bounded "
                "item's own acceptance criteria and current-stage checklist are "
                "satisfied, use `status=done` even though later project stages or "
                "the final research target remain open. Never use "
                "`research_incomplete` solely because the whole project is not yet "
                "complete.\n"
                if (scope or "").strip().lower().replace("-", "_") == "bounded"
                else ""
            )
            research_result_instruction = (
                "For this targeted research mission, `research_result` is REQUIRED "
                "on every verdict. Judge result_class, correctness_status, "
                "novelty_status, significance_status, and any domain-specific "
                "fidelity field independently; use concrete evidence and limitations.\n"
                f"The Manager-persisted `research_target_level` is "
                f"`{_research_target_level}`. {_bounded_research_contract}"
                "For non-bounded completion, use exactly this success bar; do not "
                "downgrade it because a report is polished or a bounded cycle ended. "
                "For `publishable` or `doctoral`, `done` requires "
                "correctness_status=verified, novelty_status=verified_new, "
                "significance_status publishable or "
                "doctoral, and an original terminal result (complete solution, "
                "verified new result/theorem, improved bound, new infinite family, "
                "new reduction, or exact counterexample). Literature review, known "
                "results, finite verification, local Lean verification, "
                "novelty-unverified work, and honest/structured failure reports are "
                "artifacts, not mission success. For NON-BOUNDED project-level "
                "routing, a genuinely novel negative or boundary result may set "
                "`significance_status=publishable` and `scientific_decision=go` only "
                "when it supports a standalone venue-relevant thesis; use a precise "
                "result class such as partial_result/counterexample rather than "
                "structured_failure_report. For NON-BOUNDED project-level "
                "completion only, when the current cycle should end without that "
                "result, use `research_incomplete`, "
                "`paused_no_breakthrough`, or `exhausted_current_methods`; these "
                "preserve evidence and permit a future resume. For `exploratory`, "
                "an independently verified honest failure report may be `done`.\n\n"
            )
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
                "Trusted, FROZEN scorer; the engineer has NO reward signal and does "
                "not control it, so its pasted RESULT (correct + cand_ms/score) is "
                "the honest norm. Your verdict turns on ONE thing: did this round's "
                "MEASURED score beat the engineer's previous best?\n"
                "Do NOT re-run the scorer yourself to re-confirm an honest, "
                "self-consistent number — the engineer self-supervises correctness "
                "by running it every round, so re-measuring burns the round for zero "
                "value. Spend a check ONLY if NO RESULT was pasted or it is "
                "self-contradictory. Otherwise: JUDGMENT + DIRECTION, not "
                "re-measurement.\n"
                "- `continue` if the score improved (lock it in, explore the NEXT "
                "mechanism) OR a clearly-different mechanism is still untried. First "
                "judge: was this mechanism genuinely novel or a re-tweak of a "
                "direction that already lost? `next_action` MUST name a CONCRETE new "
                "direction (a different SOTA/library approach, a hardware technique, "
                "the profiled bottleneck) — push mechanism diversity; never ask to "
                "re-tweak a losing direction or re-paste a shown result.\n"
                "- `blocked` ONLY on a real plateau (several rounds, no improvement, "
                "distinct mechanisms exhausted) or an operator-only blocker. When "
                "only the OPERATOR can unblock (route, budget, which task, GPU, a "
                "yes/no), ALSO set `operator_question`: ONE plain-language question "
                "in the operator's language (Chinese here), answerable in a sentence "
                "— no jargon/JSON/template names.\n"
                "- `done` is rare here — only at/above the known ceiling.\n"
                "Ignore GROUND_TRUTH/gate/marker/status/provenance files (the harness "
                "ignores them) and artifact hygiene — the scorer's number is the only "
                "evidence. A round that MEASURED a real number, even a worse one, made "
                "progress by ruling out a mechanism. This OVERRIDES the generic "
                "demand-evidence / re-run rules below."
            )
        elif is_final_submission or stage == "submission":
            stage_checklist = format_full_pipeline_checklist(role="reviewer", project_root=_proot)
        else:
            stage_checklist = format_stage_checklist(
                stage,
                role="reviewer",
                project_root=_proot,
                scope=scope_normalized,
            )

        # Academic peer-review benchmark skill: advisory rubric for reviewing
        # a near-complete manuscript. Gate it on the structured stage/scope
        # signal — final_submission, or the paper-writing stages (review /
        # submission) — instead of keyword-sniffing the objective/evidence
        # for tokens like "main.pdf". `draft` is excluded so mid-production
        # drafting isn't held to final peer-review standards prematurely.
        paper_review_skill_block = _format_academic_paper_review_skill_block(
            include=is_final_submission or stage in {"review", "submission"},
        )
        wiki_curator_text = _load_wiki_curator_skill_if_present(working_dir)
        wiki_curator_skill_block = (
            "## Wiki curator (fixed when a wiki exists)\n"
            f"{wiki_curator_text}\n\n"
            if wiki_curator_text
            else ""
        )
        direct_memory_edit_block = _direct_memory_edit_block(
            self.skill_store,
            working_dir,
        )

        venv_skill_block = (
            "## Dependency rule\n"
            "A missing project package is repairable: tell the Engineer to install "
            "it with `./.venv/bin/pip`; never modify the Argus framework venv."
        )

        # Upstream-evidence defect REPORT. When the reviewer notices that an
        # upstream stage's evidence is missing or unreliable while working a
        # later stage, the correct move is to REPORT it so the Manager can roll
        # the stage back — the reviewer does NOT edit the pipeline state machine
        # itself (stage authority is the Manager's). The instruction lives here
        # (not in the individual checklist items) so it applies uniformly.
        stage_idx = (
            CANONICAL_STAGE_ORDER.index(stage)
            if stage in CANONICAL_STAGE_ORDER
            else 0
        )
        earlier_stages = ", ".join(CANONICAL_STAGE_ORDER[:stage_idx]) or "(none)"
        rollback_block = (
            "## Upstream defects\n"
            f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
            "If earlier-stage evidence is broken and this mission cannot repair it "
            "within its own scope, return `replan_requested` (never `continue`) and "
            "name the earliest broken stage in `reason` and "
            "`planner_report.blocker`. Set `planner_report.plan_signal` to "
            "`reconsider` with a non-empty `plan_signal_reason`; the Manager owns rollback. "
            "Never edit `research/PIPELINE_STATE.json`."
        )
        # Checklist-feedback channel. The PLANNER owns the per-stage checklist
        # (it authors/edits it via checklist_ops). The reviewer is FEEDBACK-ONLY:
        # if the checklist ITSELF is wrong for this task, it reports rather than
        # working around or silently honoring a broken item.
        checklist_feedback_block = (
            "## Checklist ownership\n"
            "Judge this round against the checklist as written. If the checklist "
            "itself is wrong, report concise `checklist_feedback`; the Planner owns "
            "edits. Never write `research/CHECKLISTS.json`."
        )
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        shared_context_block = _format_engineer_shared_context(
            skill_used=active_skill_id,
            prev_review_summary=prev_review_summary,
        )
        # v12 phase-4: when callers (e.g. harbor_adapter) collect richer
        # post-round evidence (engineer self-report verbatim, runtime probe,
        # official verifier output with "ground truth, trust this" framing),
        # they pass it as ``raw_evidence`` so the reviewer has the strongest
        # signal grounded in actual container state, not just the engineer's
        # prose. Empty string → legacy v3 behaviour.
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
                "it — or, if nothing else can proceed, emit "
                "`control = {\"action\": \"wait_for_subagent\", \"task_id\": "
                "\"<task_id>\"}` in the FINAL JSON handoff. Do NOT encode this wait "
                "in prose (`reason`, `next_action`, `round_summary_markdown`); the "
                "harness ignores prose for control flow. When you use `control`, keep "
                "`status = continue` and write `next_action` for what the engineer "
                "should do AFTER the wait resumes, not another poll instruction.\n"
            )
        # ``prior_checkpoint`` is accepted only for source compatibility with
        # older callers. The live handoff is the ordinary Markdown file that the
        # Engineer already edited and the Reviewer must now edit directly.
        _ = prior_checkpoint
        checkpoint_block = shared_checkpoint_instructions(
            Path(checkpoint_path) if checkpoint_path else None,
            role="reviewer",
        )
        if checkpoint_block:
            checkpoint_block += "\n\n"
        # Anti-livelock escalation directive (supplied by the round loop once a
        # mission passes the soft round limit): tell the reviewer to escalate an
        # unresolvable EXTERNAL blocker to `blocked` instead of looping `continue`.
        escalate_block = ""
        if escalate_hint:
            escalate_block = (
                "## Escalation directive (operator harness — IMPORTANT)\n"
                f"{escalate_hint}\n\n"
            )
        # Engineer execution-log audit (process correctness). The reviewer runs
        # in the project work-tree and only receives the engineer's final
        # summary, so it cannot otherwise SEE how a result was produced. When the
        # supervisor threads the absolute path to this mission's execution log
        # (``<life_dir>/events.jsonl``), give the reviewer grep recipes to audit
        # PROCESS correctness — not just whether the artifact matches the
        # checklist, but whether the engineer reached it honestly. Empty path
        # (memory backend / tests / unresolvable life_dir) → block omitted, prompt
        # byte-for-byte unchanged (back-compat).
        engineer_log_audit_block = _engineer_log_audit_block(
            engineer_log_path,
            engineer_call_id=engineer_call_id,
            round_index=round_index,
            measured=_measured,
            compact=not bool((main_error or "").strip()),
        )
        # Final-submission completion contract. This block replaces the
        # retired hardcoded EMNLP validators: instead of the supervisor
        # running ``validate_full_paper_readiness`` and friends, the reviewer
        # is the single source of truth for whether the *whole project* is
        # ready to submit. It only fires for final_submission missions.
        final_submission_block = ""
        if is_final_submission:
            final_submission_block = (
                "## Final paper review\n"
                "Read the current manuscript, rendered PDF, and claim-critical sources "
                "as an independent venue reviewer. Use `done` only when the research "
                "objective and selected venue bar are genuinely met; otherwise return "
                "`continue` with the few highest-leverage scientific or writing changes. "
                "Do not require or manufacture an assurance memo, reviewer-question "
                "bundle, or other certification packet.\n\n"
            )
        if not _full_paper:
            # non-paper vertical: no paper stages to roll back to, and no
            # final-submission certification — judge only the vertical's metric.
            rollback_block = ""
            final_submission_block = ""
        # Byte-stable static policy; every fresh Reviewer receives it in full.
        static = (
            _reviewer_evidence_contract(
                resolve_evidence_mode(_proot),
                mandatory_engineering_audit=_requires_engineering_audit,
            )
            + optimize_banner
            + research_result_instruction
            + EFFECTIVE_TASK_CONTRACT
            + "\n\n## Reviewer role\n"
            "Independently judge the current objective against real evidence and the "
            "applicable checklist. Preserve scope: bounded work may finish without "
            "the whole project; final-submission work may not. Use `done` only for "
            "verified completion, `continue` for agent-fixable gaps, and `blocked` "
            "only for genuine operator/external dependencies.\n\n"
            + (
                ""
                if _requires_engineering_audit
                else _verification_directive()
            )
            + "## Output protocol\n"
            "Talk normally, reason, and use tools; do not format intermediate messages as "
            "JSON. ONLY your FINAL message is the structured handoff: one FINAL "
            "handoff JSON object matching the attached schema, with nothing after "
            "it. Before that final message, directly edit CHECKPOINT.md as instructed "
            "in the round context.\n\n"
            + paper_review_skill_block
            + wiki_curator_skill_block
            + direct_memory_edit_block
            + matched_review_skill_block
            + stage_checklist
            + "\n\n"
            + final_submission_block
            + rollback_block
            + "\n\n"
            + checklist_feedback_block
            + "\n\n"
            + venv_skill_block
            + "\n\n## Final handoff fields\n"
            "The attached schema is authoritative; fill every required key. Keep "
            "`round_summary_markdown` concise and make `next_action` specific.\n"
            "- `planner_report` is the Planner's briefing: honest forward_progress, "
            "one headline, the root blocker, a concrete next focus, and useful "
            "evidence file pointers. Use `plan_signal=reconsider` only when new "
            "evidence invalidates the remaining project plan; otherwise use "
            "`continue` and leave plan_signal_reason empty.\n"
            "- `step_back` is required for a measured result, including success: "
            "independently state support, surprises, new questions, and cheap "
            "alternative directions; use null only when nothing was measured.\n"
            "- Every valid measured result must identify the strongest supported "
            "finding in `planner_report.headline`. Preserve clean negative, null, "
            "boundary, and diagnostic evidence, but do not automatically turn it "
            "into a paper. First audit implementation adequacy and plausible repairs. "
            "Recommend publication work only when the result supports a standalone, "
            "venue-relevant thesis beyond 'we tried and it failed'; otherwise set "
            "`scientific_decision` to pivot/no_go and request a replacement plan. "
            "There is no fixed retry count: judge further engineering by the diagnosed "
            "cause, expected information gain, and remaining resources.\n"
            "- `failure_cause` classifies non-done outcomes. Reusable skill/wiki "
            "learning must already have been edited directly during this Reviewer "
            "turn, except Wiki pages, which must use the schema's structured "
            "`wiki_ops`. Never directly edit `.autors/**/wiki/pages/**`, and never "
            "encode other memory edits in the final JSON.\n"
            "- `failure_source` is independent acceptance provenance. Use null "
            "without a diagnosed acceptance failure; otherwise choose exactly one "
            "structured kind and cite concrete artifact observations. A "
            "`validator_defect` requires a stable validator_id plus exact project-"
            "relative repair_paths limited to validator/test/provenance files. "
            "Never list raw scientific evidence, preregistration, thresholds, or "
            "success criteria as repair_paths. Classification does "
            "not authorize repair. Never label missing/failed scientific evidence "
            "as a validator defect. Set `scientific_decision` independently to "
            "go, pivot, no_go, undecided, or null.\n"
            "- `failure_layer` is orthogonal and must be one of `platform`, "
            "`orchestration`, `evaluator`, `evidence_packaging`, `scientific`, "
            "`operator`, or `unknown`. Platform/program/evaluator/packaging failures "
            "must request repair and must not be used as evidence against the idea.\n"
            "- `operator_question` is only for an operator-only blocker. "
            "`checklist_feedback` is only when the checklist itself is wrong.\n\n"
            "Decision rules:\n"
            "- Set `progress_class`. Do not add a separate explanation: `decision`, "
            "`evidence`, `setup_only`, `artifact_sync_only`, or `none`.\n"
            "- `done` requires concrete evidence and exact adherence to material "
            "operator constraints. A generic acknowledgment is never enough.\n"
            "- Default to `continue` whenever the agent's claims are not backed by "
            "shown/checkable evidence; once sufficient evidence is present, do not "
            "burn another round re-running it.\n"
            "- On `continue`, name the missing outcome/evidence and the specific "
            "NEXT work or unexplored direction; leave implementation freedom unless "
            "a deterministic failure identifies the repair.\n"
            "- `continue` is ONLY for a repair that remains inside the current "
            "mission objective, acceptance check, non-goals, stage, and resource "
            "contract. If the next work needs a new/separate/scoped mission, a "
            "replacement plan, or any change to those boundaries, return "
            "`replan_requested` instead; set `planner_report.plan_signal` to "
            "`reconsider` with a concrete non-empty reason. Reviewer reports the "
            "defect but never authorizes scope expansion.\n"
            "- `blocked` is only for credentials, inaccessible resources, or a "
            "decision/specification only the operator can provide.\n"
            "- When a supervised background task is healthy and nothing else is "
            "actionable, use structured `control = {\"action\": "
            "\"wait_for_subagent\", \"task_id\": \"<id>\"}` with action "
            "`wait_for_subagent`; never encode the "
            "wait only in prose.\n"
            "- New measured evidence or a measured failed mechanism can be forward "
            "progress; setup, bookkeeping, repeated re-scoring, and near-identical "
            "unproductive tweaks are not. A smoke run proves wiring, not final "
            "evidence. Do not declare a method dead from a misconfigured run.\n"
            "- Final-submission `done` requires every full-pipeline checklist item; "
            "bounded scope uses only its objective and relevant stage items.\n\n"
            "Original operator request:\n"
            f"{(original_objective or objective).strip()}\n\n"
            "Current mission objective:\n"
            f"{objective}\n\n"
            "Operator messages:\n"
            f"{operator_text}\n\n"
            "Planner guidance:\n"
            f"{planner_review_instruction or 'none'}\n\n"
        )
        # Per-round DELTA — everything that changes round to round. Fresh
        # Reviewers receive this after the full static rubric every time.
        delta = (
            (_REEVALUATE_HEADER if resumed else "")
            + search_altitude_block
            + f"{checkpoint_block}"
            + f"{escalate_block}"
            + f"{engineer_log_audit_block}"
            + (
                f"Round: {round_index}/{round_max}\n"
                if round_max > 0
                else f"Round: {round_index}\n"
            )
            + f"Session ID: {session_id or 'none'}\n"
            + f"{shared_context_block}"
            + f"{background_block}"
            + f"Main agent fatal error: {error_text}\n\n"
            + "Main agent last summary:\n"
            + f"{main_summary}\n\n"
            + f"{evidence_block}"
        )
        objective_context = (
            f"{(original_objective or objective).strip()}\n"
            f"{objective}\n"
            f"{operator_text}\n"
            f"{planner_review_instruction or 'none'}"
        )
        self._last_prompt_block_stats = _prompt_block_stats(
            {
                "static_total": static,
                "delta_total": delta,
                "stage_checklist": stage_checklist,
                "matched_skill": matched_review_skill_block,
                "direct_memory": direct_memory_edit_block,
                "wiki_curator": wiki_curator_skill_block,
                "paper_review": paper_review_skill_block,
                "research_result": research_result_instruction,
                "final_submission": final_submission_block,
                "objective_context": objective_context,
                "checkpoint": checkpoint_block,
                "execution_log_audit": engineer_log_audit_block,
                "background": background_block,
                "shared_context": shared_context_block,
                "main_summary": main_summary,
                "raw_evidence": evidence_block,
            }
        )
        return static, delta

    def _build_prompt(self, **kwargs: Any) -> str:
        """Full reviewer prompt (static + round-1 delta). Kept for the unit tests
        and any non-resuming caller; ``evaluate`` uses ``_render`` directly."""
        static, delta = self._render(resumed=False, **kwargs)
        return static + delta

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


_MAX_SHARED_CTX_CHARS = 100_000_000  # effectively no cap: reviewer must see the FULL engineer reasoning/prev-review to audit honesty


def _format_engineer_shared_context(
    *,
    skill_used: str | None,
    prev_review_summary: str,
) -> str:
    """Render the read-only shared context block injected into reviewer prompts.

    Keep this renderer stable because the same block is consumed across
    engineer/reviewer round boundaries.

    The engineer's final message is rendered exactly once, under "Main agent
    last summary"; its full reasoning/process is available to the reviewer via
    the ``engineer_log_path`` audit block. We therefore do NOT echo a separate
    ``engineer_reasoning_summary`` here — the sole caller fed it the same string
    as ``main_summary``, so it only duplicated input tokens every reviewer round.
    """
    skill = (skill_used or "").strip()
    prev = (prev_review_summary or "").strip()
    if not skill and not prev:
        return ""
    parts = ["Shared read-only context (do NOT modify; advisory only):"]
    if skill:
        parts.append(f"- skill_used: {skill}")
    if prev:
        if len(prev) > _MAX_SHARED_CTX_CHARS:
            prev = prev[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in prev.splitlines())
        parts.append("- previous_review_summary:\n" + indented)
    return "\n".join(parts) + "\n\n"
