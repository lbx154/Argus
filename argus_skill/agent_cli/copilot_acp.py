"""Persistent (warm) ``copilot --acp`` client — kills per-turn cold starts.

The cockpit Manager front-door makes short, tool-free classify calls (see
``life.router.classify_front_door``). Spawning a fresh ``copilot`` CLI for each
one costs ~5.5s (it reloads MCP servers + skills every time). ``copilot --acp``
speaks the Agent Client Protocol (JSON-RPC 2.0, newline-delimited, over stdio):
one warm process, initialized once, answers a ``session/prompt`` in ~1.6–3s.

This module keeps one process alive and gives it isolated logical sessions for
the cheap front-door classifier and the operator-facing Manager conversation.
The latter can use Copilot's built-in file/shell tools: ACP reports those tools
as ``session/update`` events while the Copilot runtime executes them in-process.
Daemon engineer/reviewer/planner mission turns remain on the CLI ``Popen`` path.

Enabled by default for Copilot-backed Manager labels. Set
``ARGUS_SKILL_COPILOT_ACP=0`` to roll back to the one-shot CLI path.
"""

from __future__ import annotations

import atexit
import itertools
import json
import os
import subprocess
import threading
import time
from typing import Any, Callable

from ._idle_watchdog import (
    STALLED_STAGE,
    TERMINATE_STAGE,
    WARNING_STAGE,
    IdleEscalation,
)
from .models import AgentRunResult, InactivitySnapshot

_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MANAGER_TIMEOUT_S = 300.0
_CANCEL_GRACE_S = 5.0
_DEFAULT_SESSION_RECYCLE = 12
_FRONT_DOOR_LABEL = "manager-frontdoor-classify"
_WINDOWS = os.name == "nt"
_TRANSPORT_CANCEL_NOTICE = "Info: Operation cancelled by user"
_CONTENT_FILTER_NOTICE = (
    "The model returned no content because the response was blocked by content filtering."
)
_TRANSPORT_INFO_PREFIXES = (
    "Info: Disabled tools:",
    "Info: Unknown tool name in the tool allowlist:",
)


def _terminate_windows_tree(proc: subprocess.Popen[str]) -> bool:
    pid = int(getattr(proc, "pid", 0) or 0)
    if pid <= 0:
        return False
    try:
        from ..daemon.state import _terminate_windows_process_tree

        return _terminate_windows_process_tree(
            pid,
            identity_check=lambda: proc.poll() is None,
        )
    except (ImportError, OSError, TypeError, ValueError):
        return False


def _prompt_timeout(run_label: str | None) -> float:
    """Return the maximum ACP inactivity allowed for this Manager role."""
    if run_label == _FRONT_DOOR_LABEL:
        env_name = "ARGUS_SKILL_COPILOT_ACP_TIMEOUT_S"
        default = _DEFAULT_TIMEOUT_S
    else:
        env_name = "ARGUS_SKILL_COPILOT_ACP_MANAGER_TIMEOUT_S"
        default = _DEFAULT_MANAGER_TIMEOUT_S
    try:
        return max(1.0, float(os.environ.get(env_name, "") or default))
    except ValueError:
        return default


def _looks_like_content_filter_notice(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).rstrip(".").casefold()
    expected = " ".join(_CONTENT_FILTER_NOTICE.split()).rstrip(".").casefold()
    return normalized == expected


def _filter_transport_notices(raw_text: str, *, final: bool = False) -> str:
    """Remove ACP transport notices while retaining ordinary assistant prose.

    The notice can arrive over several chunks. Until the final chunk, hold a
    trailing line that is still a possible notice prefix so it is never streamed
    to the operator and then made impossible to retract.
    """
    if not raw_text:
        return ""
    kept: list[str] = []
    lines = raw_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        stripped = body.strip()
        if stripped == _TRANSPORT_CANCEL_NOTICE or _looks_like_content_filter_notice(
            stripped
        ):
            continue
        is_unterminated_last = index == len(lines) - 1 and not line.endswith(("\n", "\r"))
        if (
            not final
            and is_unterminated_last
            and stripped
            and (
                _TRANSPORT_CANCEL_NOTICE.startswith(stripped)
                or _CONTENT_FILTER_NOTICE.startswith(stripped)
            )
        ):
            continue
        kept.append(line)
    return "".join(kept)


