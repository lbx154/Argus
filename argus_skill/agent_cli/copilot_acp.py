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

from .models import AgentRunResult

_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_SESSION_RECYCLE = 50
_FRONT_DOOR_LABEL = "manager-frontdoor-classify"


class _Turn:
    """State for the single in-flight prompt (serialized by the turn-lock)."""

    __slots__ = (
        "session_id",
        "on_block",
        "emit",
        "text",
        "tool_titles",
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
        self.text = ""
        self.tool_titles: dict[str, str] = {}
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
    ) -> None:
        self._agent_bin = agent_bin
        self._model = model
        self._reasoning_effort = reasoning_effort
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
        self._front_door_sid: str | None = None
        self._front_door_uses = 0
        self._session_premium_totals: dict[str, float] = {}
        self._session_premium_multipliers: dict[str, float] = {}
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
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # unread stderr PIPE would deadlock; we don't need it
            text=True,
            bufsize=1,
        )
        self._alive = True
        with self._pending_lock:
            self._pending.clear()
        self._sessions.clear()
        self._front_door_sid = None
        self._front_door_uses = 0
        self._session_premium_totals.clear()
        self._session_premium_multipliers.clear()
        self._active_turn = None
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(self._proc,),
            name="copilot-acp-reader",
            daemon=True,
        )
        self._reader.start()
        resp = self._request(
            "initialize", {"protocolVersion": 1, "clientCapabilities": {}}, timeout=20
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
        self._front_door_sid = None
        self._front_door_uses = 0
        self._session_premium_totals.clear()
        self._session_premium_multipliers.clear()

    def close(self) -> None:
        """Terminate the warm ACP subprocess and release all session state."""
        with self._start_lock:
            proc = self._proc
            self._proc = None
            self._alive = False
            self._active_turn = None
            if proc is not None and proc.poll() is None:
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

    # ── reader / dispatch ────────────────────────────────────────────────────
    def _reader_loop(self, proc: subprocess.Popen[str]) -> None:
        try:
            assert proc.stdout is not None
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
        turn.text += text
        if turn.emit is not None:
            try:
                turn.emit(text)
            except Exception:  # noqa: BLE001 — a UI sink must never break the turn
                pass
        if turn.on_block is not None:
            try:
                turn.on_block(turn.text)  # accumulated → front-end mergeFragment replaces in place
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
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> "dict[str, Any] | None":
        rid = next(self._ids)
        ev = threading.Event()
        slot: dict[str, Any] = {"event": ev, "msg": None}
        with self._pending_lock:
            self._pending[rid] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        except Exception:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise
        got = ev.wait(timeout)
        with self._pending_lock:
            self._pending.pop(rid, None)
        return slot["msg"] if got else None

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ── sessions ─────────────────────────────────────────────────────────────
    def _remember_session(self, sid: str, result: dict[str, Any]) -> None:
        """Register an ACP session and its Copilot premium-request multiplier."""
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

    def _new_session(self, cwd: str) -> str:
        resp = self._request("session/new", {"cwd": cwd, "mcpServers": []}, timeout=25)
        if resp is None or "error" in resp:
            raise RuntimeError(f"session/new failed: {resp}")
        sid = (resp.get("result") or {}).get("sessionId")
        if not sid:
            raise RuntimeError("session/new returned no sessionId")
        sid = str(sid)
        self._remember_session(sid, resp.get("result") or {})
        return sid

    def _session_for(
        self,
        resume_thread_id: str | None,
        cwd: str,
        run_label: str | None,
    ) -> str:
        if resume_thread_id:
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
        try:
            timeout = float(
                os.environ.get("ARGUS_SKILL_COPILOT_ACP_TIMEOUT_S", "") or _DEFAULT_TIMEOUT_S
            )
        except ValueError:
            timeout = _DEFAULT_TIMEOUT_S
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
            stop = threading.Event()
            prov = getattr(options, "external_interrupt_reason_provider", None)
            inactivity_cb = getattr(options, "inactivity_callback", None)
            try:
                soft_idle = max(
                    0.0, float(getattr(options, "watchdog_soft_idle_seconds", 0) or 0)
                )
            except (TypeError, ValueError):
                soft_idle = 0.0
            try:
                hard_idle = max(
                    0.0, float(getattr(options, "watchdog_hard_idle_seconds", 0) or 0)
                )
            except (TypeError, ValueError):
                hard_idle = 0.0

            def _watchdog() -> None:
                deadline = time.monotonic() + timeout
                last_soft_check_at = turn.last_activity_at
                active_thresholds = [v for v in (soft_idle, hard_idle) if v > 0]
                poll_s = (
                    min(0.25, max(0.01, min(active_thresholds) / 2.0))
                    if active_thresholds
                    else 0.25
                )

                def _cancel(reason: str) -> None:
                    cancelled["v"] = True
                    cancel_reason["v"] = reason
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
                    if now > deadline:
                        _cancel(f"ACP prompt timed out after {timeout:g}s")
                        return

                    idle_seconds = max(0.0, now - turn.last_activity_at)
                    if (
                        soft_idle > 0
                        and inactivity_cb is not None
                        and idle_seconds >= soft_idle
                        and (now - last_soft_check_at) >= soft_idle
                    ):
                        last_soft_check_at = now
                        try:
                            # Import lazily: copilot_acp is itself loaded by
                            # AgentCliRunner's warm fast path, so a module-level
                            # import would create a cycle.
                            from .agent_cli_runner import InactivitySnapshot

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
                                "Restart requested after "
                                f"{int(idle_seconds)}s without an ACP stream event"
                            )
                            return

                    if hard_idle > 0 and idle_seconds >= hard_idle:
                        _cancel(
                            f"Hard idle timeout after {int(idle_seconds)}s "
                            f"(last ACP event: {turn.last_event})"
                        )
                        return

            wd = threading.Thread(target=_watchdog, name="copilot-acp-watchdog", daemon=True)
            wd.start()
            try:
                resp = self._request(
                    "session/prompt",
                    {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
                    timeout=timeout + 5,
                )
            except Exception as exc:  # noqa: BLE001
                resp = {"error": {"message": str(exc)}}
            finally:
                stop.set()
                self._active_turn = None

            text = turn.text.strip()
            if resp is None:
                return self._fail_result("acp prompt timed out", sid=sid, text=text)
            if "error" in resp:
                return self._fail_result(f"acp error: {resp.get('error')}", sid=sid, text=text)
            stop_reason = str((resp.get("result") or {}).get("stopReason") or "")
            completed = (stop_reason == "end_turn") and not cancelled["v"]
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
            )

    def _fail_result(self, msg: str, *, sid: str | None = None, text: str = "") -> AgentRunResult:
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
        )


# Module-level singletons, keyed by (agent_bin, model, effort): one warm process
# per execution configuration so changing a Manager's effort takes effect without
# mutating an already-live Copilot process.
_CLIENTS: dict[tuple[str, str, str], CopilotAcpClient] = {}
_CLIENTS_LOCK = threading.Lock()


def get_client(
    agent_bin: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> CopilotAcpClient:
    key = (agent_bin, model or "", reasoning_effort or "")
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = CopilotAcpClient(agent_bin, model, reasoning_effort)
            _CLIENTS[key] = client
        return client


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


__all__ = ["CopilotAcpClient", "close_all_clients", "get_client"]
