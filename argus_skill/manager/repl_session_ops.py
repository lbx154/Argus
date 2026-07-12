"""Status, role, diagnostics, planning, attach, and resume commands."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..apps._runtime import _SplitMemory
from .front_door import _ensure_manager_runner, _life_dir_for
from .repl_completion import _format_elapsed
from .repl_follow import _follow_events_stream
from .repl_ops import _daemon_log_tail

log = logging.getLogger(__name__)

def _status_cmd(
    mem: _SplitMemory,
    chat_state: dict[str, Any] | None = None,
    *,
    life_dir_for: Callable[[Any], Path] = _life_dir_for,
) -> None:
    """Lightweight status print (mirrors `argus-skill life status` output)."""
    from ..apps._inbox import count_pending_inbox_messages
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state

    # Fixed label width so every "label: value" row's colon lines up in one
    # column instead of drifting per-line (each print used to hand-pick its
    # own padding, which fell out of sync as fields were added over time).
    _LBL = 10

    identity = mem.identity.read().strip()
    if identity:
        first = identity.splitlines()[0][:80]
        print(f"{'identity':<{_LBL}}: {first}{'…' if len(identity) > 80 else ''}")
    else:
        print(f"{'identity':<{_LBL}}: (empty)")
    # Every backlog item whose mission ended "blocked" on a reviewer question
    # the operator hasn't answered yet (BacklogItem.pending_question — set by
    # life/supervisor/_core.py, cleared by enqueue_mission once answered).
    # Surfaced FIRST and unconditionally (not tucked behind a live REPL
    # session's chat_state) so it is visible after a fresh `argus` launch,
    # after a daemon-only run, or when more than one item is waiting — none
    # of which the old chat_state-only ``blocked_question`` could show.
    pending_qs = [it for it in mem.backlog.all() if (it.pending_question or "").strip()]
    if pending_qs:
        print(f"{'questions':<{_LBL}}: {len(pending_qs)} awaiting your answer")
        for it in pending_qs[:5]:
            print(f"  ❓ ({it.id}) {it.pending_question.strip()[:160]}")
        if len(pending_qs) > 5:
            print(f"  … {len(pending_qs) - 5} more")
    pending = mem.backlog.pending()
    print(f"{'backlog':<{_LBL}}: {len(pending)} pending  "
          f"({len(mem.backlog.all())} total)")
    for it in pending[:5]:
        print(f"  - {it.id} (p={it.priority}): {it.title}")
    if len(pending) > 5:
        print(f"  … {len(pending) - 5} more")
    last = mem.journal.tail(3)
    if last:
        print("recent journal:")
        for e in last:
            ts_str = datetime.fromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [{ts_str}] {e.kind} — {e.title}")
    cont = None
    if chat_state is not None:
        cont = chat_state.get("continuous_state")
    if not isinstance(cont, ContinuousConfigState):
        cont = read_continuous_state(mem.project.root)
    print(f"{'continuous':<{_LBL}}: {'on' if cont.enabled else 'off'}")
    print(f"{'inbox':<{_LBL}}: {count_pending_inbox_messages(mem.project.root)} pending")
    _SUBLBL = 11  # fits "done_reason", the longest of this nested trio
    if cont.objective:
        print(f"  {'objective':<{_SUBLBL}}: {cont.objective}")
    if cont.done_reason:
        print(f"  {'done_reason':<{_SUBLBL}}: {cont.done_reason}")
    if cont.done_at:
        print(f"  {'done_at':<{_SUBLBL}}: {cont.done_at}")
    if chat_state is not None:
        started = chat_state.get("session_started_s")
        if started is not None:
            uptime = time.monotonic() - started
            count = int(chat_state.get("mission_count", 0))
            total = float(chat_state.get("total_elapsed_s", 0.0))
            last_e = chat_state.get("last_elapsed_s")
            line = f"{'timing':<{_LBL}}: uptime {_format_elapsed(uptime)}"
            if count:
                line += (
                    f"  ·  {count} mission{'s' if count != 1 else ''}"
                    f" totaling {_format_elapsed(total)}"
                )
            if last_e is not None:
                line += f"  ·  last {_format_elapsed(last_e)}"
            print(line)
    # Background daemon status — surfaces the 7×24 worker so /status
    # answers "is anything running while I'm idle?".
    try:
        from ..apps.cli import _format_short_duration
        from ..daemon.life_worker import read_daemon_status
        ds = read_daemon_status(mem.project.root)
    except Exception:  # noqa: BLE001
        ds = None
    if ds is not None:
        if ds.alive and ds.pid is not None:
            up = _format_short_duration(ds.uptime_seconds or 0.0)
            # ds.backend is "codex" (a real CLI backend — historically named
            # after the first one supported) vs "memory" (deterministic test
            # double). It is NOT which real CLI is actually configured per
            # role (that's ARGUS_SKILL_RUNNER_BACKEND, shown correctly in
            # /roles). Printing the raw "codex" here reads as "this daemon is
            # calling the Codex CLI" even when running claude/copilot, which
            # contradicts /roles right next to it — so relabel the real-mode
            # case instead of echoing the misleading literal string.
            backend_label = (
                "memory (test)" if ds.backend == "memory" else "live — see /roles"
            )
            print(f"{'daemon':<{_LBL}}: alive (pid {ds.pid}, up {up}, "
                  f"backend {backend_label})")
        else:
            print(f"{'daemon':<{_LBL}}: not running   (start with `/daemon start`)")
            tid = chat_state.get("last_thread_id") if chat_state is not None else None
            if tid:
                print(f"{'codex':<{_LBL}}: reusing the previous session  (/reset to start fresh)")
    # Compact four-role action line. Backend/model/effort live in /roles, not in
    # every status snapshot. Fail-soft (never breaks /status).
    try:
        from ..cli.roles_status import resolve_all_roles, role_activity
        acts = role_activity(life_dir_for(mem))
        active = next((r for r in ("engineer", "reviewer", "planner", "manager")
                       if acts.get(r) and acts[r].active), None)
        cfgs = {c.role: c for c in resolve_all_roles()}
        if active and active in cfgs:
            print(f"{'roles':<{_LBL}}: ● {active} · {acts[active].label[:40]}"
                  f"   (/roles for details)")
        else:
            print(f"{'roles':<{_LBL}}: idle   (/roles for details)")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------

def _roles_cmd(
    mem: Any,
    chat_state: dict[str, Any],
    arg_text: str = "",
    *,
    life_dir_for: Callable[[Any], Path] = _life_dir_for,
) -> None:
    """`/roles` — show each role's backend / model / reasoning-effort and what
    it is doing right now. ``/roles watch`` live-refreshes until Ctrl-C."""
    theme = chat_state.get("theme")
    from ..cli.roles_status import render_roles_snapshot
    life_dir = life_dir_for(mem)

    def _daemon_right() -> str:
        try:
            from ..daemon.life_worker import read_daemon_status
            st = read_daemon_status(mem.project.root)
            if getattr(st, "alive", False) and getattr(st, "pid", None):
                s = f"● daemon {st.pid}"
                return theme.bold_green(s) if theme is not None else s
            s = "○ no daemon"
            return theme.gray(s) if theme is not None else s
        except Exception:  # noqa: BLE001
            return ""

    watch = arg_text.strip().lower() in ("watch", "-w", "--watch", "live", "-f")
    if not watch:
        width = theme.live_width() if theme is not None else 80
        print(render_roles_snapshot(life_dir, theme, width=width,
                                    header_right=_daemon_right(),
                                    show_config=True), flush=True)
        return

    # Live refresh: redraw the panel in place every ~1s until Ctrl-C. Only when
    # attached to a TTY (else fall back to a single snapshot).
    if not sys.stdout.isatty():
        width = theme.live_width() if theme is not None else 80
        print(render_roles_snapshot(life_dir, theme, width=width), flush=True)
        return
    hint = "Live · press Ctrl-C to return, then type" if theme is not None else "live · Ctrl-C to stop, then type"
    print(theme.dim(hint) if theme is not None else hint, flush=True)
    prev_lines = 0
    try:
        sys.stdout.write("\x1b[?25l")  # hide cursor during redraw
        while True:
            # Re-queried every redraw (see ``Theme.live_width``) — ``/roles
            # watch`` can sit open for a long time, well past any terminal
            # resize, and a width fixed at function entry would wrap this
            # padded header the moment it disagrees with the real terminal.
            width = theme.live_width() if theme is not None else 80
            panel = render_roles_snapshot(life_dir, theme, width=width,
                                          header_right=_daemon_right(),
                                          show_config=True)
            n = panel.count("\n") + 1
            if prev_lines:
                # cursor up, then clear from cursor to end of screen so no stale
                # (possibly wrapped) rows are left behind → no duplicate header.
                sys.stdout.write(f"\x1b[{prev_lines}A\x1b[J")
            sys.stdout.write(panel + "\n")
            sys.stdout.flush()
            prev_lines = n
            time.sleep(1.0)
    except KeyboardInterrupt:
        print()
        return
    finally:
        try:
            sys.stdout.write("\x1b[?25h")  # restore cursor
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def _doctor_cmd(mem: Any, chat_state: dict[str, Any], global_root: Any) -> None:
    """`/doctor` — diagnose why no daemon / why auto-spawn failed, with fixes."""
    theme = chat_state.get("theme")
    try:
        from ..tools.doctor import render_report, run_diagnostics

        checks = run_diagnostics(mem.project.root, global_root=global_root)
        out = render_report(checks, theme)
        out = _rewrite_cockpit_daemon_fix(out)
        tail = _recent_daemon_log_tail(mem.project.root)
        if tail:
            out = out.rstrip() + "\n\n" + tail
    except Exception as exc:  # noqa: BLE001 — doctor must never crash the REPL
        out = f"/doctor failed: {type(exc).__name__}: {exc}"
    print(out, flush=True)


def _rewrite_cockpit_daemon_fix(text: str) -> str:
    """Doctor runs inside the cockpit; prefer the cockpit-native start command."""
    return text.replace(
        "run: argus-skill --daemon",
        "run: /daemon start  (or argus-skill --daemon from another shell)",
    )


def _recent_daemon_log_tail(
    life_dir: Path | str,
    *,
    max_age_seconds: float = 900.0,
) -> str:
    path = Path(life_dir) / "daemon.log"
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return ""
    if age > max_age_seconds:
        return ""
    return _daemon_log_tail(life_dir)


def _plan_cmd(
    mem: Any,
    chat_state: dict[str, Any],
    objective: str,
    *,
    ensure_runner: Callable[[dict[str, Any], Any], Any] = _ensure_manager_runner,
    free_text_cmd: Callable[[Any, str, dict[str, Any]], None] | None = None,
) -> None:
    """`/plan <objective>` — preview a step plan, then optionally queue it.

    Codex/Claude-Code/Cursor parity: see HOW the agent would approach the work
    before anything reaches the backlog. Drafting the plan never executes work.
    """
    theme = chat_state.get("theme")
    if not objective.strip():
        msg = "usage: /plan <objective>  — preview a step plan before queuing it"
        print(theme.gray(msg) if theme is not None else msg, flush=True)
        return
    runner = ensure_runner(chat_state, mem)
    from ..cli.live_status import LiveStatus
    from ..manager import plan_mode
    with LiveStatus(
        "drafting a plan…",
        theme=theme,
        phrases=["Understanding the goal…", "Breaking down steps…", "Drafting a plan…"],
    ):
        plan = plan_mode.draft_plan(runner, objective)
    print(plan_mode.render_plan(plan, theme), flush=True)
    if getattr(plan, "error", ""):
        note = "plan was not queued because drafting failed; fix the runner or rephrase the objective and try /plan again."
        print(theme.gray(note) if theme is not None else note, flush=True)
        return
    # Ask before queuing — the whole point of a preview is approval.
    prompt = "queue this plan as a task? [y/N] "
    try:
        ans = input(theme.cyan(prompt) if theme is not None else prompt)
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans.strip().lower() in ("y", "yes"):
        if free_text_cmd is not None:
            free_text_cmd(mem, objective, chat_state)
    else:
        note = "plan not queued (nothing executed). Edit the objective and /plan again, or just type it to run."
        print(theme.gray(note) if theme is not None else note, flush=True)


def _daemons_cmd(chat_state: dict[str, Any], global_root: Any, current_root: Any) -> None:
    """`/daemons` — list every live daemon across all projects (cross-project)."""
    theme = chat_state.get("theme")
    try:
        from ..apps.cli import _format_short_duration
        from ..core.session import live_daemon_sessions
        from ..daemon.life_worker import read_daemon_status

        sessions = live_daemon_sessions(global_root)
    except Exception as exc:  # noqa: BLE001
        print(f"/daemons failed: {type(exc).__name__}: {exc}", flush=True)
        return
    if not sessions:
        msg = "no live daemons running. Start one: argus-skill --daemon"
        print(theme.gray(msg) if theme is not None else msg, flush=True)
        return
    print(theme.bold("live daemons") if theme is not None else "live daemons", flush=True)
    for s in sessions:
        proj = Path(global_root) / "projects" / s.id
        try:
            st = read_daemon_status(proj)
            up = _format_short_duration(st.uptime_seconds or 0.0)
            pid = st.pid
        except Exception:  # noqa: BLE001
            up, pid = "?", "?"
        name = s.display_name or (s.objective[:36] if s.objective else "(unnamed)")
        here = "  (this session)" if str(proj) == str(current_root) else ""
        line = f"  ● {s.id}  pid {pid}  up {up}  ·  {name}{here}"
        print(theme.green(line) if theme is not None else line, flush=True)
    tip = "attach to one:  /attach <id>   ·   or relaunch:  argus-skill --resume <id>"
    print(theme.dim(tip) if theme is not None else tip, flush=True)


def _attach_cmd(
    chat_state: dict[str, Any],
    global_root: Any,
    target: str,
    *,
    follow_events_stream: Callable[..., Any] = _follow_events_stream,
) -> None:
    """`/attach <id>` — live-follow another project's daemon (read-only tail)."""
    theme = chat_state.get("theme")
    if not target.strip():
        msg = "usage: /attach <session-id>   (see /daemons)"
        print(theme.gray(msg) if theme is not None else msg, flush=True)
        return
    target = target.strip()
    try:
        from ..core.session import live_daemon_sessions

        live = live_daemon_sessions(global_root)
    except Exception:  # noqa: BLE001
        live = []
    match = next((s.id for s in live if s.id == target), None) \
        or next((s.id for s in live if s.id.startswith(target)), None)
    if match is None:
        msg = f"no live daemon matches {target!r}. See /daemons."
        print(theme.yellow(msg) if theme is not None else msg, flush=True)
        return
    proj = Path(global_root) / "projects" / match
    print((theme.gray if theme is not None else str)(
        f"following daemon {match} (Ctrl-C to stop observing; it keeps running)…"
    ), flush=True)
    follow_events_stream(proj, theme=theme, header=None)


