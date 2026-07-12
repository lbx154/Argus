"""Manager front-door routing shared by line REPL, TUI and Web."""

from __future__ import annotations

import argparse
import json
import os
import time
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable

from ..core.knobs import resolve_role_model


def _life_dir_for(mem: Any) -> Path:
    """Resolve the per-project life-dir that holds ``events.jsonl``.

    Works for both ``MemoryBundle`` (``.project.root`` / ``.project_root``)
    and the bare ``LifeMemory`` facade (``.root``) used in tests.
    """
    project_root = getattr(mem, "project_root", None)
    if project_root is None:
        project = getattr(mem, "project", None)
        project_root = getattr(project, "root", None)
    if project_root is None:
        project_root = getattr(mem, "root", None)
    if project_root is None:
        raise AttributeError(
            "cannot resolve life-dir: memory has no project_root / project.root / root"
        )
    return Path(project_root)


# ---------------------------------------------------------------------------
# Slash-command helpers (in-process; mirror the public CLI subcommands)
# ---------------------------------------------------------------------------

# Sentinel stored in chat_state when a Manager runner cannot be built (or is
# not applicable, e.g. the memory backend). Lets us cache the "no front-end
# triage" decision so we don't retry the build on every line typed.
_MANAGER_RUNNER_UNAVAILABLE = object()


def _ensure_manager_runner(chat_state: dict[str, Any], mem: Any) -> Any:
    """Lazily build (and cache) a Manager-front-end runner for chat triage.

    The runner is used ONLY to classify free text as chat-vs-task and, when
    chat, to reply in-band BEFORE anything reaches the backlog. It is built
    once per REPL session and cached on ``chat_state["manager_runner"]``.

    Returns the runner, or ``None`` when front-end triage is not available
    (memory backend, or a build failure — in which case all free text falls
    through to the task path unchanged).
    """
    cached = chat_state.get("manager_runner")
    if cached is not None:
        return None if cached is _MANAGER_RUNNER_UNAVAILABLE else cached

    backend = chat_state.get("backend")
    # The memory backend has no real LLM runner; never triage — every line is
    # a task (preserves existing memory-backend behaviour and its tests).
    if backend == "memory":
        chat_state["manager_runner"] = _MANAGER_RUNNER_UNAVAILABLE
        return None

    try:
        # ``manager_session_root`` MUST match the daemon's own
        # ``ns.manager_session_root = str(cfg.life_dir)`` (see
        # ``daemon/life_worker.py:_runner_namespace``) — otherwise this
        # front-door Manager (built once per REPL session, used for
        # SELF/TEAM routing + ``divide()``) reads/writes
        # ``research/PIPELINE_STATE.json`` and ``research/DOMAINS/*.json``
        # against a DIFFERENT root than the daemon that actually executes
        # the mission. That mismatch silently drops a Manager-authored
        # custom domain (e.g. an operator task that doesn't match any
        # built-in vertical) and logs a spurious
        # ``load_vertical(...): unknown/half-built vertical`` warning the
        # next time the daemon resolves the vertical from ITS (correct,
        # session-scoped) root. ``mem.project_root`` is the per-project
        # session dir; ``mem.root`` (used below for ``life_dir``, a
        # differently-scoped, currently-unread-by-this-path field) is the
        # GLOBAL ``~/.argus-skill`` root — do not conflate the two.
        session_root = getattr(mem, "project_root", None)
        ns = argparse.Namespace(
            backend=backend or "codex",
            engineer_model=resolve_role_model(
                "engineer",
                role_env="ARGUS_SKILL_ENGINEER_MODEL",
            ),
            reviewer_model=resolve_role_model(
                "reviewer",
                role_env="ARGUS_SKILL_REVIEWER_MODEL",
            ),
            engineer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh"
            ),
            reviewer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "xhigh"
            ),
            plan_mode="auto",
            plan_model=None,
            max_rounds=500,
            # A web-created session has no operator-selected repository.  Keep
            # every Manager artifact (vertical, pipeline state, authored domain)
            # in the same isolated project root the daemon will execute in.
            # Leaving this as None made the web process use its launch cwd while
            # the detached daemon used cwd=/, splitting one mission across two
            # unrelated trees.
            workdir=str(session_root) if session_root else None,
            manager_session_root=str(session_root) if session_root else None,
            project_state_dir=str(session_root) if session_root else None,
            life_dir=getattr(mem, "root", None),
            stop_event=None,
        )
        from ..apps._runtime import build_life_runner

        runner = build_life_runner(ns)
    except Exception:  # noqa: BLE001 — triage is best-effort; fall back to task path
        chat_state["manager_runner"] = _MANAGER_RUNNER_UNAVAILABLE
        return None

    chat_state["manager_runner"] = runner
    return runner


