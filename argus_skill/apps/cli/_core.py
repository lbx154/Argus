"""argus-skill CLI — single-entry 7×24 lifetime agent.

The product has exactly one positioning: a long-running supervised
coding agent that drains a backlog forever. There is therefore exactly
one entry point — ``argus-skill`` — which:

* launches the Ink cockpit, and
* by default ensures a detached daemon is alive draining the backlog
  in the background even after you log out.

Top-level flags control daemon lifecycle and read-only operator help
(``--daemon``, ``--daemon-fg``, ``--daemon-stop``, ``--status``,
``--daemon-runbook``, ``--no-daemon``). The only subcommand is a small
admin helper for explicitly bootstrapping and backfilling per-project
idea wikis: ``argus-skill wiki init <project>`` and
``argus-skill wiki ingest --wiki <path>``. The cockpit and backlog remain the
single runtime workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ...core import paths as core_paths
from ...life.status import count_backlog_statuses, select_current_running_item
from .._inbox import count_pending_inbox_messages, queue_inbox_message
from .._target_paths import resolve_life_root
from ._follow import (
    _clean_follow_text,
    _follow_layer_from_event,
    _format_daemon_activity_status_lines,
    _format_follow_event,
    _format_follow_heartbeat,
    _format_telemetry_status_lines,
    _read_backlog_rows,
    _resolve_follow_events_path,
    _select_backlog_row_by_id,
)
from ._parser import build_parser


def _continuous_contract_error(
    *,
    continuous: bool,
    objective: str,
    backend: str,
) -> str:
    from ...daemon.life_worker import continuous_mode_error
    return continuous_mode_error(backend, continuous, objective)


def _resolve_global_root(args: argparse.Namespace) -> Path:
    return resolve_life_root(args.life_dir)


def _session_mode(args: argparse.Namespace) -> tuple[str, str | None]:
    """Map the --new/--resume/--continue flags to (mode, session_id|None)."""
    if getattr(args, "continue_session", False):
        return "continue", None
    resume = getattr(args, "resume", None)
    if resume is not None:  # --resume given (with or without an id)
        return "resume", (resume or None)
    return "new", None


def _pick_session(global_root: Path) -> str | None:
    """Interactive picker of recent sessions for a bare ``--resume``.

    Returns the chosen session id, or None if the user aborts / none exist.
    """
    from ...core.session import list_sessions, live_daemon_sessions

    sessions = list_sessions(global_root, include_empty=False)
    if not sessions:
        sys.stderr.write("argus-skill: no previous sessions to resume.\n")
        return None
    live_ids = {s.id for s in live_daemon_sessions(global_root)}
    now = time.time()
    sys.stdout.write("Resume which session?\n")
    for i, s in enumerate(sessions[:20], 1):
        age = max(0, now - (s.last_active or 0))
        age_s = (f"{int(age // 86400)}d" if age >= 86400
                 else f"{int(age // 3600)}h" if age >= 3600
                 else f"{int(age // 60)}m")
        name = s.display_name or (s.objective[:40] if s.objective else "(unnamed)")
        mark = "● live" if s.id in live_ids else "      "
        sys.stdout.write(f"  {i:>2}. {mark}  {s.id}  {age_s:>4} ago  ·  {name}\n")
    try:
        raw = input("  number (or id, blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx].id
        sys.stderr.write("argus-skill: out of range.\n")
        return None
    return raw  # treat as a session id


def _resolve_session_id(
    args: argparse.Namespace, global_root: Path, *, default_to_new: bool
) -> tuple[str | None, bool]:
    """Resolve the session id from flags. Returns (session_id, is_new).

    With NO session flag: the cockpit / daemon-start (``default_to_new=True``)
    opens a FRESH session; management commands (``default_to_new=False``) return
    (None, False) so the caller keeps the legacy cwd identity — unchanged.
    """
    from ...core.session import SessionResolutionError, resolve_session

    explicit = (
        bool(getattr(args, "new", False))
        or bool(getattr(args, "continue_session", False))
        or getattr(args, "resume", None) is not None
    )
    mode, sid = _session_mode(args)
    if not explicit:
        if not default_to_new:
            return None, False  # legacy cwd identity
        mode, sid = "new", None
    if mode == "resume" and not sid:
        sid = _pick_session(global_root)
        if not sid:
            return None, False
    try:
        return resolve_session(global_root=global_root, mode=mode,
                               session_id=sid, cwd=Path.cwd())
    except SessionResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return None, False


def _resolve_project_bundle(args: argparse.Namespace):
    from ...life import MemoryBundle

    global_root = _resolve_global_root(args)
    # Management commands keep the legacy cwd identity unless an explicit session
    # flag (--resume/--continue) is given.
    sid, _is_new = _resolve_session_id(args, global_root, default_to_new=False)
    if sid is None:
        return MemoryBundle.for_cwd(Path.cwd(), global_root=global_root)
    from ...core.session import (
        SessionMeta,
        read_session_meta,
        resolve_session_workdir,
        write_session_meta,
    )

    state_dir = Path(global_root) / "projects" / sid
    meta = read_session_meta(global_root, sid)
    if meta is None:
        # Legacy cwd-fingerprint projects had no session.json. The explicit
        # resume cwd is their only trustworthy worktree signal; persist it once
        # so every later CLI/Web role uses the same root.
        workdir = Path.cwd().resolve()
        meta = SessionMeta(
            id=sid,
            cwd=str(workdir),
            workdir=str(workdir),
        )
        write_session_meta(global_root, meta)
    try:
        workdir = resolve_session_workdir(meta, state_dir=state_dir)
    except (OSError, RuntimeError) as exc:
        raise core_paths.PathResolutionError(
            f"session {sid} workdir is unavailable: {exc}"
        ) from exc
    return MemoryBundle.for_cwd(
        workdir,
        global_root=global_root,
        fingerprint=sid,
    )


def _lifetime_entry_error(args: argparse.Namespace) -> str:
    """Return an actionable error if the lifetime agent is under-configured.

    The lifetime daemon / cockpit requires trusted machine house rules, but it
    may start without an objective. The first substantive user prompt is routed
    through the Manager, which decides BOUNDED versus STANDING and authors the
    persisted execution objective for a standing campaign.
    """
    from ...life.special_prompts import describe_special_prompt_gate

    ok, detail = describe_special_prompt_gate()
    if not ok:
        return detail
    return ""



_FOLLOW_HEARTBEAT_SECONDS = 20.0
































def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.skill_stats = bool(args.skill_stats or args.skill_stats_json)
    from ...core.knobs import resolve_role_backend

    backend_default = resolve_role_backend("")
    continuous_error = _continuous_contract_error(
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2

    # ---- mutual exclusion -----------------------------------------
    # Action-style flags pick exactly one mission; --no-daemon and
    # --life-dir are modifiers and may combine with any of them.
    action_flags = (
        bool(args.daemon)
        + bool(args.daemon_fg)
        + bool(args.daemon_stop)
        + bool(args.status)
        + bool(args.daemon_runbook)
        + bool(getattr(args, "config_help", False))
        + bool(getattr(args, "config_snapshot", None))
        + bool(getattr(args, "gc", False))
        + bool(args.watch)
        + bool(args.follow)
        + bool(getattr(args, "dashboard", False))
        + bool(getattr(args, "web", False))
        + bool(args.notify)
        + bool(args.init_identity)
        + bool(args.setup)
        + bool(args.model_api_status)
        + bool(args.init_model_api)
        + bool(args.skill_stats)
        + bool(args.skill_cleanse)
        + bool(args.export_builtin_skills is not None)
        + bool(args.evidence_chain_check)
        + bool(args.anti_mediocrity_check)
        + bool(args.lifecycle_status)
        + bool(args.lifecycle_resume)
        + bool(args.lifecycle_archive)
        + bool(getattr(args, "command", None))
    )
    if action_flags > 1:
        sys.stderr.write(
            "argus-skill: --daemon / --daemon-fg / --daemon-stop / --status / "
            "--daemon-runbook / --config-help / --config-snapshot / "
            "--watch / --follow / --notify / --init-identity / "
            "--model-api-status / --init-model-api / --skill-stats / "
            "--skill-cleanse / --export-builtin-skills / "
            "--evidence-chain-check / --anti-mediocrity-check / --lifecycle-status / "
            "wiki subcommands "
            "are mutually exclusive.\n"
        )
        return 2
    if args.command == "wiki" and args.wiki_cmd == "init":
        return _run_with_path_resolution_errors(lambda: _cmd_wiki_init(args))
    if args.command == "wiki" and args.wiki_cmd == "ingest":
        return _run_with_path_resolution_errors(lambda: _cmd_wiki_ingest(args))
    if args.command == "wiki" and args.wiki_cmd == "migrate":
        return _run_with_path_resolution_errors(lambda: _cmd_wiki_migrate(args))
    if args.command == "query":
        return _run_with_path_resolution_errors(lambda: _cmd_query(args))
    if args.command == "learn":
        return _run_with_path_resolution_errors(lambda: _cmd_learn(args))
    if args.command == "report":
        from ...tools.research_report import run_report

        return _run_with_path_resolution_errors(lambda: run_report(args))
    if args.daemon:
        return _run_with_path_resolution_errors(
            lambda: _cmd_daemon_start(args, foreground=False)
        )
    if args.daemon_fg:
        return _run_with_path_resolution_errors(
            lambda: _cmd_daemon_start(args, foreground=True)
        )
    if args.daemon_stop:
        return _run_with_path_resolution_errors(lambda: _cmd_daemon_stop(args))
    if args.status:
        return _run_with_path_resolution_errors(lambda: _cmd_status(args))
    if args.daemon_runbook:
        return _run_with_path_resolution_errors(lambda: _cmd_daemon_runbook(args))
    if getattr(args, "config_help", False):
        from ...core.knobs import format_config_help
        sys.stdout.write(format_config_help())
        return 0
    if getattr(args, "config_snapshot", None):
        return _run_with_path_resolution_errors(lambda: _cmd_config_snapshot(args))
    if getattr(args, "gc", False):
        return _run_with_path_resolution_errors(lambda: _cmd_gc(args))
    if args.watch:
        return _run_with_path_resolution_errors(lambda: _cmd_watch(args))
    if args.follow:
        return _run_with_path_resolution_errors(lambda: _cmd_follow(args))
    if getattr(args, "dashboard", False):
        from ...tools.dashboard import serve
        return serve(port=int(getattr(args, "dashboard_port", 8787) or 8787))
    if getattr(args, "web", False):
        entry_error = _lifetime_entry_error(args)
        if entry_error:
            sys.stderr.write(f"argus-skill: {entry_error}\n")
            return 2
        try:
            from ...webapi.server import serve as serve_web
        except ImportError:
            sys.stderr.write(
                "argus-skill: --web needs the web extra — install it with "
                "`pip install 'argus-skill[web]'` (fastapi + uvicorn).\n"
            )
            return 2
        return serve_web(
            host=str(getattr(args, "web_host", "127.0.0.1") or "127.0.0.1"),
            port=int(getattr(args, "web_port", 8799) or 8799),
        )
    if args.notify:
        return _run_with_path_resolution_errors(lambda: _cmd_notify(args))
    if args.init_identity:
        return _run_with_path_resolution_errors(lambda: _cmd_init_identity(args))
    if args.setup:
        from ...tools.setup import run_setup
        return run_setup()
    if args.model_api_status:
        return _run_with_path_resolution_errors(lambda: _cmd_model_api_status(args))
    if args.init_model_api:
        return _run_with_path_resolution_errors(lambda: _cmd_init_model_api(args))
    if args.skill_stats:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_stats(args))
    if args.skill_cleanse:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_cleanse(args))
    if args.export_builtin_skills is not None:
        return _run_with_path_resolution_errors(
            lambda: _cmd_export_builtin_skills(args)
        )
    if args.evidence_chain_check:
        return _run_with_path_resolution_errors(
            lambda: _cmd_evidence_chain_check(args)
        )
    if args.anti_mediocrity_check:
        return _run_with_path_resolution_errors(
            lambda: _cmd_anti_mediocrity_check(args)
        )
    if args.lifecycle_status:
        return _run_with_path_resolution_errors(
            lambda: _cmd_lifecycle_status(args)
        )
    if args.lifecycle_resume:
        return _run_with_path_resolution_errors(
            lambda: _cmd_lifecycle_transition(args, action="resume")
        )
    if args.lifecycle_archive:
        return _run_with_path_resolution_errors(
            lambda: _cmd_lifecycle_transition(args, action="archive")
        )

    # All interactive use goes through the Ink cockpit; ``argus-skill`` remains
    # the daemon/admin CLI for explicit flags.
    entry_error = _lifetime_entry_error(args)
    if entry_error:
        sys.stderr.write(f"argus-skill: {entry_error}\n")
        return 2
    from ..tui_launcher import main as run_tui

    forwarded = list(sys.argv[1:] if argv is None else argv)
    return run_tui(forwarded)


# ---------------------------------------------------------------------------
# 7×24 daemon dispatchers
# ---------------------------------------------------------------------------


def _build_worker_config(args: argparse.Namespace):
    from ...daemon.life_worker import LifeWorkerConfig
    bundle = _resolve_project_bundle(args)
    from ...core.knobs import resolve_role_backend

    backend = getattr(args, "backend", None) or resolve_role_backend("")
    from ...core.knobs import (
        resolve_budget_caps,
        resolve_role_model,
        resolve_role_reasoning_effort,
    )

    budget = resolve_budget_caps(
        project_state_dir=bundle.project.root,
        global_root=bundle.global_root,
    )

    return LifeWorkerConfig(
        life_dir=bundle.project.root,
        global_root=bundle.global_root,
        project_workdir=bundle.project_worktree,
        project_fingerprint=bundle.project.fingerprint,
        project_label=bundle.project.label,
        backend=backend,
        engineer_model=resolve_role_model(
            "engineer",
            role_env="ARGUS_SKILL_ENGINEER_MODEL",
        ),
        reviewer_model=resolve_role_model(
            "reviewer",
            role_env="ARGUS_SKILL_REVIEWER_MODEL",
        ),
        engineer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT"
        ),
        reviewer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT"
        ),
        per_mission_cap_usd=budget.per_mission_cap_usd,
        daily_cap_usd=budget.daily_cap_usd,
        global_daily_cap_usd=budget.global_daily_cap_usd,
        planner_task_iteration_max_cycles=int(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "6")),
        planner_task_iteration_budget_usd=float(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_BUDGET_USD", "30.0")),
        poll_interval=float(os.environ.get("ARGUS_SKILL_DAEMON_POLL_S", "5.0")),
        continuous=getattr(args, "continuous", False),
        continuous_objective=getattr(args, "objective", ""),
        resume_continuous=getattr(args, "resume_continuous", False),
        continuous_open_ended=not bool(getattr(args, "bounded", False)),
    )


def _cmd_daemon_start(args: argparse.Namespace, *, foreground: bool) -> int:
    from ...core.knobs import resolve_role_backend
    from ...daemon.commands import execute_daemon_command
    from ...daemon.life_worker import run_foreground, spawn_detached_daemon

    backend_default = resolve_role_backend("")
    continuous_error = _continuous_contract_error(
        continuous=bool(getattr(args, "continuous", False)),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2
    entry_error = _lifetime_entry_error(args)
    if entry_error:
        sys.stderr.write(f"argus-skill: {entry_error}\n")
        return 2
    cfg = _build_worker_config(args)
    if foreground:
        return run_foreground(cfg)
    receipt = execute_daemon_command(
        cfg.life_dir,
        operation="start",
        issuer="cli",
        handler=lambda: {"rc": spawn_detached_daemon(cfg)},
    )
    return int(receipt.result.get("rc", 3 if receipt.status != "applied" else 0))


def _cmd_daemon_stop(args: argparse.Namespace) -> int:
    from ...daemon.commands import execute_daemon_command
    from ...daemon.life_worker import stop_daemon
    bundle = _resolve_project_bundle(args)
    drain = bool(getattr(args, "drain", False))
    force = bool(getattr(args, "force", False))
    receipt = execute_daemon_command(
        bundle.project.root,
        operation="kill" if force else "drain" if drain else "stop",
        args={"drain": drain, "force": force},
        issuer="cli",
        handler=lambda: {
            "rc": stop_daemon(
                bundle.project.root,
                drain=drain,
                force=force,
            )
        },
    )
    return int(receipt.result.get("rc", 3 if receipt.status != "applied" else 0))


def _cmd_watch(args: argparse.Namespace) -> int:
    from .._watch import run_watch
    return run_watch(_resolve_project_bundle(args))


def _cmd_follow(args: argparse.Namespace) -> int:
    """Tail events.jsonl with pretty formatting — like ``tail -f``."""
    events_path = _resolve_follow_events_path(args)
    backlog_path = events_path.parent / "backlog.jsonl"

    import json as _json

    print(f"argus-skill: following {events_path}  (Ctrl-C to stop)", flush=True)
    print("━" * 60, flush=True)
    fh = None
    current_layer = "engineer"
    current_mission: dict[str, str] = {"item_id": "", "title": "", "objective": ""}
    from ...cli.theme import Theme
    from ...core import log_view as lv
    from ._follow import _FollowCoalescer
    state = lv.LogState()
    theme = Theme.auto()
    last_event_at = time.monotonic()
    last_heartbeat_at = 0.0

    def _emit(ev: dict) -> None:
        # Render + print exactly one committed event. Runs the stateful
        # connector/timestamp advance ONCE per printed line (not per streamed
        # beat), so coalesced messages don't desync the grouping connectors.
        nonlocal current_layer, current_mission, last_event_at, last_heartbeat_at
        current_layer = _follow_layer_from_event(ev, current_layer)
        etype = str(ev.get("type") or "")
        connector = lv.interior(state, lv.advance(state, etype, ev))
        if etype in {"life.mission.started", "life.mission.completed"}:
            item_id = str(ev.get("item_id") or current_mission.get("item_id") or "")
            title = str(ev.get("title") or current_mission.get("title") or "")
            objective = str(ev.get("objective") or current_mission.get("objective") or "")
            if item_id:
                row = _select_backlog_row_by_id(
                    _read_backlog_rows(backlog_path), item_id
                )
                if row is not None:
                    title = str(row.get("title") or title)
                    objective = str(row.get("objective") or objective)
            current_mission = {
                "item_id": item_id,
                "title": title,
                "objective": objective,
            }
        body = _format_follow_event(
            ev,
            current_layer,
            mission_context=current_mission,
            theme=theme,
        )
        if not body:
            return
        ts_field = lv.format_timestamp(ev.get("ts"), state.prev_ts)
        try:
            state.prev_ts = float(ev.get("ts"))
        except (TypeError, ValueError):
            state.prev_ts = time.time()
        if connector == lv.OPEN:
            print(flush=True)  # blank line before a new mission / planner group
        print(
            lv.follow_line(
                ts_field,
                connector,
                body,
                width=theme.width,
                paint_connector=(theme.dim if theme.enabled else None),
            ),
            flush=True,
        )
        last_event_at = time.monotonic()
        last_heartbeat_at = 0.0

    coalescer = _FollowCoalescer(_emit)
    try:
        while fh is None:
            try:
                fh = events_path.open("r", encoding="utf-8")
                fh.seek(0, 2)
                pos = fh.tell()
                fh.seek(max(0, pos - 8192))
                if pos > 8192:
                    fh.readline()  # skip partial line
            except FileNotFoundError:
                print(f"argus-skill: waiting for {events_path} ...", flush=True)
                time.sleep(0.5)
            except OSError as exc:
                sys.stderr.write(f"argus-skill: cannot open {events_path}: {exc}\n")
                return 1
        while True:
            line = fh.readline()
            if not line:
                # Settle a streamed message that has gone quiet, then idle.
                coalescer.flush_idle()
                time.sleep(0.5)
                now = time.monotonic()
                idle = now - last_event_at
                if (
                    idle >= _FOLLOW_HEARTBEAT_SECONDS
                    and now - last_heartbeat_at >= _FOLLOW_HEARTBEAT_SECONDS
                ):
                    print(
                        _format_follow_heartbeat(events_path, current_layer, idle),
                        flush=True,
                    )
                    last_heartbeat_at = now
                # Check if file was rotated
                try:
                    if events_path.stat().st_ino != os.fstat(fh.fileno()).st_ino:
                        fh.close()
                        fh = events_path.open("r", encoding="utf-8")
                except OSError:
                    pass
                continue
            line = line.strip()
            if not line:
                continue
            try:
                ev = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            coalescer.feed(ev)
    except KeyboardInterrupt:
        coalescer.flush()
        print("\nargus-skill: stopped following", flush=True)
    finally:
        coalescer.flush()
        if fh is not None:
            fh.close()
    return 0


def _cmd_notify(args: argparse.Namespace) -> int:
    """Append a free-form nudge to ``<life_dir>/inbox.jsonl``.

    The next engineer round picks it up via the supervisor's
    ``user_inbox`` callable and splices it into the prompt as
    operator guidance.
    """
    msg = (args.notify or "").strip()
    if not msg:
        sys.stderr.write("argus-skill: --notify requires a non-empty message\n")
        return 2
    bundle = _resolve_project_bundle(args)
    bundle.project.root.mkdir(parents=True, exist_ok=True)
    inbox = bundle.project.root / "inbox.jsonl"
    queue_inbox_message(bundle.project.root, msg, source="cli.notify")
    print(f"argus-skill: queued nudge ({len(msg)} chars) → {inbox}")
    return 0


def _cmd_init_identity(args: argparse.Namespace) -> int:
    from .._init_identity import run_init_identity
    return run_init_identity(_resolve_global_root(args))


def _cmd_wiki_init(args: argparse.Namespace) -> int:
    from ...wiki.bootstrap import init_wiki

    root = init_wiki(args.project, base=args.base)
    print(f"wiki ready at {root}")
    return 0


def _project_root_for_wiki_path(wiki: Path) -> Path:
    wiki = wiki.expanduser()
    resolved = wiki.resolve() if wiki.exists() else wiki.absolute()
    if (
        resolved.name == "wiki"
        and resolved.parent.parent.name == ".autors"
    ):
        return resolved.parent.parent.parent
    return resolved.parent.parent


def _cmd_wiki_ingest(args: argparse.Namespace) -> int:
    from ...wiki.bootstrap import init_wiki, is_initialized_wiki
    from ...wiki.ingest import ingest_lit_matrix, ingest_refs_bib
    from ...wiki.store import WikiStore

    wiki = args.wiki.expanduser()
    if not is_initialized_wiki(wiki):
        if args.init:
            project_root = _project_root_for_wiki_path(wiki)
            if wiki.name == "wiki" and wiki.parent.name:
                init_wiki(wiki.parent.name, base=project_root)
            else:
                sys.stderr.write(f"argus-skill: cannot infer project from --wiki {wiki}\n")
                return 2
        else:
            sys.stderr.write(
                f"argus-skill: {wiki} is not an initialized wiki; "
                "run `argus-skill wiki init <project>` or pass --init\n"
            )
            return 2
    if not is_initialized_wiki(wiki):
        sys.stderr.write(f"argus-skill: failed to initialize wiki at {wiki}\n")
        return 2
    store = WikiStore(wiki)
    project_root = _project_root_for_wiki_path(wiki)
    refs = args.refs.expanduser() if args.refs else project_root / "paper" / "refs.bib"
    lit = (
        args.lit_matrix.expanduser()
        if args.lit_matrix
        else project_root / "research" / "LIT_MATRIX.tsv"
    )

    if refs.exists():
        bib_result = ingest_refs_bib(
            store,
            bib_path=refs,
            ingested_by=args.ingested_by,
        )
        print(f"ingested {len(bib_result.written)} new source(s) from {refs}")
        for warning in bib_result.warnings:
            sys.stderr.write(f"warning: {warning}\n")
    else:
        print(f"no refs.bib at {refs}, skipping bib ingest")

    if lit.exists():
        lit_result = ingest_lit_matrix(store, tsv_path=lit)
        print(f"enriched {lit_result.enriched_count} source(s) from {lit}")
        for warning in lit_result.warnings:
            sys.stderr.write(f"warning: {warning}\n")
    else:
        print(f"no LIT_MATRIX.tsv at {lit}, skipping enrichment")

    return 0


def _cmd_learn(args: argparse.Namespace) -> int:
    import json

    from ...skills.vertical_select import persist_vertical
    from ...verticals.learning.ingest import ingest_material
    from ...wiki.bootstrap import init_wiki
    from ...wiki.store import WikiStore

    base = args.base.expanduser()
    wiki_root = init_wiki(args.project, base=base)
    store = WikiStore(wiki_root)

    manifests: list[dict] = []
    for material in args.material:
        path = material.expanduser()
        if not path.exists():
            sys.stderr.write(f"argus-skill: material not found: {path}\n")
            return 2
        try:
            manifest = ingest_material(path, store, ingested_by=args.ingested_by)
        except ValueError as exc:
            sys.stderr.write(f"argus-skill: {exc}\n")
            return 2
        manifests.append(manifest)
        status = "ingested" if manifest["written"] else "already present (immutable)"
        print(f"{status}: {manifest['source_id']} "
              f"({manifest['char_count']} chars via {manifest['extractor']})")

    manifest_dir = base / "learning"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "MATERIAL_MANIFEST.json").write_text(
        json.dumps({"materials": manifests}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    persist_vertical(base, "learning")

    print(f"\nmaterial staged immutably under {wiki_root / 'sources' / 'notes'}")
    print(f"vertical persisted (learning) at {base}")
    print(
        "next: run the daemon in this workdir to start the learning mission, e.g.\n"
        f"  cd {base} && argus-skill --daemon --continuous "
        "--objective 'Study the ingested material and update your skill+wiki libraries'"
    )
    return 0


def _cmd_wiki_migrate(args: argparse.Namespace) -> int:
    from ...wiki.bootstrap import is_initialized_wiki
    from ...wiki.migrate import migrate_orphan_sources
    from ...wiki.store import WikiStore

    wiki = args.wiki.expanduser()
    if not is_initialized_wiki(wiki):
        sys.stderr.write(f"argus-skill: {wiki} is not an initialized wiki\n")
        return 2
    moved = migrate_orphan_sources(WikiStore(wiki))
    print(f"migrated {len(moved)} orphan source note(s)")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    """``argus-skill query <text>`` — unified trajectory + skills + wiki search."""
    import json as _json

    from ...tools.query_unified import render_text, unified_query

    q = " ".join(args.text)
    result = unified_query(
        q,
        top_k=int(getattr(args, "top_k", 5) or 5),
        auto_index=not bool(getattr(args, "no_index", False)),
    )
    if getattr(args, "json", False):
        print(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_text(result))
    return 0


def _model_api_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env["ARGUS_SKILL_CAPABILITY_VAULT"] = str(
        _resolve_global_root(args) / "capabilities" / "model_api.json"
    )
    return env


def _cmd_model_api_status(args: argparse.Namespace) -> int:

    from ...tools.capability_vault import status_payload

    print(json.dumps(status_payload(_model_api_env(args)), indent=2, sort_keys=True))
    return 0


def _cmd_init_model_api(args: argparse.Namespace) -> int:
    from ...tools.capability_vault import bootstrap_model_api_vault

    path = bootstrap_model_api_vault(_model_api_env(args))
    print(f"argus-skill: model API capability saved at {path} (0600, secret not printed)")
    return 0


def _cmd_config_snapshot(args: argparse.Namespace) -> int:
    from ...core.config_snapshot import write_config_snapshot

    raw = getattr(args, "config_snapshot", None) or "argus_runtime_settings.md"
    out = core_paths.resolve_runtime_path(raw, context="--config-snapshot")
    path = write_config_snapshot(out, env=os.environ)
    print(f"argus-skill: config snapshot written to {path}")
    return 0


def _resolve_skills_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "skills_dir", None):
        return core_paths.resolve_runtime_path(args.skills_dir, context="--skills-dir")
    return _resolve_global_root(args) / "skills"


def _run_with_path_resolution_errors(action) -> int:
    try:
        return action()
    except core_paths.PathResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 2


def _cmd_skill_stats(args: argparse.Namespace) -> int:
    from .._skill_stats import run_skill_stats
    return run_skill_stats(
        _resolve_project_bundle(args).project.root,
        as_json=bool(args.skill_stats_json),
    )


def _cmd_skill_cleanse(args: argparse.Namespace) -> int:
    from .._skill_cleanse import run_cleanse
    return run_cleanse(
        _resolve_skills_dir(args),
        dry_run=not bool(args.apply),
    )


def _cmd_export_builtin_skills(args: argparse.Namespace) -> int:
    from ...skills.builtins import (
        DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
        builtin_skill_source_path,
        seed_builtin_skills,
        seed_builtin_skills_for_vertical,
    )
    from ...skills.vertical_select import VerticalResolutionError, resolve_vertical

    raw_target = args.export_builtin_skills or DEFAULT_PROJECT_BUILTIN_SKILLS_DIR
    target = core_paths.resolve_runtime_path(
        raw_target,
        context="--export-builtin-skills",
    )
    if not target.is_absolute():
        target = Path.cwd() / target
    # Vertical-aware export: a non-research vertical also seeds its OWN domain
    # skills (verticals/<v>/skills/), with the real bodies overwriting any
    # builtin pointer stub, so the agent workspace carries the real skill that
    # the vertical's REVIEWER_CHECKLISTS reference. ``--vertical`` overrides; by
    # default the active vertical is resolved from research/PIPELINE_STATE.json
    # (env ARGUS_SKILL_VERTICAL wins) in the target/cwd. This is a standalone
    # operator command that may run outside a decided mission, so fall back to
    # the ``research`` seed when no vertical is resolvable (the mission-internal
    # readers all run post-bootstrap, where resolve_vertical is fail-hard).
    explicit = getattr(args, "vertical", None)
    if explicit:
        vertical = explicit
    else:
        try:
            vertical = resolve_vertical(Path.cwd())
        except VerticalResolutionError:
            vertical = "research"
    if vertical and vertical != "research":
        result = seed_builtin_skills_for_vertical(
            target, vertical, overwrite=bool(args.apply)
        )
    else:
        result = seed_builtin_skills(target, overwrite=bool(args.apply))
    written = sum(1 for changed in result.values() if changed)
    skipped = len(result) - written
    source_path = builtin_skill_source_path()
    source = (
        str(source_path)
        if source_path.exists()
        else "package resource argus_skill.builtin_skills"
    )
    action = "created/replaced" if args.apply else "created"
    print(f"argus-skill: exported built-in skills to {target}")
    print(f"  source : {source}")
    print(f"  vertical: {vertical}")
    print(
        f"  files  : {written} {action}, {skipped} preserved, "
        f"{len(result)} total"
    )
    if skipped and not args.apply:
        print("  hint   : pass --apply to replace existing copied built-in skill files")
    return 0


def _cmd_evidence_chain_check(args: argparse.Namespace) -> int:
    """Run F4 evidence-chain validator. Exits non-zero on broken chain."""
    from ...skills.evidence_chain import main as _evidence_chain_main

    return _evidence_chain_main(["--project-root", str(args.project_root)])


def _cmd_anti_mediocrity_check(args: argparse.Namespace) -> int:
    """Run F3 anti-mediocrity gates. Exits non-zero on any gate failure."""
    from ...skills.anti_mediocrity import main as _anti_mediocrity_main

    argv = ["--project-root", str(args.project_root)]
    if args.proposed_condition:
        argv += ["--proposed-condition", str(args.proposed_condition)]
    if args.baseline_condition:
        argv += ["--baseline-condition", str(args.baseline_condition)]
    return _anti_mediocrity_main(argv)


def _resolve_lifecycle_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return the observable worktree and canonical persisted lifecycle root."""
    from ...life import MemoryBundle

    worktree = Path(args.project_root).resolve()
    global_root = _resolve_global_root(args)
    session_id, _is_new = _resolve_session_id(
        args,
        global_root,
        default_to_new=False,
    )
    explicit_session = (
        bool(getattr(args, "new", False))
        or bool(getattr(args, "continue_session", False))
        or getattr(args, "resume", None) is not None
    )
    if explicit_session and session_id is None:
        raise core_paths.PathResolutionError(
            "explicit session could not be resolved; lifecycle command aborted"
        )
    bundle = MemoryBundle.for_cwd(
        worktree,
        global_root=global_root,
        fingerprint=session_id,
    )
    return worktree, bundle.project_root


