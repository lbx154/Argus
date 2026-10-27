"""Manager turn classification, control handling, and mission dispatch.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Owns the classify/control/config/SELF/TEAM phase helpers used
by ``manager_message`` (handoff building, authorization/steer/pause/abort control,
config-intent application, triage fallbacks, and mission dispatch), plus the
execution/continuous/bounded handoff entry points.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.mission_view import say, uses_cjk
from .manager_pending_question import _emit_ui_turn
from .manager_state import _chat_state_for, _lock_for, manager_control_generation


def _authorization_workdir(
    chat_state: dict[str, Any],
    life_dir: Path,
) -> Path:
    from ..manager.front_door import _operator_workspace

    return _operator_workspace(chat_state, life_dir)


@contextmanager
def _manager_request_scope(sid: str, generation: int | None = None) -> Iterator[Callable[[], bool]]:
    from ..core.run_gateway import run_interrupt_scope
    if generation is None:
        generation = manager_control_generation(sid)

    def cancelled() -> bool:
        return manager_control_generation(sid) != generation

    with run_interrupt_scope(lambda: "operator interrupted Manager request" if cancelled() else None):
        yield cancelled


def manager_execution_handoff(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    root_task_id: str | None = None,
) -> str:
    """Resolve a direct Web/TUI command into Manager's role-clean handoff."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_execution_task

    generation = manager_control_generation(sid)
    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _manager_request_scope(sid, generation) as cancelled, _lock_for(sid):
        if cancelled():
            from ..manager.front_door import ManagerHandoffSupersededError

            raise ManagerHandoffSupersededError("Manager request was superseded before preparation")
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        return manager_execution_task(
            mem,
            text,
            chat_state,
            root_task_id=root_task_id,
        )


def manager_continuous_handoff(
    sid: str,
    requested_objective: str,
    *,
    global_root: Path | str | None = None,
    name_session: bool = False,
    control_generation: int | None = None,
) -> str:
    """Atomically enable a Manager-authored continuous handoff."""
    from ..daemon.state import read_continuous_state
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_continuous_handoff as commit_handoff

    generation = manager_control_generation(sid) if control_generation is None else control_generation
    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _manager_request_scope(sid, generation) as cancelled, _lock_for(sid):
        if cancelled():
            from ..manager.front_door import ManagerHandoffSupersededError

            raise ManagerHandoffSupersededError("Manager request was superseded before preparation")
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        # An objective given here is the operator's explicit ongoing intent.
        chat_state["_continuous_open_ended"] = bool(requested_objective.strip()) or read_continuous_state(
            mem.project_root
        ).open_ended
        from ..manager.front_door import ManagerHandoffError, ManagerHandoffSupersededError

        try:
            execution_objective = commit_handoff(mem, requested_objective, chat_state, cancelled=cancelled)
        except ManagerHandoffError as exc:
            if cancelled():
                raise ManagerHandoffSupersededError("Manager request was superseded before commit") from exc
            raise
        from .manager_state import sync_manager_continuous

        sync_manager_continuous(sid, generation, execution_objective)
        return execution_objective


def disable_manager_continuous(
    sid: str,
    *,
    life_dir: Path,
) -> None:
    """Stop without waiting behind the Manager turn being cancelled."""
    from ..daemon.state import disable_continuous_config
    from ..manager.front_door import ManagerHandoffError
    from .manager_state import interrupt_manager_turns

    interrupt_manager_turns(sid, clear_continuous=False)
    persisted = disable_continuous_config(life_dir)
    if persisted.enabled:
        raise ManagerHandoffError("continuous stop could not be persisted")
    interrupt_manager_turns(sid)