def _derive_session_name(text: str, *, limit: int = 48) -> str:
    """Derive a short, human-readable session label from the first real task.

    Codex / Claude-Code name a session after its opening message. We mirror
    that: take the first non-empty line, collapse whitespace, and truncate.
    Naming is domain-agnostic plumbing (a picker label), so the harness may do
    it deterministically — no agent judgment required.
    """
    for raw in (text or "").splitlines():
        line = " ".join(raw.split()).strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _maybe_name_session(chat_state: dict[str, Any], task_text: str) -> None:
    """Name the current session after its first real task (once, fail-soft).

    A resumed session keeps its original name (``session_named`` is already
    True). Only the first task in a freshly-minted, still-unnamed session sets
    the display_name shown in the resume picker.
    """
    if chat_state.get("session_named"):
        return
    sid = chat_state.get("session_id")
    gr = chat_state.get("global_root")
    if not sid or gr is None:
        return
    name = _derive_session_name(task_text)
    if not name:
        return
    try:
        from ..core.session import touch_session

        touch_session(gr, sid, display_name=name)
        chat_state["session_named"] = True
    except Exception:  # noqa: BLE001 — naming is cosmetic, never block the task
        pass


def _emit_manager_event(mem: Any, event: dict[str, Any]) -> None:
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=_life_dir_for(mem)).append(event)
    except Exception:  # noqa: BLE001
        pass


def _with_manager_spinner(theme: object | None, label: str, fn: Callable[[], Any]) -> Any:
    """Run blocking ``fn`` while showing the cockpit's manager-tinted braille
    spinner, so a model round-trip on the TEAM-handoff path never looks frozen.
    No-op animation on non-TTY / piped / NO_COLOR (LiveStatus gates itself).

    ``fn`` runs EXACTLY once: if the spinner cannot be built we fall back to a
    bare call, but an exception from ``fn`` itself propagates unchanged."""
    try:
        from ..cli.live_status import LiveStatus
        from ..cli.roles_status import ROLE_COLOR_BOLD

        cm = LiveStatus(
            label, theme=theme, accent=ROLE_COLOR_BOLD.get("manager", "magenta")
        )
    except Exception:  # noqa: BLE001 — spinner setup only; never mask fn
        return fn()
    with cm:
        return fn()


def _accepts_keyword(fn: Any, name: str) -> bool:
    try:
        parameters = signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == name or parameter.kind == Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _manager_divide_user_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    theme: object | None = None,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
) -> None:
    """Run Manager division for an operator-submitted task before enqueue.

    This is intentionally a USER-ENTRY gate. Planner-generated backlog items are
    already the Planner's decomposition and must not be routed back through
    Manager again.

    ``Manager.divide`` makes a blocking model round-trip (``decide_vertical``), so
    the caller passes ``theme`` to keep the cockpit's spinner animating during it
    — otherwise the TEAM-handoff window looks frozen.
    """
    intent_id = f"intent-{int(time.time() * 1000)}"
    _emit_manager_event(mem, {
        "type": "life.manager.intent.started",
        "agent_layer": "manager",
        "intent_id": intent_id,
        "item_id": root_task_id,
        "source": "user",
        "objective": body,
        "text": "manager interpreting user task",
    })
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None:
            from ..manager import Manager

            # Match the primary path's root (see ``_ensure_manager_runner``):
            # the session-scoped project dir, NOT the git worktree — so a
            # degraded (no-runner) divide still persists vertical/domain state
            # where the daemon's mission execution will actually look for it.
            mgr = Manager(
                project_root=getattr(mem, "project_root", None) or Path.cwd(),
                runner=None,
            )
        def _divide() -> Any:
            if root_task_id is None or not _accepts_keyword(
                mgr.divide,
                "root_task_id",
            ):
                return mgr.divide(body, ask_on_new_domain=False)
            return mgr.divide(
                body,
                ask_on_new_domain=False,
                root_task_id=root_task_id,
            )

        division = _with_manager_spinner(
            theme,
            "Manager choosing the vertical…",
            _divide,
        )
        payload = {
            "type": "life.manager.intent.completed",
            "agent_layer": "manager",
            "intent_id": intent_id,
            "item_id": root_task_id,
            "source": "user",
            "objective": body,
            "vertical": getattr(division, "vertical", ""),
            "kind": getattr(division, "kind", ""),
            "regular": bool(getattr(division, "regular", False)),
            "stages": list(getattr(division, "stages", []) or []),
            "reason": getattr(division, "headline", lambda: "")(),
            "text": (
                f"manager interpreted user task as "
                f"{getattr(division, 'vertical', '')}"
            ),
        }
        _emit_manager_event(mem, payload)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "type": "life.manager.intent.failed",
            "agent_layer": "manager",
            "intent_id": intent_id,
            "item_id": root_task_id,
            "source": "user",
            "objective": body,
            "error": f"{type(exc).__name__}: {exc}",
            "text": "manager intent interpretation failed",
        }
        _emit_manager_event(mem, payload)