def _cmd_lifecycle_status(args: argparse.Namespace) -> int:
    """Print the F5 ProjectStatus inferred from current project memory
    plus any persisted quarantine / done / archived state.

    Reads observable signals (evidence bundles, paper/main.tex|pdf,
    project mtime) and overlays the persisted state from
    ``<life-dir>/lifecycle.json`` so quarantine survives daemon
    restarts.
    """
    from ...life.project_lifecycle import (
        advisory_time_signals,
        decide_next_state,
        infer_observable_status,
        is_token_allocatable,
    )
    from ...life.project_lifecycle_io import (
        LifecycleIOError,
        apply_persisted_to_status,
        load_history,
        load_persisted,
    )

    worktree, lifecycle_root = _resolve_lifecycle_roots(args)
    if not worktree.exists():
        sys.stderr.write(f"argus-skill: project root not found: {worktree}\n")
        return 2

    status = infer_observable_status(worktree, project_id=lifecycle_root.name)
    try:
        persisted = load_persisted(lifecycle_root)
    except LifecycleIOError as exc:
        sys.stderr.write(
            f"argus-skill: lifecycle sidecar at {lifecycle_root}/lifecycle.json is "
            f"malformed: {exc}\n"
        )
        persisted = {}
    overlaid = apply_persisted_to_status(status, persisted)
    event = decide_next_state(overlaid)
    history = load_history(lifecycle_root)
    signals = advisory_time_signals(overlaid)

    print("argus-skill — project lifecycle (F5)")
    print(f"  worktree          : {worktree}")
    print(f"  state_root        : {lifecycle_root}")
    print(f"  observed_state    : {status.state.value}")
    print(
        f"  effective_state   : {overlaid.state.value}"
        + ("  (persisted)" if persisted.get("state") else "")
    )
    print(f"  has_draft         : {overlaid.has_draft}")
    print(f"  has_submission    : {overlaid.has_submission_artifact}")
    print(
        f"  last_evidence_at  : "
        f"{overlaid.last_evidence_at.isoformat() if overlaid.last_evidence_at else '(none)'}"
    )
    print(f"  token_allocatable : {is_token_allocatable(overlaid)}")
    if event is None:
        print("  next_action       : no transition warranted at this tick")
    else:
        print(
            f"  next_action       : transition "
            f"{event.from_state.value} → {event.to_state.value} "
            f"({event.reason})"
        )

    # Advisory time signals are facts the AGENT reads to decide whether
    # to pivot / push / give up. The harness does not act on them.
    if signals:
        print(f"  advisory signals  : {len(signals)}  (agent reads, harness does not act)")
        for sig in signals:
            print(f"    - [{sig.kind}] {sig.message}")

    if history:
        print(f"  history ({len(history)} event(s), most recent first):")
        for ev in reversed(history[-5:]):
            print(
                f"    - {ev.at.isoformat()}  "
                f"{ev.from_state.value} → {ev.to_state.value}  ({ev.reason})"
            )
    return 0


