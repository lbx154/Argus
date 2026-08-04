"""Foreground Manager routing and bounded SELF reply execution."""

from __future__ import annotations

import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..core.knobs import resolve_manager_reply_model, resolve_role_reasoning_effort
from ..core.models import RunnerOptions
from ..core.ports import EventSink
from ..core.progress_step import REPLY_KINDS, describe_progress_step
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import known_secret_values, redact_secrets_record
from ..engineer.runner import should_clear_thread_id_after_outcome
from ._env import env_flag, env_int
from ._runtime_backends import _Outcome

_SELF_RETRYABLE_ACP_ERRORS = (
    "acp restart requested",
    "acp process died",
    "stopreason=cancelled",
)

_STATUS_MUTATION_MARKERS = (
    "修改",
    "修复",
    "实现",
    "优化",
    "重启",
    "停止",
    "继续",
    "取消",
    "提交",
    "创建",
    "删除",
    "更新文件",
    "改进",
    "总结",
    "建议",
    "分析",
    "解释",
    "fix ",
    "change ",
    "implement ",
    "optimize ",
    "restart",
    "stop ",
    "continue",
    "cancel",
    "commit",
    "create ",
    "delete ",
    "update file",
    "summarize",
    "recommend",
    "analyze",
    "explain",
)
_ZH_STATUS_QUERY = re.compile(
    r"^(?:请|麻烦)?(?:你)?(?:帮我)?"
    r"(?:看|查看|检查|告诉我|汇报)?(?:一下)?"
    r"(?:(?:项目|任务|研究|运行|当前|现在|目前)的?)*"
    r"(?:运行)?(?:进度|状态|运行情况)"
    r"(?:如何|怎么样|怎样|到哪(?:了)?|是什么|呢|吗)?[？?。]*$"
)
_EN_STATUS_QUERY = re.compile(
    r"^(?:please )?(?:(?:show(?: me)?|check|tell me|what is|what's)\s+)?(?:the )?"
    r"(?:current )?(?:project |run |mission )?"
    r"(?:status|progress)(?: please)?[?.]*$"
)
_EN_STATUS_PHRASES = frozenset({
    "how is it going",
    "how's it going",
    "how is the project going",
    "how's the project going",
    "how is the mission going",
    "how's the mission going",
    "how far along are we",
    "are we done yet",
    "what's the current status",
    "what's the current progress",
    "what is running",
    "what are you doing now",
})


def _redact_live_event(event: dict[str, Any]) -> dict[str, Any]:
    safe = redact_secrets_record(event, known_values=known_secret_values())
    return safe if isinstance(safe, dict) else event


def self_retryable_transport_failure(result: Any) -> bool:
    """Retry only an empty ACP transport failure with no possible side effects."""
    if (getattr(result, "last_agent_message", "") or "").strip():
        return False
    if bool(getattr(result, "tool_activity_observed", False)):
        return False
    fatal = str(getattr(result, "fatal_error", "") or "").strip().casefold()
    if not fatal:
        return int(getattr(result, "exit_code", 0) or 0) == 0
    if fatal.startswith(("external interrupt:", "refused before start:")):
        return False
    return any(marker in fatal for marker in _SELF_RETRYABLE_ACP_ERRORS)


def looks_like_status_query(text: str) -> bool:
    """Return true only for read-only requests for the current runtime state."""
    normalized = re.sub(r"\s+", " ", str(text or "").strip()).casefold()
    if not normalized or len(normalized) > 120:
        return False
    if any(marker in normalized for marker in _STATUS_MUTATION_MARKERS):
        return False
    punctuation_stripped = normalized.rstrip("?.! ")
    return bool(
        _ZH_STATUS_QUERY.fullmatch(normalized)
        or _EN_STATUS_QUERY.fullmatch(normalized)
        or punctuation_stripped in _EN_STATUS_PHRASES
    )