_DO_NOT_RUN_MARKERS: tuple[str, ...] = (
    # Chinese (simplified + a few traditional variants)
    "不要运行", "不要執行", "不要执行", "不要启动", "不要啟動",
    "别运行", "別運行", "别启动", "別啟動", "不要跑", "不要派发", "不要分派",
    "不要运行任务", "只做状态检查", "只检查状态", "只看状态", "只查状态",
    "状态检查", "狀態檢查", "请回复状态正常", "請回復狀態正常",
    # English
    "do not run", "don't run", "dont run",
    "do not execute", "don't execute", "dont execute",
    "do not start", "don't start", "dont start",
    "do not launch", "don't launch", "do not dispatch", "do not spawn",
    "status check only", "status-only", "status only",
    "just check status", "only check status",
)


def looks_like_do_not_run_request(text: str) -> bool:
    """True iff ``text`` explicitly forbids running / asks for status only.

    Used ONLY to make the triage-failure fallback safe (see
    :func:`manager_triage`): when the Manager's classify call ERRORS, the front
    door normally biases to "task" ("never drop work to a bad classify"), but if
    the operator explicitly said "do not run / status only" then creating a real
    mission on a *failed* classify is the wrong default — that is exactly how a
    Chinese "请只做状态检查，不要运行任务" message got dispatched to the team on
    2026-07-11 (the Manager's classify call had been blocked by the cost gate, so
    triage raised and the message was treated as work). This never overrides a
    SUCCESSFUL classify decision, so it cannot silently drop genuine work.
    """
    if not text:
        return False
    raw = str(text)
    low = raw.lower()
    for marker in _DO_NOT_RUN_MARKERS:
        if marker in raw or marker.lower() in low:
            return True
    return False


_DO_NOT_RUN_SAFE_REPLY = (
    "[not dispatched] The Manager could not classify this request and your "
    "message asks not to run anything (status-only / do-not-run), so no task was "
    "queued. Use /status for pipeline state or /doctor to diagnose; rephrase "
    "without the do-not-run constraint if you actually want to queue work."
)


