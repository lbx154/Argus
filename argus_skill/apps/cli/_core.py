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
from collections import deque
from pathlib import Path
from typing import Any

from ...core import paths as core_paths
from ...life.mission_outcome import outcome_dimension_summary
from ...life.status import count_backlog_statuses, select_current_running_item
from .._inbox import count_pending_inbox_messages, queue_inbox_message
from .._target_paths import resolve_life_root
from ._follow import (
    _clean_follow_text,
    _follow_layer_from_event,
    _format_follow_heartbeat,
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


def _missing_web_dependency() -> str | None:
    """Name the first absent web dependency, without importing it.

    `webapi.server` imports uvicorn lazily inside `serve()`, so a guard on the
    module import alone never fired: the pairing banner offered a URL and a
    bare ImportError then escaped as a traceback. Probing the spec is cheap and
    has no import side effects, so it can run before anything is promised.
    """
    from importlib.util import find_spec

    for module in ("fastapi", "uvicorn"):
        try:
            if find_spec(module) is None:
                return module
        except (ImportError, ValueError):
            return module
    return None


def _report_missing_web_dependency(missing: str) -> int:
    """Explain a broken web install instead of raising through the CLI.

    fastapi and uvicorn are required dependencies, not an extra, so the old
    advice to install `argus-skill[web]` named an extra that does not exist.
    """
    sys.stderr.write(
        f"argus-skill: --web cannot start because {missing} is missing. It "
        "ships as a required dependency, so this is a broken install: "
        "`pip install --force-reinstall argus-skill`.\n"
    )
    return 2


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


def _session_for_current_workdir(
    global_root: Path,
    *,
    workdir: Path | None = None,
) -> str | None:
    """Newest session bound to the selected workdir, preferring a live daemon.

    Web/TUI sessions are keyed by session id rather than the legacy cwd
    fingerprint. Without this bridge, running ``argus --status`` from the exact
    execution directory can report a different empty project while that
    session's daemon is visibly alive in the cockpit.
    """
    from ...core.session import (
        list_sessions,
        live_daemon_sessions,
        resolve_session_workdir,
    )

    try:
        current = (workdir if workdir is not None else Path.cwd()).resolve(
            strict=True
        )
    except (OSError, RuntimeError):
        return None
    live_ids = {meta.id for meta in live_daemon_sessions(global_root)}
    matches: list[str] = []
    for meta in list_sessions(global_root):
        state_dir = core_paths.session_state_root(meta.id, root=global_root)
        try:
            workdir = resolve_session_workdir(meta, state_dir=state_dir).resolve(
                strict=True
            )
        except (OSError, RuntimeError):
            continue
        if workdir == current:
            matches.append(meta.id)
    return next((sid for sid in matches if sid in live_ids), matches[0] if matches else None)


def _resolve_project_bundle(
    args: argparse.Namespace,
    *,
    create_if_missing: bool = True,
):
    from ...life import MemoryBundle

    global_root = _resolve_global_root(args)
    # Explicit session flags win. Otherwise, management commands attach to the
    # newest session for this exact workdir (live first) before falling back to
    # the legacy cwd fingerprint.
    sid, _is_new = _resolve_session_id(args, global_root, default_to_new=False)
    if sid is None:
        raw_workdir = getattr(args, "project_root", None)
        selected_workdir = (
            core_paths.resolve_runtime_path(raw_workdir, context="--project-root")
            if raw_workdir is not None
            else Path.cwd()
        )
        try:
            selected_workdir = selected_workdir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise core_paths.PathResolutionError(
                f"--project-root is unavailable: {exc}"
            ) from exc
        if not selected_workdir.is_dir():
            raise core_paths.PathResolutionError(
                f"--project-root is not a directory: {selected_workdir}"
            )
        sid = _session_for_current_workdir(
            global_root,
            workdir=selected_workdir,
        )
    else:
        selected_workdir = Path.cwd()
    if sid is None:
        if not create_if_missing:
            from ...core.project import project_fingerprint

            fingerprint = project_fingerprint(selected_workdir).fingerprint
            state_dir = core_paths.session_state_root(fingerprint, root=global_root)
            if not state_dir.is_dir():
                return None
        return MemoryBundle.for_cwd(selected_workdir, global_root=global_root)
    from ...core.session import (
        migrate_legacy_session_workdir,
        read_session_meta,
        resolve_session_workdir,
        session_workdir_is_bound,
    )

    state_dir = core_paths.session_state_root(sid, root=global_root)
    if not create_if_missing and not state_dir.is_dir():
        return None
    meta = read_session_meta(global_root, sid)
    try:
        if not session_workdir_is_bound(meta):
            # Prefer the last daemon workspace over the shell cwd. A Web/CLI
            # restart may be initiated from the state directory, which must
            # never become the execution root for a legacy or partially
            # initialized external-worktree session.
            from ...daemon.state import read_daemon_status

            prior = read_daemon_status(state_dir).project_workdir
            workdir = migrate_legacy_session_workdir(
                global_root,
                sid,
                state_dir=state_dir,
                candidates=(prior, Path.cwd()),
            )
        else:
            workdir = resolve_session_workdir(meta, state_dir=state_dir)
    except (OSError, RuntimeError) as exc:
        raise core_paths.PathResolutionError(
            f"session {sid} workdir is unavailable: {exc}"
        ) from exc
    try:
        workdir = workdir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise core_paths.PathResolutionError(
            f"session {sid} workdir is unavailable: {exc}"
        ) from exc
    return MemoryBundle.for_cwd(
        workdir,
        global_root=global_root,
        fingerprint=sid,
    )


_FOLLOW_HEARTBEAT_SECONDS = 20.0
































def main(argv: list[str] | None = None) -> int:
    from ...core.runtime_env import (
        configure_framework_python_env,
        load_backend_runtime_env,
    )

    configure_framework_python_env()
    load_backend_runtime_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    objective_file = getattr(args, "objective_file", None)
    if objective_file:
        objective_path = Path(objective_file).expanduser().resolve()
        try:
            args.objective = objective_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            sys.stderr.write(
                f"argus-skill: could not read --objective-file {objective_file!r}: {exc}\n"
            )
            return 2
        if not str(args.objective).strip():
            sys.stderr.write("argus-skill: --objective-file must not be empty\n")
            return 2
        args.objective_file = str(objective_path)
    from ...core.knobs import resolve_role_backend

    # ``--backend`` parses with ``default=None``, so a value here was typed on
    # THIS invocation and is the most explicit signal available: it outranks an
    # ambient env var or a persisted knob, both of which may be stale. Letting
    # the chain outrank it would silently substitute a backend the operator did
    # not ask for — the same class of fault this change exists to remove.
    #
    # codex is safe to assume HERE specifically: this value's only consumer is
    # _continuous_contract_error -> continuous_mode_error, which compares it
    # against "memory" and nothing else, so every real backend behaves
    # identically. And this line runs for EVERY argus-skill invocation
    # (--wiki-init, --export-skills, --status), so it must never be the thing
    # that refuses to run on a host that has not picked a backend yet.
    # ``--backend`` parses with ``default=None``, so a value here was typed on
    # THIS invocation and is the most explicit signal available: it outranks an
    # ambient env var or a persisted knob, both of which may be stale. Letting
    # the chain outrank it would silently substitute a backend the operator did
    # not ask for — the same class of fault this change exists to remove.
    backend_default = getattr(args, "backend", None) or resolve_role_backend(
        "", default="codex"
    )
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
        + bool(getattr(args, "update", False))
        + bool(args.daemon_fg)
        + bool(args.daemon_stop)
        + bool(args.status)
        + bool(args.daemon_runbook)
        + bool(getattr(args, "config_help", False))
        + bool(getattr(args, "config_snapshot", None))
        + bool(getattr(args, "gc", False))
        + bool(args.watch)
        + bool(args.follow)
        + bool(getattr(args, "web", False))
        + bool(args.notify)
        + (getattr(args, "ask", None) is not None)
        + bool(getattr(args, "answer", None))
        + bool(args.init_identity)
        + bool(args.setup)
        + bool(getattr(args, "doctor", False))
        + bool(args.model_api_status)
        + bool(args.init_model_api)
        + bool(args.install_ppt_master)
        + bool(args.ppt_master_status)
        + bool(args.export_builtin_skills is not None)
        + bool(args.evidence_chain_check)
        + bool(args.anti_mediocrity_check)
        + bool(args.lifecycle_status)
        + bool(args.lifecycle_resume)
        + bool(args.lifecycle_archive)
        + bool(getattr(args, "command", None))
    )
    if getattr(args, "answer_item", "") and not getattr(args, "answer", None):
        sys.stderr.write("argus-skill: --answer-item requires --answer TEXT\n")
        return 2
    if getattr(args, "notify_stage", "") and not args.notify:
        sys.stderr.write("argus-skill: --notify-stage requires --notify MSG\n")
        return 2
    setup_only = (
        bool(getattr(args, "non_interactive", False))
        or bool(getattr(args, "set_git_global", False))
        or bool(getattr(args, "configure_codex", False))
    )
    if setup_only and not args.setup:
        sys.stderr.write(
            "argus-skill: --non-interactive / --set-git-global / "
            "--configure-codex require --setup\n"
        )
        return 2
    readiness_modifier = (
        getattr(args, "backend", None)
        or getattr(args, "auth_mode", None)
        or bool(getattr(args, "allow_prerelease", False))
    )
    if readiness_modifier and not (
        args.setup
        or getattr(args, "doctor", False)
        or getattr(args, "command", None) in {"doctor", "repair"}
        or args.daemon
        or args.daemon_fg
    ):
        sys.stderr.write(
            "argus-skill: --backend / --auth-mode / --allow-prerelease "
            "require --setup, doctor/repair, --doctor, --daemon, or --daemon-fg\n"
        )
        return 2
    if action_flags > 1:
        sys.stderr.write(
            "argus-skill: --daemon / --daemon-fg / --daemon-stop / --status / "
            "--daemon-runbook / --update / --config-help / --config-snapshot / "
            "--watch / --follow / --notify / --ask / --init-identity / "
            "--setup / --doctor / "
            "--model-api-status / --init-model-api / "
            "--install-ppt-master / --ppt-master-status / "
            "--export-builtin-skills / "
            "--evidence-chain-check / --anti-mediocrity-check / --lifecycle-status / "
            "wiki subcommands "
            "are mutually exclusive.\n"
        )
        return 2
    if args.command == "doctor":
        return _run_with_path_resolution_errors(lambda: _cmd_doctor(args))
    if args.command == "repair":
        try:
            return _run_with_path_resolution_errors(lambda: _cmd_repair(args))
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            sys.stderr.write(f"argus-skill: repair refused: {exc}\n")
            return 3
    if getattr(args, "update", False) or args.command == "update":
        from ..update import run_update

        return run_update()
    if args.command == "wiki" and args.wiki_cmd == "init":
        return _run_with_path_resolution_errors(lambda: _cmd_wiki_init(args))
    if args.command == "wiki" and args.wiki_cmd == "ingest":
        return _run_with_path_resolution_errors(lambda: _cmd_wiki_ingest(args))
    if args.command == "wiki" and args.wiki_cmd == "migrate":
        return _run_with_path_resolution_errors(lambda: _cmd_wiki_migrate(args))
    if args.command == "learn":
        return _run_with_path_resolution_errors(lambda: _cmd_learn(args))
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
    if getattr(args, "pair_plan", False):
        # Internal bridge for the Ink cockpit. It spawns the backend detached
        # with stdio discarded, so it cannot see the banner the child prints;
        # it asks for the plan here instead, passes the token down to the
        # child, and prints the banner itself. Keeps token minting, URL
        # construction, and QR rendering in one implementation.
        import json as _json

        from ...webapi.pairing import is_loopback_host, pairing_plan

        pair_host = str(getattr(args, "web_host", "127.0.0.1") or "127.0.0.1")
        plan = pairing_plan(
            pair_host,
            int(getattr(args, "web_port", 8799) or 8799),
        )
        sys.stdout.write(
            _json.dumps(
                {
                    "token": plan.token,
                    "url": plan.url,
                    "banner": plan.banner,
                    # The cockpit prints its own one-line URL for a loopback
                    # bind; the full banner is only worth showing when a phone
                    # actually has to pair.
                    "pairing": not is_loopback_host(pair_host),
                }
            )
        )
        return 0
    if getattr(args, "web", False):
        # Nothing may promise a URL before the stack that serves it is known
        # to be present.
        missing = _missing_web_dependency()
        if missing:
            return _report_missing_web_dependency(missing)
        try:
            from ...webapi.server import serve as serve_web
        except ImportError as exc:
            return _report_missing_web_dependency(exc.name or "a web dependency")
        from ...webapi.pairing import pairing_plan

        host = str(getattr(args, "web_host", "127.0.0.1") or "127.0.0.1")
        port = int(getattr(args, "web_port", 8799) or 8799)
        # A LAN bind is authenticated by default: without a configured token
        # one is minted for this run rather than serving the daemon's control
        # surface in the clear. Prints the pairing URL and QR code.
        plan = pairing_plan(host, port)
        if plan.banner:
            sys.stderr.write(f"{plan.banner}\n")
            sys.stderr.flush()
        try:
            return serve_web(
                host=host,
                port=port,
                global_root=_resolve_global_root(args),
                auth_token=plan.token or None,
            )
        except ImportError as exc:
            return _report_missing_web_dependency(exc.name or "a web dependency")
    if getattr(args, "answer", None):
        return _cmd_answer(args)
    if args.notify:
        return _run_with_path_resolution_errors(lambda: _cmd_notify(args))
    if getattr(args, "ask", None) is not None:
        return _run_with_path_resolution_errors(lambda: _cmd_ask(args))
    if args.init_identity:
        return _run_with_path_resolution_errors(lambda: _cmd_init_identity(args))
    if args.setup:
        from ...tools.setup import run_setup
        return run_setup(
            backend=getattr(args, "backend", None),
            auth_mode=getattr(args, "auth_mode", None),
            non_interactive=bool(getattr(args, "non_interactive", False)),
            allow_prerelease=bool(getattr(args, "allow_prerelease", False)),
            api_url=getattr(args, "api_url", None),
            api_key=getattr(args, "api_key", None),
            api_model=getattr(args, "api_model", None),
        )
    if getattr(args, "doctor", False):
        return _run_with_path_resolution_errors(lambda: _cmd_doctor(args))
    if args.model_api_status:
        return _run_with_path_resolution_errors(lambda: _cmd_model_api_status(args))
    if args.init_model_api:
        return _run_with_path_resolution_errors(lambda: _cmd_init_model_api(args))
    if args.install_ppt_master:
        return _run_with_path_resolution_errors(lambda: _cmd_install_ppt_master(args))
    if args.ppt_master_status:
        return _run_with_path_resolution_errors(lambda: _cmd_ppt_master_status(args))
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
    from .._runtime_construction import _resolve_role_runner_backend_name

    # See main(): the typed flag wins, then the chain, then an explicit codex.
    # value becomes LifeWorkerConfig.backend, which the daemon now exports as
    # ARGUS_SKILL_RUNNER_BACKEND at boot (LifeWorkerBootMixin
    # ._rf_bootstrap_environment) so every later role resolution sees it. It is
    # safe to assume codex here only because _cmd_daemon_start has already run
    # check_backend_readiness on the same value and refused if it is not usable.
    # ``--backend`` was typed on this invocation; it outranks the ambient env
    # and the persisted knob. This value becomes LifeWorkerConfig.backend, which
    # the daemon exports as ARGUS_SKILL_RUNNER_BACKEND at boot, so a stale env
    # winning here would not just pick the wrong backend — it would propagate
    # that choice to every role for the life of the daemon.
    backend = getattr(args, "backend", None) or resolve_role_backend(
        "", default="codex"
    )
    engineer_backend = _resolve_role_runner_backend_name("engineer", backend)
    reviewer_backend = _resolve_role_runner_backend_name("reviewer", backend)
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
            backend=engineer_backend,
        ),
        reviewer_model=resolve_role_model(
            "reviewer",
            role_env="ARGUS_SKILL_REVIEWER_MODEL",
            backend=reviewer_backend,
        ),
        engineer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT"
        ),
        reviewer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT"
        ),
        global_daily_cap_usd=budget.global_daily_cap_usd,
        mission_width=getattr(args, "mission_width", 2),
        planner_task_iteration_max_cycles=int(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "0")),
        poll_interval=float(os.environ.get("ARGUS_SKILL_DAEMON_POLL_S", "5.0")),
        continuous=getattr(args, "continuous", False),
        continuous_objective=getattr(args, "objective", ""),
        continuous_objective_file=(
            Path(str(args.objective_file)).expanduser()
            if getattr(args, "objective_file", None)
            else None
        ),
        resume_continuous=getattr(args, "resume_continuous", False),
        continuous_open_ended=not bool(getattr(args, "bounded", False)),
    )