def manager_bounded_handoff(
    sid: str,
    text: str,
    persist: Any,
    *,
    global_root: Path | str | None = None,
    root_task_id: str | None = None,
    name_session: bool = False,
) -> Any:
    """Commit Manager state and caller persistence under one pipeline lock."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_bounded_handoff as commit_handoff

    generation = manager_control_generation(sid)
    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _manager_request_scope(sid, generation) as cancelled, _lock_for(sid):
        if cancelled():
            from ..manager.front_door import ManagerHandoffSupersededError

            raise ManagerHandoffSupersededError("Manager request was superseded before preparation")
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        if name_session:
            from ..core.session import read_session_meta, session_name_is_open

            meta = read_session_meta(mem.global_root, sid)
            if session_name_is_open(meta):
                from ..manager.front_door import _maybe_name_session

                _maybe_name_session(chat_state, text, replacing=True)
        return commit_handoff(
            mem,
            text,
            chat_state,
            persist,
            root_task_id=root_task_id,
            cancelled=cancelled,
        )


def _item_to_dict(item: Any, fallback_title: str) -> dict[str, Any] | None:
    if item is None:
        return None
    for attr in ("to_dict", "asdict", "_asdict"):
        fn = getattr(item, attr, None)
        if callable(fn):
            try:
                return dict(fn())
            except Exception:  # noqa: BLE001
                break
    try:
        return dict(item)  # mapping-like
    except Exception:  # noqa: BLE001
        return {
            "id": getattr(item, "id", None),
            "title": getattr(item, "title", fallback_title),
            "status": getattr(item, "status", "pending"),
            "pending_question": getattr(item, "pending_question", ""),
            "operator_decision": getattr(item, "operator_decision", {}),
        }


def _build_handoff(life_dir: Any) -> str:
    """A STRUCTURED handoff seeded as the first message of the Manager session
    a new web process starts for an existing conversation. Minimal by design
    (the operator's rule: don't pre-chew — give the identity + where the logs
    live, and let the Manager read them itself): who it is, the project path,
    and the last few turns for continuity. Everything else it self-serves.
    """
    lines = [
        "[SESSION HANDOFF — this Manager session starts after a restart.",
        "You are the Argus Manager for this project — the SINGLE interface between the operator",
        "and the autonomous research system (a black box to them). You reply to chat, dispatch",
        "real work to the planner/engineer/reviewer team, and answer 'what's happening' by",
        f"reading the project's own logs. Project workspace / logs: {life_dir}",
        "You can read events.jsonl / backlog.jsonl / transcript.jsonl there yourself — check state",
        "from those, do not expect it spoon-fed.",
    ]
    try:
        from ..core.transcript import read_turns

        turns = read_turns(life_dir, limit=6)
        if turns:
            lines.append("Recent conversation:")
            for t in turns:
                who = "operator" if str(t.get("role")) == "operator" else "you(Argus)"
                lines.append(f"  {who}: {str(t.get('text', '')).strip()[:200]}")
    except Exception:  # noqa: BLE001
        pass
    lines.append("Continue seamlessly.]")
    return "\n".join(lines)


def _cancelled_result() -> dict[str, Any]:
    return {
        "kind": "cancelled",
        "reply": "Cancelled.",
    }


def _journal_argus_reply(
    life_dir: Path,
    turn_id: str,
    reply: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist ``reply`` to transcript.jsonl and stream it to the live UI.

    This exact journal-then-emit pair follows every terminal Manager reply in
    ``manager_message``; hoisted verbatim (including the best-effort
    ``except Exception`` swallow) so every phase helper below shares
    identical journaling behavior.
    """
    from ..core.transcript import append_turn

    try:
        append_turn(life_dir, "argus", reply, message_id=f"{turn_id}-argus", metadata=metadata)
    except Exception:  # noqa: BLE001
        pass
    _emit_ui_turn(
        life_dir,
        "argus",
        reply,
        message_id=f"{turn_id}-argus",
        metadata=metadata,
    )


# Phase kinds that are observable work of the turn (as opposed to routing
# narration such as "handling it solo…"). These are what a reader wants to see
# under the reply afterwards: which tools ran, on what, and how each ended.
TURN_STEP_KINDS = frozenset({"tool_use", "command_execution", "file_change", "tool_result"})
_TURN_STEP_LIMIT = 80
_TURN_STEP_TEXT_LIMIT = 240
_TERMINAL_STEP_STATUSES = frozenset({"completed", "failed", "cancelled", "canceled", "error", "done"})


