"""``argus-skill --watch`` — read-only live cockpit.

Tails the current project's ``events.jsonl``, ``daemon.status.json``,
and ``backlog.jsonl`` while reading the shared global ``journal.jsonl``
and renders a four-pane ``rich.Live`` layout:

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
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from ..daemon.life_worker import read_continuous_state, read_daemon_status, resolve_effective_budget
from ..life.status import describe_continuous_state, select_current_running_item
from ._inbox import count_pending_inbox_messages, format_inbox_event


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


def _clean_text(text: str, *, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _read_backlog_rows(backlog_path: Path) -> list[dict[str, Any]]:
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


def _select_current_backlog_row(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    proxies = [SimpleNamespace(**row) for row in rows if isinstance(row, dict)]
    current = select_current_running_item(proxies)
    if current is None:
        return None
    return dict(vars(current))


def _mission_context_lines(
    *,
    mission: _MissionState,
    current_row: dict[str, Any] | None,
    continuous: Any,
) -> list[tuple[str, str]]:
    cont = describe_continuous_state(continuous)
    current_id = _clean_text(str((current_row or {}).get("id", "")), limit=18) or "-"
    current_title = _clean_text(str((current_row or {}).get("title", "")), limit=60) or "-"
    current_objective = _clean_text(
        str((current_row or {}).get("objective", "")),
        limit=120,
    ) or "-"
    return [
        ("status", mission.status),
        ("item", current_id),
        ("title", current_title),
        ("objective", current_objective),
        ("continuous", "on" if cont.enabled else ("done" if cont.is_completed else "off")),
        ("continuous objective", _clean_text(cont.objective, limit=120) or "-"),
        ("done_reason", _clean_text(cont.done_reason, limit=120) or "-"),
        ("done_at", cont.done_at or "-"),
        ("rounds", str(mission.rounds)),
        ("tokens_in", f"{mission.tokens_in:,}"),
        ("tokens_out", f"{mission.tokens_out:,}"),
    ]


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
class _BudgetLineCache:
    """Cache the rendered budget line until its inputs change."""

    signature: tuple[tuple[int, int, int, int] | None, float, float] | None = None
    line: str = ""

    def render(self, *, journal_path: Path, journal: Any, status: Any) -> str:
        budget = resolve_effective_budget(status)
        signature = (
            _path_signature(journal_path),
            budget.per_mission_cap_usd,
            budget.daily_cap_usd,
        )
        if signature != self.signature:
            self.signature = signature
            remaining = budget.remaining_today(journal)
            tail = " (paused)" if remaining <= 0 else ""
            self.line = (
                "budget   : "
                f"per-mission ${budget.per_mission_cap_usd:.2f} · "
                f"daily ${budget.daily_cap_usd:.2f} · "
                f"remaining ${remaining:.2f}{tail}"
            )
        return self.line


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


def run_watch(life: Any, *, refresh_hz: float = 2.0) -> int:
    if hasattr(life, "project") and hasattr(life, "global_root"):
        bundle = life  # MemoryBundle-like
        project_root = Path(getattr(bundle.project, "root"))
        global_root = Path(getattr(bundle, "global_root"))
        # Split memory: the project journal is memory.jsonl (no global log).
        journal_file = project_root / "memory.jsonl"
    else:
        bundle = None
        project_root = Path(life)
        global_root = project_root
        # Legacy single-project facade keeps its journal at journal.jsonl.
        journal_file = project_root / "journal.jsonl"
    if not project_root.exists():
        print(f"watch: life-dir not found: {project_root}", file=sys.stderr)
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

    events_path = project_root / "events.jsonl"
    journal_path = journal_file
    backlog_path = project_root / "backlog.jsonl"

    console = Console()
    refresh = max(1, int(refresh_hz))

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="top", size=14),
        Layout(name="bottom"),
    )
    layout["top"].split_row(Layout(name="mission"), Layout(name="events"))
    layout["bottom"].split_row(Layout(name="journal"), Layout(name="backlog"))

    state = _WatchState(events_path=events_path, roll_path=project_root / "events.jsonl.1")

    journal = getattr(bundle, "journal", None)
    if journal is None:
        from ..life.memory import Journal

        journal = Journal(journal_path)
    budget_cache = _BudgetLineCache()
    plain_console = None if sys.stdout.isatty() else Console(force_terminal=False, color_system=None)

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

    def _mission_panel() -> Panel:
        mission = state.mission
        rows = _read_backlog_rows(backlog_path)
        current_row = _select_current_backlog_row(rows)
        continuous = read_continuous_state(project_root)
        body = Table.grid(padding=(0, 1))
        body.add_column(style="bold cyan")
        body.add_column()
        for label, value in _mission_context_lines(
            mission=mission,
            current_row=current_row,
            continuous=continuous,
        ):
            body.add_row(label, value)
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
            inbox_text = format_inbox_event(ev) if isinstance(ev, dict) else None
            text = inbox_text or (ev.get("text") or ev.get("title") or ev.get("reason") or "")
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
        rows = _read_backlog_rows(backlog_path)
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
        st = read_daemon_status(project_root)
        alive = st.alive
        pid = st.pid if st.alive and st.pid is not None else "-"
        backend = st.backend if st.alive and st.backend else "-"
        inbox_pending = count_pending_inbox_messages(project_root)
        budget_line = budget_cache.render(journal_path=journal_path, journal=journal, status=st)
        header = Text.from_markup(
            f"[bold]argus-skill watch[/bold]  [cyan]global[/cyan]={global_root}\n"
            f"[cyan]project[/cyan]={project_root}  "
            f"[cyan]daemon[/cyan]={'[green]alive[/green]' if alive else '[red]down[/red]'}  "
            f"pid={pid}  backend={backend}\n"
            f"{budget_line}\n"
            f"[cyan]inbox[/cyan]={inbox_pending} pending  [dim](Ctrl-C to exit)[/dim]"
        )
        layout["header"].update(header)
        return layout

    def _print_snapshot() -> None:
        if plain_console is None:
            return
        with plain_console.capture() as capture:
            plain_console.print(_render())
        sys.stdout.write(capture.get())
        sys.stdout.flush()

    def _raise_keyboard_interrupt(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    previous_handlers: dict[int, Any] = {}
    try:
        for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if signum is None:
                continue
            previous_handlers[int(signum)] = signal.getsignal(signum)
            signal.signal(signum, _raise_keyboard_interrupt)
        if plain_console is None:
            with Live(_render(), refresh_per_second=refresh, console=console, screen=False) as live:
                while True:
                    time.sleep(1.0 / refresh)
                    live.update(_render())
        else:
            while True:
                _print_snapshot()
                time.sleep(1.0 / refresh)
    except KeyboardInterrupt:
        return 0
    finally:
        for signum, previous in previous_handlers.items():
            try:
                signal.signal(signum, previous)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["run_watch"]