def build_status_snapshot_reply(root: Path | str, objective: str) -> str:
    """Render a live, bounded status snapshot without invoking a model."""
    try:
        from ..core.mission_view import snapshot_mission_view
        from ..daemon.life_worker import read_continuous_state, read_daemon_status
        from ..life.memory import Backlog
        from ..life.role_activity import role_activity

        path = Path(root).expanduser()
        daemon = read_daemon_status(path)
        continuous_state = read_continuous_state(path)
        continuous = {
            "enabled": continuous_state.enabled,
            "objective": continuous_state.objective,
            "done_reason": continuous_state.done_reason,
            "done_at": continuous_state.done_at,
        }
        backlog_items = Backlog(path / "backlog.jsonl").all()
        activity = role_activity(path)
        roles = [
            {
                "role": name,
                "active": state.active,
                "status": state.status,
                "label": state.label,
                "age_s": state.age_s,
                "backend": getattr(state, "backend", ""),
                "model": getattr(state, "model", ""),
                "effort": getattr(state, "effort", None),
            }
            for name, state in activity.items()
        ]
        view = snapshot_mission_view(
            path,
            session={},
            daemon={"alive": daemon.alive},
            roles=roles,
            backlog=[asdict(item) for item in backlog_items],
            continuous=continuous,
            enrich_skill_content=False,
        )
        mission = view.get("mission")
        mission = mission if isinstance(mission, dict) else {}
        stage = view.get("stage")
        stage = stage if isinstance(stage, dict) else {}
        review = view.get("review")
        review = review if isinstance(review, dict) else {}
        role_rows = view.get("roles")
        role_rows = role_rows if isinstance(role_rows, list) else []
        timeline_rows = view.get("timeline")
        timeline_rows = timeline_rows if isinstance(timeline_rows, list) else []
        roles = [row for row in role_rows if isinstance(row, dict)]
        timeline = [row for row in timeline_rows if isinstance(row, dict)]
    except Exception:  # noqa: BLE001 - caller falls back to the Manager model
        return ""

    chinese = bool(re.search(r"[\u3400-\u9fff]", objective))
    health = str(getattr(daemon, "health_state", "") or "unknown")
    mission_status = str(mission.get("status") or "idle")
    if not daemon.alive and mission_status == "working":
        mission_status = "interrupted"
    title = " ".join(str(mission.get("title") or "").split())[:180]
    queued_campaign = bool(
        mission_status == "queued"
        and continuous.get("enabled")
        and str(continuous.get("objective") or "").strip()
    )
    if queued_campaign:
        title = " ".join(str(continuous["objective"]).split())[:180]
    stage_label = " ".join(
        str(stage.get("label") or stage.get("id") or "").split()
    )[:120]
    active_role = str(view.get("active_role") or "").strip()
    role_row = next(
        (row for row in roles if str(row.get("role") or "") == active_role),
        None,
    )
    role_is_active = role_row is not None
    if role_row is None:
        role_row = next(
            (
                row for row in reversed(roles)
                if str(row.get("status") or "") not in {"", "idle", "waiting"}
            ),
            None,
        )
        if role_row is not None:
            active_role = str(role_row.get("role") or "").strip()
    role_label = (
        " ".join(str(role_row.get("label") or "").split())[:160]
        if role_row is not None
        else ""
    )
    review_status = str(review.get("status") or "").strip()
    review_reason = " ".join(str(review.get("reason") or "").split())[:360]
    if queued_campaign:
        review_status = ""
        review_reason = ""
    try:
        last_event_ts = float(view.get("last_event_ts") or 0.0)
    except (TypeError, ValueError):
        last_event_ts = 0.0
    age_s = max(0, int(time.time() - last_event_ts)) if last_event_ts else None
    recent = [
        row for row in timeline
        if str(row.get("title") or "").strip()
    ][-2:]

    if chinese:
        lines = [
            "当前即时状态：",
            (
                f"- daemon：{'运行中' if daemon.alive else '已停止'}"
                f"（健康状态：{health}）"
            ),
            f"- 任务：{title or '当前没有活动任务'}（{mission_status}）",
        ]
        if stage_label:
            lines.append(f"- 阶段：{stage_label}")
        if active_role:
            lines.append(
                f"- {'当前' if role_is_active else '最近'}角色：{active_role}"
                + (f" — {role_label}" if role_label else "")
            )
        if review_status:
            lines.append(
                f"- 最近审查：{review_status}"
                + (f" — {review_reason}" if review_reason else "")
            )
        if recent:
            lines.append("- 最近事件：" + "；".join(
                " ".join(str(row.get("title") or "").split())[:160]
                for row in recent
            ))
        if age_s is not None:
            lines.append(f"- 快照事件距今：{age_s} 秒")
        return "\n".join(lines)

    lines = [
        "Current live status:",
        (
            f"- daemon: {'running' if daemon.alive else 'stopped'} "
            f"(health: {health})"
        ),
        f"- mission: {title or 'no active mission'} ({mission_status})",
    ]
    if stage_label:
        lines.append(f"- stage: {stage_label}")
    if active_role:
        lines.append(
            f"- {'active' if role_is_active else 'last'} role: {active_role}"
            + (f" — {role_label}" if role_label else "")
        )
    if review_status:
        lines.append(
            f"- latest review: {review_status}"
            + (f" — {review_reason}" if review_reason else "")
        )
    if recent:
        lines.append("- recent events: " + "; ".join(
            " ".join(str(row.get("title") or "").split())[:160]
            for row in recent
        ))
    if age_s is not None:
        lines.append(f"- snapshot event age: {age_s}s")
    return "\n".join(lines)


