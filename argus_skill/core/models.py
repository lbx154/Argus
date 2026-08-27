"""Core dataclasses shared by runners, reviewers, and mission loops."""

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
    "paused_external_work",
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
    """Per-call options for an LLM runner backend."""

    model: str | None = None
    reasoning_effort: str | None = None
    working_dir: str | None = None
    add_dirs: list[str] | None = None
    # Role-owned Skill paths for backends with an explicit native loader. Other
    # backends use the path-only discovery block in the prompt.
    skill_paths: list[str] | None = None
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
    # Preserve the requested sandbox mode for this call even when the process
    # default keeps legacy full-access behavior for normal runtime roles.
    force_safe_mode: bool = False
    # Remove all model-visible tools for prompts that contain untrusted
    # diagnostic text. Unsupported backends must fail closed before spawning.
    disable_tools: bool = False
    # Strong process-level confinement used by isolated framework maintenance. Unlike
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
    """Result returned by a ``RunnerBackend.run_exec`` call."""

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
    role_decisions: list[dict[str, Any]] = field(default_factory=list)
    operator_context_revision: int = 0

    @property
    def last_agent_message(self) -> str:
        if not self.agent_messages:
            return ""
        return self.agent_messages[-1]

    @property
    def message(self) -> str:
        """Concatenated agent message text for backend compatibility."""
        return "\n".join(self.agent_messages)


@dataclass
class ReviewDecision:
    """Reviewer verdict on one Engineer round."""

    status: ReviewStatus
    reason: str
    next_action: str
    operator_question: str = ""
    operator_options: list[dict[str, Any]] = field(default_factory=list)
    checkpoint_recommended: bool = False
    # Strategic judgment is separate from bounded implementation acceptance.
    # A round may be correctly ``done`` yet still fail to move the operator's
    # objective; LifeSupervisor uses this signal to surface repeated hollow work.
    planner_report: dict[str, Any] = field(default_factory=dict)
    # Runtime-owned semantic transition and explicit role-session signal. The
    # model states these on tolerant named lines; no JSON schema is required.
    frontier_report: dict[str, Any] = field(default_factory=dict)
    session_signal: dict[str, str] = field(default_factory=dict)
    review_source: str = "reviewer"
    prompt_block_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    premium_requests: float = 0.0
    thread_id: str | None = None
    static_fingerprint: str = ""
    session_resumed: bool = False
    backend_unavailable: bool = False
    backend_fatal_error: str = ""
    backend_exit_code: int | None = None
    backend_stop_kind: StopKind | None = None
    research_result: dict[str, Any] | None = None
    manuscript_snapshot: dict[str, str] | None = None

    @property
    def final_submission_certified(self) -> bool:
        """A final-submission caller may treat a ``done`` verdict as certified."""
        return self.status == "done"

    def to_event_payload(self, **extras: Any) -> dict[str, Any]:
        """Build the compact ``round.review.completed`` event."""
        payload: dict[str, Any] = {
            "type": EventType.ROUND_REVIEW_COMPLETED,
            "status": self.status,
            "reason": self.reason,
            "next_action": self.next_action,
            "operator_question": self.operator_question or "",
            "operator_options": [
                dict(option)
                for option in (self.operator_options or [])
                if isinstance(option, dict)
            ],
            "checkpoint_recommended": bool(self.checkpoint_recommended),
            "review_source": self.review_source or "reviewer",
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
        if isinstance(self.manuscript_snapshot, dict):
            payload["manuscript_snapshot"] = dict(self.manuscript_snapshot)
        report = self.planner_report if isinstance(self.planner_report, dict) else {}
        forward_progress = report.get("forward_progress")
        if isinstance(forward_progress, bool):
            payload["forward_progress"] = forward_progress
        plan_signal = str(report.get("plan_signal") or "").strip()
        if plan_signal:
            payload["plan_signal"] = plan_signal
        for source_key, event_key in (
            ("challenge", "plan_challenge"),
            ("alternative", "plan_alternative"),
            ("authority_impact", "authority_impact"),
        ):
            value = str(report.get(source_key) or "").strip()
            if value:
                payload[event_key] = value
        frontier = self.frontier_report if isinstance(self.frontier_report, dict) else {}
        frontier_change = str(frontier.get("change") or "").strip()
        if frontier_change:
            payload["frontier_change"] = frontier_change
            payload["frontier_summary"] = str(frontier.get("summary") or "")[:2000]
        signal = self.session_signal if isinstance(self.session_signal, dict) else {}
        if str(signal.get("kind") or "").strip():
            payload["session_signal"] = dict(signal)
        if self.research_result is not None:
            payload["research_result"] = dict(self.research_result)
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
    """Final verdict and round-by-round mission trail."""

    status: LoopStatus
    rounds: list[RoundRecord]
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

    @property
    def final_planner_report(self) -> dict[str, Any]:
        if not self.rounds:
            return {}
        return dict(self.rounds[-1].review.planner_report or {})

    @property
    def final_review_reason(self) -> str:
        if not self.rounds:
            return ""
        return str(self.rounds[-1].review.reason or "").strip()
