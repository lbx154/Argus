"""Manager streaming/messages API domain: the Manager front-door chat
endpoints (blocking + SSE-streaming twins) and the live project event
WebSocket stream.

See :mod:`.meta` for the extraction convention this module follows.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from contextlib import suppress
from typing import Any

from fastapi import Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from .context import ServerContext
from .models import CancelMessageIn, MessageIn

_UPLOAD_READ_CHUNK_BYTES = 64 * 1024


class _ManagerStreamingResponse(StreamingResponse):
    """Propagate disconnect before the response waits for its worker to drain."""

    def __init__(self, *args, cancel_event: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_event = cancel_event

    async def __call__(self, scope, receive, send):
        async def receive_checked():
            try:
                message = await receive()
            except BaseException:
                self.cancel_event.set()
                raise
            if message.get("type") == "http.disconnect":
                self.cancel_event.set()
            return message

        async def send_checked(message):
            try:
                await send(message)
            except BaseException:
                self.cancel_event.set()
                raise

        try:
            await super().__call__(scope, receive_checked, send_checked)
        finally:
            self.cancel_event.set()


async def _read_uploaded_attachments(
    files: list[UploadFile],
) -> list[tuple[str, str, bytes]]:
    from ..attachments import attachment_limits

    uploads = list(files or [])
    limits = attachment_limits()

    payload: list[tuple[str, str, bytes]] = []
    total_bytes = 0
    primary_error: BaseException | None = None
    try:
        if not uploads:
            raise ValueError("no attachments were uploaded")
        if len(uploads) > limits["max_count"]:
            raise ValueError(
                f"too many attachments; limit is {limits['max_count']} files per message"
            )
        for upload in uploads:
            file_name = str(upload.filename or "")
            file_mime = str(upload.content_type or "")
            buffer = bytearray()
            file_bytes = 0
            while True:
                remaining_file_bytes = limits["max_bytes_per_file"] - file_bytes
                remaining_total_bytes = limits["max_total_bytes"] - total_bytes
                if remaining_file_bytes <= 0:
                    overflow = await upload.read(1)
                    if overflow:
                        label = file_name or "attachment"
                        raise ValueError(
                            f"{label} exceeds the {limits['max_bytes_per_file']} byte per-file limit"
                        )
                    break
                if remaining_total_bytes <= 0:
                    overflow = await upload.read(1)
                    if overflow:
                        raise ValueError(
                            "combined attachments exceed the "
                            f"{limits['max_total_bytes']} byte total limit"
                        )
                    break
                read_size = min(
                    _UPLOAD_READ_CHUNK_BYTES,
                    remaining_file_bytes,
                    remaining_total_bytes,
                )
                try:
                    chunk = await upload.read(read_size)
                except Exception as exc:  # noqa: BLE001 - explicit client-facing upload error
                    label = file_name or "uploaded attachment"
                    raise RuntimeError(f"failed to read {label}: {exc}") from exc
                if not chunk:
                    break
                buffer.extend(chunk)
                file_bytes += len(chunk)
                total_bytes += len(chunk)
            payload.append((file_name, file_mime, bytes(buffer)))
    except BaseException as exc:
        primary_error = exc

    close_errors: list[str] = []
    for upload in uploads:
        try:
            await upload.close()
        except Exception as exc:  # noqa: BLE001 - preserve explicit close failures
            label = str(upload.filename or "uploaded attachment")
            close_errors.append(f"{label}: {type(exc).__name__}: {exc}")
    if close_errors:
        detail = "failed to close upload stream(s): " + "; ".join(close_errors)
        if primary_error is not None:
            raise RuntimeError(f"{primary_error}; {detail}") from primary_error
        raise RuntimeError(detail)
    if primary_error is not None:
        raise primary_error
    return payload


def register_manager_routes(app, ctx: ServerContext, server_mod) -> None:
    from ..manager_state import manager_control_generation
    from ..message_requests import (
        MessageRequestCancelled,
        MessageRequestCapacityError,
        MessageRequestConflict,
        MessageRequestRegistry,
    )

    requests = MessageRequestRegistry()
    # Project snapshots read this live registry after their cached filesystem
    # projection.  Keeping the registry app-local preserves cancellation
    # isolation while allowing a reloaded browser to recover the request id.
    app.state.message_requests = requests

    def _begin_message(sid: str, request_id: str):
        try:
            return requests.begin(sid, request_id)
        except (MessageRequestCancelled, MessageRequestConflict, MessageRequestCapacityError) as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.post("/api/projects/{sid}/message/cancel", dependencies=[Depends(ctx.require_auth)])
    async def _cancel_message(sid: str, body: CancelMessageIn) -> dict[str, Any]:
        ctx.project_root_or_404(sid)
        try:
            # Memory-only cancellation: no model/session lock or shared HTTP
            # worker is needed to interrupt a blocked foreground request.
            return requests.cancel(sid, body.request_id)
        except MessageRequestCapacityError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def _visible_daemon(sid: str) -> dict[str, Any]:
        from ..project_state import daemon_dict

        life_dir = ctx.resolve_or_404(sid)
        return daemon_dict(server_mod.read_daemon_status(life_dir), life_dir=life_dir)

    def _record_spawn_result(result: dict[str, Any], spawned: Any) -> None:
        result["daemon"] = spawned
        if not isinstance(spawned, dict):
            return
        visible = spawned.get("daemon")
        if not isinstance(visible, dict):
            visible = spawned
        if "alive" in visible:
            result["daemon_alive"] = bool(visible["alive"])
        if "pid" in visible:
            result["daemon_pid"] = visible["pid"]
        if "control_available" in visible:
            result["daemon_control_available"] = bool(visible["control_available"])

    def _finish_message(
        sid: str, result: dict[str, Any], generation: int, *,
        global_root, text: str, request_cancelled, on_fragment=None,
    ) -> dict[str, Any]:
        """Deliver the handoff without restarting work superseded by Stop.

        Slow status reads run outside the lifecycle lock. Only startup and its
        acknowledgement share the same lock as daemon control commands.
        """
        from ...core.operator_messages import uses_cjk
        from ...daemon.commands import daemon_command_execution_lock
        from ..manager_pending_question import record_task_dispatch_ack

        def superseded() -> bool:
            if not request_cancelled() and manager_control_generation(sid) == generation:
                return False
            result.update(kind="cancelled", reply=(
                "这次请求已取消。" if uses_cjk(text) else "This request was cancelled."
            ))
            return True

        if request_cancelled() and superseded():
            return result
        starts_executor = (
            result.get("kind") == "task"
            and (result.get("dispatch_state") != "already_queued"
                 or str((result.get("item") or {}).get("status") or "") == "pending")
        ) or (result.get("kind") == "pending_question" and bool(result.get("resolved")))
        daemon_view = _visible_daemon(sid)
        result["daemon_alive"] = daemon_view["alive"]
        result["daemon_control_available"] = daemon_view["control_available"]
        if not starts_executor and result.get("kind") != "task":
            # A control reply can itself advance the generation (natural
            # language Pause). Only explicit request cancellation supersedes
            # that successful reply while its final status read was pending.
            if request_cancelled():
                superseded()
            return result

        if superseded():
            return result
        with daemon_command_execution_lock(ctx.resolve_or_404(sid), blocking=False) as acquired:
            if superseded():
                return result
            if not acquired and starts_executor:
                result["daemon"] = {
                    "rc": 3, "control_busy": True, "error": "Daemon control is busy; retry shortly.",
                }
            elif starts_executor and not result.get("daemon_alive"):
                try:
                    spawned = ctx.daemon_services.start(
                        sid, global_root=global_root,
                        resume_continuous=bool(result.get("continuous")), reclaim_idle=True,
                    )
                    _record_spawn_result(result, spawned)
                except Exception as exc:  # noqa: BLE001 — report startup failure in both transports
                    result["daemon"] = {
                        "rc": 2, "error": "The background worker could not start.",
                        "diagnostic": f"{type(exc).__name__}: {exc}",
                    }
            # Cancellation can arrive while startup is blocked. A late success
            # must not publish an acknowledgement for a superseded request.
            if superseded():
                return result
            if result.get("kind") == "task":
                try:
                    record_task_dispatch_ack(
                        sid, result, global_root=global_root, on_fragment=on_fragment, operator_text=text,
                        cancelled=superseded,
                    )
                except Exception as exc:  # noqa: BLE001 — preserve the task if its ACK write fails
                    result["ack_error"] = "The task was queued, but its confirmation could not be saved."
                    result["ack_diagnostic"] = f"{type(exc).__name__}: {exc}"
            superseded()
        return result

    async def _resolve_message_attachments(
        sid: str,
        body: MessageIn,
        *,
        global_root,
    ) -> list[dict[str, Any]]:
        if not body.attachments:
            return []
        from ..attachments import resolve_attachment_refs

        try:
            return await run_in_threadpool(
                resolve_attachment_refs,
                sid,
                [row.model_dump() for row in body.attachments],
                global_root=global_root,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{sid}/attachments", dependencies=[Depends(ctx.require_auth)])
    async def _upload_attachments(
        sid: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        project_root = ctx.project_root_or_404(sid)
        from ..attachments import upload_attachments

        try:
            payload = await _read_uploaded_attachments(files)
            return await run_in_threadpool(
                upload_attachments,
                sid,
                payload,
                global_root=project_root,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{sid}/message", dependencies=[Depends(ctx.require_auth)])
    async def _post_message(sid: str, body: MessageIn) -> dict[str, Any]:
        """The Manager front-door: route natural language through the SAME triage
        the Manager pipeline uses. A conversational message ("你好") gets a Manager
        reply and never becomes a mission; only TEAM/complex work is enqueued.
        Runs in a threadpool because the Manager triage is a blocking LLM call.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty message")
        generation = manager_control_generation(sid)
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import manager_message
        lease = _begin_message(sid, body.request_id)
        handoff = threading.Lock()
        started = abandoned = False

        def _run() -> dict[str, Any]:
            nonlocal started
            with handoff:
                if abandoned:
                    return {"kind": "cancelled", "reply": "This request was cancelled."}
                started = True
            try:
                result = manager_message(sid, body.text, **kwargs)
                return _finish_message(
                    sid, result, generation, global_root=project_root,
                    text=body.text, request_cancelled=lease.cancelled,
                )
            finally:
                # HTTP cancellation can leave this thread running. Its request
                # must remain cancellable until the provider actually returns.
                lease.finish()

        try:
            attachments = await _resolve_message_attachments(sid, body, global_root=project_root)
            kwargs: dict[str, Any] = {
                "global_root": project_root,
                "defer_dispatch_ack": True,
                "cancelled": lambda: lease.cancelled() or manager_control_generation(sid) != generation,
            }
            if attachments:
                kwargs["attachments"] = attachments
            if body.route_override != "auto":
                kwargs["route_override"] = body.route_override
            if body.domain_answer is not None:
                kwargs["domain_answer"] = body.domain_answer.model_dump()

            return await run_in_threadpool(_run)
        except BaseException:
            with handoff:
                if not started:
                    requests.cancel(sid, lease.request_id)
                    abandoned = True
                    lease.finish()
            raise

    @app.post("/api/projects/{sid}/message/stream", dependencies=[Depends(ctx.require_auth)])
    async def _post_message_stream(sid: str, body: MessageIn):
        """Streaming twin of ``/message`` (Server-Sent Events).

        The Manager turn is a blocking CLI call, but copilot/codex emit the reply
        as blocks *during* the turn and phase transitions fire live — the plain
        POST throws all that away, so the front-end looks frozen until the whole
        turn ends. Here we run ``manager_message`` on a worker thread with an
        ``on_fragment`` callback that pushes each block / phase onto a thread-safe
        queue; a synchronous generator drains the queue into SSE ``data:`` frames.
        A sync generator means Starlette runs it in a threadpool — no asyncio
        queue bridging, which keeps this robust and easy to reason about.

        Frame kinds: ``{"type":"phase",...}`` · ``{"type":"delta",...}`` ·
        ``{"type":"done","result":{...}}`` · ``{"type":"error","error":...}``.
        The blocking ``/message`` stays as the fallback for non-streaming clients.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty message")
        generation = manager_control_generation(sid)
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import manager_message
        lease = _begin_message(sid, body.request_id)
        try:
            attachments = await _resolve_message_attachments(sid, body, global_root=project_root)
        except BaseException:
            lease.finish()
            raise

        q: "queue.Queue[dict | None]" = queue.Queue()
        stream_closed = threading.Event()

        def request_cancelled() -> bool:
            # Navigation closes the HTTP subscription, not the accepted task.
            # Explicit cancel/daemon-stop remains authoritative after a reload.
            return lease.cancelled() or manager_control_generation(sid) != generation

        def _run() -> None:
            def _on_fragment(kind: str, payload: dict) -> None:
                if not request_cancelled() and not stream_closed.is_set():
                    q.put({"type": kind, **payload})

            def _on_terminal(payload: dict) -> None:
                # Detach stale reads even if a cancelled request partially committed.
                ctx.invalidate_read_caches()
                if not stream_closed.is_set():
                    q.put(payload)

            try:
                kwargs: dict[str, Any] = {
                    "global_root": project_root,
                    "defer_dispatch_ack": True,
                    "on_fragment": _on_fragment,
                    "cancelled": request_cancelled,
                }
                if attachments:
                    kwargs["attachments"] = attachments
                if body.route_override != "auto":
                    kwargs["route_override"] = body.route_override
                if body.domain_answer is not None:
                    kwargs["domain_answer"] = body.domain_answer.model_dump()
                result = manager_message(
                    sid,
                    body.text,
                    **kwargs,
                )
                result = _finish_message(
                    sid, result, generation, global_root=project_root, text=body.text, on_fragment=_on_fragment,
                    request_cancelled=lease.cancelled,
                )
                _on_terminal({"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                _on_terminal({
                    "type": "error",
                    "error": "I couldn't finish handling that request.",
                    "diagnostic": f"{type(exc).__name__}: {exc}",
                })
            finally:
                lease.finish()
                if not stream_closed.is_set():
                    q.put(None)  # sentinel: generator stops

        try:
            threading.Thread(target=_run, name=f"manager-stream-{sid}", daemon=True).start()
        except BaseException:
            lease.finish()
            raise

        def _gen():
            for item in server_mod._iter_manager_stream_items(
                q,
                heartbeat_s=server_mod._manager_stream_heartbeat_seconds(),
            ):
                if stream_closed.is_set():
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        return _ManagerStreamingResponse(
            _gen(),
            cancel_event=stream_closed,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.websocket("/api/projects/{sid}/stream")
    async def _stream(ws: WebSocket, sid: str, replay: int = 40,
                      view: str = Query(default="full", pattern="^(full|ui)$"),
                      token_q: str | None = Query(default=None, alias="token")) -> None:
        project_root = ctx.root_for_project(sid)
        life_dir = (
            server_mod.project_life_dir(sid, global_root=project_root)
            if project_root is not None
            else None
        )
        await ws.accept()
        if ctx.token and token_q != ctx.token:
            await ws.close(code=4401, reason="unauthorized")
            return
        if life_dir is None:
            await ws.close(code=4404, reason="unknown project")
            return
        iterator = server_mod.tail_events(
            life_dir,
            replay_limit=max(0, min(replay, 200)),
        ).__aiter__()
        event_task = asyncio.create_task(anext(iterator))
        receive_task = asyncio.create_task(ws.receive())
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {event_task, receive_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receive_task in done:
                    message = receive_task.result()
                    if message.get("type") == "websocket.disconnect":
                        return
                    receive_task = asyncio.create_task(ws.receive())
                if event_task in done:
                    try:
                        ev = event_task.result()
                    except StopAsyncIteration:
                        return
                    event_task = asyncio.create_task(anext(iterator))
                    if view == "ui" and not server_mod._event_visible_in_web_ui(ev):
                        continue
                    await ws.send_json(ev)
        except asyncio.CancelledError:
            return
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 — a stream error must not crash the server
            try:
                await ws.close(code=1011)
            except Exception:  # noqa: BLE001
                pass
        finally:
            for task in (event_task, receive_task):
                task.cancel()
            await asyncio.gather(event_task, receive_task, return_exceptions=True)
            # ``asyncio.CancelledError`` is a BaseException on supported
            # Python versions, so ``suppress(Exception)`` does not cover the
            # normal TestClient/server-shutdown cancellation path.
            with suppress(asyncio.CancelledError, Exception):
                await iterator.aclose()