def _cmd_lifecycle_transition(
    args: argparse.Namespace, *, action: str
) -> int:
    """Handle ``--lifecycle-resume`` and ``--lifecycle-archive``."""
    from datetime import datetime, timezone

    from ...life.project_lifecycle import (
        archive as _lifecycle_archive,
    )
    from ...life.project_lifecycle import (
        infer_observable_status,
    )
    from ...life.project_lifecycle import (
        resume as _lifecycle_resume,
    )
    from ...life.project_lifecycle_io import (
        LifecycleIOError,
        append_event,
        apply_persisted_to_status,
        load_persisted,
    )

    worktree, lifecycle_root = _resolve_lifecycle_roots(args)
    if not worktree.exists():
        sys.stderr.write(f"argus-skill: project root not found: {worktree}\n")
        return 2

    status = infer_observable_status(worktree, project_id=lifecycle_root.name)
    try:
        persisted = load_persisted(lifecycle_root)
    except LifecycleIOError as exc:
        sys.stderr.write(
            f"argus-skill: lifecycle sidecar malformed: {exc}\n"
        )
        return 2
    status = apply_persisted_to_status(status, persisted)

    now = datetime.now(timezone.utc)
    try:
        if action == "resume":
            new_status, event = _lifecycle_resume(status, now=now)
        elif action == "archive":
            new_status, event = _lifecycle_archive(status, now=now)
        else:
            raise ValueError(f"unknown lifecycle action {action!r}")
    except ValueError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 1

    try:
        append_event(lifecycle_root, new_status=new_status, event=event)
    except OSError as exc:
        sys.stderr.write(f"argus-skill: cannot persist transition: {exc}\n")
        return 1

    resumed_items = []
    if action == "resume":
        from ...life.memory import LifeMemory

        resumed_items = LifeMemory.open(lifecycle_root).backlog.resume_all_paused()

    print(
        f"argus-skill: lifecycle transition "
        f"{event.from_state.value} → {event.to_state.value} "
        f"({event.reason})"
    )
    print(f"  worktree   : {worktree}")
    print(f"  state_root : {lifecycle_root}")
    print(f"  state : {new_status.state.value}")
    if resumed_items:
        print(f"  resumed backlog items : {len(resumed_items)}")
    return 0


