"""Core dataclasses shared by runners, reviewers, and mission loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .event_catalog import EventType
from .stop_kinds import StopKind

#: Who holds the work when a round settles: the role or person its handoff
#: names, or ``done`` for a named candidate accepted (core/handoff.py).
Holder = Literal["engineer", "planner", "manager", "operator", "done"]
LoopStatus = Literal[
    "done",
    "max_rounds",
    # The host refused to go on (an isolation breach, a refused credential).
    "blocked",
    "no_progress",
    "error",
    # Waiting on something; ``LoopOutcome.park_reason`` says what. In the
    # round loop's own result the reason is the last round's stop kind.
    "parked",
    # The Manager parked the run between role operations for the Planner to retire.
    "manager_stop",
    "aborted",
    # Handed past the Engineer and the Reviewer: the holder that has the work.
    "planner",
    "manager",
    "operator",
]

# Copilot CLI tools for sub-agents, dynamic workflows, the session store and
# semantic code search. No Planner, Reviewer or Observer thread called one in
# the October 2026 six-task, trial and experiment logs, and nothing tells these
# roles to; on the full tool surface their schemas rode along on every request
# (10K tool tokens per Planner turn, 3.9K without them).
READING_ROLE_UNUSED_COPILOT_TOOLS = (
    "task",
    "read_agent",
    "list_agents",
    "write_agent",
    "run_dynamic_workflow",
    "dynamic_workflows_manage",
    "session_store_sql",
    "semantic_code_search",
)


@dataclass
class RunnerOptions:
    """Per-call options for an LLM runner backend."""

    model: str | None = None
    reasoning_effort: str | None = None
    working_dir: str | None = None
    add_dirs: list[str] | None = None
    # Of ``add_dirs``, the ones the call writes. A sandboxed call only reads
    # every other directory it is given: a library recalled for reference,
    # another project's knowledge.
    write_dirs: list[str] | None = None
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
    # Native JSON Schema output for a tools-disabled text call. Supported
    # transports must enforce it; unsupported backends fail before spawning.
    output_schema: dict[str, Any] | None = None
    # Strong process-level confinement used by isolated framework maintenance. Unlike
    # backend-native sandbox flags, this applies to every CLI backend and fails
    # closed when the host cannot provide isolation.
    isolate_workdir: bool = False
    # Drop the parts of an agent CLI's own tool surface that a role never
    # uses. Every tool's schema and usage notes ride along on each model
    # request of the call, i.e. on every tool step. For Copilot CLI this is
    # the session SQL todo database and the CLI's self-documentation tool;
    # built-in MCP servers stay attached. Other backends ignore it.
    lean_tool_surface: bool = False
    # Further Copilot CLI tools and built-in MCP servers a role never uses,
    # dropped together with the lean surface. Other backends ignore them.
    excluded_tools: tuple[str, ...] = ()
    disabled_mcp_servers: tuple[str, ...] = ()
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
    # progress -- no stdout event, no CPU time in the CLI's process tree --
    # for ``watchdog_soft_idle_seconds``); it can return ``"restart"`` or
    # ``"restart: <who decided, and why>"`` to force termination + retry
    # semantics, or any other value to keep waiting.
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
    trusted_extensions: list[str] | None = None
    trusted_tool_names: list[str] | None = None
    extension_env: dict[str, str] | None = None


def may_act(options: Any) -> bool:
    """Whether a call may reach tools beyond its backend's reads and its Argus
    actions: not one without tools, nor one its caller pinned read-only."""
    return not (options.disable_tools or (options.force_safe_mode and options.sandbox_mode == "read-only"))


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
    # The hidden reasoning part of ``output_tokens``, already billed with it.
    # 输出 token 里隐藏的推理部分，已随输出计费，不再另计。
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
    operator_context_revision: int = 0

    @property
    def last_agent_message(self) -> str:
        if not self.agent_messages:
            return ""
        return self.agent_messages[-1]


@dataclass
class ReviewDecision:
    """Reviewer verdict on one Engineer round."""

    # The holder the verdict hands the work to; ``blocked`` on a record the
    # host wrote when the round was stopped and not judged: it names no holder.
    status: Holder | Literal["blocked"]
    reason: str
    next_action: str
    operator_question: str = ""
    operator_options: list[dict[str, Any]] = field(default_factory=list)
    # On an accepting verdict, the Reviewer's judgment whether the finished
    # mission is worth a post-mission reflection, and why.
    learning: str = ""
    learning_reason: str = ""
    # Explicit role-session signal, supplied through the native review action.
    session_signal: dict[str, str] = field(default_factory=dict)
    # The readings the Reviewer chose in the operator's place (``core.handoff``).
    assumptions: list[dict[str, str]] = field(default_factory=list)
    # The Reviewer's own judgment that the fact it disputes cannot be observed
    # through any view it has (a masked display, for example), so repeating the
    # finding cannot settle it. Routed to the Manager; it decides nothing here.
    verification_obstacle: str = ""
    # The task, packet or environment statement, quoted verbatim, that makes
    # the check in ``verification_obstacle`` impossible here (its resource
    # exists only at grading or deploy time). The host checked the quote.
    verification_obstacle_basis: str = ""
    # Where that quote is from: "task", or the workspace file it was found in.
    verification_obstacle_basis_source: str = ""
    # On an accepting verdict: a decisive check impossible here, approved on an
    # acceptance the operator (or, with no operator, the Manager) made for that
    # check. ``residual_risk`` is the one-line statement every completion
    # surface shows instead of a plain "verified" (CLI review and mission
    # lines, the mission summary and delivery card, the Manager's observation);
    # ``residual_risk_detail`` keeps the check, quoted basis, evidence and
    # acceptance. The host validated both before recording the verdict.
    residual_risk: str = ""
    residual_risk_detail: dict[str, Any] = field(default_factory=dict)
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
    # Runtime provenance for the pre-Reviewer operator-abort short circuit.
    engineer_aborted_before_review: bool = False
    research_result: dict[str, Any] | None = None
    manuscript_snapshot: dict[str, str] | None = None
    venue_review: dict[str, Any] | None = None
    # Host-owned provenance; the parser never accepts these from a model.
    # ``reviewer_authored`` is set only on a judgment the independent Reviewer
    # call itself returned; host placeholders, self-reviews, and host
    # replacements keep (or reset to) False, so they are never carried forward
    # as the Reviewer's own findings.
    reviewer_authored: bool = False
    # The Reviewer's own status/reason/next_action before any host rewrite.
    # Later rounds quote these.
    reviewer_words: dict[str, str] | None = None
    # The host's interventions beside this verdict, which never rewrite it:
    # each names the ``policy`` applied, the ``action`` taken instead, when
    # (``at``), and a ``note``. Settlement reads verdict and interventions.
    interventions: list[dict[str, Any]] = field(default_factory=list)
    # Workspace paths that changed while the Reviewer judged, from the round's
    # snapshots. A fact beside the verdict, which stands as written.
    changed_during_review: list[str] = field(default_factory=list)
    # Why a host-written record stands where no Reviewer judgment was made,
    # e.g. ``provider_turn_cap``, ``backend_failure``, ``silent_command``.
    # Those records name the same kind in ``review_source``, so no consumer
    # that trusts ``review_source == "reviewer"`` reads them as a verdict.
    host_placeholder: str = ""
    # These are host-owned facts; the parser never accepts them from a model.
    venue_review_required: bool = False
    venue_review_passed: bool = False
    venue_review_snapshot: dict[str, str] | None = None

    def intervention(self, policy: str) -> dict[str, Any] | None:
        """The host's intervention under ``policy`` beside this verdict, if any."""
        return next((row for row in self.interventions if row.get("policy") == policy), None)

    @property
    def final_submission_certified(self) -> bool:
        """A paper's final acceptance requires its explicit venue recommendation."""
        return self.status == "done" and (
            not self.venue_review_required or self.venue_review_passed
        )

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
        if self.interventions:
            payload["interventions"] = [dict(row) for row in self.interventions]
        if isinstance(self.manuscript_snapshot, dict):
            payload["manuscript_snapshot"] = dict(self.manuscript_snapshot)
        if self.venue_review_required or self.venue_review is not None:
            payload["venue_review"] = self.venue_review
            payload["venue_review_required"] = self.venue_review_required
            payload["venue_review_passed"] = self.venue_review_passed
            payload["venue_review_snapshot"] = self.venue_review_snapshot
        for key in ("learning", "learning_reason"):
            value = str(getattr(self, key) or "").strip()
            if value:
                payload[key] = value
        signal = self.session_signal if isinstance(self.session_signal, dict) else {}
        if str(signal.get("kind") or "").strip():
            payload["session_signal"] = dict(signal)
        obstacle = str(self.verification_obstacle or "").strip()
        if obstacle:
            payload["verification_obstacle"] = obstacle[:2000]
        basis = str(self.verification_obstacle_basis or "").strip()
        if basis:
            payload["verification_obstacle_basis"] = basis[:1000]
            source = str(self.verification_obstacle_basis_source or "").strip()
            if source:
                payload["verification_obstacle_basis_source"] = source[:300]
        residual_risk = str(self.residual_risk or "").strip()
        if residual_risk:
            payload["residual_risk"] = residual_risk[:2000]
            if self.residual_risk_detail:
                payload["residual_risk_detail"] = dict(self.residual_risk_detail)
        if self.research_result is not None:
            payload["research_result"] = dict(self.research_result)
        if self.changed_during_review:
            payload["changed_during_review"] = list(self.changed_during_review)
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
    park_reason: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return self.status == "done"

    @property
    def round_count(self) -> int:
        return len(self.rounds)

    @property
    def final_review_reason(self) -> str:
        if not self.rounds:
            return ""
        return str(self.rounds[-1].review.reason or "").strip()
