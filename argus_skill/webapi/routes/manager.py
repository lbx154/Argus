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
from .models import MessageIn

_UPLOAD_READ_CHUNK_BYTES = 64 * 1024


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
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import manager_message
        from ..manager_pending_question import record_task_dispatch_ack
        attachments = await _resolve_message_attachments(
            sid,
            body,
            global_root=project_root,
        )

        kwargs = {"global_root": project_root}
        if attachments:
            kwargs["attachments"] = attachments
        if body.route_override != "auto":
            kwargs["route_override"] = body.route_override

        result = await run_in_threadpool(
            manager_message, sid, body.text, **kwargs,
        )
        # A task classification lazily spawns the executor, mirroring /tasks.
        starts_executor = (
            (
                result.get("kind") == "task"
                and (
                    result.get("dispatch_state") != "already_queued"
                    or str((result.get("item") or {}).get("status") or "")
                    == "pending"
                )
            )
            or (
                result.get("kind") == "pending_question"
                and bool(result.get("resolved"))
            )
        )
        daemon_view = await run_in_threadpool(_visible_daemon, sid)
        result["daemon_alive"] = daemon_view["alive"]
        result["daemon_control_available"] = daemon_view["control_available"]
        if starts_executor and not result.get("daemon_alive"):
            spawned = await run_in_threadpool(
                server_mod.start_project_daemon, sid, global_root=project_root,
                resume_continuous=bool(result.get("continuous")),
                reclaim_idle=True,
            )
            _record_spawn_result(result, spawned)
        if result.get("kind") == "task":
            await run_in_threadpool(
                record_task_dispatch_ack, sid, result, global_root=project_root,
            )
        return result

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
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import manager_message
        from ..manager_pending_question import record_task_dispatch_ack
        attachments = await _resolve_message_attachments(
            sid,
            body,
            global_root=project_root,
        )

        q: "queue.Queue[dict | None]" = queue.Queue()

        def _run() -> None:
            def _on_fragment(kind: str, payload: dict) -> None:
                q.put({"type": kind, **payload})
            try:
                kwargs = {
                    "global_root": project_root,
                    "on_fragment": _on_fragment,
                }
                if attachments:
                    kwargs["attachments"] = attachments
                if body.route_override != "auto":
                    kwargs["route_override"] = body.route_override
                result = manager_message(
                    sid,
                    body.text,
                    **kwargs,
                )
                # Mirror the blocking endpoint: a task classification lazily spawns
                # the executor so streamed dispatch behaves like /message + /tasks.
                starts_executor = (
                    result.get("kind") == "task"
                    or (
                        result.get("kind") == "pending_question"
                        and bool(result.get("resolved"))
                    )
                )
                daemon_view = _visible_daemon(sid)
                result["daemon_alive"] = daemon_view["alive"]
                result["daemon_control_available"] = daemon_view["control_available"]
                if (
                    starts_executor
                    and not result.get("daemon_alive")
                ):
                    try:
                        spawned = server_mod.start_project_daemon(
                            sid,
                            global_root=project_root,
                            resume_continuous=bool(result.get("continuous")),
                            reclaim_idle=True,
                        )
                        _record_spawn_result(result, spawned)
                    except Exception as exc:  # noqa: BLE001 — surface failure in done frame
                        result["daemon"] = {
                            "rc": 2,
                            "error": "The background worker could not start.",
                            "diagnostic": f"{type(exc).__name__}: {exc}",
                        }
                # Persist truthful dispatch acknowledgement for task results
                if result.get("kind") == "task":
                    try:
                        record_task_dispatch_ack(
                            sid, result,
                            global_root=project_root,
                            on_fragment=_on_fragment,
                        )
                    except Exception as exc:  # noqa: BLE001 — surface in done frame
                        result["ack_error"] = (
                            "The task was queued, but its confirmation could not be saved."
                        )
                        result["ack_diagnostic"] = f"{type(exc).__name__}: {exc}"
                q.put({"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                q.put({
                    "type": "error",
                    "error": "I couldn't finish handling that request.",
                    "diagnostic": f"{type(exc).__name__}: {exc}",
                })
            finally:
                q.put(None)  # sentinel: generator stops

        threading.Thread(target=_run, name=f"manager-stream-{sid}", daemon=True).start()

        def _gen():
            for item in server_mod._iter_manager_stream_items(
                q,
                heartbeat_s=server_mod._manager_stream_heartbeat_seconds(),
            ):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            _gen(),
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
