"""Core dataclasses shared across the loop.

Provenance: most types here are vendored or adapted from
``ArgusBot/agent_cli/models.py``. Trimmed to what argus-skill actually
uses (no planner snapshots — argus-skill is reviewer-only for v0.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .event_catalog import EventType

ReviewStatus = Literal["done", "continue", "blocked"]
LoopStatus = Literal["done", "max_rounds", "blocked", "no_progress", "error", "budget_exhausted"]


@dataclass
class RunnerOptions:
    """Per-call knobs for an LLM runner backend.

    Vendored shape from ArgusBot's RunnerOptions. Watchdog hooks are
    optional and only honoured by backends that wrap a real subprocess
    (e.g. ``AgentCliBackend``); ``MemoryBackend`` and other
    deterministic backends ignore them.
    """
    model: str | None = None
    reasoning_effort: str | None = None
    output_schema_path: str | None = None
    working_dir: str | None = None
    extra_args: list[str] | None = None
    skip_git_repo_check: bool = False
    # Enable codex's native live web_search tool for this call (``codex exec
    # --search``). Off by default; turned on for the research/ideation stage so
    # idea discovery does real live literature search instead of cached/recalled
    # results. No-op on backends that do not build a codex command.
    live_search: bool = False
    # Explicit subprocess sandbox. Used by Manager rendering/stage calls to
    # inspect project state without granting write access; None preserves each
    # backend's existing default behavior.
    sandbox_mode: str | None = None
    full_auto: bool = False
    dangerous_yolo: bool = False
    # Internal provider-side spend fences. These are populated from the atomic
    # call reservation by AgentCliBackend, not by ordinary role configuration.
    max_budget_usd: float | None = None
    max_ai_credits: int | None = None
    # Watchdog hooks — propagated to the codex subprocess so an outer
    # supervisor (e.g. ArgusBot's LoopEngine, the MissionDaemon) can
    # interrupt a long-running engineer turn promptly.
    #
    # ``external_interrupt_reason_provider`` is polled by the runner
    # while the subprocess is alive; when it returns a non-empty
    # string the subprocess is terminated and the result carries
    # ``fatal_error="External interrupt: <reason>"``.
    #
    # ``inactivity_callback`` is invoked on soft-idle boundaries (no
    # stdout for ``watchdog_soft_idle_seconds``); it can return
    # ``"restart"`` to force termination + retry semantics, or any
    # other value to keep waiting.
    #
    # ``watchdog_soft_idle_seconds`` / ``watchdog_hard_idle_seconds``
    # are absolute idle thresholds; ``0`` disables that level.
    external_interrupt_reason_provider: Callable[[], str | None] | None = None
    inactivity_callback: Callable[[Any], str | None] | None = None
    watchdog_soft_idle_seconds: int = 0
    watchdog_hard_idle_seconds: int = 0
    # ``on_agent_message`` is invoked with each NEW assistant message block the
    # moment it arrives on the CLI's stdout stream (copilot/codex emit the reply
    # as one or more complete blocks during a turn, not a single final blob).
    # Lets a front-end stream the reply live instead of waiting for the whole
    # turn. Opt-in: default ``None`` means the runner behaves byte-for-byte as
    # before — only the Manager chat front-door sets it, so the 7×24 daemon's
    # role turns are entirely unaffected. A callback exception never breaks the
    # turn (it is swallowed by the runner).
    on_agent_message: Callable[[str], None] | None = None


@dataclass
class RunnerResult:
    """Result returned by a RunnerBackend.run_exec call.

    A slim version of ArgusBot's AgentRunResult — we keep only the parts
    the loop / reviewer / parsers actually look at.
    """
    exit_code: int
    agent_messages: list[str] = field(default_factory=list)
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    thread_id: str | None = None
    fatal_error: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    # Additional hidden reasoning tokens billed at the output rate; real usage not
    # shown in visible completion text. 额外的隐藏 reasoning token 按输出单价计费，真实计费但不显示在可见回复文本里。
    reasoning_output_tokens: int = 0
    # Copilot bills in PREMIUM REQUESTS, not tokens (it reports no input tokens),
    # so this is copilot's native cost unit — this call's DELTA (already
    # de-cumulated per thread by the backend adapter). 0.0 for codex/claude.
    # Copilot 以「高级请求数」计费而非 token（它不报输入 token），故这是 copilot 的
    # 原生成本单位——本次调用的增量（适配层已按线程去累计）。codex/claude 恒为 0.0。
    premium_requests: float = 0.0
    # Stable call identity and usage-presence metadata.  Zero-valued token fields
    # alone cannot distinguish a real zero from a provider that omitted usage.
    call_id: str = ""
    input_tokens_present: bool = False
    cached_input_tokens_present: bool = False
    cache_write_tokens_present: bool = False
    output_tokens_present: bool = False
    reasoning_output_tokens_present: bool = False
    premium_requests_present: bool = False
    usage_model: str = ""
    total_nano_aiu: int | None = None
    pricing_status: str = ""
    cost_usd: float | None = None
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_ms: int = 0
    model_usage: list[dict[str, Any]] = field(default_factory=list)
    # True when the provider reported a tool call during this turn. A failed
    # direct-reply turn with tool activity is not safe to replay automatically,
    # even when it produced no assistant text.
    tool_activity_observed: bool = False

    @property
    def last_agent_message(self) -> str:
        if not self.agent_messages:
            return ""
        return self.agent_messages[-1]

    @property
    def message(self) -> str:
        """Concatenated agent message text (skill_store.find_relevant uses
        this name to read the matcher response)."""
        return "\n".join(self.agent_messages)


@dataclass
class ReviewDecision:
    """Reviewer verdict on one engineer round. Vendored from ArgusBot."""
    status: ReviewStatus
    reason: str
    next_action: str
    # ONE plain-language question, in the operator's own language, asked when a
    # ``blocked`` verdict needs an OPERATOR decision (route/budget/which-task)
    # the agent cannot make alone. The cockpit surfaces this verbatim and the
    # operator's free-text reply continues the same objective — so a block is a
    # human question, not a JSON gate packet. Empty on done/continue or when the
    # block is purely engineer-repairable. Distinct from ``next_action`` (the
    # engineer-facing instruction).
    operator_question: str = ""
    round_summary_markdown: str = ""
    completion_summary_markdown: str = ""
    failure_cause: str = ""
    verification_summary: str = ""
    # Optional project-level research achievement independently certified by
    # this reviewer. The loop emits the sole authoritative
    # ``research.achievement.certified`` event only for a ``done`` verdict with
    # this structured payload. Ordinary task completion leaves it ``None``.
    achievement: dict[str, Any] | None = None
    # Reviewer completion contract (replaces the old hardcoded paper-validator
    # gate). For ``final_submission`` missions the reviewer must set
    # ``scope == "final_submission"`` and populate ``checklist`` with one
    # entry ``{"item", "satisfied", "evidence"}`` per full-pipeline
    # checklist item. A ``done`` verdict only certifies project completion
    # when every item is satisfied with concrete evidence. Empty for
    # ordinary bounded missions.
    scope: str = ""
    checklist: list[dict[str, Any]] = field(default_factory=list)
    # Planner-facing structured briefing authored by the reviewer. The L4
    # planner routes the next mission from this clean, structured report
    # rather than from raw engineer output or noisy verdict prose. Shape:
    # ``{"forward_progress": bool, "headline": str, "blocker": str,
    # "recommended_next": str, "evidence_files": [{"path", "why"}]}``. The
    # ``evidence_files`` point the planner at the concrete artifacts (source
    # script, data provenance, metric series, NO_GO docs) to OPEN and read
    # before routing the next mission. Fail-soft: empty dict when the reviewer
    # omitted it or the round errored before a verdict.
    planner_report: dict[str, Any] = field(default_factory=dict)
    # Curated working-memory checkpoint authored by the reviewer (the memory
    # auditor) from the engineer's end-of-turn handoff proposal. Carried across
    # session rolls so a fresh engineer session resumes from a small, curated
    # handoff instead of a giant compacted history. Shape:
    # ``{"goal", "done": [...], "tried_and_failed": [...], "open_blocker",
    # "next_step"}``. Fail-soft: empty dict when the reviewer omitted it or the
    # round errored before a verdict (runner then keeps the prior checkpoint).
    checkpoint: dict[str, Any] = field(default_factory=dict)
    # Skill-memory operations the reviewer requests for THIS round (success or
    # failure). The reviewer is the SOLE authority — there is no Manager
    # approval gate. ``create``/``update`` persist an immediately active,
    # versioned project-layer capability playbook after structural validation;
    # later task trajectories inform Reviewer-authored updates or retirement.
    # ``delete``/``archive`` retire a matched skill the reviewer found wrong or
    # harmful. Each item: ``{"op": "create|update|delete|archive", "name": str,
    # "content": str, "why": str}``. Empty list when this round warrants no
    # skill change.
    skill_ops: list[dict[str, Any]] = field(default_factory=list)
    # Wiki-memory operations the reviewer requests for THIS round — the
    # project idea-wiki's structured counterpart to ``skill_ops`` above, both
    # applied by the harness with NO Manager gate (the reviewer is the sole
    # authority here too). ``create_page``/``update_page`` PROPOSE a page
    # (``body`` markdown); every cited ``evidence`` span is mechanically
    # verified to quote an immutable wiki source verbatim (anti-fabrication —
    # see ``skills.provenance.verify_evidence``), so a fabricated citation is
    # rejected regardless of the reviewer's judgment. ``retire_page`` tombstones
    # a page (never a hard delete — always reversible). Each item:
    # ``{"op": "create_page|update_page|retire_page", "id": str,
    # "card_type": str, "title": str, "status": str, "body": str,
    # "evidence": [{"source_id": str, "quote": str, "locator": str}],
    # "tags": [str], "related_runs": [str], "related_projects": [str],
    # "why": str}``. Empty list when this round warrants no wiki change, or
    # when the project has no initialized wiki.
    wiki_ops: list[dict[str, Any]] = field(default_factory=list)
    # Reviewer → Planner checklist feedback (ADVISORY; the reviewer is
    # feedback-only and NEVER writes the checklist store). When the reviewer
    # judges the per-stage checklist itself wrong / incomplete / over-strict for
    # this task, it emits this so the Planner (the checklist OWNER) fixes it next
    # cycle via ``checklist_ops``. Shape: ``{"stage": str, "summary": str,
    # "items": [{"id": str, "problem": str, "suggested_fix": str}]}``. Empty/None
    # when the reviewer has no complaint about the checklist itself.
    checklist_feedback: dict[str, Any] | None = None
    # Reviewer → Planner STEP-BACK reflection on THIS round's measured result
    # (the anti-plan-lock-in channel). Distinct from ``planner_report`` (which
    # only carries real signal when ``forward_progress=False``): ``step_back`` is
    # authored on EVERY round that produced a measured result — INCLUDING a clean
    # success — as a fresh-skeptic critique that surfaces NEW questions and
    # alternative directions even when the plan is "working". Shape:
    # ``{"supported_by_results": "yes|partial|no", "surprises": str,
    # "new_questions": [str], "alt_directions": [{"direction", "why",
    # "cheap_to_test"}]}``. The planner is REQUIRED (planner rule 17d) to triage
    # each ``alt_direction`` — spawn it as a new DAG branch or explicitly
    # defer/reject it. Fail-soft: ``None`` when the round had no measured result
    # (pure wiring/run-wait) or the reviewer omitted it.
    step_back: dict[str, Any] | None = None
    # Side-channel: token usage of the reviewer subprocess that produced
    # this decision. Populated by ``Reviewer.evaluate`` and consumed by
    # telemetry/cost reporting. Not part of the reviewer's semantic output.
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    # Additional hidden reasoning tokens billed at the output rate; real usage not
    # shown in visible completion text. 额外的隐藏 reasoning token 按输出单价计费，真实计费但不显示在可见回复文本里。
    reasoning_output_tokens: int = 0
    # Copilot's native cost unit for the reviewer subprocess (this round's
    # DELTA; 0.0 for codex/claude). Consumed by cost-tracking sinks alongside
    # the token side-channels above.
    # Copilot 下 reviewer 子进程的原生成本单位（本轮增量；codex/claude 为 0.0）。
    premium_requests: float = 0.0
    # F7 side-channel (like input_tokens above — NOT semantic output, deliberately
    # absent from ``to_event_payload``): the codex thread_id the reviewer ran on,
    # and the sha256 of the STATIC preamble it sent. The supervised loop reads
    # these to resume the reviewer's OWN thread across rounds (re-sending only the
    # per-round delta), and to force a full re-send when the static rubric changes
    # mid-mission (stage/objective/vertical drift) — the fingerprint guard.
    thread_id: str | None = None
    static_fingerprint: str = ""
    # True ONLY when the reviewer rendered NO verdict because its BACKEND was
    # unavailable — the codex subprocess died, the output-schema file was
    # missing, or the runner raised. This is an INFRASTRUCTURE failure, never a
    # model judgment. The supervised loop routes a ``backend_unavailable`` review
    # through the same transient-backoff + escalate-to-error path as an engineer
    # backend failure, instead of the silent ``continue`` that once ran the sole
    # completion gate BLIND for ~1.5h (2026-06-25, a stale import-time schema
    # path made every reviewer round exit 1). Distinct from a genuine
    # ``status="blocked"`` verdict (e.g. "blocked on GPU quota") which is a
    # real model judgment and is NOT a backend failure.
    backend_unavailable: bool = False
    # Raw transport outcome for backend-unavailable decisions. Hidden from the
    # reviewer schema/event payload; the supervised loop uses it to distinguish
    # an intentional operator/daemon interrupt from a genuine backend outage.
    backend_fatal_error: str = ""
    backend_exit_code: int | None = None

    @property
    def final_submission_certified(self) -> bool:
        """True when this verdict certifies whole-project final-submission
        readiness: a ``done`` verdict scoped to ``final_submission`` whose
        checklist is non-empty and every item is satisfied with concrete
        evidence. Fail-closed: any missing/empty field means not certified.
        """
        if self.status != "done" or self.scope != "final_submission":
            return False
        if not self.checklist:
            return False
        for item in self.checklist:
            if not isinstance(item, dict):
                return False
            if not bool(item.get("satisfied")):
                return False
            if not str(item.get("evidence", "")).strip():
                return False
        return True

    def to_event_payload(self, **extras: Any) -> dict[str, Any]:
        """Build the full ``round.review.completed`` event dict.

        The reviewer JSON schema requires 11 top-level fields. Earlier
        emit sites in runner/engine forwarded only 6, silently dropping
        ``checklist`` (per-item structured eval), ``planner_report``
        (planner-facing briefing), ``scope``,
        ``checkpoint``, and ``verification_summary``. That made postmortem
        of "why did reviewer let this pass?" impossible from events.jsonl.

        Token counts are read off ``self``, so the synthesized verdicts
        for daemon-stop / backend-failure paths (zero tokens) and the
        genuine LLM verdict (real tokens) flow through the same payload
        builder.

        ``extras`` are merged in last so callers can attach call-site-
        specific fields (``round_max``, ``session_id``, ``review_skipped``,
        ``text``, ``type``) without losing them to a key collision.
        """
        payload: dict[str, Any] = {
            "type": EventType.ROUND_REVIEW_COMPLETED,
            "status": self.status,
            "reason": self.reason,
            "next_action": self.next_action,
            "operator_question": self.operator_question or "",
            "round_summary_markdown": self.round_summary_markdown or "",
            "completion_summary_markdown": self.completion_summary_markdown or "",
            "failure_cause": self.failure_cause or "",
            "verification_summary": self.verification_summary or "",
            "achievement": (
                dict(self.achievement) if isinstance(self.achievement, dict) else None
            ),
            "scope": self.scope or "",
            "checklist": list(self.checklist or []),
            "planner_report": dict(self.planner_report or {}),
            "checkpoint": dict(self.checkpoint or {}),
            "skill_ops": list(self.skill_ops or []),
            "wiki_ops": list(self.wiki_ops or []),
            "checklist_feedback": dict(self.checklist_feedback or {}),
            "step_back": (dict(self.step_back) if isinstance(self.step_back, dict) else None),
            # Token bookkeeping (cost-tracking sinks read these).
            "input_tokens": int(self.input_tokens or 0),
            "cached_input_tokens": int(self.cached_input_tokens or 0),
            "output_tokens": int(self.output_tokens or 0),
            "reasoning_output_tokens": int(self.reasoning_output_tokens or 0),
            # Copilot premium-request delta (cost sinks fold it into USD).
            "premium_requests": float(self.premium_requests or 0.0),
            "backend_unavailable": bool(self.backend_unavailable),
            "usage_scope": "delta",
        }
        payload.update(extras)
        return payload


@dataclass
class RoundRecord:
    """A snapshot of one engineer round + reviewer verdict."""
    round_index: int
    engineer_message: str
    engineer_exit_code: int
    review: ReviewDecision
    fatal_error: str | None = None


@dataclass
class LoopOutcome:
    """What SkillLoop.run returns.

    Captures both the final verdict and the round-by-round trail so the
    caller can render a report, persist a decision log, or write the
    successful trajectory back into the skill store.
    """
    status: LoopStatus
    rounds: list[RoundRecord]
    skill_used: str | None
    skill_distilled: bool
    final_message: str
    reason: str
    workdir: str
    last_thread_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return self.status == "done"

    @property
    def round_count(self) -> int:
        return len(self.rounds)