class SelfReplyMixin:
    """Operator-facing Manager front door mixed into ``_SkillLoopRunner``."""

    def _maybe_chat_outcome(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        route: str | None = None,
        root_task_id: str | None = None,
    ) -> _Outcome | None:
        workdir = (
            Path(self._args.workdir).expanduser()
            if getattr(self._args, "workdir", None)
            else Path.cwd()
        )
        safe_mode = env_flag("ARGUS_SKILL_SAFE_MODE", False)

        def _classify_run_exec(prompt: str) -> Any:
            return gateway_run_exec(
                self._backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=self._args.engineer_model,
                    reasoning_effort="low",
                    full_auto=safe_mode,
                    skip_git_repo_check=True,
                    dangerous_yolo=not safe_mode,
                    working_dir=str(workdir),
                ),
                run_label="router-classify",
                resume_thread_id=None,
            )

        def _phase(
            label: str,
            *,
            role: str = "manager",
            kind: str = "",
            detail: str = "",
        ) -> None:
            if not callable(phase_cb):
                return
            for kwargs in (
                {"role": role, "kind": kind, "detail": detail},
                {"role": role},
                {},
            ):
                try:
                    phase_cb(label, **kwargs)
                    return
                except TypeError:
                    continue
                except Exception:  # noqa: BLE001 - UI callbacks never own the turn
                    return

        class _PhaseSink:
            def __init__(self, inner: EventSink) -> None:
                self._inner = inner

            def handle_event(self, event: dict[str, Any]) -> None:
                safe_event = _redact_live_event(event)
                event_type = str(safe_event.get("type") or "")
                kind = str(safe_event.get("kind") or "")
                is_reply = event_type == "engineer.progress" and kind in REPLY_KINDS
                if event_type == "loop.start":
                    _phase(
                        f"{backend_label} working on your message…",
                        kind="loop.start",
                    )
                elif event_type == "engineer.progress" and not is_reply:
                    label, detail = describe_progress_step(safe_event)
                    _phase(label, kind=kind, detail=detail)
                self._inner.handle_event(safe_event)

            def handle_stream_line(self, stream: str, line: str) -> None:
                handler = getattr(self._inner, "handle_stream_line", None)
                if callable(handler):
                    handler(stream, line)

            def close(self) -> None:
                closer = getattr(self._inner, "close", None)
                if callable(closer):
                    closer()

        from ..core.role_config import runner_backend_label

        backend_label = runner_backend_label()
        _phase(f"Deciding: {backend_label} solo vs. the Argus team…")
        if route not in ("simple", "complex"):
            if root_task_id is None:
                route = self.manager.route(objective, run_exec=_classify_run_exec)
            else:
                route = self.manager.route(
                    objective,
                    run_exec=_classify_run_exec,
                    root_task_id=root_task_id,
                )
        if route == "simple":
            if looks_like_status_query(objective):
                _phase(
                    "Reading the current bounded status snapshot…",
                    kind="status_snapshot",
                )
                status_outcome = self._status_quick_reply(
                    objective=objective,
                    sink=_PhaseSink(sink),
                )
                if status_outcome is not None:
                    return status_outcome
            _phase(f"{backend_label} handling it solo…")
            return self._simple_quick_reply(
                objective=objective,
                sink=_PhaseSink(sink),
                seed_thread_id=seed_thread_id,
            )
        _phase("Handing off to Planner / Engineer / Reviewer…")
        return None


    def chat_reply_if_conversational(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        route: str | None = None,
        root_task_id: str | None = None,
    ) -> bool:
        with self.task_usage_context(root_task_id):
            return self._maybe_chat_outcome(
                objective=objective,
                sink=sink,
                seed_thread_id=seed_thread_id,
                phase_cb=phase_cb,
                route=route,
                root_task_id=root_task_id,
            ) is not None

    def reset_chat_session(self) -> None:
        self._next_seed_thread_id = None
        self.last_thread_id = None

    def _manager_reply_runtime_context(self, run_label: str) -> str:
        workspace_context = ""
        team_log_context = ""
        try:
            from ..roles.prompts.manager import manager_workspace_capability_prompt

            configured_workspace = str(
                getattr(self._args, "operator_workspace", "")
                or getattr(self._args, "workdir", "")
                or ""
            ).strip()
            workspace = (
                Path(configured_workspace).expanduser()
                if configured_workspace
                else Path.cwd()
            )
            state_root = (
                Path(self._manager_session_root).expanduser()
                if getattr(self, "_manager_session_root", None)
                else workspace
            )
            workspace_context = manager_workspace_capability_prompt(
                workspace,
                manifest_root=state_root,
            )
            if getattr(self, "_manager_session_root", None):
                team_log = state_root / "events.jsonl"
                team_log_context = (
                    f"Authoritative Team log: {team_log}\n"
                    "Team replies are not automatically copied into this chat. "
                    "When the operator asks about Team work, read that log yourself "
                    "before answering."
                )
        except Exception:  # noqa: BLE001 — context must never block a reply
            workspace_context = ""
            team_log_context = ""
        try:
            runner = getattr(self._backend, "_runner", None)
            if runner is None or not runner._acp_enabled(run_label):
                return "\n\n".join(
                    part for part in (workspace_context, team_log_context) if part
                )
        except Exception:  # noqa: BLE001 - metadata must never block a reply
            return "\n\n".join(
                part for part in (workspace_context, team_log_context) if part
            )
        runtime_fact = (
            "Runtime fact (answer accurately if the operator asks): this "
            "operator-facing Manager conversation is one logical session on a "
            "long-lived Copilot ACP process. Ordinary turns use session/prompt "
            "on that same live process and session; they do NOT spawn a fresh "
            "CLI process with --resume, and Argus does NOT resend the full chat "
            "transcript each turn. The front-door classifier is isolated from "
            "this conversation, and the background task daemon is a separate "
            "process. A deliberate context rotation starts a new conversation "
            "session with a structured handoff."
        )
        return "\n\n".join(
            part for part in (workspace_context, team_log_context, runtime_fact) if part
        )

    def _live_mission_status_block(self) -> str:
        session_root = getattr(self, "_manager_session_root", None)
        if not session_root:
            return ""
        try:
            from ..daemon.life_worker import read_daemon_status
            from ..life.memory import Backlog
            from ..life.role_activity import role_activity

            root = Path(session_root)
            daemon = read_daemon_status(root)
            daemon_lines = [
                "## Authoritative daemon status",
                f"- alive: {'true' if daemon.alive else 'false'}",
                f"- pid: {daemon.pid if daemon.alive and daemon.pid is not None else 'none'}",
                (
                    "- evidence source: daemon status/pid files for this session; "
                    "WebAPI activity and events.jsonl writes do not prove daemon liveness."
                ),
            ]
            daemon_block = "\n".join(daemon_lines)
            backlog_items = Backlog(root / "backlog.jsonl").all()
            running = [
                item
                for item in backlog_items
                if item.status == "running"
            ]
            if not running:
                mission = self._recent_mission_history_block(root, backlog_items)
            else:
                item = running[0]
                activity = role_activity(root)
                lines = (
                    [
                        "## Live mission status",
                        "A mission is currently running under your supervision in a "
                        f"separate daemon process (life_dir={root}):",
                    ]
                    if daemon.alive
                    else [
                        "## Interrupted mission status",
                        "The backlog still marks a mission as running, but the "
                        "authoritative daemon status is offline:",
                    ]
                )
                lines.append(
                    f'- item: "{(item.title or "").strip()[:120]}" (id={item.id})'
                )
                started = getattr(item, "started_ts", None)
                if isinstance(started, (int, float)) and started > 0:
                    lines[-1] += (
                        f", running for {max(0, int(time.time() - started))}s"
                    )
                for role in ("planner", "engineer", "reviewer"):
                    role_state = activity.get(role)
                    if role_state is None or role_state.status == "idle":
                        continue
                    lines.append(
                        f"- {role}: {role_state.label} ({role_state.status})"
                    )
                lines.extend([
                    "",
                    "Verify progress yourself before answering if useful — you have "
                    f"shell access and Manager authority over state under {root}.",
                    "Operator steering and abort requests are durable control actions. "
                    "Never say you are read-only or unable to direct the team.",
                ])
                mission = "\n".join(lines)
            maintenance = self._self_maintenance_status_block(root)
            return "\n\n".join(
                block for block in (daemon_block, mission, maintenance) if block
            )
        except Exception:  # noqa: BLE001 - status context is optional
            return ""

    @staticmethod
    def _self_maintenance_status_block(root: Path) -> str:
        from ..daemon.self_maintenance import read_self_maintenance_snapshot

        snapshot = read_self_maintenance_snapshot(root)
        if snapshot is None:
            return ""
        if snapshot.maintenance_available is True:
            isolation = "available"
        elif snapshot.maintenance_available is False:
            isolation = "unavailable"
        else:
            isolation = "unknown"
        phase = snapshot.phase or (
            "ready" if snapshot.maintenance_available is True else "idle"
        )
        lines = [
            "## Manager self-maintenance state",
            f"- phase: {phase}",
            f"- isolated repair capability: {isolation}",
        ]
        if snapshot.last_audit_at > 0:
            lines.append(
                "- last audit: "
                f"{max(0, int(time.time() - snapshot.last_audit_at))}s ago"
            )
        if snapshot.pr_url:
            lines.append(f"- open maintenance PR: {snapshot.pr_url}")
        if snapshot.awaiting_commit:
            # A reviewed, canaried fix is already live locally and is waiting on
            # the operator only to leave the machine. Say what to type, or the
            # gate turns into a pile nobody notices.
            lines.append(
                "- **awaiting your approval to publish**: "
                f"{snapshot.awaiting_commit[:12]} "
                f"(`argus-skill --approve-publication {snapshot.awaiting_commit[:12]}`)"
            )
        if snapshot.publication_status:
            lines.append(f"- upstream publication: {snapshot.publication_status}")
        if snapshot.publication_error:
            lines.append(f"- publication note: {snapshot.publication_error}")
        return "\n".join(lines)

    def _recent_mission_history_block(
        self,
        root: Path,
        backlog_items: list[Any] | None = None,
    ) -> str:
        try:
            from ..life.memory import EventJournal

            recent = EventJournal(root / "events.jsonl").tail(1)
            latest_item = backlog_items[-1] if backlog_items else None
            if not recent and latest_item is None:
                return ""
            lines = [
                "## Recent mission history",
                "No mission is running right now under your supervision "
                f"(life_dir={root}).",
            ]
            if latest_item is not None:
                lines.append(
                    "- latest backlog task: "
                    f"[{latest_item.status}] "
                    f'"{(latest_item.title or "").strip()[:120]}" '
                    f"(id={latest_item.id})"
                )
                objective = " ".join(
                    str(
                        latest_item.original_objective
                        or latest_item.objective
                        or ""
                    ).split()
                )
                if objective:
                    lines.append(f"  operator objective: {objective[:600]}")
            if recent:
                entry = recent[0]
                age_s = max(0, int(time.time() - float(entry.ts)))
                lines.append(
                    f'- latest recorded event ({age_s}s ago): {entry.kind}: '
                    f'"{(entry.title or "").strip()[:120]}"'
                )
                summary = (entry.summary or "").strip()
                if summary:
                    lines.append(f"  {summary[:300]}")
            lines.extend([
                "",
                "This may or may not be what the operator is asking about — judge "
                "relevance from its age and content. Verify yourself if useful "
                f"(grep logs, read files); you have real shell access under {root}.",
            ])
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - history context is optional
            return ""

    def _status_snapshot_reply(self, objective: str) -> str:
        session_root = getattr(self, "_manager_session_root", None)
        if not session_root:
            return ""
        return build_status_snapshot_reply(session_root, objective)

    def _status_quick_reply(
        self,
        *,
        objective: str,
        sink: EventSink,
    ) -> _Outcome | None:
        reply = self._status_snapshot_reply(objective)
        if not reply:
            return None
        sink.handle_event({
            "type": "loop.start",
            "text": "SELF: reading bounded runtime status",
            "transient": True,
        })
        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "exit_code": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "premium_requests": 0.0,
            "model": "deterministic-status-snapshot",
            "usage_scope": "delta",
            "last_message": reply,
            "session_id": None,
            "turn_completed": True,
            "attempt_count": 0,
            "transient": True,
        })
        sink.handle_event({
            "type": "loop.done",
            "text": "status=done rounds=0 (status snapshot)",
            "transient": True,
        })
        return _Outcome(
            success=True,
            status="done",
            stop_reason="",
            rounds=0,
            last_thread_id=None,
            chat_mode=False,
            auth_failure=False,
        )

    def _simple_quick_reply(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
    ) -> _Outcome:
        from ..core.role_config import runner_backend_label
        from ..roles.prompts.manager import build_simple_prompt

        args = self._args
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        backend_label = runner_backend_label()
        sink.handle_event({
            "type": "loop.start",
            "text": f"SELF: one {backend_label} handling {objective[:120]}",
        })

        self._current_sink = sink
        self._current_failure_ledger = None
        configured_workspace = str(
            getattr(args, "operator_workspace", "") or ""
        ).strip()
        workdir = (
            Path(configured_workspace).expanduser()
            if configured_workspace
            else Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        prompt = build_simple_prompt(
            objective=objective,
            identity_card=self.manager.role_context(),
            mission_status=self._live_mission_status_block(),
            runtime_context=self._manager_reply_runtime_context("simple-1"),
            operator_workspace=str(workdir),
        )
        session_root = getattr(self, "_manager_session_root", None)
        read_dirs = (
            [str(Path(session_root).expanduser())]
            if session_root and Path(session_root).expanduser() != workdir
            else None
        )

        def _self_inactivity(snapshot: Any) -> str | None:
            try:
                idle = int(getattr(snapshot, "idle_seconds", 0) or 0)
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "codex_idle",
                    "text": f"{backend_label} process running; no stream output for {idle}s",
                })
            except Exception:  # noqa: BLE001
                pass
            return None

        reply_message_id = f"manager-reply-{id(sink):x}"

        def _emit_block(block: str) -> None:
            body = (block or "").strip()
            if not body:
                return
            try:
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "assistant_message",
                    "agent_layer": "manager",
                    "message_id": reply_message_id,
                    "text": body,
                })
            except Exception:  # noqa: BLE001 - UI sinks never own the turn
                pass

        reply_model = resolve_manager_reply_model()
        options = RunnerOptions(
            model=reply_model,
            reasoning_effort=resolve_role_reasoning_effort(
                "ARGUS_SKILL_SELF_REASONING_EFFORT",
                default="xhigh",
            ),
            full_auto=False,
            skip_git_repo_check=True,
            dangerous_yolo=True,
            sandbox_mode=None,
            working_dir=str(workdir),
            add_dirs=read_dirs,
            watchdog_hard_idle_seconds=env_int(
                "ARGUS_SKILL_SELF_HARD_IDLE_SECONDS", 120
            ),
            watchdog_soft_idle_seconds=env_int(
                "ARGUS_SKILL_SELF_SOFT_IDLE_SECONDS", 5
            ),
            inactivity_callback=_self_inactivity,
            on_agent_message=_emit_block,
        )
        attempt_results: list[Any] = []
        try:
            result = gateway_run_exec(
                self._backend,
                prompt=prompt,
                options=options,
                run_label="simple-1",
                resume_thread_id=seed,
            )
            attempt_results.append(result)
            if self_retryable_transport_failure(result):
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "provider_retry",
                    "agent_layer": "manager",
                    "text": (
                        "Copilot reply transport stalled; retrying once in a fresh session"
                    ),
                })
                result = gateway_run_exec(
                    self._backend,
                    prompt=prompt,
                    options=options,
                    run_label="simple-1",
                    resume_thread_id=None,
                )
                attempt_results.append(result)
        finally:
            self._current_sink = None

        last_msg = (result.last_agent_message or "").strip()
        fatal = getattr(result, "fatal_error", None)
        success = result.exit_code == 0 and not fatal and bool(last_msg)
        new_thread_id = getattr(result, "thread_id", None)
        round_thread_id = new_thread_id or seed
        result_status = "done" if success else "error"
        if should_clear_thread_id_after_outcome(
            status=result_status,
            fatal_error=str(getattr(result, "fatal_error", "") or ""),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_thread_id = None
        elif new_thread_id:
            self.last_thread_id = new_thread_id
            self._next_seed_thread_id = new_thread_id

        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "exit_code": int(getattr(result, "exit_code", 0) or 0),
            "input_tokens": sum(
                int(getattr(attempt, "input_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "cached_input_tokens": sum(
                int(getattr(attempt, "cached_input_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "output_tokens": sum(
                int(getattr(attempt, "output_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "reasoning_output_tokens": sum(
                int(getattr(attempt, "reasoning_output_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "premium_requests": sum(
                float(getattr(attempt, "premium_requests", 0.0) or 0.0)
                for attempt in attempt_results
            ),
            "model": str(
                getattr(result, "usage_model", "") or reply_model or ""
            ),
            "usage_scope": "delta",
            "last_message": last_msg,
            "session_id": round_thread_id,
            "turn_completed": bool(
                success
            ),
            "attempt_count": len(attempt_results),
        })

        status = "done" if success else "error"
        stop_reason = (
            ""
            if success
            else str(
                fatal
                or (
                    "Manager SELF turn completed without an assistant message"
                    if result.exit_code == 0
                    else f"exit={result.exit_code}"
                )
            )
        )
        auth_failure = self._consume_auth_failure()
        sink.handle_event({
            "type": "loop.done",
            "text": f"status={status} rounds=1 (simple)",
        })
        return _Outcome(
            success=success,
            status=status,
            stop_reason=stop_reason,
            rounds=1,
            last_thread_id=new_thread_id,
            chat_mode=False,
            auth_failure=auth_failure,
        )


__all__ = [
    "SelfReplyMixin",
    "build_status_snapshot_reply",
    "looks_like_status_query",
    "self_retryable_transport_failure",
]
