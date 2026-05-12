"""``argus-skill --watch`` — read-only live cockpit.

Tails ``events.jsonl``, ``journal.jsonl``, ``daemon.status.json``, and
``backlog.jsonl`` from the configured life-dir and renders a four-pane
``rich.Live`` layout:

  +-------------------+--------------------+
  | Current mission   | Recent events      |
  | (rounds, tokens,  | (latest 20 from    |
  |  cost, status)    |  events.jsonl)     |
  +-------------------+--------------------+
  | Journal tail      | Backlog            |
  | (last 10 entries) | (pending/running)  |
  +-------------------+--------------------+

Multiple operators can attach simultaneously — this process never
writes to anything in life-dir; it's purely a presentation layer over
the existing on-disk state.
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any


def run_watch(life_dir: Path, *, refresh_hz: float = 2.0) -> int:
    life_dir = Path(life_dir)
    if not life_dir.exists():
        print(f"watch: life-dir not found: {life_dir}", file=sys.stderr)
        return 2

    try:
        from rich.console import Console
        from rich.layout import Layout
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ModuleNotFoundError:
        print(
            "watch: rich is required for the live cockpit; install the package "
            "to use `argus-skill --watch`",
            file=sys.stderr,
        )
        return 2

    events_path = life_dir / "events.jsonl"
    journal_path = life_dir / "journal.jsonl"
    backlog_path = life_dir / "backlog.jsonl"
    status_path = life_dir / "daemon.status.json"

    console = Console()
    refresh = max(1, int(refresh_hz))

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=1),
        Layout(name="top", size=14),
        Layout(name="bottom"),
    )
    layout["top"].split_row(Layout(name="mission"), Layout(name="events"))
    layout["bottom"].split_row(Layout(name="journal"), Layout(name="backlog"))

    events_buf: deque[dict[str, Any]] = deque(maxlen=20)
    events_offset = 0

    def _read_status() -> dict[str, Any]:
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_journal_tail(n: int = 10) -> list[dict[str, Any]]:
        try:
            with journal_path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                tail = min(size, 32 * 1024)
                fh.seek(size - tail)
                blob = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        rows = []
        for line in blob.splitlines()[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _read_backlog() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with backlog_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return rows

    def _drain_events() -> None:
        nonlocal events_offset
        try:
            with events_path.open("rb") as fh:
                fh.seek(events_offset)
                chunk = fh.read()
                events_offset = fh.tell()
        except OSError:
            return
        for raw in chunk.splitlines():
            if not raw:
                continue
            try:
                events_buf.append(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

    def _mission_panel() -> Panel:
        # Mine the latest engineer/review event for live mission state.
        rounds = 0
        tokens_in = 0
        tokens_out = 0
        last_status = "idle"
        last_item_id = ""
        for ev in events_buf:
            t = ev.get("type", "")
            if t == "round.start":
                rounds = max(rounds, int(ev.get("round", 0) or 0))
            if t in ("round.main.completed", "round.review.completed"):
                tokens_in += int(ev.get("input_tokens", 0) or 0)
                tokens_out += int(ev.get("output_tokens", 0) or 0)
            if t == "life.mission.started":
                last_status = "running"
                last_item_id = str(ev.get("item_id", ""))[:12]
                rounds = 0
                tokens_in = 0
                tokens_out = 0
            if t == "life.mission.completed":
                last_status = "done" if ev.get("success") else "failed"
        body = Table.grid(padding=(0, 1))
        body.add_column(style="bold cyan")
        body.add_column()
        body.add_row("status", last_status)
        body.add_row("item", last_item_id or "-")
        body.add_row("rounds", str(rounds))
        body.add_row("tokens_in", f"{tokens_in:,}")
        body.add_row("tokens_out", f"{tokens_out:,}")
        return Panel(body, title="Current mission", border_style="cyan")

    def _events_panel() -> Panel:
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column(style="dim", width=8)
        tbl.add_column(style="bold yellow", width=24, no_wrap=True)
        tbl.add_column()
        for ev in list(events_buf)[-12:]:
            ts = ev.get("ts", time.time())
            try:
                ts_s = time.strftime("%H:%M:%S", time.localtime(float(ts)))
            except (TypeError, ValueError):
                ts_s = "?"
            t = str(ev.get("type", "?"))[:24]
            text = (ev.get("text") or ev.get("title") or ev.get("reason") or "")
            text = str(text)[:120].replace("\n", " ")
            tbl.add_row(ts_s, t, text)
        return Panel(tbl, title="Events (latest)", border_style="yellow")

    def _journal_panel() -> Panel:
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column(style="dim", width=8)
        tbl.add_column(style="bold magenta", width=18, no_wrap=True)
        tbl.add_column(width=10, justify="right")
        tbl.add_column()
        for row in _read_journal_tail(10):
            ts = row.get("ts", 0)
            try:
                ts_s = time.strftime("%H:%M:%S", time.localtime(float(ts)))
            except (TypeError, ValueError):
                ts_s = "?"
            kind = str(row.get("kind", "?"))
            cost = float(row.get("cost_usd", 0.0) or 0.0)
            title = str(row.get("title", ""))[:80]
            tbl.add_row(ts_s, kind, f"${cost:.4f}", title)
        return Panel(tbl, title="Journal (latest)", border_style="magenta")

    def _backlog_panel() -> Panel:
        rows = _read_backlog()
        pending = [r for r in rows if r.get("status") == "pending"]
        running = [r for r in rows if r.get("status") == "running"]
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column(style="bold green", width=10)
        tbl.add_column(style="dim", width=14, no_wrap=True)
        tbl.add_column()
        for r in running:
            tbl.add_row("running", str(r.get("id", ""))[:12], str(r.get("title", ""))[:80])
        for r in pending[:8]:
            tbl.add_row("pending", str(r.get("id", ""))[:12], str(r.get("title", ""))[:80])
        if not pending and not running:
            tbl.add_row("", "", "(empty)")
        return Panel(tbl, title="Backlog", border_style="green")

    def _render() -> Layout:
        _drain_events()
        layout["mission"].update(_mission_panel())
        layout["events"].update(_events_panel())
        layout["journal"].update(_journal_panel())
        layout["backlog"].update(_backlog_panel())
        st = _read_status()
        alive = st.get("alive", False)
        pid = st.get("pid", "-")
        backend = st.get("backend", "-")
        header = Text.from_markup(
            f"[bold]argus-skill watch[/bold]  [cyan]life-dir[/cyan]={life_dir}  "
            f"[cyan]daemon[/cyan]={'[green]alive[/green]' if alive else '[red]down[/red]'}  "
            f"pid={pid}  backend={backend}  [dim](Ctrl-C to exit)[/dim]"
        )
        layout["header"].update(header)
        return layout

    try:
        with Live(_render(), refresh_per_second=refresh, console=console, screen=False) as live:
            while True:
                time.sleep(1.0 / refresh)
                live.update(_render())
    except KeyboardInterrupt:
        return 0


__all__ = ["run_watch"]