def _cmd_daemon_start(args: argparse.Namespace, *, foreground: bool) -> int:
    from ...core.backend_readiness import (
        check_backend_readiness,
        format_backend_readiness,
    )
    from ...core.knobs import resolve_role_backend
    from ...daemon.commands import execute_daemon_command
    from ...daemon.life_worker import run_foreground, spawn_detached_daemon

    # Same resolution as main(): the typed flag wins, then the chain, then an
    # explicit codex. Safe here because backend_default only feeds
    # the "is it memory?" continuous gate. The backend that will really be used
    # is validated for real by check_backend_readiness below, which refuses the
    # daemon start (rc 3) rather than letting an unusable backend through.
    # ``--backend`` parses with ``default=None``, so a value here was typed on
    # THIS invocation and is the most explicit signal available: it outranks an
    # ambient env var or a persisted knob, both of which may be stale. Letting
    # the chain outrank it would silently substitute a backend the operator did
    # not ask for — the same class of fault this change exists to remove.
    backend_default = getattr(args, "backend", None) or resolve_role_backend(
        "", default="codex"
    )
    continuous_error = _continuous_contract_error(
        continuous=bool(getattr(args, "continuous", False)),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2
    if bool(getattr(args, "allow_prerelease", False)):
        os.environ["ARGUS_SKILL_ALLOW_BACKEND_PRERELEASE"] = "1"
    skip_vault_probe = (
        os.environ.get("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "").strip() == "1"
    )
    readiness = check_backend_readiness(
        getattr(args, "backend", None) or backend_default,
        getattr(args, "auth_mode", None),
        probe_auth=True,
        probe_vault=not skip_vault_probe,
        allow_prerelease=bool(getattr(args, "allow_prerelease", False)),
    )
    if not readiness.ok:
        sys.stderr.write(format_backend_readiness(readiness) + "\n")
        return 3
    if skip_vault_probe:
        sys.stderr.write(
            "argus-skill: UNSAFE diagnostic override: model-api network "
            "readiness probe skipped; backend/auth/config checks still passed.\n"
        )
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


def _doctor_payload(checks, *, verification: bool = False) -> dict[str, Any]:
    codes = {
        "backend preflight": "ARGUS-BACKEND-001",
        "model API capability": "ARGUS-BACKEND-002",
        "daemon": "ARGUS-DAEMON-001",
        "lock sanity": "ARGUS-STATE-001",
        "empty session": "ARGUS-STATE-002",
    }
    return {
        "schema_version": 1,
        "ok": all(check.ok for check in checks),
        "verification": verification,
        "checks": [
            {
                "code": codes.get(check.name, "ARGUS-CHECK-001"),
                "name": check.name,
                "ok": check.ok,
                "detail": check.detail,
                "fix": check.fix,
            }
            for check in checks
        ],
    }


def _maintenance_context(args: argparse.Namespace):
    from ...core.runtime_identity import source_root
    from ...maintenance.doctor import DoctorContext

    global_root = _resolve_global_root(args)
    resume = str(getattr(args, "resume", "") or "").strip()
    project_root = (
        core_paths.session_state_root(resume, root=global_root)
        if resume else global_root
    )
    source = source_root()
    checkout = source if (source / "pyproject.toml").is_file() else None
    from ...maintenance.repair import read_path_memory

    remembered = read_path_memory(global_root)
    if checkout is None and remembered.get("checkout"):
        candidate = Path(str(remembered["checkout"])).expanduser()
        if (candidate / "pyproject.toml").is_file():
            checkout = candidate.resolve()
    install_mode = (
        "frozen" if getattr(sys, "frozen", False)
        else "source" if checkout is not None
        else "wheel"
    )
    desktop_user_data = None
    if remembered.get("desktop_user_data"):
        desktop_user_data = Path(str(remembered["desktop_user_data"])).expanduser()
    elif os.name == "nt" and os.environ.get("APPDATA"):
        desktop_user_data = Path(os.environ["APPDATA"]) / "argus-desktop"
    return DoctorContext(
        global_root=global_root,
        project_root=project_root,
        checkout=checkout,
        python_executable=Path(sys.executable),
        web_host=str(getattr(args, "web_host", "127.0.0.1") or "127.0.0.1"),
        web_port=int(getattr(args, "web_port", 8799) or 8799),
        desktop_user_data=desktop_user_data,
        install_mode=install_mode,
        backend=getattr(args, "backend", None),
        auth_mode=getattr(args, "auth_mode", None),
        allow_prerelease=bool(getattr(args, "allow_prerelease", False)),
    )


def _cmd_doctor(args: argparse.Namespace) -> int:
    import json

    from ...maintenance.doctor import render_full_report, run_full_doctor
    from ...maintenance.repair import apply_plan, create_plan

    context = _maintenance_context(args)
    report = run_full_doctor(
        context,
        include_backend=True,
        # A backend whose CLI is installed but not logged in is the first
        # thing a new user needs told. Reporting "ready" and hiding the
        # login behind --deep sends them off to fail on their first task.
        probe_auth=True,
    )
    repair_payload = None
    if bool(getattr(args, "fix_safe", False)):
        plan = create_plan(context, [item for item in report.findings if not item.ok])
        repaired = apply_plan(context, plan.plan_id, safe_only=True)
        repair_payload = repaired.to_jsonable()
        report = run_full_doctor(
            context,
            include_backend=True,
            # A backend whose CLI is installed but not logged in is the first
        # thing a new user needs told. Reporting "ready" and hiding the
        # login behind --deep sends them off to fail on their first task.
        probe_auth=True,
        )
    from ...maintenance.advisor import run_doctor_advisor

    advisor = run_doctor_advisor(
        report,
        context,
        # Diagnostics must remain read-only unless the operator explicitly
        # selects an advisor.  The top-level `--doctor` compatibility flag has
        # no --advisor argument, so its missing attribute must mean `none`.
        requested=str(getattr(args, "advisor", "none") or "none"),
        # A backend whose CLI is installed but not logged in is the first
        # thing a new user needs told. Reporting "ready" and hiding the
        # login behind --deep sends them off to fail on their first task.
        probe_auth=True,
    )
    if advisor.get("attempts"):
        report = run_full_doctor(
            context,
            include_backend=True,
            # A backend whose CLI is installed but not logged in is the first
        # thing a new user needs told. Reporting "ready" and hiding the
        # login behind --deep sends them off to fail on their first task.
        probe_auth=True,
        )
    repaired_with_tools = any(
        bool(item.get("tool_activity_observed"))
        for item in advisor.get("attempts", ())
    )
    if report.ok and repaired_with_tools and advisor["status"] == "failed":
        advisor["status"] = "completed"
        advisor["error"] = ""
        advisor["analysis"] = (
            advisor.get("analysis")
            or "Agent repairs passed final deterministic verification."
        )
        advisor["recovered_by_final_verification"] = True
    advisor["verified"] = report.ok
    advisor["remaining_findings"] = [
        item.code for item in report.findings if not item.ok
    ]
    payload = report.to_jsonable()
    payload["verification"] = bool(getattr(args, "verify", False))
    if repair_payload is not None:
        payload["repair"] = repair_payload
    agent_ok = advisor["status"] in {"completed", "disabled"}
    payload["deterministic_ok"] = report.ok
    payload["ok"] = report.ok and agent_ok
    payload["advisor"] = advisor
    if bool(getattr(args, "json", False)):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_full_report(report) + "\n")
        if advisor["status"] == "completed":
            sys.stdout.write(
                f"\nCode Agent repair ({advisor['backend']}):\n"
                f"{advisor['analysis'].strip()}\n"
            )
        elif advisor["status"] == "failed":
            sys.stdout.write(
                f"\nCode Agent repair failed ({advisor['backend']}): "
                f"{advisor['error']}\n"
            )
            if advisor.get("analysis"):
                sys.stdout.write(f"{advisor['analysis'].strip()}\n")
        elif advisor["status"] == "unavailable":
            sys.stdout.write(
                "\nCode Agent repair unavailable: no supported Agent CLI was "
                "found on PATH. Deterministic findings above are still valid.\n"
            )
        if repair_payload is not None:
            sys.stdout.write(
                f"safe repair plan {repair_payload['plan_id']}: "
                f"{repair_payload['status']}\n"
            )
    return 0 if report.ok and agent_ok else 3


def _cmd_repair(args: argparse.Namespace) -> int:
    import json

    from ...maintenance.doctor import render_full_report, run_full_doctor
    from ...maintenance.repair import (
        apply_plan,
        create_plan,
        prepare_pr_report,
        submit_pr,
    )

    context = _maintenance_context(args)
    json_output = bool(getattr(args, "json", False))
    if bool(getattr(args, "plan", False)) or bool(getattr(args, "safe", False)):
        before = run_full_doctor(context, include_backend=True, probe_auth=True)
        plan = create_plan(context, [item for item in before.findings if not item.ok])
        if bool(getattr(args, "plan", False)):
            payload = {
                "schema_version": 1,
                "mode": "plan",
                "plan_id": plan.plan_id,
                "path": str(plan.path),
                "actions": [item.to_jsonable() for item in plan.actions],
                "diagnostics": before.to_jsonable(),
            }
            rc = 0
        else:
            result = apply_plan(context, plan.plan_id, safe_only=True)
            payload = result.to_jsonable()
            rc = 0 if result.status in {"completed", "already_applied"} else 3
    elif getattr(args, "apply", None):
        result = apply_plan(
            context,
            str(args.apply),
            confirmed=bool(getattr(args, "yes", False)),
        )
        payload = result.to_jsonable()
        rc = 0 if result.status in {"completed", "already_applied"} else 3
    elif getattr(args, "prepare_pr", None):
        report_path = prepare_pr_report(context, str(args.prepare_pr))
        payload = {"schema_version": 1, "mode": "prepare-pr", "path": str(report_path)}
        rc = 0
    elif getattr(args, "submit_pr", None):
        url = submit_pr(
            context,
            str(args.submit_pr),
            confirmed=bool(getattr(args, "yes", False)),
        )
        payload = {"schema_version": 1, "mode": "submit-pr", "url": url}
        rc = 0
    else:  # pragma: no cover - argparse requires one mode
        raise ValueError("repair mode is required")

    if json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        if payload.get("diagnostics"):
            from ...maintenance.models import DoctorFinding, DoctorReport
            raw = payload["diagnostics"]
            findings = tuple(DoctorFinding(
                code=item["code"], scope=item["scope"], severity=item["severity"],
                ok=item["ok"], status=item["status"], detail=item["detail"],
                evidence=item.get("evidence") or {},
                repair_action_ids=tuple(item.get("repair_action_ids") or ()),
                recommendation=item.get("recommendation") or "",
            ) for item in raw["findings"])
            sys.stdout.write(render_full_report(DoctorReport(
                schema_version=1,
                target_fingerprint=raw["target_fingerprint"],
                generated_at=raw["generated_at"],
                findings=findings,
            )) + "\n")
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return rc


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
    """Stream WebAPI events live, falling back to durable file tailing."""
    events_path = _resolve_follow_events_path(args)
    backlog_path = events_path.parent / "backlog.jsonl"

    import json as _json

    print(
        f"argus-skill: following project {events_path.parent.name} "
        "(live WebSocket with file fallback, Ctrl-C to stop)",
        flush=True,
    )
    print("━" * 60, flush=True)
    fh = None
    current_layer = "engineer"
    current_mission: dict[str, str] = {"item_id": "", "title": "", "objective": ""}
    from ...cli.theme import Theme
    from ...core import log_view as lv
    from ._follow import _FollowCoalescer, _FollowEventRenderer
    state = lv.LogState()
    theme = Theme.auto()
    renderer = _FollowEventRenderer(theme=theme)
    last_event_at = time.monotonic()
    last_heartbeat_at = 0.0
    seen_order: deque[str] = deque(maxlen=512)
    seen: set[str] = set()

    def _emit(ev: dict) -> None:
        # Render + print exactly one committed event. Runs the stateful
        # connector/timestamp advance ONCE per printed line (not per streamed
        # beat), so coalesced messages don't desync the grouping connectors.
        nonlocal current_layer, current_mission, last_event_at, last_heartbeat_at
        explicit = str(ev.get("event_id") or ev.get("id") or "")
        seen_key = explicit or _json.dumps(
            {
                key: ev.get(key)
                for key in (
                    "type", "ts", "message_id", "kind", "agent_layer",
                    "text", "item_id", "status",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if seen_key in seen:
            return
        if len(seen_order) == seen_order.maxlen:
            seen.discard(seen_order[0])
        seen_order.append(seen_key)
        seen.add(seen_key)
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
        body = renderer.render(
            ev,
            current_layer,
            mission_context=current_mission,
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

    def _idle() -> None:
        nonlocal last_heartbeat_at
        coalescer.flush_idle()
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

    try:
        from ._follow import _stream_follow_websocket

        if not _stream_follow_websocket(
            args,
            coalescer.feed,
            on_idle=_idle,
        ):
            coalescer.flush()
            print(
                "argus-skill: live WebSocket unavailable; "
                f"falling back to {events_path}",
                flush=True,
            )
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
                time.sleep(0.5)
                _idle()
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
        renderer.close()
        if fh is not None:
            fh.close()
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    """Answer ``--ask <question>`` inline via the Manager quick-reply path.

    Reuses the same fast path the chat/web ``/ask`` is built on —
    ``build_quick_reply_prompt`` fed through ``run_exec`` via the front-door
    Manager runner — so a CLI question is answered headlessly (no TTY, no
    ``--continuous``, no daemon) and, by that path's contract, is never queued
    as a backlog item. The reply goes to stdout and the process exits 0.
    """
    question = (getattr(args, "ask", "") or "").strip()
    if not question:
        sys.stderr.write("argus-skill: --ask requires a non-empty question\n")
        return 2
    bundle = _resolve_project_bundle(args)
    from ...core.models import RunnerOptions
    from ...core.operator_context import (
        append_operator_context,
        build_operator_context_block,
        import_deterministic_credential,
    )
    from ...core.run_gateway import run_exec as gateway_run_exec
    from ...manager.config_intent import _front_door_classify
    from ...manager.front_door import _ensure_manager_runner
    from ...manager.stage_decider import extract_answer
    from ...roles.prompts.manager import build_quick_reply_prompt
    from ...webapi.manager_state import _chat_state_for

    sid = bundle.project.root.name
    chat_state = _chat_state_for(sid)
    chat_state["session_id"] = sid
    chat_state["global_root"] = str(bundle.global_root)
    question, credential = import_deterministic_credential(
        bundle.project.root,
        question,
        global_root=bundle.global_root,
    )
    chat_state["_frontdoor_credential_imported"] = credential is not None
    _front_door_classify(bundle, question, chat_state)
    runner = _ensure_manager_runner(chat_state, bundle)
    if runner is None:
        reason = str(chat_state.get("manager_runner_error") or "").strip()
        sys.stderr.write(
            "argus-skill: --ask cannot answer inline"
            + (f": {reason}" if reason else "")
            + " — nothing was queued\n"
        )
        return 1
    operator_context, _revision = build_operator_context_block(
        "manager", bundle.project.root, consume_once=False
    )
    prompt = build_quick_reply_prompt(objective=question)
    prompt = append_operator_context(prompt, operator_context)
    result = gateway_run_exec(
        runner,
        prompt=prompt,
        options=RunnerOptions(skip_git_repo_check=True),
        run_label="manager-ask",
    )
    # A permanent spawn failure (missing runner binary, backend down) is a
    # backend fault, not an empty reply. Gate on it the way the daemon gates
    # on readiness (``daemon/_life_worker_run.py:_rf_vault_preflight``), so the
    # operator sees the actual reason instead of "received an empty reply".
    exit_code = int(getattr(result, "exit_code", 0) or 0)
    fatal_error = str(getattr(result, "fatal_error", "") or "").strip()
    if exit_code != 0 or fatal_error:
        sys.stderr.write(
            "argus-skill: --ask refused before answering inline"
            + (f": {fatal_error}" if fatal_error else f" (exit {exit_code})")
            + "; nothing was queued\n"
        )
        return 1
    reply = extract_answer(result).strip()
    if not reply:
        sys.stderr.write(
            "argus-skill: --ask received an empty reply; nothing was queued\n"
        )
        return 1
    sys.stdout.write(reply.rstrip() + "\n")
    return 0


def _cmd_answer(args: argparse.Namespace) -> int:
    """Answer the question a mission is paused on, and let it run again.

    A nudge is guidance the next round happens to read. This is the other
    thing an operator needs: a mission that stopped to ask something is
    ``paused_operator`` until the question is answered, and until this
    existed the only way to clear that was the web cockpit — so an
    unattended box could sit blocked on "may I install torch?" indefinitely.

    Clearing ``pending_question`` and resuming the same item is NOT enough,
    and looks like it is: the mission runs again, re-reads the objective that
    made it ask, and asks the identical question. One live campaign burned
    five attempts that way, answered every time. ``continue_with_operator_reply``
    is the path the cockpit uses — it enqueues a CONTINUATION whose objective
    carries the answer as authority over the inherited one, so the next round
    reads what it was told instead of the question it already asked.
    """
    answer = (getattr(args, "answer", "") or "").strip()
    if not answer:
        sys.stderr.write("argus-skill: --answer requires non-empty text\n")
        return 2
    bundle = _resolve_project_bundle(args)
    from ...life.memory import Backlog

    backlog = Backlog(bundle.project.root / "backlog.jsonl")
    waiting = [
        item
        for item in backlog.active()
        if str(getattr(item, "pending_question", "") or "").strip()
    ]
    if not waiting:
        sys.stderr.write("argus-skill: no mission is waiting on an answer\n")
        return 1

    wanted = str(getattr(args, "answer_item", "") or "").strip()
    if wanted:
        waiting = [item for item in waiting if item.id == wanted]
        if not waiting:
            sys.stderr.write(f"argus-skill: {wanted} is not waiting on an answer\n")
            return 1
    elif len(waiting) > 1:
        sys.stderr.write("argus-skill: several missions are waiting; pick one with --answer-item\n")
        for item in waiting:
            sys.stderr.write(f"  {item.id}  {item.title}\n")
        return 2

    item = waiting[0]
    question = str(getattr(item, "pending_question", "") or "").strip()
    card = dict(getattr(item, "operator_decision", {}) or {})
    if card.get("decision_kind") == "framework_deployment":
        option_id = answer.casefold()
        if option_id not in {"adopt", "decline"}:
            sys.stderr.write(
                "argus-skill: answer this deployment decision with adopt or decline\n"
            )
            return 2
        from ...webapi.manager_pending_question import (
            manager_resolve_operator_decision,
        )

        result = manager_resolve_operator_decision(
            bundle.project.fingerprint,
            str(card.get("id") or ""),
            option_id,
            global_root=bundle.global_root,
        )
        if result is None or result.get("error"):
            message = str((result or {}).get("error") or "decision is unavailable")
            sys.stderr.write(f"argus-skill: {message}\n")
            return 1
        sys.stdout.write(f"argus-skill: answered {item.id} ({item.title})\n")
        reply = str(result.get("reply") or "").strip()
        if reply:
            sys.stdout.write(f"  result: {reply}\n")
        return 0

    blocked, continuation = backlog.continue_with_operator_reply(
        item.id, answer, manager_decision=answer
    )
    if blocked is None:
        sys.stderr.write(f"argus-skill: {item.id} is no longer in the backlog\n")
        return 1
    if continuation is None:
        sys.stderr.write(f"argus-skill: {item.id} is no longer waiting on an answer\n")
        return 1
    sys.stdout.write(f"argus-skill: answered {item.id} ({item.title})\n")
    if question:
        sys.stdout.write(f"  asked:  {question[:160]}\n")
    sys.stdout.write(f"  answer: {answer[:160]}\n")
    sys.stdout.write(f"  continues as: {continuation.id} ({continuation.status})\n")
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
    target_stage = str(getattr(args, "notify_stage", "") or "").strip()
    if target_stage:
        from ...daemon.state import read_daemon_status
        from ...skills.stage_machine import normalize_stage_for_project

        status = read_daemon_status(bundle.project.root)
        stage_root = (
            Path(status.project_workdir)
            if status.project_workdir
            else Path.cwd()
        )
        target_stage = normalize_stage_for_project(
            stage_root,
            target_stage,
            require_known=True,
        )
        if not target_stage:
            sys.stderr.write("argus-skill: --notify-stage is not valid for the active vertical\n")
            return 2
    queue_inbox_message(
        bundle.project.root,
        msg,
        source="cli.notify",
        stage=target_stage,
    )
    from .._inbox import inbox_path

    inbox = inbox_path(bundle.project.root, target_stage)
    suffix = f" (stage={target_stage})" if target_stage else ""
    print(f"argus-skill: queued nudge ({len(msg)} chars){suffix} → {inbox}")
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

    print(f"\nmaterial staged under {wiki_root / 'pages' / 'materials'}")
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


def _run_with_path_resolution_errors(action) -> int:
    try:
        return action()
    except core_paths.PathResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 2


def _cmd_export_builtin_skills(args: argparse.Namespace) -> int:
    from ...skills.builtins import (
        DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
        builtin_skill_source_path,
        remove_unmodified_inactive_context_skill_seeds,
        retire_orphaned_builtin_seeds,
        seed_builtin_skills,
        seed_builtin_skills_for_context,
    )
    from ...skills.vertical_select import (
        VerticalResolutionError,
        resolve_domain_if_decided,
        resolve_vertical_if_decided,
    )

    raw_target = args.export_builtin_skills or DEFAULT_PROJECT_BUILTIN_SKILLS_DIR
    target = core_paths.resolve_runtime_path(
        raw_target,
        context="--export-builtin-skills",
    )
    if not target.is_absolute():
        target = Path.cwd() / target
    # Export the target project's decided vertical, never the caller cwd's.
    # Before Manager has decided, only cross-vertical builtins are safe to seed.
    try:
        vertical = resolve_vertical_if_decided(target.parent)
        domain = resolve_domain_if_decided(target.parent)
    except VerticalResolutionError as exc:
        sys.stderr.write(f"argus-skill: cannot resolve target vertical: {exc}\n")
        return 2
    removed = remove_unmodified_inactive_context_skill_seeds(
        target,
        vertical,
        active_domain=domain,
    )
    removed = sorted({*removed, *retire_orphaned_builtin_seeds(target)})
    if vertical is not None:
        result = seed_builtin_skills_for_context(
            target,
            vertical,
            domain=domain,
            overwrite=bool(args.apply),
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
    print(f"  vertical: {vertical or 'none (common skills only)'}")
    print(f"  domain : {domain or 'none'}")
    print(
        f"  files  : {written} {action}, {skipped} preserved, "
        f"{len(result)} total"
    )
    if removed:
        print(f"  pruned : {len(removed)} inactive unmodified context seed(s)")
    if skipped and not args.apply:
        print("  hint   : pass --apply to replace existing copied built-in skill files")
    return 0


def _cmd_install_ppt_master(args: argparse.Namespace) -> int:
    from ...tools.ppt_master import install_ppt_master

    status = install_ppt_master(global_root=_resolve_global_root(args))
    sys.stdout.write(
        f"PPT Master ready at {status.skill_root}\n"
        f"revision: {status.revision}\n"
    )
    return 0


def _cmd_ppt_master_status(args: argparse.Namespace) -> int:
    from ...tools.ppt_master import ppt_master_status

    status = ppt_master_status(global_root=_resolve_global_root(args))
    sys.stdout.write(
        f"PPT Master: {status.detail}\n"
        f"root: {status.root}\n"
        f"skill: {status.skill_root}\n"
        f"revision: {status.revision or 'unknown'}\n"
    )
    return 0 if status.valid and status.dependencies_installed else 1


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
    2. ``<bundle.project.root>/code/`` for compatibility with legacy nested
       project layouts
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


def _cmd_gc(args: argparse.Namespace) -> int:
    """Prune stale projects (no live daemon + untouched for --gc-days)."""
    from ...core.project_gc import gc_stale_projects, retention_days_default

    root = _resolve_global_root(args)
    days = getattr(args, "gc_days", None)
    if days is None:
        days = retention_days_default()
    if days < 0:
        sys.stderr.write(
            f"argus-skill: --gc-days must not be negative (got {days}). A "
            "negative retention window puts the cutoff in the future, so every "
            "project would be trashed.\n"
        )
        return 2
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


def _latest_user_visible_reply(project_root: Path, *, limit: int = 240) -> str:
    """Return the latest cockpit reply without replaying internal role events."""
    path = project_root / "events.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            handle.seek(max(0, end - 64 * 1024))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines[-200:]):
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if str(event.get("type") or "") != "ui.argus":
            continue
        text = _clean_follow_text(str(event.get("text") or ""), limit=limit)
        if text:
            return text
    return ""


def _cmd_status(args: argparse.Namespace) -> int:
    from ...daemon.life_worker import (
        format_budget_status,
        read_continuous_state,
        read_daemon_status,
    )
    bundle = _resolve_project_bundle(args, create_if_missing=False)
    if bundle is None:
        print(f"argus-skill — global-root: {_resolve_global_root(args)}")
        print("  project  : no session for this workdir")
        print("  daemon   : not running")
        print("  next     : run `argus` to create a session")
        return 0
    status = read_daemon_status(bundle.project.root)
    all_items = bundle.backlog.history()
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
        width = (
            f", width {getattr(status, 'mission_width', None)}"
            if getattr(status, "mission_width", None) is not None
            else ""
        )
        print(
            f"  daemon   : alive (pid {status.pid}, up {uptime}, "
            f"backend {backend_label}{width})"
        )
        health_state = getattr(status, "health_state", None)
        if health_state is not None:
            health_detail = (
                ", no progress for "
                + _format_short_duration(
                    getattr(status, "seconds_since_progress", 0.0) or 0.0
                )
                if bool(getattr(status, "stalled", False))
                else ""
            )
            print(f"  health   : {health_state}{health_detail}")
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
    print(f"  inbox    : {count_pending_inbox_messages(bundle.project.root)} pending")
    # Items written straight into backlog.jsonl bypass the Manager, so no
    # vertical is chosen and the run merely looks like the Manager is idle.
    # Nothing errors, so say it here or it stays invisible.
    from ...life.supervisor.backlog_guard import describe_undecided

    _undecided = describe_undecided(bundle.backlog.active())
    if _undecided:
        print(f"  manager  : {_undecided}")
    # The one thing an operator most needs from --status: a run that stopped
    # because it needs *them*. A blocked reviewer verdict carrying an
    # operator_question is persisted on the item precisely so this can list it,
    # but nothing did — a real run tonight ended with
    # "Provision CUDA-visible NVIDIA GPU and CUDA C++/cuBLAS toolchain" waiting
    # and --status reported only "outcome: blocked", leaving the operator to
    # dig through events.jsonl to find out what was being asked.
    waiting_on_operator = [
        item
        for item in all_items
        if str(getattr(item, "pending_question", "") or "").strip()
    ]
    if waiting_on_operator:
        print(f"  waiting on you : {len(waiting_on_operator)} unanswered question(s)")
        for item in waiting_on_operator:
            question = _clean_follow_text(
                str(getattr(item, "pending_question", "")), limit=160
            )
            title = _clean_follow_text(
                str(getattr(item, "title", "current task")), limit=80
            )
            print(f"    - {title}: {question}")
        print("    answer with: argus (then just reply), or argus --notify '<answer>'")
    history_parts = [part for part in (
        f"{done} done" if done else "",
        f"{failed} failed" if failed else "",
        f"{skipped} skipped" if skipped else "",
    ) if part]
    if history_parts:
        print(f"  history  : {' · '.join(history_parts)}")
    outcome_items = [
        (float(getattr(item, "finished_ts", 0.0) or 0.0), index, item)
        for index, item in enumerate(all_items)
        if outcome_dimension_summary(getattr(item, "outcome", None))
    ]
    if outcome_items:
        latest_outcome_item = max(outcome_items)[2]
        summary = outcome_dimension_summary(
            getattr(latest_outcome_item, "outcome", None)
        )
        print(f"  result   : {' · '.join(summary)}")
    latest_reply = _latest_user_visible_reply(Path(bundle.project.root))
    if latest_reply:
        print(f"  last reply: {latest_reply}")
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
    active_vertical = None
    try:
        from ...skills.vertical_select import (
            resolve_domain_if_decided,
            resolve_vertical_if_decided,
        )

        active_vertical = resolve_vertical_if_decided(research_workdir)
        active_domain = resolve_domain_if_decided(research_workdir)
        if active_vertical:
            domain_suffix = f" · domain={active_domain}" if active_domain else ""
            print(f"  pipeline : vertical={active_vertical}{domain_suffix}")
    except Exception:  # noqa: BLE001 - status projection remains best effort
        pass
    if all_items or cont.objective or active_vertical or not latest_reply:
        lifecycle_lines = _render_lifecycle_status_lines(
            research_workdir,
            state_root=Path(bundle.project.root),
        )
        for line in lifecycle_lines:
            print(line)
    elif latest_reply:
        print("  lifecycle: chat/local task (no background campaign)")

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
