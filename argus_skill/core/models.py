"""Core dataclasses shared across the loop.

Provenance: most types here are vendored or adapted from
``ArgusBot/agent_cli/models.py``. Trimmed to what argus-skill actually
uses (no planner snapshots — argus-skill is reviewer-only for v0.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

ReviewStatus = Literal["done", "continue", "blocked"]
LoopStatus = Literal["done", "max_rounds", "blocked", "no_progress", "error"]


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
    # ``watchdog_soft_idle_seconds`` / ``watchdog_hard_idle_seconds``
    # are absolute idle thresholds; ``0`` disables that level.
    external_interrupt_reason_provider: Callable[[], str | None] | None = None
    inactivity_callback: Callable[[Any], str | None] | None = None
    watchdog_soft_idle_seconds: int = 0
    watchdog_hard_idle_seconds: int = 0


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
    output_tokens: int = 0

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
class CheckResult:
    """An acceptance-check command's result. Vendored from ArgusBot."""
    command: str
    exit_code: int
    passed: bool
    output_tail: str


@dataclass
class ReviewDecision:
    """Reviewer verdict on one engineer round. Vendored from ArgusBot."""
    status: ReviewStatus
    confidence: float
    reason: str
    next_action: str
    round_summary_markdown: str = ""
    completion_summary_markdown: str = ""
    # Phase 1 reviewer→skill feedback loop. Reviewer may classify the
    # round's failure mode (``skill_gap`` / ``execution_mistake`` /
    # ``ambiguous_objective`` / ``environmental`` / ``unknown``). When
    # ``failure_cause == "skill_gap"`` the reviewer also emits a
    # ``mission_lesson`` — a one-paragraph patch the next round's prompt
    # should carry verbatim. Empty on success / clean done verdicts.
    failure_cause: str = ""
    mission_lesson: str = ""
    # Process self-distillation (judged EVERY mission, success or failure):
    # a reusable lesson about the agent's own PROCESS — how it worked, where it
    # wasted/repeated rounds, an incentive friction it hit, or a workaround that
    # worked — distinct from ``mission_lesson`` (the research METHOD). Distills
    # PROCESS only; never the outcome/metric/verifier (those stay frozen). Empty
    # when this mission's process had nothing reusable.
    process_lesson: str = ""
    verification_summary: str = ""
    # Reviewer completion contract (replaces the hardcoded EMNLP validator
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
    # Side-channel: token usage of the reviewer subprocess that produced
    # this decision. Populated by ``Reviewer.evaluate`` and consumed by
    # telemetry/cost reporting. Not part of the reviewer's semantic output.
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    # True ONLY when the reviewer rendered NO verdict because its BACKEND was
    # unavailable — the codex subprocess died, the output-schema file was
    # missing, or the runner raised. This is an INFRASTRUCTURE failure, never a
    # model judgment. The supervised loop routes a ``backend_unavailable`` review
    # through the same transient-backoff + escalate-to-error path as an engineer
    # backend failure, instead of the silent ``continue`` that once ran the sole
    # completion gate BLIND for ~1.5h (2026-06-25, a stale import-time schema
    # path made every reviewer round exit 1). Distinct from a genuine
    # ``status="blocked"`` verdict (e.g. "blocked on GPU quota") which carries a
    # real confidence and is NOT a backend failure.
    backend_unavailable: bool = False

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
        (planner-facing briefing), ``mission_lesson``, ``scope``,
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
            "type": "round.review.completed",
            "status": self.status,
            "confidence": self.confidence,
            "reason": self.reason,
            "next_action": self.next_action,
            "round_summary_markdown": self.round_summary_markdown or "",
            "completion_summary_markdown": self.completion_summary_markdown or "",
            "failure_cause": self.failure_cause or "",
            # Previously dropped — these are the structured-eval fields
            # the reviewer is REQUIRED to emit per reviewer_schema.json.
            "mission_lesson": self.mission_lesson or "",
            "process_lesson": self.process_lesson or "",
            "verification_summary": self.verification_summary or "",
            "scope": self.scope or "",
            "checklist": list(self.checklist or []),
            "planner_report": dict(self.planner_report or {}),
            "checkpoint": dict(self.checkpoint or {}),
            # Token bookkeeping (cost-tracking sinks read these).
            "input_tokens": int(self.input_tokens or 0),
            "cached_input_tokens": int(self.cached_input_tokens or 0),
            "output_tokens": int(self.output_tokens or 0),
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
    checks: list[CheckResult]
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
