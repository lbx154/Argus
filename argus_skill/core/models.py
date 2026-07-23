"""Core dataclasses shared across the loop.

Provenance: most types here are vendored or adapted from
``ArgusBot/agent_cli/models.py``. Trimmed to what argus-skill actually
uses (no planner snapshots — argus-skill is reviewer-only for v0.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .event_catalog import EventType
from .stop_kinds import StopKind

ResearchPauseStatus = Literal[
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
]
ReviewStatus = Literal[
    "done",
    "continue",
    "blocked",
    "replan_requested",
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
]
LoopStatus = Literal[
    "done",
    "max_rounds",
    "blocked",
    "no_progress",
    "error",
    "budget_exhausted",
    "paused_budget",
    "paused_provider_cooldown",
    "paused_provider_fence",
    "paused_daemon_shutdown",
    "paused_operator",
    "aborted",
    "infra_blocked",
    "replan_requested",
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
]


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
    # Strong process-level confinement used by daemon self-maintenance. Unlike
    # backend-native sandbox flags, this applies to every CLI backend and fails
    # closed when the host cannot provide isolation.
    isolate_workdir: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
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
    # Idle thresholds use ``None`` to inherit backend defaults and ``0`` to
    # disable that stage for this call.
    external_interrupt_reason_provider: Callable[[], str | None] | None = None
    inactivity_callback: Callable[[Any], str | None] | None = None
    watchdog_soft_idle_seconds: int | None = None
    watchdog_stalled_idle_seconds: int | None = None
    watchdog_hard_idle_seconds: int | None = None
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
    stop_kind: StopKind | None = None
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
    # True only when ``call_id`` is the top-level identity persisted in the
    # configured agent I/O log. Gateway-generated tracing IDs leave this false.
    call_id_log_correlated: bool = False
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
    # Objective process-ownership facts from the CLI runner. A non-zero group id
    # means the provider process exited while descendants still occupied its
    # private process group; the runner attempted cleanup by that exact PGID.
    orphan_process_group_id: int = 0
    orphan_process_group_cleanup_succeeded: bool = False

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


def canonical_planner_report(value: object) -> dict[str, Any]:
    """Return the one-to-one Reviewer→Planner signal shape."""
    if not isinstance(value, dict):
        return {}
    if not any(
        key in value
        for key in ("forward_progress", "plan_signal", "evidence_files")
    ):
        return {}
    out: dict[str, Any] = {}
    if isinstance(value.get("forward_progress"), bool):
        out["forward_progress"] = value["forward_progress"]
    signal = str(value.get("plan_signal") or "").strip().lower()
    out["plan_signal"] = signal if signal in {"continue", "reconsider"} else "continue"
    evidence = value.get("evidence_files")
    out["evidence_files"] = list(evidence) if isinstance(evidence, list) else []
    return out


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
    # Legacy replay fields. New Reviewer schemas use ``reason`` / ``next_action``
    # and CHECKPOINT.md instead of duplicate summaries.
    round_summary_markdown: str = ""
    completion_summary_markdown: str = ""
    failure_cause: str = ""
    # Acceptance-failure provenance, authored only by Reviewer structured JSON.
    # ``failure_cause`` above remains the skill-learning diagnosis; this field
    # controls whether a restricted repair can even be considered.
    failure_source: str = ""
    failure_source_evidence: list[dict[str, str]] = field(default_factory=list)
    validator_id: str = ""
    repair_paths: list[str] = field(default_factory=list)
    scientific_decision: str = ""
    # Orthogonal failure layer. Infrastructure/program/evaluator/packaging
    # failures must repair their own layer and never become scientific evidence.
    failure_layer: str = ""
    # Compact reviewer-authored decision-progress classification. The harness
    # counts it but never infers it from filenames, keywords, or tool activity.
    progress_class: str = ""
    # Structured reviewer-authored control request, parsed ONLY from the JSON
    # ``control`` object in the final verdict. Empty strings mean "no control
    # request". The runner currently honors ``wait_for_subagent`` only after a
    # real reviewed round and never infers it from prose.
    control_action: str = ""
    control_task_id: str = ""
    # Legacy Engineer self-review projection; no longer emitted in new events.
    verification_summary: str = ""
    # Internal provenance for the verdict.  ``reviewer`` is an independent L2
    # decision; ``engineer_self_review`` is a bounded waiver that the Manager
    # may still evaluate against the current stage checklist.  This field is
    # not authored by the Reviewer model.
    review_source: str = "reviewer"
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
    # Structured research assessment used only when the Manager persisted a
    # research_target_level. Ordinary missions leave this ``None``.
    research_result: dict[str, Any] | None = None
    # Raw machine-readable certification authored by the independent Reviewer.
    # This is a persistence channel only: the harness may parse it to route the
    # round, but must not infer or synthesize scientific claims from it.
    certification_payload: dict[str, Any] | None = None
    # Planner-only structured signals authored by the reviewer. Verdict prose
    # stays in ``reason`` / ``next_action``. Shape:
    # ``{"forward_progress": bool, "plan_signal": "continue"|"reconsider",
    # "evidence_files": [{"path", "why"}]}``. The
    # ``evidence_files`` point the planner at the concrete artifacts (source
    # script, data provenance, metric series, NO_GO docs) to OPEN and read
    # before routing the next mission. Fail-soft: empty dict when the reviewer
    # omitted it or the round errored before a verdict.
    planner_report: dict[str, Any] = field(default_factory=dict)
    # Legacy structured checkpoint field retained for event/parser compatibility.
    # The live runtime now uses a directly edited CHECKPOINT.md file instead.
    # Historical shape:
    # ``{"goal", "done": [...], "tried_and_failed": [...], "open_blocker",
    # "next_step"}``. Fail-soft: empty dict when the reviewer omitted it or the
    # round errored before a verdict (runner then keeps the prior checkpoint).
    checkpoint: dict[str, Any] = field(default_factory=dict)
    # Legacy replay field. Current Reviewer schemas do not expose skill_ops;
    # executable Reviewers edit the injected project skill path directly.
    skill_ops: list[dict[str, Any]] = field(default_factory=list)
    # Legacy replay field. Current Reviewer schemas do not expose wiki_ops;
    # executable Reviewers edit injected project wiki paths directly.
    wiki_ops: list[dict[str, Any]] = field(default_factory=list)
    # Reviewer → Planner checklist feedback (ADVISORY; the reviewer is
    # feedback-only and NEVER writes the checklist store). When the reviewer
    # judges the per-stage checklist itself wrong / incomplete / over-strict for
    # this task, it emits this so the Planner (the checklist OWNER) fixes it next
    # cycle via ``checklist_ops``. Shape: ``{"stage": str, "summary": str,
    # "items": [{"id": str, "problem": str, "suggested_fix": str}]}``. Empty/None
    # when the reviewer has no complaint about the checklist itself.
    checklist_feedback: dict[str, Any] | None = None
    # Harness-owned arbitration metadata. Never authored by the Reviewer model
    # and never merged back into reviewer reason/next_action/planner_report.
    harness_control: dict[str, Any] = field(default_factory=dict)
    # Legacy structured reflection retained for old event replay. New Reviewers
    # write measured surprises and alternative directions once in CHECKPOINT.md.
    step_back: dict[str, Any] | None = None
    # Prompt observability side-channel populated by Reviewer.evaluate. Each
    # block records chars/bytes/estimated_tokens so token regressions can be
    # attributed to concrete prompt components rather than one opaque total.
    prompt_block_stats: dict[str, dict[str, int]] = field(default_factory=dict)
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
    backend_stop_kind: StopKind | None = None
    # Reviewer-authored observation-only routing judgment. It records whether
    # another Engineer round should proceed before L4 Planner judgment, but no
    # runtime component consumes it for routing. Appended to preserve every
    # existing positional ReviewDecision constructor argument.
    routing_decision: str = ""
    routing_reason: str = ""
    routing_handoff: str = ""

    @property
    def final_submission_certified(self) -> bool:
        """True when this verdict certifies whole-project final-submission
        readiness: a ``done`` verdict scoped to ``final_submission`` whose
        checklist is non-empty and every item is satisfied with concrete
        evidence. Fail-closed: any missing/empty field means not certified.
        """
        if self.status != "done" or self.scope != "final_submission":
            return False
        if self.scientific_decision in {"pivot", "no_go", "undecided"}:
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

        Emit the canonical structured verdict once. Natural-language state lives
        in CHECKPOINT.md; legacy summary/checkpoint fields remain readable on old
        events but are not copied into new ones.

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
            "failure_cause": self.failure_cause or "",
            "failure_source": self.failure_source or "",
            "failure_source_evidence": list(self.failure_source_evidence or []),
            "validator_id": self.validator_id or "",
            "repair_paths": list(self.repair_paths or []),
            "scientific_decision": self.scientific_decision or "",
            "failure_layer": self.failure_layer or "",
            "progress_class": self.progress_class or "",
            "control_action": self.control_action or "",
            "control_task_id": self.control_task_id or "",
            "review_source": self.review_source or "reviewer",
            "achievement": (
                dict(self.achievement) if isinstance(self.achievement, dict) else None
            ),
            "scope": self.scope or "",
            "routing_decision": self.routing_decision or "",
            "routing_reason": self.routing_reason or "",
            "routing_handoff": self.routing_handoff or "",
            "checklist": list(self.checklist or []),
            "planner_report": canonical_planner_report(self.planner_report),
            "checklist_feedback": dict(self.checklist_feedback or {}),
            "harness_control": dict(self.harness_control or {}),
            "prompt_block_stats": {
                str(name): dict(stats)
                for name, stats in (self.prompt_block_stats or {}).items()
                if isinstance(stats, dict)
            },
            # Token bookkeeping (cost-tracking sinks read these).
            "input_tokens": int(self.input_tokens or 0),
            "cached_input_tokens": int(self.cached_input_tokens or 0),
            "output_tokens": int(self.output_tokens or 0),
            "reasoning_output_tokens": int(self.reasoning_output_tokens or 0),
            # Copilot premium-request delta (cost sinks fold it into USD).
            "premium_requests": float(self.premium_requests or 0.0),
            "backend_unavailable": bool(self.backend_unavailable),
            "stop_kind": self.backend_stop_kind,
            "usage_scope": "delta",
        }
        if isinstance(self.research_result, dict):
            payload["research_result"] = dict(self.research_result)
        if isinstance(self.certification_payload, dict) and self.certification_payload:
            payload["certification_payload"] = dict(self.certification_payload)
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
    stop_kind: StopKind | None = None


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
    stop_kind: StopKind | None = None
    recoverable: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return self.status == "done"

    @property
    def round_count(self) -> int:
        return len(self.rounds)