def _resolve_research_workdir(bundle: Any) -> Path:
    """Find where the actual research project lives (paper/ benchmarks/
    research/ etc.) for surfaces like --status that need to inspect
    research artifacts, not the life-dir state.

    Resolution order (matches supervisor._project_workdir):

    1. ``ARGUS_SKILL_WORKDIR`` env var (operator override)
    2. ``<bundle.project.root>/code/`` if it exists (the
       ``new_auto_research_project`` layout seeds code under code/)
    3. ``bundle.project.root`` (life dir; may not have research/ but
       at worst the gates render empty findings, never crash)
    """
    env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
    if env_workdir:
        return Path(env_workdir).expanduser()
    project_root = Path(bundle.project.root)
    code = project_root / "code"
    if code.is_dir():
        return code
    return project_root


def _read_current_stage(workdir: Path) -> str | None:
    """Best-effort read of ``research/PIPELINE_STATE.json``. None if
    the project hasn't reached stage tracking yet."""
    state_path = workdir / "research" / "PIPELINE_STATE.json"
    if not state_path.exists():
        return None
    try:
        import json as _json
        data = _json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        stage = data.get("current_stage")
        if not isinstance(stage, str):
            return None
        stage = stage.strip()
        return stage or None
    except (OSError, ValueError):
        return None


