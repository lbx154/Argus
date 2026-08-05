"""Manager turn classification, control handling, and mission dispatch.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Owns the classify/control/config/SELF/TEAM phase helpers used
by ``manager_message`` (handoff building, authorization/steer/abort control,
config-intent application, triage fallbacks, and mission dispatch), plus the
execution/continuous/bounded handoff entry points. Public names are
re-exported from ``manager_bridge`` unchanged so existing imports/monkeypatches
keep working.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manager_pending_question import (
    _emit_ui_turn,
    _resolve_pending_question_with_manager,
)
from .manager_state import _STATES, _chat_state_for, _rotate_after

_NO_DISPATCH_FALLBACK = (
    "[not dispatched] The Manager kept this request inline as instructed, but "
    "could not complete the read-only reply. No task was queued and no daemon "
    "was started."
)


def _bridge():
    """Lazily resolve ``manager_bridge`` so tests that monkeypatch
    ``manager_bridge._lock_for`` / ``manager_bridge._authorization_workdir``
    still take effect for calls made from this module."""
    from . import manager_bridge

    return manager_bridge


def _authorization_workdir(
    chat_state: dict[str, Any],
    life_dir: Path,
) -> Path:
    from ..manager.front_door import _operator_workspace

    return _operator_workspace(chat_state, life_dir)


def _project_paths_overlap(left: object, right: object) -> bool:
    left_path = Path(str(left or "").strip().replace("\\", "/"))
    right_path = Path(str(right or "").strip().replace("\\", "/"))
    return bool(
        left_path == right_path
        or left_path in right_path.parents
        or right_path in left_path.parents
    )


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

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _bridge()._lock_for(sid):
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
) -> str:
    """Atomically enable a Manager-authored continuous handoff."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_continuous_handoff as commit_handoff

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _bridge()._lock_for(sid):
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        if name_session:
            from ..manager.config_intent import _front_door_classify

            _front_door_classify(mem, requested_objective, chat_state)
        execution_objective = commit_handoff(mem, requested_objective, chat_state)
        chat_state.setdefault("config", {})["continuous"] = True
        chat_state["continuous_objective"] = execution_objective
        return execution_objective


def disable_manager_continuous(
    sid: str,
    *,
    life_dir: Path,
) -> None:
    """Persist Web stop and synchronize Manager state under one session lock."""
    from ..daemon.state import disable_continuous_config
    from ..manager.front_door import ManagerHandoffError

    with _bridge()._lock_for(sid):
        persisted = disable_continuous_config(life_dir)
        if persisted.enabled:
            raise ManagerHandoffError("continuous stop could not be persisted")
        chat_state = _STATES.get(sid)
        if chat_state is None:
            return
        chat_state.setdefault("config", {})["continuous"] = False
        chat_state["continuous_objective"] = ""
        chat_state.pop("_continuous_pending_manager_handoff", None)


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

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _bridge()._lock_for(sid):
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        if name_session:
            from ..core.session import read_session_meta

            meta = read_session_meta(mem.global_root, sid)
            if meta is None or not meta.display_name.strip():
                from ..manager.config_intent import _front_door_classify

                _front_door_classify(
                    mem,
                    text,
                    chat_state,
                    root_task_id=root_task_id,
                )
        return commit_handoff(
            mem,
            text,
            chat_state,
            persist,
            root_task_id=root_task_id,
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
        }


def _build_handoff(life_dir: Any) -> str:
    """A STRUCTURED handoff seeded as the first message of a fresh Manager
    session when the old one's context fills. Minimal by design (the operator's
    rule: don't pre-chew — give the identity + where the logs live, and let the
    Manager read them itself): who it is, the project path, and the last few
    turns for continuity. Everything else it self-serves.
    """
    lines = [
        "[SESSION HANDOFF — the previous Manager session filled its context and was rotated.",
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
        "reply": "Manager request cancelled; no task was dispatched.",
    }


