"""argus-skill CLI — single-entry 7×24 lifetime agent.

The product has exactly one positioning: a long-running supervised
coding agent that drains a backlog forever. There is therefore exactly
one entry point — ``argus-skill`` — which:

* drops you into the unified life REPL (the cockpit), and
* by default ensures a detached daemon is alive draining the backlog
  in the background even after you log out.

Top-level flags control daemon lifecycle and read-only operator help
(``--daemon``, ``--daemon-fg``, ``--daemon-stop``, ``--status``,
``--daemon-runbook``, ``--no-daemon``). The only subcommand is a small
admin helper for explicitly bootstrapping and backfilling per-project
idea wikis: ``argus-skill wiki init <project>`` and
``argus-skill wiki ingest --wiki <path>``. The REPL and backlog remain
the single runtime workflow.
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


def _resolve_project_bundle(args: argparse.Namespace):
    from ...life import MemoryBundle

    return MemoryBundle.for_cwd(Path.cwd(), global_root=_resolve_global_root(args))


def _lifetime_entry_error(args: argparse.Namespace) -> str:
    """Return an actionable error if the lifetime agent is under-configured.

    The lifetime daemon / cockpit refuses to start unless the operator has
    explicitly supplied BOTH a mission objective and at least one trusted
    special prompt (machine house rules). This replaces any implicit guessing:
    the agent must be told its mission and its operating rules up front.

    The objective is satisfied by ``--objective`` (which requires
    ``--continuous``) or by a previously-persisted ``continuous.json`` for the
    current project. Returns ``""`` when both requirements are met.
    """
    from ...daemon.life_worker import read_continuous_config
    from ...life.special_prompts import describe_special_prompt_gate

    objective = str(getattr(args, "objective", "") or "").strip()
    if not objective:
        try:
            bundle = _resolve_project_bundle(args)
            _, persisted = read_continuous_config(bundle.project.root)
            objective = persisted.strip()
        except Exception:  # noqa: BLE001 — under-configured path resolution
            objective = ""
    if not objective:
        return (
            "no mission objective configured — the lifetime agent must be told "
            "what to work on. Launch with `--continuous --objective \"<goal>\"` "
            "(persisted to <life_dir>/continuous.json for later runs)."
        )

    ok, detail = describe_special_prompt_gate()
    if not ok:
        return detail
    return ""



_FOLLOW_HEARTBEAT_SECONDS = 20.0
































def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.skill_stats = bool(args.skill_stats or args.skill_stats_json)
    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
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
        + bool(args.watch)
        + bool(args.follow)
        + bool(getattr(args, "dashboard", False))
        + bool(args.notify)
        + bool(args.init_identity)
        + bool(args.setup)
        + bool(args.model_api_status)
        + bool(args.init_model_api)
        + bool(args.skill_stats)
        + bool(args.skill_cleanse)
        + bool(args.skill_compact)
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
            "--daemon-runbook / --watch / --follow / --notify / --init-identity / "
            "--model-api-status / --init-model-api / --skill-stats / "
            "--skill-cleanse / --skill-compact / --export-builtin-skills / "
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
    if args.watch:
        return _run_with_path_resolution_errors(lambda: _cmd_watch(args))
    if args.follow:
        return _run_with_path_resolution_errors(lambda: _cmd_follow(args))
    if getattr(args, "dashboard", False):
        from ...tools.dashboard import serve
        return serve(port=int(getattr(args, "dashboard_port", 8787) or 8787))
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
    if args.skill_compact:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_compact(args))
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

    # Default path: the unified Manager-conversation REPL. A bare interactive
    # launch needs NO pre-set objective — the operator states the task in the
    # conversation, and the REPL auto-spawns a daemon that drains whatever gets
    # queued. But ``--continuous`` puts the auto-spawned daemon into autonomous
    # 7×24 mode, which DOES require a mission objective + house rules (same gate
    # as the explicit ``--daemon``). So hard-gate only the continuous case; a
    # bare launch always enters the conversation.
    if getattr(args, "continuous", False):
        entry_error = _lifetime_entry_error(args)
        if entry_error:
            sys.stderr.write(f"argus-skill: {entry_error}\n")
            return 2

    from ...manager.repl import run_manager_repl
    from ...tools.capability_vault import resolve_route_model

    repl_args = argparse.Namespace(
        life_dir=args.life_dir,
        color=None,
        backend=backend_default,
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL")
        or resolve_route_model("engineer"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
        engineer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high"
        ),
        reviewer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
        ),
        plan_mode="auto",
        plan_model=None,
        max_rounds=500,
        check=[],
        workdir=None,
        no_daemon=bool(args.no_daemon),
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
        bounded=bool(getattr(args, "bounded", False)),
    )
    return _run_with_path_resolution_errors(lambda: run_manager_repl(repl_args))


# ---------------------------------------------------------------------------
# 7×24 daemon dispatchers
# ---------------------------------------------------------------------------


def _build_worker_config(args: argparse.Namespace):
    from ...daemon.life_worker import LifeWorkerConfig
    bundle = _resolve_project_bundle(args)
    backend = getattr(args, "backend", None) or os.environ.get(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
    )
    from ...tools.capability_vault import resolve_route_model

    return LifeWorkerConfig(
        life_dir=bundle.project.root,
        global_root=bundle.global_root,
        project_workdir=Path.cwd(),
        project_fingerprint=bundle.project.fingerprint,
        project_label=bundle.project.label,
        backend=backend,
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL")
        or resolve_route_model("engineer"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL")
        or resolve_route_model("reviewer"),
        engineer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high"
        ),
        reviewer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
        ),
        per_mission_cap_usd=float(os.environ.get("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0")),
        daily_cap_usd=float(os.environ.get("ARGUS_SKILL_DAILY_CAP_USD", "180.0")),
        planner_task_iteration_max_cycles=int(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "6")),
        planner_task_iteration_budget_usd=float(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_BUDGET_USD", "30.0")),
        poll_interval=float(os.environ.get("ARGUS_SKILL_DAEMON_POLL_S", "5.0")),
        continuous=getattr(args, "continuous", False),
        continuous_objective=getattr(args, "objective", ""),
        continuous_open_ended=not bool(getattr(args, "bounded", False)),
    )


def _cmd_daemon_start(args: argparse.Namespace, *, foreground: bool) -> int:
    from ...daemon.life_worker import run_foreground, spawn_detached_daemon
    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
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
    return spawn_detached_daemon(cfg)


def _cmd_daemon_stop(args: argparse.Namespace) -> int:
    from ...daemon.life_worker import stop_daemon
    bundle = _resolve_project_bundle(args)
    return stop_daemon(
        bundle.project.root,
        drain=bool(getattr(args, "drain", False)),
        force=bool(getattr(args, "force", False)),
    )


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
    state = lv.LogState()
    theme = Theme.auto()
    last_event_at = time.monotonic()
    last_heartbeat_at = 0.0
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
            )
            if body:
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
    except KeyboardInterrupt:
        print("\nargus-skill: stopped following", flush=True)
    finally:
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


def _cmd_skill_compact(args: argparse.Namespace) -> int:
    from ...skills.compaction import DEFAULT_SIM_THRESHOLD, run_compact
    threshold = (
        float(args.sim_threshold)
        if args.sim_threshold is not None
        else DEFAULT_SIM_THRESHOLD
    )
    return run_compact(
        _resolve_skills_dir(args),
        sim_threshold=threshold,
        dry_run=not bool(args.apply),
    )


def _cmd_export_builtin_skills(args: argparse.Namespace) -> int:
    from ...skills.builtins import (
        DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
        builtin_skill_source_path,
        seed_builtin_skills,
        seed_builtin_skills_for_vertical,
    )
    from ...skills.vertical_select import resolve_vertical

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
    # (env ARGUS_SKILL_VERTICAL wins) in the target/cwd.
    vertical = getattr(args, "vertical", None) or resolve_vertical(Path.cwd())
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

    root = Path(args.project_root).resolve()
    if not root.exists():
        sys.stderr.write(f"argus-skill: project root not found: {root}\n")
        return 2

    status = infer_observable_status(root, project_id=root.name)
    try:
        persisted = load_persisted(root)
    except LifecycleIOError as exc:
        sys.stderr.write(
            f"argus-skill: lifecycle sidecar at {root}/lifecycle.json is "
            f"malformed: {exc}\n"
        )
        persisted = {}
    overlaid = apply_persisted_to_status(status, persisted)
    event = decide_next_state(overlaid)
    history = load_history(root)
    signals = advisory_time_signals(overlaid)

    print("argus-skill — project lifecycle (F5)")
    print(f"  root              : {root}")
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

    root = Path(args.project_root).resolve()
    if not root.exists():
        sys.stderr.write(f"argus-skill: project root not found: {root}\n")
        return 2

    status = infer_observable_status(root, project_id=root.name)
    try:
        persisted = load_persisted(root)
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
        append_event(root, new_status=new_status, event=event)
    except OSError as exc:
        sys.stderr.write(f"argus-skill: cannot persist transition: {exc}\n")
        return 1

    print(
        f"argus-skill: lifecycle transition "
        f"{event.from_state.value} → {event.to_state.value} "
        f"({event.reason})"
    )
    print(f"  root  : {root}")
    print(f"  state : {new_status.state.value}")
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
        stage = data.get("current_stage")
        return str(stage) if stage else None
    except (OSError, ValueError):
        return None


def _render_lifecycle_status_lines(workdir: Path) -> list[str]:
    """Render the F5 lifecycle block for --status / cockpit.

    Pure projection of observable + persisted state through the F5
    state machine. Returns the lines to print (no I/O of its own).
    Fail-soft: any error returns an empty list — --status must not
    crash on a missing or corrupt lifecycle sidecar.
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
        status = infer_observable_status(workdir, project_id=workdir.name)
        try:
            persisted = load_persisted(workdir)
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