def manager_triage(mem: Any, body: str, chat_state: dict[str, Any],
                   *, on_phase: Any = None, on_fragment: Any = None,
                   route: str | None = None,
                   root_task_id: str | None = None,
                   ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
                   ) -> str | None:
    """Front-door route: one-Codex SELF work returns a reply; TEAM work returns
    ``None`` so the caller queues the Argus Planner/Engineer/Reviewer pipeline.

    ``on_phase(label, *, role=...)`` — optional callback invoked at the REAL
    phase transitions (classify → reply), so a live status line reflects what
    the Manager is actually doing rather than a timed cosmetic rotation.
    ``role`` is a best-effort extra (falls back to the plain one-arg call for
    any callback that does not accept it) naming which of the four roles
    drove this update, so the caller can retint a live spinner to match.

    ``on_fragment(kind, payload)`` — optional streaming callback for a live
    front-end (the web/TUI SSE bridge). Fires ``("delta", {"text", "message_id"})``
    for each assistant reply block the instant it arrives, and ``("phase",
    {"role", "label"})`` at each phase transition. Opt-in: default ``None``
    leaves triage behaving exactly as the line REPL.
    """
    runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
    if runner is None or not hasattr(runner, "chat_reply_if_conversational"):
        return None
    captured: list[str] = []

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        if not callable(on_fragment):
            return
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — a UI callback must never break triage
            pass

    def _progress_label(event: dict[str, Any]) -> tuple[str, str] | None:
        try:
            from ..apps.cli._follow import _clean_follow_text
            txt = str(
                event.get("text")
                or event.get("title")
                or event.get("reason")
                or event.get("kind")
                or ""
            ).strip()
            if not txt:
                return None
            role = str(event.get("agent_layer") or "manager").strip() or "manager"
            title = {
                "manager": "Manager",
                "planner": "Planner",
                "engineer": "Engineer",
                "reviewer": "Reviewer",
            }.get(role, role.title())
            return role, title + " · " + _clean_follow_text(txt, limit=64)
        except Exception:  # noqa: BLE001
            return None

    def _emit_phase(role: str, label: str) -> None:
        # The terminal REPL consumes ``on_phase`` directly; the web/TUI bridge
        # consumes ``on_fragment("phase", ...)``. Relay every real runner phase
        # to both surfaces. Previously the runner received the raw ``on_phase``
        # argument (which is None on the web path), so SSE never saw classify /
        # direct-reply transitions and could only display a generic spinner.
        if callable(on_phase):
            try:
                on_phase(label, role=role)
            except TypeError:
                try:
                    on_phase(label)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 — a UI callback must never break triage
                pass
        _fragment("phase", {"role": role, "label": label})

    def _runner_phase(label: str, *, role: str = "manager") -> None:
        _emit_phase(str(role or "manager"), str(label or ""))

    class _Capture:
        def __init__(self, *, progress_phases: bool) -> None:
            self._progress_phases = progress_phases

        def handle_event(self, event: dict[str, Any]) -> None:
            try:
                etype = str(event.get("type") or "")
                # A live assistant reply block → stream it as a delta fragment
                # (grows the reply in the front-end) rather than treating it as a
                # phase label. Keep capturing the authoritative reply below.
                if etype == "engineer.progress" and str(event.get("kind") or "") == "assistant_message":
                    blk = str(event.get("text") or "").strip()
                    if blk:
                        _fragment("delta", {
                            "text": blk,
                            "message_id": str(event.get("message_id") or ""),
                        })
                    return
                if etype in {"loop.start", "engineer.progress"}:
                    # The current runner reports these same events through its
                    # phase_cb wrapper, already normalized as Manager activity.
                    # Only legacy runners (which reject phase_cb and hit the
                    # fallback below) need the capture sink to synthesize them.
                    if self._progress_phases:
                        parsed = _progress_label(event)
                        if parsed:
                            _emit_phase(*parsed)
                    return
                if etype != "round.main.completed":
                    return
                text = _extract_chat_reply_text(str(event.get("last_message") or ""))
                if text:
                    captured.append(text)
            except Exception:  # noqa: BLE001
                pass

    try:
        triage_kwargs: dict[str, Any] = {
            "objective": body,
            "sink": _Capture(progress_phases=False),
            "seed_thread_id": chat_state.get("last_thread_id"),
            "phase_cb": _runner_phase,
            "route": route,
        }
        if root_task_id is not None and _accepts_keyword(
            runner.chat_reply_if_conversational,
            "root_task_id",
        ):
            triage_kwargs["root_task_id"] = root_task_id
        if runner.chat_reply_if_conversational(**triage_kwargs):
            chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
            return captured[0] if captured else "(no reply)"
    except TypeError:
        # Older runner without phase_cb / route support — retry without them
        # (fail-soft; the older runner will classify route internally).
        try:
            if runner.chat_reply_if_conversational(
                objective=body, sink=_Capture(progress_phases=True),
                seed_thread_id=chat_state.get("last_thread_id"),
            ):
                chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
                return captured[0] if captured else "(no reply)"
        except Exception:  # noqa: BLE001 — triage failure
            if looks_like_do_not_run_request(body):
                return _DO_NOT_RUN_SAFE_REPLY
            return None
    except Exception:  # noqa: BLE001 — triage failure: bias to task ("never drop
        # work to a bad classify") UNLESS the operator explicitly forbade running
        # (status-only / do-not-run). Dispatching a real mission on a classify we
        # could not even complete is how a status request reached the Engineer.
        if looks_like_do_not_run_request(body):
            return _DO_NOT_RUN_SAFE_REPLY
        return None
    return None

def _extract_chat_reply_text(msg: str) -> str:
    """Pull the human reply out of a chat result (plain text, or JSON-wrapped)."""
    msg = (msg or "").strip()
    if msg.startswith("{") and msg.endswith("}"):
        try:
            data = json.loads(msg)
            for key in ("reply", "message", "text", "answer", "response"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:  # noqa: BLE001
            pass
    return msg

__all__ = [
    "_DO_NOT_RUN_SAFE_REPLY",
    "_accepts_keyword",
    "_MANAGER_RUNNER_UNAVAILABLE",
    "_derive_session_name",
    "_emit_manager_event",
    "_ensure_manager_runner",
    "_extract_chat_reply_text",
    "_life_dir_for",
    "_manager_divide_user_task",
    "_maybe_name_session",
    "_with_manager_spinner",
    "looks_like_do_not_run_request",
    "manager_triage",
]