def _clip_step_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > _TURN_STEP_TEXT_LIMIT:
        return text[: _TURN_STEP_TEXT_LIMIT - 1] + "…"
    return text


def record_turn_step(
    steps: list[dict[str, Any]],
    payload: dict[str, Any],
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Fold one streamed ``phase`` frame into the turn's durable step list.

    A tool result closes the step that started the same call (``call_id``)
    instead of adding a row of its own; a new call closes whatever step was
    still open, because a single agent works one call at a time, and so does
    a result from a backend that names no call. Heartbeats and routing
    narration are not steps. The list is bounded so a very long turn cannot
    bloat the transcript. Returns the steps the frame closed or opened, in
    that order, so the caller can persist each change as it happens.
    """
    if not isinstance(payload, dict) or payload.get("heartbeat"):
        return []
    kind = str(payload.get("kind") or "").strip()
    if kind not in TURN_STEP_KINDS:
        return []
    ts = float(now if now is not None else time.time())
    call_id = str(payload.get("call_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if kind == "tool_result":
        for step in reversed(steps):
            if (step.get("call_id") == call_id) if call_id else not step.get("ended_ts"):
                step["status"] = status or "completed"
                step["ended_ts"] = ts
                output = _clip_step_text(payload.get("output"))
                if output:
                    step["output"] = output
                return [step]
        return []
    touched: list[dict[str, Any]] = []
    for step in reversed(steps):
        if not step.get("ended_ts"):
            step["ended_ts"] = ts
            if not step.get("status") or step.get("status") == "running":
                step["status"] = "completed"
            touched.append(step)
        break
    if len(steps) >= _TURN_STEP_LIMIT:
        return touched
    step: dict[str, Any] = {
        "kind": kind,
        "label": _clip_step_text(payload.get("label")),
        "started_ts": ts,
        "ended_ts": 0.0,
        "status": status or "running",
    }
    for key in ("detail", "tool", "tool_kind"):
        value = _clip_step_text(payload.get(key))
        if value:
            step[key] = value
    if call_id:
        step["call_id"] = call_id
    steps.append(step)
    touched.append(step)
    return touched


def turn_step_event(turn_id: str, step: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """The durable twin of one turn step: an ``engineer.progress`` row the
    Manager owns, attributed to the turn's map card (``turn:<id>``).

    The same step yields the same ``message_id`` when it opens and when it
    closes, so the mission view keeps one work record per tool call and
    updates its status in place. ``turn_step`` marks the row for readers that
    already show the reply's step list and must not show the call twice.
    """
    call_id = str(step.get("call_id") or "").strip()
    label = str(step.get("label") or "")
    output = str(step.get("output") or "")
    status = str(step.get("status") or "running")
    event: dict[str, Any] = {
        "type": "engineer.progress",
        "kind": str(step.get("kind") or "tool_use"),
        "agent_layer": "manager",
        "actor": "manager",
        "item_id": f"turn:{turn_id}",
        "message_id": f"{turn_id}:{call_id or step.get('started_ts') or 0}",
        "text": label,
        "action_summary": f"{label} · {output}" if output else label,
        "status": status,
        "turn_step": True,
        "ts": float(now if now is not None else time.time()),
    }
    for source, target in (("tool", "tool_name"), ("tool_kind", "tool_kind"), ("call_id", "call_id"), ("detail", "detail")):
        value = step.get(source)
        if value not in (None, ""):
            event[target] = value
    return event


def finish_turn_steps(
    steps: list[dict[str, Any]],
    now: float | None = None,
    *,
    failed: bool = False,
) -> list[dict[str, Any]]:
    """Close any step still open when the reply lands; return the list."""
    ts = float(now if now is not None else time.time())
    for step in steps:
        unfinished = not step.get("ended_ts")
        if unfinished:
            step["ended_ts"] = ts
        if str(step.get("status") or "") in ("", "running"):
            step["status"] = "interrupted" if failed and unfinished else "completed"
    return steps


@dataclass
class _TurnEmitter:
    """Bundles the per-turn streaming/journaling callbacks shared by the
    ``manager_message`` phase helpers below, so a phase that needs one more of
    them doesn't have to grow its own parameter list.
    """

    life_dir: Path
    turn_id: str
    fragment: Callable[[str, dict[str, Any]], None]
    # Tool steps streamed during this turn, journaled with the reply so the
    # conversation and the map can show the work behind a single-agent answer.
    steps: list[dict[str, Any]] = field(default_factory=list)
    task_objective: str = ""
    solo: bool = False
    task_started: bool = False
    turn_kind: str = ""

    def start_task(self) -> None:
        if not self.solo or self.task_started:
            return
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=self.life_dir).append({
            "type": "manager.turn.started", "message_id": self.turn_id,
            "text": self.task_objective, "ts": time.time(),
            **({"turn_kind": self.turn_kind} if self.turn_kind else {}),
        })
        self.task_started = True

    def record_steps(self, steps: list[dict[str, Any]]) -> None:
        """Persist step changes once the turn has a card to own them."""
        if not steps or not self.task_started:
            return
        from ..life.event_log import JsonlEventSink

        sink = JsonlEventSink(None, life_dir=self.life_dir)
        for step in steps:
            sink.append(turn_step_event(self.turn_id, step))

    def cancel_task(self) -> None:
        if self.task_started:
            from ..life.event_log import JsonlEventSink

            JsonlEventSink(None, life_dir=self.life_dir).append({
                "type": "manager.turn.cancelled", "message_id": self.turn_id, "ts": time.time(),
            })

    def phase(self, label: str, *, role: str = "manager") -> None:
        self.fragment("phase", {"role": role, "label": label})

    def reply_fragment(self, text: str, *, message_id: str | None = None) -> None:
        payload: dict[str, Any] = {"text": text, "fragment_mode": "snapshot"}
        if message_id is not None:
            payload["message_id"] = message_id
        self.fragment("delta", payload)

    def journal(self, text: str, result: dict[str, Any]) -> None:
        metadata = {
            key: result[key]
            for key in (
                "mission_result",
                "item_id",
                "success",
                "summary",
                "delivery_id",
                "delivery",
                "decision_card",
            )
            if key in result
        }
        if self.task_started:
            metadata.update(task_turn=True, success=result.get("success", result.get("kind") != "error"))
        if self.turn_kind:
            metadata["turn_kind"] = self.turn_kind
        if self.steps:
            still_open = [step for step in self.steps if not step.get("ended_ts")]
            metadata["steps"] = finish_turn_steps(
                self.steps, failed=result.get("success") is False,
            )
            result["steps"] = metadata["steps"]
            self.record_steps(still_open)
        _journal_argus_reply(
            self.life_dir,
            self.turn_id,
            text,
            metadata or None,
        )

    def emit_only(self, text: str) -> None:
        """Stream ``text`` to the UI without journaling it to transcript.jsonl.

        Used only for the final "Queued · <title>" status line, which mirrors
        the enqueued task rather than a Manager reply the operator sent.
        """
        _emit_ui_turn(self.life_dir, "argus", text, message_id=f"{self.turn_id}-argus")

    def respond(
        self,
        text: str,
        result: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """Emit ``text`` as a delta fragment, journal+stream it, and return
        ``result`` merged with the reply. This is the standard terminal-reply
        pattern used by nearly every ``manager_message`` branch.
        """
        self.reply_fragment(text, message_id=message_id)
        self.journal(text, result)
        return {"reply": text, **result}

    def journal_and_respond(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        """Like :meth:`respond` but for replies already streamed by the
        callee (e.g. ``manager_triage`` drives ``on_fragment`` itself), so no
        extra delta fragment is emitted here.
        """
        self.journal(text, result)
        return {"reply": text, **result}


def _standing_notice_text(instruction: str) -> str:
    text = " ".join(str(instruction or "").split())
    if len(text) > 60:
        text = text[:59] + "…"
    if any("\u3400" <= ch <= "\u9fff" for ch in text):
        return f"已记为长期指令：{text}（说“取消这条长期指令”即可撤销）"
    return f"Saved as a standing instruction: {text} (say “cancel that standing instruction” to revoke it)"


def _announce_standing_directive(
    chat_state: dict[str, Any], life_dir: Path, turn_id: str,
) -> None:
    """Tell the operator, in the conversation, that an instruction now persists.

    Long-term instructions steer every later prompt; recording one silently
    leaves the operator unable to see or undo it.
    """
    instruction = str(chat_state.pop("_standing_notice", "") or "").strip()
    if instruction:
        _journal_argus_reply(
            life_dir, f"{turn_id}-standing", _standing_notice_text(instruction),
        )


def _handle_authorization_control(
    chat_state: dict[str, Any],
    life_dir: Path,
    authorization_actions: list[Any],
    source_channel: str,
    source_message_id: str,
    turn_id: str,
    emitter: _TurnEmitter,
) -> dict[str, Any]:
    """Record (or reject) an operator authorization against the latest
    Reviewer finding. Always terminal — the merged classify call already
    decided this turn is an authorization, so there is no fall-through to
    triage/dispatch after this.
    """
    from ..manager.control_state import CampaignControlStore

    try:
        control_store = CampaignControlStore(
            life_dir,
            project_root=_authorization_workdir(chat_state, life_dir),
        )
        head = control_store.read_head()
        snapshot = control_store.read_snapshot(head)
        terminal_evidence = list((snapshot or {}).get("terminal_evidence") or [])
        diagnosis = (
            terminal_evidence[-1]
            if terminal_evidence
            and isinstance(terminal_evidence[-1], dict)
            else {}
        )
        if head is None or not diagnosis:
            raise ValueError("no current Reviewer finding is awaiting authorization")
        validator_repair = "validator_repair" in authorization_actions
        if (
            validator_repair
            and diagnosis.get("failure_source") != "validator_defect"
        ):
            raise ValueError(
                "current Reviewer diagnosis is not validator_defect"
            )
        repair_paths = list(diagnosis.get("repair_paths") or [])
        validator_id = str(diagnosis.get("validator_id") or "")
        identity = control_store.campaign_identity(
            campaign_epoch=head.campaign_epoch,
        )
        if (
            identity.campaign_id != head.campaign_id
            or identity.objective != head.objective
        ):
            raise ValueError("active campaign identity changed")
        authorization = control_store.issue_authorization(
            identity=identity,
            blocker_fingerprint=(
                validator_id or str(diagnosis.get("failure_source") or "review")
            ),
            allowed_actions=authorization_actions,
            scope="active_blocker",
            allowed_write_paths=repair_paths,
            source_channel=source_channel,
            source_message_id=source_message_id or turn_id,
            validator_id=validator_id,
            acceptance_retries=(
                1 if validator_repair else 0
            ),
            expected_state_revision=head.state_revision,
        )
        reply = (
            "Authorization saved for the current blocker. "
            "The team can use it when the task resumes."
        )
        result = {
            "kind": "control",
            "control": "authorization",
            "authorization_id": authorization.authorization_id,
            "campaign_id": authorization.campaign_id,
            "state_revision": authorization.state_revision,
            "allowed_actions": list(authorization.allowed_actions),
        }
    except (OSError, TypeError, ValueError) as exc:
        reply = f"Authorization not recorded: {exc}. No task was dispatched."
        result = {"kind": "control", "control": "authorization_rejected"}
    return emitter.respond(reply, result, message_id="authorization")


def _handle_steer_control(
    life_dir: Path,
    emitter: _TurnEmitter,
    *,
    chat_state: dict[str, Any],
    body: str,
    directive: str,
    lifetime: str,
) -> dict[str, Any]:
    """Persist steering and also wake the currently running mission."""
    from ..apps._inbox import queue_inbox_message
    from ..manager.directive import (
        active_manager_directive_message,
        newer_direction,
        set_active_manager_directive,
    )

    manager_directive = directive.strip()
    from ..daemon.state import read_continuous_state

    current = read_continuous_state(life_dir)
    promoted_to_standing = False
    standing_objective = ""
    if lifetime == "standing":
        from ..daemon.state import compare_and_swap_continuous_config
        from ..life.memory import LifeMemory

        running = [
            item
            for item in LifeMemory.open(life_dir).backlog.active()
            if item.status == "running"
        ]
        active_objective = ""
        if running:
            active = max(
                running,
                key=lambda item: float(item.started_ts or 0.0),
            )
            active_objective = str(
                active.original_objective
                or active.objective
                or ""
            ).strip()
        # The ongoing campaign carries the operator's newer direction after the
        # work it continues, so the Planner reads it as superseding that work's
        # terms (s-fe7a9f4f: a standing steer kept "…供用户选择" as the objective).
        standing_objective = newer_direction(active_objective or current.objective, manager_directive)
    set_active_manager_directive(
        life_dir,
        manager_directive,
        source="manager.steer",
        authorized_objective=standing_objective,
        scope_objective=current.objective,
    )
    if lifetime == "standing":
        promoted_to_standing = compare_and_swap_continuous_config(
            life_dir,
            expected=current,
            enabled=True,
            objective=standing_objective,
            open_ended=True,
        )
        if promoted_to_standing:
            chat_state.setdefault("config", {})["continuous"] = True
            chat_state["continuous_objective"] = standing_objective
        else:
            latest = read_continuous_state(life_dir)
            chat_state.setdefault("config", {})["continuous"] = latest.enabled
            chat_state["continuous_objective"] = latest.objective
    directive = active_manager_directive_message(life_dir)
    if directive:
        queue_inbox_message(
            life_dir,
            directive,
            source="manager.steer",
        )
    # The directive is the Manager's instruction to the team; the operator
    # hears that their direction was taken, not the instruction itself.
    return emitter.respond(
        say(
            "steer_taken" if lifetime != "standing"
            else "steer_taken_ongoing" if promoted_to_standing
            else "steer_taken_not_ongoing",
            uses_cjk(body or manager_directive),
        ),
        {
            "kind": "control",
            "control": "steer",
            "continuous": promoted_to_standing,
        },
        message_id="steer",
    )


def _handle_pause_control(
    body: str,
    chat_state: dict[str, Any] | None,
    life_dir: Path,
    emitter: _TurnEmitter,
) -> dict[str, Any]:
    """Clock out the whole session immediately, preserving resumable state.

    Unlike ABORT (one running backlog item), PAUSE disables the standing
    campaign, interrupts the active item, and asks the daemon process to exit so
    it cannot claim the next pending item. The objective and backlog remain on
    disk for an explicit later resume.
    """
    from ..daemon.state import (
        disable_continuous_config,
        read_continuous_state,
        request_daemon_stop,
    )
    from ..life.memory import request_running_item_abort

    before = read_continuous_state(life_dir)
    after = disable_continuous_config(
        life_dir,
        done_reason="operator pause",
    )
    pause_persisted = not after.enabled
    if pause_persisted and chat_state is not None:
        chat_state.setdefault("config", {})["continuous"] = False
        chat_state["continuous_objective"] = ""

    abort_requested, item_id = request_running_item_abort(
        life_dir,
        reason=f"operator paused session: {body}",
        requested_by="manager",
    )
    daemon_stop_requested, daemon_pid = request_daemon_stop(life_dir)
    daemon_stop_failed = daemon_pid is not None and not daemon_stop_requested

    chinese = uses_cjk(body)
    if not pause_persisted:
        reply = (
            "我已要求当前任务和后台工作进程停止，但暂停状态未能保存。"
            "请先检查项目状态，再恢复工作。"
            if chinese
            else "I asked the current task and background worker to stop, but "
            "could not save the paused state. Check the project status before resuming."
        )
    elif daemon_stop_failed:
        reply = (
            "任务已暂停，已有工作保留，但后台工作进程没能停下，恢复前请检查它的状态。"
            if chinese
            else "The task is paused and existing work is kept, but the background "
            "worker did not stop. Check its status before resuming."
        )
    else:
        reply = say("paused_stopping" if abort_requested or daemon_stop_requested else "paused", chinese)

    return emitter.respond(
        reply,
        {
            "kind": "control",
            "control": "pause",
            "pause_persisted": pause_persisted,
            "continuous_was_enabled": before.enabled,
            "continuous_enabled": after.enabled,
            "requested": pause_persisted or abort_requested or daemon_stop_requested,
            "item_id": item_id,
            "daemon_stop_requested": daemon_stop_requested,
            "daemon_stop_failed": daemon_stop_failed,
            "daemon_pid": daemon_pid,
        },
        message_id="pause",
    )


def _handle_abort_control(
    body: str,
    life_dir: Path,
    emitter: _TurnEmitter,
) -> dict[str, Any]:
    """Request a stop for the currently running mission task, if any."""
    from ..life.memory import request_running_item_abort

    requested, item_id = request_running_item_abort(
        life_dir,
        reason=f"operator requested: {body}",
        requested_by="manager",
    )
    chinese = uses_cjk(body)
    if requested:
        reply = "我已要求当前任务停止。" if chinese else "I asked the current task to stop."
    elif item_id is not None:
        reply = (
            "当前任务未能停止；请先检查其状态，再重试。"
            if chinese
            else "I couldn't ask the current task to stop. Check its status before trying again."
        )
    else:
        reply = (
            "当前没有运行中的任务；待办任务未受影响。"
            if chinese
            else "No task is currently running. Pending tasks were left unchanged."
        )
    return emitter.respond(
        reply,
        {"kind": "control", "control": "abort", "requested": requested, "item_id": item_id},
    )


def _model_catalog_ids(chat_state: dict[str, Any], backend: str) -> list[str]:
    """The model ids ``backend`` accepts, for vetting chat model changes."""
    from .mission_items import backend_model_ids

    return backend_model_ids(backend, chat_state.get("global_root"))


def _apply_config(
    mem: Any, intents: Any, chat_state: dict[str, Any], emitter: _TurnEmitter, operator_text: str,
) -> dict[str, Any] | None:
    """Apply the settings the Manager filed and confirm each; ``None`` when none applied."""
    from ..manager.config_intent import _apply_config_intent, localize_config_confirmation

    lines: list[str] = []
    try:
        applied = _apply_config_intent(
            mem, intents, chat_state, on_confirm=lines.append,
            model_catalog=lambda backend: _model_catalog_ids(chat_state, backend),
        )
    except Exception:  # noqa: BLE001 — a config-apply hiccup must never block the message
        applied = False
    if not applied:
        return None
    chinese = uses_cjk(operator_text)
    reply = "\n".join(localize_config_confirmation(line, chinese=chinese) for line in lines).strip()
    return emitter.respond(reply or ("已应用设置。" if chinese else "Done — setting applied."), {"kind": "chat"},
                           message_id="config")


def ask_text(handoff: Any, chinese: bool) -> str:
    """The Manager's ask as the operator reads it: the one place they see the question."""
    from ..life.supervisor.pending_notify import numbered_options

    return "\n\n".join(part for part in (handoff.question, numbered_options(handoff.options, chinese)) if part)


def _settle_manager_turn(
    mem: Any,
    reply: str,
    handoff: Any,
    chat_state: dict[str, Any],
    emitter: _TurnEmitter,
    *,
    source_channel: str,
    source_message_id: str,
    operator_text: str,
) -> dict[str, Any] | None:
    """What Argus does with the Manager's turn: its reply, or what it filed.

    Returns the turn's terminal result, or ``None`` when the Manager handed
    the work to the team and the caller dispatches the ticket.
    """
    failure = chat_state.pop("_self_failure", None)
    if failure:
        chat_state.pop("_self_delivery", None)
        return emitter.respond(reply, {"kind": "error", "success": False, "error_code": "inline_reply_failed"})
    life_dir = emitter.life_dir
    # The files the Manager's own turn changed are its delivery, whatever it also filed.
    result: dict[str, Any] = {"kind": "chat"}
    delivery = chat_state.pop("_self_delivery", None)
    if isinstance(delivery, dict):
        result.update(
            mission_result=True, success=True, summary=str(delivery.get("summary") or ""),
            delivery_id=str(delivery.get("delivery_id") or ""), delivery=delivery,
        )
    if handoff is None or handoff.to == "operator":
        if handoff is not None:
            reply = ask_text(handoff, uses_cjk(operator_text))
        return emitter.journal_and_respond(reply, result) if handoff is None else emitter.respond(reply, result)
    if handoff.to == "team":
        return None
    if handoff.get("answer"):
        from .manager_pending_question import apply_manager_answer

        # One answer, one acknowledgement: the Manager's reply is it once the
        # answer applies; if it does not, the operator reads that instead of a
        # "resuming" that did not happen (k1-split-cents, 2026-10-24: the
        # Manager's reply and Argus's confirmation both acknowledged it).
        applied = apply_manager_answer(mem, handoff, operator_text, reply=reply)
        return emitter.respond(applied["reply"], {**result, "kind": "pending_question", **applied})
    # Argus applies what the Manager filed and confirms it after its reply.
    emitter.journal(reply, result)
    emitter.turn_id = f"{emitter.turn_id}-applied"
    control = handoff.get("control", {})
    if handoff.get("authorization"):
        return _handle_authorization_control(
            chat_state, life_dir, list(dict.fromkeys(handoff.get("authorization"))),
            source_channel, source_message_id, emitter.turn_id, emitter,
        )
    if control.get("kind") == "steer":
        return _handle_steer_control(
            life_dir, emitter, chat_state=chat_state, body=operator_text,
            directive=control["directive"], lifetime=handoff.get("lifetime", "bounded"),
        )
    if control.get("kind") == "pause":
        from .manager_state import interrupt_manager_turns

        interrupt_manager_turns(str(chat_state.get("session_id") or ""))
        return _handle_pause_control(operator_text, chat_state, life_dir, emitter)
    if control.get("kind") == "abort":
        return _handle_abort_control(operator_text, life_dir, emitter)
    from ..manager.form import config_intents

    intents = config_intents(handoff.get("config", ()))
    applied = intents and _apply_config(mem, intents, chat_state, emitter, operator_text)
    return applied or {"kind": "chat", "reply": reply}


def _dispatch_team_mission(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    root_task_id: str,
    cancelled: Callable[[], bool],
    emitter: _TurnEmitter,
    *,
    attachment_context_refs: list[dict[str, str]] | None = None,
    reference_deps: list[str] | None = None,
    public_objective: str | None = None,
) -> tuple[Any, bool, int | None]:
    """Resume a done lifecycle and run the operator's TEAM mission in a
    Planner-run campaign. Raises on failure — the caller catches it and turns
    it into a structured ``{"kind": "error"}`` reply."""
    from ..manager.dispatch import (
        enqueue_mission,
        resume_done_lifecycle_for_team_dispatch,
    )
    from ..manager.front_door import prepare_manager_execution_task

    chinese = uses_cjk(body)
    emitter.phase(
        "正在选择合适的工作流程…"
        if chinese
        else "Choosing the right workflow…"
    )
    approved_domain = chat_state.get("_approved_domain_decision")
    prepared = prepare_manager_execution_task(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
        public_objective=public_objective,
    )
    if (chat_state.get("_domain_setup_enabled")
            and getattr(prepared.decision, "choice", "") == "new"
            and prepared.decision is not approved_domain):
        from ..manager.domain_intake import DomainOfferRequired, handle_intake, read_intake

        root = prepared.manager.project_root
        request = public_objective or body
        current = read_intake(root)
        if current.get("request") != request:
            handle_intake(root, request, {"action": "cancel"}, route="complex", self_mode="inspect", known_verticals=())
        offer = handle_intake(root, request, {"action": "offer"}, route="complex", self_mode="inspect", known_verticals=())
        raise DomainOfferRequired(offer["reply"] if offer else current["last_question"])
    prepared.cancelled = cancelled
    emitter.phase(
        "正在确认这个项目能否恢复…"
        if chinese
        else "Checking whether this project can resume…"
    )
    resume_done_lifecycle_for_team_dispatch(mem)
    emitter.phase(
        "正在将任务写入待办…"
        if chinese
        else "Adding the task to the backlog…"
    )
    return enqueue_mission(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
        cancelled=cancelled,
        prepared_handoff=prepared,
        context_refs=attachment_context_refs,
        reference_deps=reference_deps,
    )