def _render_stage_budget_lines(bundle: Any, *, current_stage: str | None) -> list[str]:
    """Render per-stage budget snapshot for --status. Facts-only; the
    reviewer / planner agent decides whether to act on advisories.
    Fail-soft: any error returns []."""
    try:
        from ...life.stage_budget import compute_snapshot
    except Exception:  # noqa: BLE001
        return []
    try:
        from ...daemon.life_worker import resolve_effective_budget
        eff = resolve_effective_budget()
        total_budget = float(getattr(eff, "daily_cap_usd", 180.0) or 180.0)
    except Exception:  # noqa: BLE001
        total_budget = 180.0
    try:
        entries = list(bundle.journal.all())
    except Exception:  # noqa: BLE001
        return []

    snap = compute_snapshot(
        journal_entries=entries,
        total_budget_usd=total_budget,
        current_stage=current_stage,
    )
    if snap.total_spent_usd <= 0.0 and not snap.spent_by_stage:
        return []

    lines = ["  stage_budget:"]
    for stage, amount in sorted(snap.spent_by_stage.items(), key=lambda kv: -kv[1]):
        fraction = (amount / total_budget * 100.0) if total_budget else 0.0
        lines.append(
            f"    {stage:14s} ${amount:7.2f}  ({fraction:5.1f}% of ${total_budget:.0f})"
        )
    if snap.advisory_signals:
        lines.append(
            f"    advisory      : {len(snap.advisory_signals)} signal(s) "
            f"(stage > 30% of total — agent reads, harness does not act)"
        )
        for sig in snap.advisory_signals:
            lines.append(f"      - [{sig.stage}] {sig.message}")
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