def _print_transcript(
    life_dir: Any, theme: Any, *, limit: int | None = None, header: str | None = None
) -> bool:
    """Print a session's saved operator↔argus conversation. Returns True if any."""
    from ..core import transcript as _transcript

    turns = _transcript.read_turns(life_dir, limit=limit)
    if not turns:
        return False
    if header:
        print(theme.bold(header) if theme is not None else header, flush=True)
    for t in turns:
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        if t.get("role") == "operator":
            tag = theme.cyan("you ›") if theme is not None else "you ›"
        else:
            tag = (theme.cyan("argus") + theme.dim(" ↳")) if theme is not None else "argus ↳"
        print(f"  {tag} {text}", flush=True)
    return True


def _resume_cmd(
    mem: Any,
    chat_state: dict[str, Any],
    global_root: Any,
    rest_text: str,
    *,
    life_dir_for: Callable[[Any], Path] = _life_dir_for,
) -> None:
    """`/resume` — switch into the PREVIOUS conversation (the most recent other
    session with saved chat). ``/resume <id>`` switches into a specific session;
    ``/resume list`` shows resumable sessions using Manager-authored metadata.

    Switching re-execs ``argus-skill --resume <id>`` (after releasing the
    singleton lock), so it is a REAL switch — session bundle, daemon
    association, cwd, banner + conversation replay — identical to relaunching
    from the shell, not a read-only preview."""
    theme = chat_state.get("theme")
    _gray = theme.gray if theme is not None else (lambda s: s)
    from ..core import transcript as _transcript
    from ..core.session import list_sessions, live_daemon_sessions

    def _projdir(sid: str) -> Path:
        return Path(global_root) / "projects" / sid

    def _label(s: Any) -> str:
        if s.display_name:
            return s.display_name
        if s.objective:
            return s.objective[:50]
        return "(unnamed)"

    try:
        sessions = list_sessions(global_root, include_empty=False)
        live = {s.id for s in live_daemon_sessions(global_root)}
    except Exception:  # noqa: BLE001
        sessions, live = [], set()

    # Current session id — excluded when defaulting to "the previous conversation".
    try:
        cur_sid = Path(life_dir_for(mem)).name if mem is not None else None
    except Exception:  # noqa: BLE001
        cur_sid = None

    def _switch_to(sid: str) -> None:
        if sid == cur_sid:
            print(_gray(f"Already in session {sid} — nothing to switch to."), flush=True)
            return
        meta = next((s for s in sessions if s.id == sid), None)
        label = _label(meta) if meta is not None else ""
        tail = f"  ·  {label}" if label and label != "(unnamed)" else ""
        # Flag the switch; the REPL loop leaves cleanly and run_manager_repl
        # re-execs `argus-skill --resume <sid>` once the singleton lock is
        # released — a real switch (daemon association + cwd + replay), not a
        # read-only preview.
        chat_state["switch_to_session"] = sid
        msg = f"↩ switching to session {sid}{tail} …"
        print((theme.cyan(msg) if theme is not None else msg), flush=True)

    def _show_list() -> None:
        if not sessions:
            print(_gray("No resumable sessions yet."), flush=True)
            return
        now = time.time()
        print(theme.bold("Resumable sessions") if theme is not None else "resumable sessions:", flush=True)
        for s in sessions[:20]:
            age = max(0.0, now - (s.last_active or 0))
            age_s = (f"{int(age // 86400)}d" if age >= 86400
                     else f"{int(age // 3600)}h" if age >= 3600
                     else f"{int(age // 60)}m")
            mark = "● live" if s.id in live else "      "
            print(_gray(f"  {mark}  {s.id}  {age_s:>4} ago  ·  {_label(s)}"), flush=True)
        print(_gray(
            "Switch into one:  /resume <id>   ·   or from the shell:  argus-skill --resume <id>"
        ), flush=True)

    target = (rest_text or "").strip()

    if target.lower() in ("list", "ls", "all"):
        _show_list()
        return

    if not target:
        # Default: switch into the PREVIOUS conversation — the most recent OTHER
        # session that actually holds a saved conversation.
        prior = next(
            (s.id for s in sessions
             if s.id != cur_sid and _transcript.has_transcript(_projdir(s.id))),
            None,
        )
        if prior is None:
            print(_gray("No previous conversation yet — `/resume list` to see all sessions."), flush=True)
            return
        _switch_to(prior)
        return

    match = next((s.id for s in sessions if s.id == target), None) \
        or next((s.id for s in sessions if s.id.startswith(target)), None)
    if match is None:
        msg = f"no session matches {target!r} — `/resume list` to see them."
        print(theme.yellow(msg) if theme is not None else msg, flush=True)
        return
    _switch_to(match)

__all__ = [
    "_attach_cmd",
    "_daemons_cmd",
    "_doctor_cmd",
    "_plan_cmd",
    "_print_transcript",
    "_recent_daemon_log_tail",
    "_resume_cmd",
    "_rewrite_cockpit_daemon_fix",
    "_roles_cmd",
    "_status_cmd",
]