def _journal_argus_reply(life_dir: Path, turn_id: str, reply: str) -> None:
    """Persist ``reply`` to transcript.jsonl and stream it to the live UI.

    This exact journal-then-emit pair follows every terminal Manager reply in
    ``manager_message``; hoisted verbatim (including the best-effort
    ``except Exception`` swallow) so every phase helper below shares
    identical journaling behavior.
    """
    from ..core.transcript import append_turn

    try:
        append_turn(life_dir, "argus", reply)
    except Exception:  # noqa: BLE001
        pass
    _emit_ui_turn(life_dir, "argus", reply, message_id=f"{turn_id}-argus")


@dataclass
class _TurnEmitter:
    """Bundles the per-turn streaming/journaling callbacks shared by the
    ``manager_message`` phase helpers below, so a phase that needs one more of
    them doesn't have to grow its own parameter list.
    """

    life_dir: Path
    turn_id: str
    fragment: Callable[[str, dict[str, Any]], None]

    def phase(self, label: str) -> None:
        self.fragment("phase", {"role": "manager", "label": label})

    def reply_fragment(self, text: str, *, message_id: str | None = None) -> None:
        payload: dict[str, Any] = {"text": text, "fragment_mode": "snapshot"}
        if message_id is not None:
            payload["message_id"] = message_id
        self.fragment("delta", payload)

    def journal(self, text: str) -> None:
        _journal_argus_reply(self.life_dir, self.turn_id, text)

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
        self.journal(text)
        return {"reply": text, **result}

    def journal_and_respond(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        """Like :meth:`respond` but for replies already streamed by the
        callee (e.g. ``manager_triage`` drives ``on_fragment`` itself), so no
        extra delta fragment is emitted here.
        """
        self.journal(text)
        return {"reply": text, **result}


def _handle_pending_question_turn(
    mem: Any,
    pending_questions: list[Any],
    body: str,
    chat_state: dict[str, Any],
    emitter: _TurnEmitter,
) -> dict[str, Any] | None:
    """Resolve the operator turn against outstanding pending questions.

    Returns a terminal result dict when the turn was consumed by a pending
    question (exactly one open question, or more than one), or ``None`` when
    there is no pending question and ``manager_message`` should continue its
    normal classify/triage/dispatch flow.
    """
    from ..life.memory import BacklogItem

    if len(pending_questions) == 1:
        emitter.phase("Manager · interpreting your answer to the blocked mission")
        result = _resolve_pending_question_with_manager(
            mem,
            pending_questions[0],
            body,
            chat_state,
            root_task_id=BacklogItem.new_id(),
        )
        if result.get("answer_intent") is not False:
            reply = str(
                result.get("reply")
                or result.get("error")
                or "Manager could not resolve the pending question."
            )
            return emitter.respond(reply, {"kind": "pending_question", **result})
        return None
    if len(pending_questions) > 1:
        reply = (
            "More than one task needs your input. Open the Needs you prompt "
            "for the specific task you want to answer."
        )
        return emitter.respond(reply, {"kind": "pending_question_choice"})
    return None


@dataclass
class _ClassifyResult:
    """Outcome of the merged front-door classify call, plus the values later
    manager_message phases need (the rotated ``send_body``/root task id, and
    any inline greeting/failure the classifier already resolved)."""

    intent: Any
    control: Any
    route: Any
    send_body: str
    root_task_id: str
    self_mode: str
    fast_reply: str
    greeting_reply: str
    frontdoor_failure: str


def _classify_operator_turn(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    active_mission: bool,
    life_dir: Path,
    startup_handoff: str,
    emitter: _TurnEmitter,
    cancelled: Callable[[], bool],
) -> "_ClassifyResult | dict[str, Any]":
    """Run rotation bookkeeping + the merged front-door classify call.

    Returns a ``_ClassifyResult`` normally, or a terminal ``{"kind":
    "cancelled", ...}`` dict if the request was cancelled mid-classify — the
    caller must check ``isinstance(result, dict)`` before reading fields.
    """
    from ..life.memory import BacklogItem
    from ..manager.config_intent import _front_door_classify
    from ..manager.front_door import _accepts_keyword

    # Emit the stage BEFORE the classifier call. Copilot ACP may produce no
    # protocol events while the model is reasoning, so without this real
    # transition the TUI can only show its generic rotating slogan.
    emitter.phase("Manager · classifying this message")

    # Persistent Manager session with context-rotation: it stays alive (the
    # codex/copilot thread is resumed via last_thread_id each turn) and is
    # only ROTATED when its context fills — a fresh thread seeded with a
    # STRUCTURED handoff (identity + project path + recent turns), so the
    # operator never notices the seam. Turn count is a cheap proxy for "full".
    chat_state["turns"] = int(chat_state.get("turns", 0)) + 1
    send_body = f"{startup_handoff}\n\n{body}" if startup_handoff else body
    root_task_id = BacklogItem.new_id()
    if chat_state["turns"] > _rotate_after():
        send_body = f"{_build_handoff(life_dir)}\n\n{body}"
        chat_state["last_thread_id"] = None  # start a fresh session thread
        # The cached runner keeps its OWN copy of the session id
        # (``_next_seed_thread_id``); ``_simple_quick_reply`` falls back to it
        # when ``seed_thread_id`` is None, so clearing only ``last_thread_id``
        # here let the runner RESURRECT the just-rotated thread — rotation never
        # took and the codex/copilot session grew unbounded (its resume cost
        # climbing every turn). Reset the runner's memory too so the fresh
        # thread is genuinely fresh.
        _runner = chat_state.get("manager_runner")
        if _runner is not None and hasattr(_runner, "reset_chat_session"):
            try:
                _runner.reset_chat_session()
            except Exception:  # noqa: BLE001 — rotation must never break the turn
                pass
        chat_state["turns"] = 1
        chat_state["rotations"] = int(chat_state.get("rotations", 0)) + 1

    # ONE merged front-door call decides config, control, route, TEAM
    # lifetime, title, and a strict pure-greeting token. A natural-language
    # config change ("set the engineer to xhigh", "use copilot for reviewer",
    # "cap the budget at $10") is applied + confirmed inline and NEVER
    # enqueued; otherwise the reusable decisions avoid a second route/lifetime
    # call. Classifier output is never an operator-facing reply; every SELF
    # message reaches the actual Manager model.
    # Classification is stateless and must see ONLY the current operator
    # message. Feeding it the startup/context-rotation handoff can make a
    # greeting look like a complex systems task; the enriched body belongs
    # only in the conversational reply session below.
    classify_kwargs = (
        {"root_task_id": root_task_id}
        if _accepts_keyword(_front_door_classify, "root_task_id")
        else {}
    )
    if _accepts_keyword(_front_door_classify, "active_mission"):
        classify_kwargs["active_mission"] = active_mission
    decision = _front_door_classify(
        mem,
        body,
        chat_state,
        **classify_kwargs,
    )
    if cancelled():
        return _cancelled_result()
    if isinstance(decision, tuple) and len(decision) == 3:
        intent, control, route = decision
    else:
        intent, route = decision
        control = None

    self_mode = str(
        chat_state.get("_frontdoor_self_mode", "inspect") or "inspect"
    ).strip().lower()
    fast_reply = str(
        chat_state.pop("_frontdoor_fast_reply", "") or ""
    ).strip()
    greeting_reply = str(
        chat_state.pop("_frontdoor_greeting_reply", "") or ""
    ).strip()
    frontdoor_failure = str(
        chat_state.pop("_frontdoor_failure", "") or ""
    ).strip()
    return _ClassifyResult(
        intent=intent,
        control=control,
        route=route,
        send_body=send_body,
        root_task_id=root_task_id,
        self_mode=self_mode,
        fast_reply=fast_reply,
        greeting_reply=greeting_reply,
        frontdoor_failure=frontdoor_failure,
    )


def _maybe_greeting_reply(
    classify: _ClassifyResult,
    body: str,
    emitter: _TurnEmitter,
) -> dict[str, Any] | None:
    """Short-circuit a safe message-only reply from the merged classifier.

    Only fires when no stateful action was decided and the classifier did not
    need the startup/rotation handoff to
    answer it (``send_body == body``) — otherwise the greeting reply could be
    stale relative to the actual enriched turn sent to the Manager.
    """
    if (
        classify.greeting_reply
        and classify.intent is None
        and classify.control is None
        and classify.route == "simple"
        and classify.send_body == body
    ):
        return emitter.respond(classify.greeting_reply, {"kind": "chat"})
    if (
        classify.fast_reply
        and classify.intent is None
        and classify.control in {None, "no_dispatch"}
        and classify.route == "simple"
        and classify.self_mode == "reply"
        and classify.send_body == body
    ):
        return emitter.respond(classify.fast_reply, {"kind": "chat"})
    return None


def _handle_authorization_control(
    chat_state: dict[str, Any],
    life_dir: Path,
    authorization_actions: list[Any],
    source_channel: str,
    source_message_id: str,
    turn_id: str,
    emitter: _TurnEmitter,
) -> dict[str, Any]:
    """Record (or reject) an operator authorization against the current
    campaign blocker. Always terminal — the merged classify call already
    decided this turn is an authorization, so there is no fall-through to
    triage/dispatch after this.
    """
    from ..manager.control_state import CampaignControlStore

    try:
        control_store = CampaignControlStore(
            life_dir,
            project_root=_bridge()._authorization_workdir(chat_state, life_dir),
        )
        head = control_store.read_head()
        snapshot = control_store.read_snapshot(head)
        active_wait = snapshot.get("active_wait") if snapshot else None
        if head is None or not isinstance(active_wait, dict):
            raise ValueError(
                "no current Manager-bound blocker is awaiting authorization"
            )
        terminal_evidence = list(
            snapshot.get("terminal_evidence") or []
        ) if snapshot else []
        diagnosis = (
            terminal_evidence[-1]
            if terminal_evidence
            and isinstance(terminal_evidence[-1], dict)
            else {}
        )
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
        watched_paths = [
            str(value)
            for value in (active_wait.get("watched_paths") or [])
            if not any(
                _project_paths_overlap(value, repair_path)
                for repair_path in repair_paths
            )
        ]
        identity = control_store.campaign_identity(
            campaign_epoch=head.campaign_epoch,
        )
        if (
            identity.campaign_id != head.campaign_id
            or identity.objective_sha256 != head.objective_sha256
        ):
            raise ValueError("active campaign identity changed")
        authorization = control_store.issue_authorization(
            identity=identity,
            blocker_fingerprint=str(
                active_wait.get("blocker_fingerprint") or ""
            ),
            allowed_actions=authorization_actions,
            scope="active_blocker",
            allowed_write_paths=repair_paths,
            evidence_paths=watched_paths,
            forbidden_mutations=watched_paths,
            source_channel=source_channel,
            source_message_id=source_message_id or turn_id,
            validator_id=validator_id,
            acceptance_retries=(
                1 if validator_repair else 0
            ),
            expected_state_revision=head.state_revision,
            expected_wait_id=str(active_wait.get("wait_id") or ""),
        )
        reply = (
            "Authorization recorded for the current campaign blocker "
            f"as {authorization.authorization_id}. No task was dispatched."
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
    chat_state: dict[str, Any],
    life_dir: Path,
    emitter: _TurnEmitter,
) -> dict[str, Any]:
    """Persist steering and also wake the currently running mission."""
    from ..apps._inbox import queue_inbox_message
    from ..manager.directive import (
        active_manager_directive_message,
        set_active_manager_directive,
    )

    manager_directive = str(
        chat_state.pop("_frontdoor_steering_directive", "") or ""
    ).strip()
    if not manager_directive:
        reply = (
            "我判断这属于当前任务的方向调整，但没有形成足够明确的团队指令；"
            "本次未修改任务，请重试或补充目标。"
        )
        return emitter.respond(
            reply,
            {"kind": "chat", "control": "steer_unresolved"},
            message_id="steer",
        )
    set_active_manager_directive(
        life_dir,
        manager_directive,
        source="manager.steer",
    )
    directive = active_manager_directive_message(life_dir)
    queue_inbox_message(
        life_dir,
        directive,
        source="manager.steer",
    )
    reply = f"我已调整团队方向：{manager_directive}"
    return emitter.respond(
        reply,
        {"kind": "control", "control": "steer"},
        message_id="steer",
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
    if requested:
        reply = f"Stop requested for running task {item_id}."
    elif item_id is not None:
        reply = f"Stop request failed for running task {item_id}."
    else:
        reply = (
            "No running task to abort. Pending tasks were left unchanged."
        )
    return emitter.respond(
        reply,
        {"kind": "control", "control": "abort", "requested": requested, "item_id": item_id},
    )


def _maybe_apply_config_intent(
    mem: Any,
    intent: Any,
    chat_state: dict[str, Any],
    life_dir: Path,
    turn_id: str,
    on_fragment: Any,
    fragment: Callable[[str, dict[str, Any]], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any] | None:
    """Apply a natural-language config-change intent inline, confirming it in
    the same turn instead of enqueuing a mission. Returns ``None`` when
    ``intent`` is falsy or the apply failed, so manager_message keeps going
    (triage/dispatch)."""
    from ..manager.config_intent import _apply_config_intent

    if intent is None:
        return None
    if cancelled():
        return _cancelled_result()
    cfg_lines: list[str] = []
    try:
        applied = _apply_config_intent(mem, intent, chat_state, on_confirm=cfg_lines.append)
    except Exception:  # noqa: BLE001 — a config-apply hiccup must never block the message
        applied = False
    if not applied:
        return None
    if on_fragment is not None:
        for _ln in cfg_lines:
            fragment("delta", {
                "text": _ln,
                "message_id": "config",
                "fragment_mode": "append",
            })
    reply = "\n".join(cfg_lines).strip() or "Done — setting applied."
    _journal_argus_reply(life_dir, turn_id, reply)
    return {"kind": "chat", "reply": reply}


def _run_triage_and_fallbacks(
    mem: Any,
    send_body: str,
    chat_state: dict[str, Any],
    route: Any,
    control: Any,
    root_task_id: str,
    frontdoor_failure: str,
    on_fragment: Any,
    emitter: _TurnEmitter,
) -> dict[str, Any] | None:
    """Run Manager triage (chat/SELF path) and its three fail-closed
    fallbacks. Returns a terminal chat reply dict, or ``None`` when none of
    the chat/fallback conditions apply and the turn should proceed to TEAM
    dispatch below.
    """
    from ..manager.front_door import manager_triage

    self_mode = str(chat_state.pop("_frontdoor_self_mode", "inspect") or "inspect")
    # 1) Manager triage — chat/SELF returns a reply; TEAM returns None. The
    # route was already decided in the merged call above, so triage skips its
    # own route classify (``route=route``).
    try:
        reply = manager_triage(
            mem,
            send_body,
            chat_state,
            on_fragment=emitter.fragment if callable(on_fragment) else None,
            route=route,
            self_mode=self_mode,
            root_task_id=root_task_id,
        )
    except Exception:  # noqa: BLE001 — triage failure biases to task
        reply = None

    if reply is not None:
        return emitter.journal_and_respond(reply, {"kind": "chat"})
    if route == "simple" and control != "no_dispatch":
        # The classifier already said SELF/chat. A failed inline Manager turn
        # must never fall through into TEAM dispatch — that queues greetings,
        # status questions, or capability chat as real missions precisely when
        # the Manager backend is unhealthy.
        reply = (
            "[not dispatched] Manager could not complete this inline reply. "
            "No task was queued; please retry the message."
        )
        return emitter.respond(reply, {"kind": "chat"})
    if control == "no_dispatch":
        reply = _NO_DISPATCH_FALLBACK
        return emitter.respond(reply, {"kind": "chat"})
    if frontdoor_failure:
        reply = (
            "[not dispatched] Manager could not classify this message. "
            "No task was queued; please retry."
        )
        return emitter.respond(reply, {"kind": "chat"})
    return None


def _dispatch_team_mission(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    root_task_id: str,
    cancelled: Callable[[], bool],
    emitter: _TurnEmitter,
) -> tuple[Any, bool, int | None]:
    """Apply the Manager's lifetime decision, resume a done lifecycle, and
    enqueue the operator's TEAM mission. Raises on failure — the caller
    catches it and turns it into a structured ``{"kind": "error"}`` reply."""
    from ..manager.dispatch import (
        enqueue_mission,
        maybe_promote_to_continuous,
        resume_done_lifecycle_for_team_dispatch,
    )

    emitter.phase("Manager · validating task lifetime")
    maybe_promote_to_continuous(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
    )
    resume_done_lifecycle_for_team_dispatch(mem)
    return enqueue_mission(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
        cancelled=cancelled,
    )
