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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.stop_kinds import normalize_stop_kind
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
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
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


def _engineer_log_audit_block(
    engineer_log_path: str,
    *,
    engineer_call_id: str = "",
    round_index: int,
    measured: bool,
    compact: bool = False,
) -> str:
    """Compatibility wrapper for the prompt-module helper."""
    from ..roles.prompts.reviewer import _engineer_log_audit_block

    return _engineer_log_audit_block(
        engineer_log_path,
        engineer_call_id=engineer_call_id,
        round_index=round_index,
        measured=measured,
        compact=compact,
    )


class Reviewer:
    """One reviewer call per round. Stateless across rounds."""

    def __init__(
        self,
        runner: RunnerBackend,
        *,
        skill_store: Any | None = None,
        memory_maintenance_enabled: bool = True,
    ) -> None:
        self.runner = runner
        self.schema_path = SCHEMA_PATH
        self._last_prompt_block_stats: dict[str, dict[str, int]] = {}
        # Optional: when wired, the reviewer runs the same role-mission skill
        # matcher every other role uses, surfacing adaptive reviewer skills
        # (e.g. stage-specific review playbooks) plus cross-role engineer
        # references on top of the fixed role/handoff context. ``None`` keeps
        # the legacy fixed-context-only behaviour.
        self.skill_store = skill_store
        self.memory_maintenance_enabled = memory_maintenance_enabled
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
        from ..roles.prompts.reviewer import assemble_reviewer_prompt

        prompt = assemble_reviewer_prompt(static, delta_base)
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
                    sandbox_mode=config.sandbox_mode,
                    isolate_workdir=config.isolate_workdir,
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
        from ..roles.prompts.reviewer import render_reviewer_prompt

        return render_reviewer_prompt(
            self,
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
            escalate_hint=escalate_hint,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            preselected_skill_block=preselected_skill_block,
            working_dir=working_dir,
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