def _render_lifecycle_status_lines(
    workdir: Path, *, state_root: Path
) -> list[str]:
    """Render the F5 lifecycle block for --status / cockpit.

    Observable facts come from ``workdir``; persisted lifecycle authority comes
    from the canonical project ``state_root`` shared with the daemon. Returns the
    lines to print. Fail-soft: any error returns an empty list.
    """
    try:
        from ...life.project_lifecycle import (
            advisory_time_signals,
            infer_observable_status,
            is_token_allocatable,
        )
        from ...life.project_lifecycle_io import (
            LifecycleIOError,
            apply_persisted_to_status,
            load_persisted,
        )
    except Exception:  # noqa: BLE001
        return []

    # ``infer_observable_status`` tolerates a non-existent workdir
    # (returns an INCUBATING status using "now" as created_at), so we
    # do NOT early-return when the dir is missing — that's the normal
    # state for a freshly-bound project that hasn't started yet.

    try:
        status = infer_observable_status(workdir, project_id=state_root.name)
        try:
            persisted = load_persisted(state_root)
        except LifecycleIOError:
            persisted = {}
        overlaid = apply_persisted_to_status(status, persisted)
        signals = advisory_time_signals(overlaid)
    except Exception:  # noqa: BLE001
        return []

    lines: list[str] = []
    lines.append("  lifecycle:")
    state_label = overlaid.state.value
    if persisted.get("state"):
        state_label += "  (persisted)"
    lines.append(f"    state         : {state_label}")
    lines.append(
        f"    allocatable   : {is_token_allocatable(overlaid)}"
    )
    if signals:
        lines.append(
            f"    advisory      : {len(signals)} signal(s) "
            f"(agent reads, harness does not act)"
        )
        for sig in signals:
            lines.append(f"      - [{sig.kind}] {sig.message}")
    return lines