def _cmd_status(args: argparse.Namespace) -> int:
    from ...daemon.life_worker import (
        format_budget_status,
        read_continuous_state,
        read_daemon_status,
    )
    bundle = _resolve_project_bundle(args)
    status = read_daemon_status(bundle.project.root)
    all_items = bundle.backlog.all()
    pending, running, done, failed, skipped = count_backlog_statuses(all_items)
    current_running = select_current_running_item(all_items)
    # Status should stay cheap even on a long-lived daemon.
    journal_tail = bundle.journal.tail(3)

    print(f"argus-skill — global-root: {bundle.global_root}")
    print(f"  project  : {bundle.project.root}")
    if status.alive and status.pid is not None:
        uptime = _format_short_duration(status.uptime_seconds or 0.0)
        backend = status.backend or "?"
        print(f"  daemon   : alive (pid {status.pid}, up {uptime}, backend {backend})")
    else:
        print("  daemon   : not running   (start with `argus-skill --daemon`)")
    print(f"  {format_budget_status(bundle.journal, status=status)}")
    print(f"  active   : {pending} pending · {running} running")
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
    # Total cost from journal
    try:
        total_cost = bundle.journal.total_cost_since(0)
        print(f"  cost     : ${total_cost:.2f} total")
    except Exception:  # noqa: BLE001
        pass
    if running and not (status.alive and status.pid is not None):
        print(
            "             ↳ orphan running items will be reaped to `failed` "
            "when a worker (REPL or --daemon) next starts."
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
    lifecycle_lines = _render_lifecycle_status_lines(research_workdir)
    for line in lifecycle_lines:
        print(line)
    current_stage = _read_current_stage(research_workdir)
    gate_lines = _render_gate_snapshot_lines(research_workdir, current_stage)
    for line in gate_lines:
        print(line)

    # Per-stage budget snapshot (Opt #2). Surfaces facts only: how
    # much each stage has spent + an advisory when any stage has
    # eaten >30% of total budget. Reviewer / planner agent decides
    # what to do; harness does not auto-quarantine on spend.
    budget_lines = _render_stage_budget_lines(bundle, current_stage=current_stage)
    for line in budget_lines:
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
