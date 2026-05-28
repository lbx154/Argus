from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class DashboardEvent:
    id: int
    ts: str
    type: str
    data: dict[str, Any]


class DashboardStore:
    def __init__(self, objective: str, max_events: int = 2000) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._events: deque[DashboardEvent] = deque(maxlen=max_events)
        self._state: dict[str, Any] = {
            "objective": objective,
            "status": "idle",
            "session_id": None,
            "current_round": 0,
            "stop_reason": None,
            "success": None,
            "plan_summary": None,
            "plan_next_objective": None,
            "plan_report_markdown": None,
            "updated_at": self._now(),
        }

    def add_stream_line(self, stream: str, line: str) -> None:
        self._push_event("stream.line", {"stream": stream, "line": line})

    def apply_loop_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", "unknown"))
        with self._lock:
            if event_type == "loop.started":
                self._state["status"] = "running"
                self._state["session_id"] = event.get("session_id")
                self._state["current_round"] = 0
                self._state["stop_reason"] = None
                self._state["success"] = None
            elif event_type == "round.started":
                self._state["current_round"] = event.get("round_index", self._state["current_round"])
                if event.get("session_id"):
                    self._state["session_id"] = event.get("session_id")
            elif event_type == "round.main.completed":
                if event.get("session_id"):
                    self._state["session_id"] = event.get("session_id")
            elif event_type in {"plan.updated", "plan.finalized"}:
                self._state["plan_summary"] = event.get("summary")
                self._state["plan_next_objective"] = event.get("suggested_next_objective")
                self._state["plan_report_markdown"] = event.get("report_markdown")
            elif event_type == "loop.completed":
                self._state["status"] = "completed"
                self._state["success"] = bool(event.get("success"))
                self._state["stop_reason"] = event.get("stop_reason")
            self._state["updated_at"] = self._now()
            self._append_event_locked(event_type, event)

    def mark_server_started(self, host: str, port: int) -> None:
        self._push_event("dashboard.started", {"url": f"http://{host}:{port}"})

    def mark_server_stopped(self) -> None:
        with self._lock:
            if self._state.get("status") == "running":
                self._state["status"] = "stopped"
            self._state["updated_at"] = self._now()
            self._append_event_locked("dashboard.stopped", {})

    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def events_after(self, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            output: list[dict[str, Any]] = []
            for item in self._events:
                if item.id > after_id:
                    output.append(
                        {
                            "id": item.id,
                            "ts": item.ts,
                            "type": item.type,
                            "data": item.data,
                        }
                    )
                    if len(output) >= limit:
                        break
            return output

    def latest_event_id(self) -> int:
        with self._lock:
            if not self._events:
                return 0
            return self._events[-1].id

    def _push_event(self, event_type: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._state["updated_at"] = self._now()
            self._append_event_locked(event_type, data)

    def _append_event_locked(self, event_type: str, data: dict[str, Any]) -> None:
        event = DashboardEvent(id=self._next_id, ts=self._now(), type=event_type, data=data)
        self._next_id += 1
        self._events.append(event)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


class DashboardServer:
    def __init__(self, store: DashboardStore, host: str, port: int) -> None:
        self.store = store
        self.host = host
        self.port = port
        self._server = ThreadingHTTPServer((host, port), _make_handler(store))
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.store.mark_server_started(self.host, self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.store.mark_server_stopped()


def _make_handler(store: DashboardStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/" or parsed.path == "/index.html":
                self._write_text(200, _INDEX_HTML, content_type="text/html; charset=utf-8")
                return
            if parsed.path == "/api/state":
                self._write_json(200, store.state_snapshot())
                return
            if parsed.path == "/api/events":
                params = parse_qs(parsed.query)
                after_raw = params.get("after", ["0"])[0]
                limit_raw = params.get("limit", ["200"])[0]
                try:
                    after = int(after_raw)
                except ValueError:
                    after = 0
                try:
                    limit = int(limit_raw)
                except ValueError:
                    limit = 200
                limit = max(1, min(limit, 500))
                events = store.events_after(after_id=max(after, 0), limit=limit)
                self._write_json(
                    200,
                    {
                        "events": events,
                        "next_after": store.latest_event_id(),
                        "server_time": datetime.now(timezone.utc).isoformat(),
                    },
                )
                return
            if parsed.path == "/healthz":
                self._write_json(200, {"ok": True, "ts": time.time()})
                return
            self._write_json(404, {"error": "not_found"})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _write_text(self, code: int, content: str, content_type: str) -> None:
            body = content.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Codex AutoLoop Dashboard</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --panel2: #1f2937;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --ok: #22c55e;
      --warn: #f59e0b;
      --bad: #ef4444;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: "IBM Plex Sans", "Noto Sans", system-ui, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      background: radial-gradient(circle at 10% 10%, #1e293b 0%, var(--bg) 45%);
      font-family: var(--sans);
    }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 18px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit,minmax(260px,1fr));
      gap: 12px;
      margin-bottom: 12px;
    }
    .card {
      background: linear-gradient(180deg, rgba(31,41,55,0.95), rgba(17,24,39,0.95));
      border: 1px solid rgba(148,163,184,0.25);
      border-radius: 12px;
      padding: 12px;
      min-height: 90px;
    }
    .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
    .value { margin-top: 6px; font-size: 15px; word-break: break-word; }
    .value.status-running { color: var(--warn); }
    .value.status-completed { color: var(--ok); }
    .value.status-stopped { color: var(--bad); }
    .events {
      background: rgba(15,23,42,0.7);
      border: 1px solid rgba(148,163,184,0.3);
      border-radius: 12px;
      height: 66vh;
      overflow: auto;
      padding: 10px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
    }
    .row { padding: 5px 4px; border-bottom: 1px dashed rgba(148,163,184,0.18); }
    .ts { color: #93c5fd; }
    .type { color: #fda4af; }
    .muted { color: var(--muted); }
    .line { white-space: pre-wrap; }
    .plan {
      margin-bottom: 12px;
      background: rgba(15,23,42,0.7);
      border: 1px solid rgba(148,163,184,0.3);
      border-radius: 12px;
      padding: 12px;
    }
    .plan pre {
      margin: 0;
      white-space: pre-wrap;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="grid">
      <div class="card"><div class="label">Status</div><div id="status" class="value">-</div></div>
      <div class="card"><div class="label">Session ID</div><div id="session_id" class="value">-</div></div>
      <div class="card"><div class="label">Current Round</div><div id="current_round" class="value">0</div></div>
      <div class="card"><div class="label">Success</div><div id="success" class="value">-</div></div>
      <div class="card"><div class="label">Updated At (UTC)</div><div id="updated_at" class="value">-</div></div>
      <div class="card"><div class="label">Stop Reason</div><div id="stop_reason" class="value">-</div></div>
      <div class="card" style="grid-column: 1 / -1;"><div class="label">Objective</div><div id="objective" class="value">-</div></div>
    </div>
    <div class="plan">
      <div class="label">Planning Snapshot</div>
      <div id="plan_summary" class="value">-</div>
      <div class="label" style="margin-top: 10px;">Suggested Next Objective</div>
      <div id="plan_next_objective" class="value">-</div>
      <div class="label" style="margin-top: 10px;">Planner Report</div>
      <pre id="plan_report_markdown">-</pre>
    </div>
    <div class="events" id="events"></div>
  </div>
  <script>
    let after = 0;
    function esc(input) {
      return String(input)
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;");
    }
    async function refreshState() {
      const r = await fetch('/api/state', {cache: 'no-store'});
      const s = await r.json();
      const statusEl = document.getElementById('status');
      statusEl.textContent = s.status ?? '-';
      statusEl.className = 'value status-' + (s.status ?? '');
      document.getElementById('session_id').textContent = s.session_id ?? '-';
      document.getElementById('current_round').textContent = String(s.current_round ?? 0);
      document.getElementById('success').textContent = s.success === null ? '-' : String(s.success);
      document.getElementById('updated_at').textContent = s.updated_at ?? '-';
      document.getElementById('stop_reason').textContent = s.stop_reason ?? '-';
      document.getElementById('objective').textContent = s.objective ?? '-';
      document.getElementById('plan_summary').textContent = s.plan_summary ?? '-';
      document.getElementById('plan_next_objective').textContent = s.plan_next_objective ?? '-';
      document.getElementById('plan_report_markdown').textContent = s.plan_report_markdown ?? '-';
    }
    function renderEvent(evt) {
      const data = evt.data || {};
      if (evt.type === 'stream.line') {
        return `<div class="row"><span class="ts">${esc(evt.ts)}</span> <span class="type">${esc(evt.type)}</span> <span class="muted">(${esc(data.stream || '')})</span><div class="line">${esc(data.line || '')}</div></div>`;
      }
      return `<div class="row"><span class="ts">${esc(evt.ts)}</span> <span class="type">${esc(evt.type)}</span><div class="line">${esc(JSON.stringify(data))}</div></div>`;
    }
    async function refreshEvents() {
      const r = await fetch('/api/events?after=' + after + '&limit=200', {cache: 'no-store'});
      const payload = await r.json();
      const events = payload.events || [];
      const container = document.getElementById('events');
      if (events.length > 0) {
        const nearBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 60;
        for (const evt of events) {
          container.insertAdjacentHTML('beforeend', renderEvent(evt));
          after = Math.max(after, Number(evt.id || 0));
        }
        if (container.childElementCount > 3000) {
          while (container.childElementCount > 2500) {
            container.removeChild(container.firstElementChild);
          }
        }
        if (nearBottom) {
          container.scrollTop = container.scrollHeight;
        }
      }
    }
    async function tick() {
      try {
        await Promise.all([refreshState(), refreshEvents()]);
      } catch (err) {
        console.error(err);
      }
    }
    setInterval(tick, 1000);
    tick();
  </script>
</body>
</html>
"""