def _render_inbox_injection_lines(bundle: Any, *, limit: int = 3) -> list[str]:
    """Surface recent inbox-injection journal entries (Opt #4).

    Lets the operator confirm that `argus-skill --notify "..."` was
    seen by the daemon and injected into a mission prompt. Returns
    [] when no inbox.injected entries exist.
    """
    try:
        entries = list(bundle.journal.tail(50))
    except Exception:  # noqa: BLE001
        return []
    injected = [
        e for e in entries
        if getattr(e, "kind", "") == "inbox.injected"
    ][-limit:]
    if not injected:
        return []
    lines = ["  inbox (last injections):"]
    for e in injected:
        ts = getattr(e, "ts", 0.0)
        try:
            import datetime as _dt
            stamp = _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
        except Exception:  # noqa: BLE001
            stamp = "?"
        summary = (getattr(e, "summary", "") or "").replace("\n", " ")
        if len(summary) > 100:
            summary = summary[:97] + "..."
        lines.append(f"    {stamp}  {summary}")
    return lines


def _render_mid_mission_progress_lines(bundle: Any, *, current_item_id: str | None) -> list[str]:
    """Tail events.jsonl for the currently-running mission and surface
    the last 3-5 events as quick-read progress. Fail-soft.

    Opt #3: avoids the operator needing to `tail -f events.jsonl`
    just to see what the current 26-minute mission is actually doing.
    """
    if not current_item_id:
        return []
    try:
        import json as _json
        project_root = Path(bundle.project.root)
        events_path = project_root / "events.jsonl"
        if not events_path.exists():
            return []
        with events_path.open("rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            read_chunk = min(end, 64 * 1024)
            fh.seek(end - read_chunk)
            raw_tail = fh.read().decode("utf-8", errors="replace")
        tail_lines = [ln for ln in raw_tail.splitlines() if ln.strip()][-200:]
        events: list[dict[str, Any]] = []
        for line in tail_lines:
            try:
                events.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
        recent = events[-4:]
        if not recent:
            return []
    except Exception:  # noqa: BLE001
        return []

    lines = ["  in_flight:"]
    for ev in recent:
        actor = ev.get("actor", "") or ev.get("agent_layer", "")
        kind = ev.get("kind", "") or ev.get("type", "")
        text = (ev.get("text") or ev.get("output_excerpt") or "")
        excerpt = text.replace("\n", " ").strip()
        if len(excerpt) > 110:
            excerpt = excerpt[:107] + "..."
        head = f"{actor or '<no-actor>'} {kind}".strip()
        lines.append(f"    {head[:38]:38s} {excerpt}")
    return lines


def _render_gate_snapshot_lines(workdir: Path, stage: str | None) -> list[str]:
    """Render the structural/advisory gate snapshot for --status.
    Runs the F3/F4 gates against the current pipeline stage and shows
    each result with its kind. Structural failures are marked ❌;
    advisory findings are marked 📋 (never failure). Fail-soft.
    """
    if not stage:
        return []
    try:
        from ...skills.automated_gates import (
            gates_for_stage,
            run_stage_gates,
        )
    except Exception:  # noqa: BLE001
        return []

    if not gates_for_stage(stage):
        return [f"  gates @ {stage}: (no gates configured at this stage)"]

    try:
        results = run_stage_gates(
            workdir,
            stage=stage,
            proposed_condition=os.environ.get("ARGUS_SKILL_PROPOSED_CONDITION") or None,
            baseline_condition=os.environ.get("ARGUS_SKILL_BASELINE_CONDITION") or None,
        )
    except Exception:  # noqa: BLE001
        return [f"  gates @ {stage}: (snapshot failed; rerun stage_check)"]

    lines = [f"  gates @ {stage}:"]
    for gate in results:
        if gate.kind == "advisory":
            mark = "📋"
        elif gate.passed:
            mark = "✅"
        else:
            mark = "❌"
        lines.append(
            f"    {mark} {gate.name} ({gate.kind}) — {gate.summary}"
        )
    return lines


def _cmd_gc(args: argparse.Namespace) -> int:
    """Prune stale projects (no live daemon + untouched for --gc-days)."""
    from ...core.project_gc import gc_stale_projects, retention_days_default

    root = _resolve_global_root(args)
    days = getattr(args, "gc_days", None)
    if days is None:
        days = retention_days_default()
    dry = bool(getattr(args, "gc_dry_run", False))
    pruned = gc_stale_projects(root, retention_days=days, dry_run=dry)
    verb = "would prune" if dry else "moved to projects_trash/"
    if not pruned:
        sys.stdout.write(
            f"argus-skill: no stale projects (retention={days}d; "
            "live daemons and recently-active projects are never touched).\n"
        )
        return 0
    sys.stdout.write(f"argus-skill: {verb} {len(pruned)} stale project(s):\n")
    for fp in pruned:
        sys.stdout.write(f"  - {fp}\n")
    if not dry:
        sys.stdout.write(
            f"  ↳ recoverable under {root / 'projects_trash'} — rm it when sure.\n"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from ...daemon.life_worker import (
        format_budget_status,
        read_continuous_state,
        read_daemon_status,
    )
    bundle = _resolve_project_bundle(args)
    status = read_daemon_status(bundle.project.root)
    all_items = bundle.backlog.all()
    pending, running, paused, done, failed, skipped = count_backlog_statuses(all_items)
    current_running = select_current_running_item(all_items)
    # Status should stay cheap even on a long-lived daemon.
    journal_tail = bundle.journal.tail(3)

    print(f"argus-skill — global-root: {bundle.global_root}")
    print(f"  project  : {bundle.project.root}")
    if status.alive and status.pid is not None:
        uptime = _format_short_duration(status.uptime_seconds or 0.0)
        # status.backend is "codex" (real CLI backend) vs "memory" (test
        # double) — not which real CLI is configured per role (that's
        # ARGUS_SKILL_RUNNER_BACKEND, shown in /roles). See the matching
        # role status view explains why the raw value
        # isn't printed here.
        backend_label = (
            "memory (test)" if status.backend == "memory" else "live — see /roles"
        )
        print(f"  daemon   : alive (pid {status.pid}, up {uptime}, backend {backend_label})")
    else:
        print("  daemon   : not running   (start with `argus-skill --daemon`)")
    print(f"  {format_budget_status(bundle.journal, status=status)}")
    print(
        f"  active   : {pending} pending · {running} running · {paused} paused"
    )
    if current_running is not None:
        print("  current  :")
        print(f"    id       : {getattr(current_running, 'id', '')}")
        print(
            f"    title    : "
            f"{_clean_follow_text(str(getattr(current_running, 'title', '')), limit=80)}"
        )
        print(
            f"    objective: "
            f"{_clean_follow_text(str(getattr(current_running, 'objective', '')), limit=120)}"
        )
    try:
        from ...life.telemetry import read_latest_telemetry
        telemetry_event = read_latest_telemetry(bundle.project.root)
        telemetry_lines = _format_telemetry_status_lines(telemetry_event)
    except Exception:  # noqa: BLE001
        telemetry_event = None
        telemetry_lines = []
    if telemetry_lines:
        print("  telemetry:")
        for line in telemetry_lines:
            print(line)
    activity_lines = _format_daemon_activity_status_lines(
        status,
        life_dir=bundle.project.root,
        telemetry_event=telemetry_event,
    )
    if activity_lines:
        print("  activity :")
        for line in activity_lines:
            print(line)
    print(f"  inbox    : {count_pending_inbox_messages(bundle.project.root)} pending")
    history_parts = [part for part in (
        f"{done} done" if done else "",
        f"{failed} failed" if failed else "",
        f"{skipped} skipped" if skipped else "",
    ) if part]
    if history_parts:
        print(f"  history  : {' · '.join(history_parts)}")
    # Total cost from the idempotent call ledger.
    try:
        from ...core.usage import format_usage_cost, project_usage_summary

        total_cost = project_usage_summary(bundle.project.root)
        print(f"  cost     : {format_usage_cost(total_cost)} cumulative")
    except Exception:  # noqa: BLE001
        pass
    if running and not (status.alive and status.pid is not None):
        print(
            "             ↳ orphan running items will be reaped to `failed` "
            "when a daemon worker next starts."
        )
    cont = read_continuous_state(bundle.project.root)
    print(f"  continuous: {'on' if cont.enabled else 'off'}")
    if cont.objective:
        print(f"    objective: {cont.objective}")
    if cont.done_reason:
        print(f"    done_reason: {cont.done_reason}")
    if cont.done_at:
        print(f"    done_at: {cont.done_at}")

    # Lifecycle (F5) + gate snapshot (F3 advisory / F4 structural).
    # Both are projections of observable state — surfacing facts the
    # agent already acts on; the harness makes no decision here.
    research_workdir = _resolve_research_workdir(bundle)
    lifecycle_lines = _render_lifecycle_status_lines(
        research_workdir,
        state_root=Path(bundle.project.root),
    )
    for line in lifecycle_lines:
        print(line)
    current_stage = _read_current_stage(research_workdir)
    gate_lines = _render_gate_snapshot_lines(research_workdir, current_stage)
    for line in gate_lines:
        print(line)

    # Mid-mission progress (Opt #3). Tails events.jsonl for the
    # currently-running mission so the operator doesn't need to
    # `tail -f` a separate file to see what the long mission is
    # actually doing right now.
    running_id = (
        getattr(current_running, "id", None) if current_running else None
    )
    progress_lines = _render_mid_mission_progress_lines(bundle, current_item_id=running_id)
    for line in progress_lines:
        print(line)

    # Inbox injections (Opt #4). Operator can now confirm via --status
    # that their --notify messages were seen by the daemon and woven
    # into a mission prompt (vs disappearing into the void).
    inbox_lines = _render_inbox_injection_lines(bundle)
    for line in inbox_lines:
        print(line)

    if journal_tail:
        print("  recent   :")
        for entry in journal_tail:
            print(f"    - {entry.kind}  {entry.summary}")
    survival_msg = _check_logout_survival(status)
    if survival_msg:
        print(f"  survival : {survival_msg}")
    return 0


def _cmd_daemon_runbook(args: argparse.Namespace) -> int:
    bundle = _resolve_project_bundle(args)
    from ...daemon.life_worker import read_daemon_status

    status = read_daemon_status(bundle.project.root)
    lines = [
        "argus-skill daemon-safe upgrade runbook",
        f"global   : {bundle.global_root}",
        f"project  : {bundle.project.root}",
        (
            f"daemon   : alive (pid {status.pid})"
            if status.alive and status.pid is not None
            else "daemon   : not running"
        ),
        "",
        "1. Open a second shell, tmux pane, or systemd session before touching the daemon.",
        "2. Treat the live daemon as the control plane: do not restart the process that owns your current session.",
        "3. Persist context first. Global identity/journal live under the global root; the backlog, inbox, and project memory live under the project root.",
        "4. For an ad-hoc detached worker, run `argus-skill --daemon-stop --drain` from the external shell (waits for the current mission to finish at a clean boundary — no mid-mission SIGKILL), then once it exits, update the code and relaunch with `argus-skill --daemon`.",
        "5. For a systemd-managed worker, edit the unit from the maintenance shell, then run `systemctl daemon-reload && systemctl restart argus-skill.service`.",
        "6. Verify the new process with `argus-skill --status` before resuming work.",
    ]
    print("\n".join(lines))
    return 0


def _check_logout_survival(status) -> str | None:  # noqa: ANN001
    """Best-effort check whether the daemon will survive logout.

    The daemon already double-forks + setsid + ignores SIGHUP, so an
    SSH disconnect or terminal close cannot kill it. The remaining
    real-world risk on Linux is ``systemd-logind KillUserProcesses=yes``
    which kills user-owned processes (regardless of session) when the
    user has no more login sessions and ``linger`` is off. We probe
    ``loginctl show-user`` and tell the operator how to fix it.
    """
    if not (status.alive and status.pid is not None):
        return None
    if sys.platform != "linux":
        return None
    try:
        import getpass
        import subprocess
        user = getpass.getuser()
        out = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    body = (out.stdout or "").strip()
    if "Linger=yes" in body:
        return "linger=on  (daemon will survive logout / SSH disconnect)"
    if "Linger=no" in body:
        return (
            "linger=off ⚠  daemon may be killed at logout. "
            f"Run `loginctl enable-linger {getpass.getuser()}` to make 7×24 honest."
        )
    return None


def _format_short_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    if seconds < 86400:
        h, rem = divmod(int(seconds), 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h {m}m"
    d, rem = divmod(int(seconds), 86400)
    h, _ = divmod(rem, 3600)
    return f"{d}d {h}h"
