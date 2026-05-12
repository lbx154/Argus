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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _path_signature(path: Path) -> tuple[int, int, int, int] | None:
    """Return a cheap fingerprint for a file's current on-disk state."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        int(stat.st_mtime_ns),
        int(stat.st_size),
        int(getattr(stat, "st_dev", 0) or 0),
        int(getattr(stat, "st_ino", 0) or 0),
    )


def _read_jsonl_since(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            blob = fh.read()
            new_offset = fh.tell()
    except OSError:
        return rows, offset
    for raw in blob.splitlines():
        if not raw:
            continue
        try:
            rows.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return rows, new_offset


@dataclass
class _MissionState:
    status: str = "idle"
    item_id: str = ""
    rounds: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def apply(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "life.mission.started":
            self.status = "running"
            self.item_id = str(event.get("item_id", ""))[:12]
            self.rounds = 0
            self.tokens_in = 0
            self.tokens_out = 0
            return
        if event_type in {"round.start", "round.started"}:
            raw_round = event.get("round_index", event.get("round", 0))
            try:
                round_no = int(raw_round or 0)
            except (TypeError, ValueError):
                round_no = 0
            self.rounds = max(self.rounds, round_no)
            return
        if event_type in {"round.main.completed", "round.review.completed"}:
            self.tokens_in += int(event.get("input_tokens", 0) or 0)
            self.tokens_out += int(event.get("output_tokens", 0) or 0)
            return
        if event_type == "life.mission.completed":
            self.status = "done" if event.get("success") else "failed"


@dataclass
class _PathTail:
    signature: tuple[int, int, int, int] | None = None
    offset: int = 0

    def sync(self, path: Path) -> bool:
        signature = _path_signature(path)
        if signature is None:
            return False
        if self.signature is None:
            self.signature = signature
            self.offset = 0
            return True
        if signature != self.signature:
            self.signature = signature
            self.offset = 0
            return True
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size < self.offset:
            self.offset = 0
        return True

    def read(self, path: Path) -> list[dict[str, Any]]:
        if not self.sync(path):
            return []
        rows, self.offset = _read_jsonl_since(path, self.offset)
        return rows


@dataclass
class _WatchState:
    events_path: Path
    roll_path: Path
    mission: _MissionState = field(default_factory=_MissionState)
    recent_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))
    _current: _PathTail = field(default_factory=_PathTail)
    _roll: _PathTail = field(default_factory=_PathTail)

    def drain(self) -> None:
        current_sig = _path_signature(self.events_path)
        roll_sig = _path_signature(self.roll_path)
        previous_current_sig = self._current.signature
        previous_current_offset = self._current.offset

        if current_sig is not None:
            if previous_current_sig is None:
                self._current.signature = current_sig
                self._current.offset = 0
            elif current_sig != previous_current_sig:
                if roll_sig is not None and roll_sig == previous_current_sig:
                    self._roll.signature = previous_current_sig
                    self._roll.offset = previous_current_offset
                self._current.signature = current_sig
                self._current.offset = 0
            else:
                try:
                    current_size = self.events_path.stat().st_size
                except OSError:
                    current_size = None
                if current_size is not None and current_size < self._current.offset:
                    self._current.offset = 0

        if roll_sig is not None:
            if self._roll.signature is None:
                self._roll.signature = roll_sig
                self._roll.offset = 0
            elif roll_sig != self._roll.signature:
                if roll_sig != previous_current_sig:
                    self._roll.signature = roll_sig
                    self._roll.offset = 0

        for event in self._roll.read(self.roll_path):
            self.recent_events.append(event)
            self.mission.apply(event)
        for event in self._current.read(self.events_path):
            self.recent_events.append(event)
            self.mission.apply(event)


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

    state = _WatchState(events_path=events_path, roll_path=life_dir / "events.jsonl.1")

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

    def _mission_panel() -> Panel:
        mission = state.mission
        body = Table.grid(padding=(0, 1))
        body.add_column(style="bold cyan")
        body.add_column()
        body.add_row("status", mission.status)
        body.add_row("item", mission.item_id or "-")
        body.add_row("rounds", str(mission.rounds))
        body.add_row("tokens_in", f"{mission.tokens_in:,}")
        body.add_row("tokens_out", f"{mission.tokens_out:,}")
        return Panel(body, title="Current mission", border_style="cyan")

    def _events_panel() -> Panel:
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column(style="dim", width=8)
        tbl.add_column(style="bold yellow", width=24, no_wrap=True)
        tbl.add_column()
        for ev in list(state.recent_events)[-12:]:
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
        state.drain()
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
