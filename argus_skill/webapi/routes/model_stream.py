"""Shared heartbeat/progress/result stream for explicit model requests."""

from __future__ import annotations

import json
import logging
import queue
import threading

from fastapi import HTTPException
from starlette.responses import StreamingResponse


def model_stream_response(generate, *, thread_name: str, unavailable_message: str):
    """Run ``generate(on_progress)`` once and preserve its result on disconnect."""
    from .. import server

    items: queue.Queue[dict | None] = queue.Queue()
    items.put({"type": "heartbeat", "quiet_s": 0})

    def run():
        try:
            result = generate(lambda phase: items.put({"type": "progress", "phase": phase}))
            items.put({"type": "done", "result": result})
        except HTTPException as exc:
            items.put({"type": "error", "error": exc.detail, "status": exc.status_code})
        except Exception:  # noqa: BLE001 — a terminal frame is required after headers
            logging.getLogger(__name__).exception("%s failed", thread_name)
            items.put({"type": "error", "error": unavailable_message, "status": 500})
        finally:
            items.put(None)

    threading.Thread(target=run, name=thread_name, daemon=True).start()

    def frames():
        for item in server._iter_manager_stream_items(
            items, heartbeat_s=server._manager_stream_heartbeat_seconds() or 5.0,
        ):
            if item.get("heartbeat"):
                item = {"type": "heartbeat", "quiet_s": item["quiet_s"]}
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        frames(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