class _Turn:
    """State for the single in-flight prompt (serialized by the turn-lock)."""

    __slots__ = (
        "session_id",
        "on_block",
        "emit",
        "raw_text",
        "text",
        "tool_titles",
        "tool_activity_observed",
        "allow_persistent",
        "last_activity_at",
        "last_event",
    )

    def __init__(
        self,
        session_id: str,
        on_block: Any,
        emit: Any,
        *,
        allow_persistent: bool,
    ) -> None:
        self.session_id = session_id
        self.on_block = on_block
        self.emit = emit
        self.raw_text = ""
        self.text = ""
        self.tool_titles: dict[str, str] = {}
        self.tool_activity_observed = False
        self.allow_persistent = allow_persistent
        self.last_activity_at = time.monotonic()
        self.last_event = "prompt_started"


class CopilotAcpClient:
    """One warm ``copilot --acp`` subprocess + a JSON-RPC/stdio client.

    Thread-safety: ``_send_lock`` serializes writes; a daemon reader thread
    dispatches responses (by id) and notifications; ``_turn_lock`` serializes
    prompts so exactly one turn is active at a time (so the reader can route
    ``session/update`` chunks to ``_active_turn`` without ambiguity). Crash / EOF
    marks the process dead, fails all waiters, and clears the session map; the
    next prompt lazily respawns.
    """

    def __init__(
        self,
        agent_bin: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
        *,
        lean: bool = False,
        read_only: bool = False,
        add_dirs: tuple[str, ...] = (),
        startup_timeout_s: float = 30.0,
    ) -> None:
        self._agent_bin = agent_bin
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._lean = bool(lean)
        self._read_only = bool(read_only)
        self._add_dirs = tuple(str(path) for path in add_dirs if str(path).strip())
        self._startup_timeout_s = max(1.0, float(startup_timeout_s))
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._alive = False
        self._start_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._sessions: dict[str, str] = {}  # resume_thread_id -> acp sessionId
        self._invalid_sessions: set[str] = set()
        self._front_door_sid: str | None = None
        self._front_door_uses = 0
        self._session_premium_totals: dict[str, float] = {}
        self._session_premium_multipliers: dict[str, float] = {}
        self._session_models: dict[str, str] = {}
        self._agent_caps: dict[str, Any] = {}
        self._active_turn: _Turn | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._alive and self._proc is not None and self._proc.poll() is None:
                return
            self._spawn()

    def _spawn(self) -> None:
        cmd = [self._agent_bin, "--acp"]
        if self._model:
            cmd += ["--model", self._model]
        if self._reasoning_effort:
            cmd += ["--reasoning-effort", self._reasoning_effort]
        if self._lean:
            # Classifier prompts are self-contained and tool-free. Repository
            # instructions and built-in MCPs only inflate their input context.
            cmd += [
                "--no-custom-instructions",
                "--disable-builtin-mcps",
                "--available-tools=",
            ]
        elif self._read_only:
            # Manager SELF is deliberately read-only. Keep it on the warm ACP
            # transport without widening the tool surface beyond the same
            # view/grep/glob allowlist accepted by the Copilot CLI.
            cmd += [
                "--available-tools",
                "view,grep,glob",
                "--allow-tool",
                "view,grep,glob",
            ]
            for path in self._add_dirs:
                cmd += ["--add-dir", path]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # unread stderr PIPE would deadlock; we don't need it
            text=True,
            # Force UTF-8 so Windows does not fall back to cp1252 and crash the
            # JSON-RPC stdio bridge on non-Latin-1 payloads.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._alive = True
        with self._pending_lock:
            self._pending.clear()
        self._sessions.clear()
        self._invalid_sessions.clear()
        self._front_door_sid = None
        self._front_door_uses = 0
        self._session_premium_totals.clear()
        self._session_premium_multipliers.clear()
        self._session_models.clear()
        self._active_turn = None
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(self._proc,),
            name="copilot-acp-reader",
            daemon=True,
        )
        self._reader.start()
        resp = self._request(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {}},
            timeout=self._startup_timeout_s,
        )
        if resp is None or "error" in resp:
            self._alive = False
            raise RuntimeError(f"acp initialize failed: {resp}")
        self._agent_caps = (resp.get("result") or {}).get("agentCapabilities") or {}

    def _on_dead(self) -> None:
        self._alive = False
        with self._pending_lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            slot["msg"] = {"error": {"message": "acp process died"}}
            slot["event"].set()
        self._sessions.clear()
        self._invalid_sessions.clear()
        self._front_door_sid = None
        self._front_door_uses = 0
        self._session_premium_totals.clear()
        self._session_premium_multipliers.clear()
        self._session_models.clear()

    def close(self) -> None:
        """Terminate the warm ACP subprocess and release all session state."""
        with self._start_lock:
            proc = self._proc
            self._proc = None
            self._alive = False
            self._active_turn = None
            if proc is not None and proc.poll() is None:
                if _WINDOWS and _terminate_windows_tree(proc):
                    self._on_dead()
                    return
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception:  # noqa: BLE001
                        pass
            self._on_dead()

    def prewarm(self, cwd: str, *, front_door_session: bool = False) -> None:
        """Start transport/session state without spending a model turn."""
        with self._turn_lock:
            self._ensure_started()
            if front_door_session:
                self._session_for(None, cwd, _FRONT_DOOR_LABEL)

    # ── reader / dispatch ────────────────────────────────────────────────────
    def _reader_loop(self, proc: subprocess.Popen[str]) -> None:
        try:
            if proc.stdout is None:
                raise RuntimeError("Copilot ACP stdout pipe was not created")
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(msg, dict):
                    try:
                        self._dispatch(msg)
                    except Exception:  # noqa: BLE001 — a dispatch fault must not kill the reader
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._on_dead()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        if mid is not None and ("result" in msg or "error" in msg):
            with self._pending_lock:
                slot = self._pending.get(mid)
            if slot is not None:
                slot["msg"] = msg
                slot["event"].set()
            return
        method = msg.get("method")
        if method and mid is not None:  # server → client request
            self._handle_server_request(mid, str(method), msg.get("params") or {})
            return
        if method:  # notification
            self._handle_notification(str(method), msg.get("params") or {})

    def _handle_server_request(self, mid: Any, method: str, params: dict[str, Any]) -> None:
        if "request_permission" in method:
            turn = self._active_turn
            opt = self._pick_allow_option(
                params,
                allow_persistent=bool(turn and turn.allow_persistent),
            )
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {"outcome": {"outcome": "selected", "optionId": opt}},
                }
            )
            return
        # Copilot executes its built-in file/shell tools in its own process. Any
        # genuinely client-owned capability that we did not advertise is rejected
        # so the turn fails fast instead of hanging forever.
        self._write(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"unsupported request: {method}"},
            }
        )

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method != "session/update":
            return
        turn = self._active_turn
        if turn is None:
            return
        sid = params.get("sessionId")
        if sid is not None and sid != turn.session_id:
            return
        upd = params.get("update") or {}
        if not isinstance(upd, dict):
            return
        update_type = str(upd.get("sessionUpdate") or "")
        # This is a real ACP event, even when it is a dialect-specific update we
        # do not render. Keep the watchdog's idle clock tied to actual model /
        # tool traffic rather than to cosmetic heartbeat messages.
        turn.last_activity_at = time.monotonic()
        turn.last_event = update_type or "session_update"
        if update_type == "tool_call":
            turn.tool_activity_observed = True
            tool_id = str(upd.get("toolCallId") or "")
            title = str(upd.get("title") or upd.get("kind") or "tool")
            if tool_id:
                turn.tool_titles[tool_id] = title
            self._emit_turn_event(
                turn,
                {
                    "type": "tool.call",
                    "data": {
                        "name": title,
                        "arguments": upd.get("rawInput") or {},
                    },
                },
            )
            return
        if update_type == "tool_call_update":
            tool_id = str(upd.get("toolCallId") or "")
            title = turn.tool_titles.get(tool_id, "tool")
            status = str(upd.get("status") or "completed")
            self._emit_turn_event(
                turn,
                {
                    "type": "tool.result",
                    "data": {"content": f"{title} ({status})"},
                },
            )
            return
        if update_type != "agent_message_chunk":
            return
        content = upd.get("content")
        text = ""
        if isinstance(content, dict):
            text = str(content.get("text") or "")
        elif isinstance(content, str):
            text = content
        if not text:
            return
        # Copilot reports its startup tool policy as an assistant chunk. This
        # is transport diagnostics, not Manager prose; dropping the separate
        # chunk keeps internal tool names out of the operator-facing reply.
        if any(text.startswith(prefix) for prefix in _TRANSPORT_INFO_PREFIXES):
            return
        turn.raw_text += text
        self._sync_turn_text(turn)

    @staticmethod
    def _sync_turn_text(turn: _Turn, *, final: bool = False) -> None:
        filtered = _filter_transport_notices(turn.raw_text, final=final)
        if filtered == turn.text:
            return
        prior = turn.text
        turn.text = filtered
        delta = filtered[len(prior) :] if filtered.startswith(prior) else ""
        if delta and turn.emit is not None:
            try:
                turn.emit(delta)
            except Exception:  # noqa: BLE001 — a UI sink must never break the turn
                pass
        if turn.on_block is not None:
            try:
                turn.on_block(filtered)  # accumulated → front-end replaces in place
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _emit_turn_event(turn: _Turn, event: dict[str, Any]) -> None:
        if turn.emit is None:
            return
        try:
            turn.emit(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        except Exception:  # noqa: BLE001 — progress reporting must not break a turn
            pass

    @staticmethod
    def _pick_allow_option(
        params: dict[str, Any],
        *,
        allow_persistent: bool = True,
    ) -> str:
        """Match ``--allow-all-tools`` / dangerous-yolo: pick an ``allow`` option.
        Prefer allow_always, then allow_once, then any allow*, then the first."""
        opts = params.get("options") or []

        def kind(o: dict[str, Any]) -> str:
            return str(o.get("kind") or "").lower()

        wants = (
            ("allow_always", "allow_once") if allow_persistent else ("allow_once", "allow_always")
        )
        for want in wants:
            for o in opts:
                if isinstance(o, dict) and kind(o) == want and o.get("optionId"):
                    return str(o["optionId"])
        for o in opts:
            if isinstance(o, dict) and kind(o).startswith("allow") and o.get("optionId"):
                return str(o["optionId"])
        if opts and isinstance(opts[0], dict) and opts[0].get("optionId"):
            return str(opts[0]["optionId"])
        return "allow"

    # ── JSON-RPC send/recv ───────────────────────────────────────────────────
    def _write(self, obj: dict[str, Any]) -> None:
        with self._send_lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise RuntimeError("acp process not running")
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None,
        cancel_event: threading.Event | None = None,
    ) -> "dict[str, Any] | None":
        rid = next(self._ids)
        ev = threading.Event()
        slot: dict[str, Any] = {"event": ev, "msg": None}
        with self._pending_lock:
            self._pending[rid] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            if cancel_event is None:
                got = ev.wait(timeout)
            else:
                got = False
                deadline = time.monotonic() + timeout if timeout is not None else None
                cancel_deadline: float | None = None
                while not got:
                    now = time.monotonic()
                    if deadline is not None and now >= deadline:
                        break
                    if cancel_event.is_set():
                        if cancel_deadline is None:
                            cancel_deadline = now + _CANCEL_GRACE_S
                        elif now >= cancel_deadline:
                            break
                    wait_for = 0.05
                    active_deadlines = [
                        value for value in (deadline, cancel_deadline) if value is not None
                    ]
                    if active_deadlines:
                        wait_for = min(
                            wait_for,
                            max(0.001, min(active_deadlines) - now),
                        )
                    got = ev.wait(wait_for)
            return slot["msg"] if got else None
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ── sessions ─────────────────────────────────────────────────────────────
    def _remember_session(self, sid: str, result: dict[str, Any]) -> None:
        """Register an ACP session and its Copilot premium-request multiplier."""
        self._invalid_sessions.discard(sid)
        self._sessions[sid] = sid
        models_value = result.get("models")
        models: dict[str, Any] = models_value if isinstance(models_value, dict) else {}
        current = str(models.get("currentModelId") or self._model or "")
        multiplier = 1.0
        for model in models.get("availableModels") or []:
            if not isinstance(model, dict) or str(model.get("modelId") or "") != current:
                continue
            meta_value = model.get("_meta")
            meta: dict[str, Any] = meta_value if isinstance(meta_value, dict) else {}
            raw = str(meta.get("copilotUsage") or "").strip().lower().removesuffix("x")
            try:
                multiplier = max(0.0, float(raw))
            except ValueError:
                multiplier = 1.0
            break
        self._session_premium_multipliers[sid] = multiplier
        self._session_premium_totals.setdefault(sid, 0.0)
        self._session_models[sid] = current

    def _invalidate_session(self, sid: str) -> None:
        """Prevent late packets from a cancelled prompt contaminating its successor."""
        self._invalid_sessions.add(sid)
        self._sessions.pop(sid, None)
        self._session_models.pop(sid, None)
        if self._front_door_sid == sid:
            self._front_door_sid = None
            self._front_door_uses = 0

    def _new_session(self, cwd: str) -> str:
        resp = self._request("session/new", {"cwd": cwd, "mcpServers": []}, timeout=25)
        if resp is None or "error" in resp:
            raise RuntimeError(f"session/new failed: {resp}")
        sid = (resp.get("result") or {}).get("sessionId")
        if not sid:
            raise RuntimeError("session/new returned no sessionId")
        sid = str(sid)
        self._remember_session(sid, resp.get("result") or {})
        selected_model = self._session_models.get(sid, "")
        if self._model and selected_model and selected_model != self._model:
            self._invalidate_session(sid)
            raise RuntimeError(
                f"session/new selected {selected_model!r}, expected {self._model!r}"
            )
        return sid

    def _session_for(
        self,
        resume_thread_id: str | None,
        cwd: str,
        run_label: str | None,
    ) -> str:
        if resume_thread_id:
            if resume_thread_id in self._invalid_sessions:
                return self._new_session(cwd)
            sid = self._sessions.get(resume_thread_id)
            if sid:
                return sid
            if self._agent_caps.get("loadSession"):
                resp = self._request(
                    "session/load",
                    {"sessionId": resume_thread_id, "cwd": cwd, "mcpServers": []},
                    timeout=25,
                )
                if resp is not None and "error" not in resp:
                    self._remember_session(resume_thread_id, (resp.get("result") or {}))
                    return resume_thread_id
            # loadSession unsupported / failed → start a fresh one below.
        # A Manager reply with no resume id means "start/rotate the conversation".
        # It must never inherit the classifier's scratch history.
        if run_label != _FRONT_DOOR_LABEL:
            return self._new_session(cwd)
        mode = (os.environ.get("ARGUS_SKILL_COPILOT_ACP_SESSION_MODE", "reuse") or "reuse").lower()
        if mode == "fresh":
            return self._new_session(cwd)
        try:
            recycle = int(
                os.environ.get("ARGUS_SKILL_COPILOT_ACP_SESSION_RECYCLE", "")
                or _DEFAULT_SESSION_RECYCLE
            )
        except ValueError:
            recycle = _DEFAULT_SESSION_RECYCLE
        # Reuse ONE warm front-door session, recycled every N calls so its history
        # can't grow unbounded (the resume-cost climb the fresh-classify fix cured).
        if self._front_door_sid is None or (recycle > 0 and self._front_door_uses >= recycle):
            self._front_door_sid = self._new_session(cwd)
            self._front_door_uses = 0
        self._front_door_uses += 1
        return self._front_door_sid

    # ── the one public entry point ───────────────────────────────────────────
    def run_prompt(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options: Any,
        run_label: str | None,
        cwd: str | None = None,
        emit: Callable[[str], None] | None = None,
        on_block: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        idle_timeout = _prompt_timeout(run_label)
        _cwd = cwd or getattr(options, "working_dir", None) or os.getcwd()

        with self._turn_lock:
            try:
                self._ensure_started()
                sid = self._session_for(resume_thread_id, _cwd, run_label)
            except Exception as exc:  # noqa: BLE001
                return self._fail_result(f"acp setup failed: {exc}")

            turn = _Turn(
                sid,
                on_block,
                emit,
                allow_persistent=bool(getattr(options, "dangerous_yolo", False)),
            )
            self._active_turn = turn
            cancelled = {"v": False}
            cancel_reason = {"v": ""}
            cancel_event = threading.Event()
            stop = threading.Event()
            prov = getattr(options, "external_interrupt_reason_provider", None)
            inactivity_cb = getattr(options, "inactivity_callback", None)
            try:
                soft_idle = max(0.0, float(getattr(options, "watchdog_soft_idle_seconds", 0) or 0))
            except (TypeError, ValueError):
                soft_idle = 0.0
            try:
                stalled_idle = max(
                    0.0,
                    float(getattr(options, "watchdog_stalled_idle_seconds", 0) or 0),
                )
            except (TypeError, ValueError):
                stalled_idle = 0.0
            try:
                hard_idle = max(0.0, float(getattr(options, "watchdog_hard_idle_seconds", 0) or 0))
            except (TypeError, ValueError):
                hard_idle = 0.0

            def _watchdog() -> None:
                last_soft_check_at = turn.last_activity_at
                observed_activity_at = turn.last_activity_at
                idle_escalation = IdleEscalation(
                    warning_seconds=soft_idle,
                    stalled_seconds=stalled_idle,
                    terminate_seconds=hard_idle,
                )
                active_thresholds = [
                    value
                    for value in (soft_idle, stalled_idle, hard_idle, idle_timeout)
                    if value > 0
                ]
                poll_s = (
                    min(0.25, max(0.01, min(active_thresholds) / 2.0))
                    if active_thresholds
                    else 0.25
                )

                def _cancel(reason: str) -> None:
                    cancelled["v"] = True
                    cancel_reason["v"] = reason
                    cancel_event.set()
                    self._invalidate_session(sid)
                    try:
                        self._notify("session/cancel", {"sessionId": sid})
                    except Exception:  # noqa: BLE001
                        pass

                while not stop.wait(poll_s):
                    reason = None
                    if prov is not None:
                        try:
                            reason = prov()
                        except Exception:  # noqa: BLE001
                            reason = None
                    now = time.monotonic()
                    if reason:
                        _cancel(f"External interrupt: {reason}")
                        return

                    if turn.last_activity_at > observed_activity_at:
                        observed_activity_at = turn.last_activity_at
                        idle_escalation.reset()
                    idle_seconds = max(0.0, now - turn.last_activity_at)
                    if (
                        soft_idle > 0
                        and inactivity_cb is not None
                        and idle_seconds >= soft_idle
                        and (now - last_soft_check_at) >= soft_idle
                    ):
                        last_soft_check_at = now
                        try:
                            decision = inactivity_cb(
                                InactivitySnapshot(
                                    idle_seconds=idle_seconds,
                                    command=[self._agent_bin, "--acp", "session/prompt", sid],
                                    thread_id=sid,
                                    last_agent_message=turn.text,
                                    stdout_tail=[],
                                    stderr_tail=[],
                                    run_label=run_label,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            decision = None
                        if decision == "restart":
                            _cancel(
                                "ACP restart requested after "
                                f"{int(idle_seconds)}s without an ACP stream event"
                            )
                            return

                    for stage in idle_escalation.newly_due(idle_seconds):
                        if stage == WARNING_STAGE:
                            self._emit_turn_event(
                                turn,
                                {
                                    "type": "watchdog.no_progress_warning",
                                    "idle_seconds": int(idle_seconds),
                                    "threshold_seconds": int(soft_idle),
                                    "operator_alert": True,
                                },
                            )
                        elif stage == STALLED_STAGE:
                            self._emit_turn_event(
                                turn,
                                {
                                    "type": "watchdog.likely_stalled",
                                    "idle_seconds": int(idle_seconds),
                                    "threshold_seconds": int(stalled_idle),
                                    "operator_alert": True,
                                    "likely_blocked": True,
                                },
                            )
                        elif stage == TERMINATE_STAGE:
                            self._emit_turn_event(
                                turn,
                                {
                                    "type": "watchdog.terminated",
                                    "idle_seconds": int(idle_seconds),
                                    "threshold_seconds": int(hard_idle),
                                    "operator_alert": True,
                                },
                            )
                            _cancel(
                                "Forced restart after hard idle timeout "
                                f"({int(hard_idle)}s without an ACP stream event; "
                                f"last event: {turn.last_event})"
                            )
                            return
                    if idle_timeout > 0 and idle_seconds >= idle_timeout:
                        _cancel(
                            "ACP prompt idle timeout after "
                            f"{idle_timeout:g}s without an ACP event "
                            f"(last ACP event: {turn.last_event})"
                        )
                        return

            wd = threading.Thread(target=_watchdog, name="copilot-acp-watchdog", daemon=True)
            wd.start()
            try:
                resp = self._request(
                    "session/prompt",
                    {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
                    timeout=None,
                    cancel_event=cancel_event,
                )
            except KeyboardInterrupt:
                cancelled["v"] = True
                cancel_reason["v"] = "External interrupt: user interrupted Manager turn"
                self._invalidate_session(sid)
                try:
                    self._notify("session/cancel", {"sessionId": sid})
                except Exception:  # noqa: BLE001
                    pass
                raise
            except Exception as exc:  # noqa: BLE001
                resp = {"error": {"message": str(exc)}}
            finally:
                self._sync_turn_text(turn, final=True)
                stop.set()
                self._active_turn = None

            text = turn.text.strip()
            if resp is None:
                self._invalidate_session(sid)
                if cancel_event.is_set():
                    self.close()
                    return self._fail_result(
                        cancel_reason["v"] or "acp prompt cancelled",
                        sid=sid,
                        text=text,
                        tool_activity_observed=turn.tool_activity_observed,
                    )
                return self._fail_result(
                    "acp prompt ended without a response",
                    sid=sid,
                    text=text,
                    tool_activity_observed=turn.tool_activity_observed,
                )
            if "error" in resp:
                self._invalidate_session(sid)
                return self._fail_result(
                    f"acp error: {resp.get('error')}",
                    sid=sid,
                    text=text,
                    tool_activity_observed=turn.tool_activity_observed,
                )
            stop_reason = str((resp.get("result") or {}).get("stopReason") or "")
            completed = (stop_reason == "end_turn") and not cancelled["v"]
            if completed and _looks_like_content_filter_notice(turn.raw_text):
                self._invalidate_session(sid)
                self.close()
                return self._fail_result(
                    "Copilot content filtering blocked the response; the identical "
                    "prompt must not be retried",
                    sid=sid,
                    stop_kind="permanent_error",
                    tool_activity_observed=turn.tool_activity_observed,
                )
            json_events: list[dict[str, Any]] = []
            if completed:
                total = self._session_premium_totals.get(sid, 0.0)
                total += self._session_premium_multipliers.get(sid, 1.0)
                self._session_premium_totals[sid] = total
                # AgentCliBackend already knows how to de-cumulate Copilot's
                # normal CLI ``result.usage.premiumRequests`` event per thread.
                # Emit the same shape so warm turns stay inside the budget meter.
                json_events.append(
                    {
                        "type": "result",
                        "usage": {"premiumRequests": total},
                    }
                )
            return AgentRunResult(
                command=[self._agent_bin, "--acp", "session/prompt", sid],
                exit_code=0 if completed else 1,
                thread_id=sid,
                agent_messages=[text] if text else [],
                json_events=json_events,
                stdout_lines=[],
                stderr_lines=[],
                turn_completed=completed,
                turn_failed=not completed,
                fatal_error=None
                if completed
                else (
                    cancel_reason["v"]
                    or (f"stopReason={stop_reason}" if stop_reason else "acp turn incomplete")
                ),
                tool_activity_observed=turn.tool_activity_observed,
                usage_model=self._session_models.get(sid, self._model or ""),
            )

    def _fail_result(
        self,
        msg: str,
        *,
        sid: str | None = None,
        text: str = "",
        stop_kind: str | None = None,
        tool_activity_observed: bool = False,
    ) -> AgentRunResult:
        return AgentRunResult(
            command=[self._agent_bin, "--acp"],
            exit_code=-1,
            thread_id=sid,
            agent_messages=[text] if text else [],
            json_events=[],
            stdout_lines=[],
            stderr_lines=[],
            turn_completed=False,
            turn_failed=True,
            fatal_error=msg,
            stop_kind=stop_kind,
            tool_activity_observed=tool_activity_observed,
            usage_model=self._session_models.get(sid or "", self._model or ""),
        )


# Module-level registry. ``scope`` isolates OS processes between Managers while
# still reusing classifier/reply transports inside one Manager.
_CLIENTS: dict[
    tuple[str, str, str, bool, bool, tuple[str, ...], str],
    CopilotAcpClient,
] = {}
_CLIENTS_LOCK = threading.Lock()


def get_client(
    agent_bin: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    *,
    lean: bool = False,
    read_only: bool = False,
    add_dirs: list[str] | tuple[str, ...] | None = None,
    scope: str = "shared",
) -> CopilotAcpClient:
    normalized_dirs = tuple(str(path).strip() for path in (add_dirs or ()) if str(path).strip())
    key = (
        agent_bin,
        model or "",
        reasoning_effort or "",
        bool(lean),
        bool(read_only),
        normalized_dirs,
        str(scope or "shared"),
    )
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = CopilotAcpClient(
                agent_bin,
                model,
                reasoning_effort,
                lean=lean,
                read_only=read_only,
                add_dirs=normalized_dirs,
            )
            _CLIENTS[key] = client
        return client


def close_clients_for_scope(scope: str) -> None:
    target = str(scope or "shared")
    with _CLIENTS_LOCK:
        keys = [key for key in _CLIENTS if key[-1] == target]
        clients = [_CLIENTS.pop(key) for key in keys]
    for client in clients:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def close_all_clients() -> None:
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


atexit.register(close_all_clients)


__all__ = [
    "CopilotAcpClient",
    "close_all_clients",
    "close_clients_for_scope",
    "get_client",
]
