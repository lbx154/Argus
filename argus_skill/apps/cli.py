"""argus-skill CLI app.

Minimal v0.1 surface: ``argus-skill run "task" [--check 'cmd'] ...``.

Defaults to a placeholder backend that errors out — real production use
requires importing your codex / claude adapter and wiring it in. For
turnkey local use, set ``ARGUS_SKILL_BACKEND`` env var to ``memory`` for
the deterministic stub (useful for smoke-testing the loop without an
LLM).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..adapters.memory_backend import CannedResponse, MemoryBackend
from ..core.ports import RunnerBackend
from ..loop import SkillLoop, SkillLoopConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="argus-skill")
    sub = parser.add_subparsers(dest="cmd", required=False)

    run_p = sub.add_parser("run", help="run a task through the supervised skill loop")
    run_p.add_argument("task", help="task description (free-form prompt)")
    run_p.add_argument(
        "--skills-dir",
        default="skills",
        help="markdown skill cache directory (default: ./skills)",
    )
    run_p.add_argument(
        "--workdir",
        default=None,
        help="working directory the engineer runs in (default: cwd)",
    )
    run_p.add_argument(
        "--check",
        action="append",
        default=[],
        help="acceptance check command (may repeat). Reviewer + check pass needed for `done`.",
    )
    run_p.add_argument("--max-rounds", type=int, default=3)
    run_p.add_argument(
        "--scientist-model",
        default=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
    )
    run_p.add_argument(
        "--engineer-model",
        default=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini"),
    )
    run_p.add_argument(
        "--reviewer-model",
        default=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
    )
    run_p.add_argument(
        "--no-skill-writeback",
        dest="skill_writeback",
        action="store_false",
        default=True,
    )
    run_p.add_argument(
        "--no-distill-on-miss",
        dest="distill_on_miss",
        action="store_false",
        default=True,
    )
    run_p.add_argument("--quiet", action="store_true")

    sub.add_parser("list-skills", help="list skills currently in the cache").add_argument(
        "--skills-dir", default="skills"
    )

    from .daemon_app import add_daemon_subcommands
    add_daemon_subcommands(sub)
    from .chat_app import add_chat_subcommand
    add_chat_subcommand(sub)
    from .mission_app import add_mission_subcommands
    add_mission_subcommands(sub)
    from .go_app import add_go_subcommand
    add_go_subcommand(sub)
    return parser


def _load_backend() -> RunnerBackend:
    """Pick a runner backend.

    For v0.1, only the in-memory stub is wired up here. Real usage
    requires the user to import their backend (e.g. CodexBackend,
    ClaudeBackend) and call SkillLoop directly. This indirection keeps
    the CLI usable for smoke/demo without forcing a CLI dependency on
    a specific external tool.
    """
    backend_name = os.environ.get("ARGUS_SKILL_BACKEND", "memory").lower()
    if backend_name == "memory":
        return _build_demo_memory_backend()
    if backend_name == "codex":
        # Lazy import: ArgusBot is an optional runtime dependency.
        try:
            from ..adapters.codex_backend import build_codex_backend_from_env
        except ImportError as exc:
            raise SystemExit(
                f"Codex backend requested but unavailable: {exc}.\n"
                "Install ArgusBot (`pip install -e /path/to/ArgusBot`) "
                "or set ARGUS_SKILL_BACKEND=memory."
            ) from exc
        return build_codex_backend_from_env()
    raise SystemExit(
        f"Backend '{backend_name}' is not wired into the CLI.\n"
        "Supported: 'memory' (stub) or 'codex' (real CLI via ArgusBot).\n"
        "For custom backends, import argus_skill.SkillLoop from your own\n"
        "script and pass a runner backend that wraps your CLI."
    )


def _build_demo_memory_backend() -> MemoryBackend:
    """Memory backend with enough canned responses to demo a 1-round done."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue(
        "distiller",
        CannedResponse(message=(
            "## Title\nDemo capability\n\n"
            "## Description\nA capability for demo purposes.\n\n"
            "## Category\ndemo\n\n"
            "## When to use\n- demo tasks\n\n"
            "## When NOT to use\n- real production work\n\n"
            "## How to solve\n- Read the task.\n- Reply concisely.\n\n"
            "## Examples\n- (none)\n\n"
            "## Response shape\n- Reply inline.\n"
        )),
    )
    backend.queue(
        "engineer-r1",
        CannedResponse(message=(
            "Done: read the task and replied as instructed.\n"
            "Remaining: none.\n"
            "Blockers: none.\n"
        )),
    )
    backend.queue(
        "reviewer",
        CannedResponse(message=json.dumps({
            "status": "done",
            "confidence": 0.9,
            "reason": "Task met the demo criterion.",
            "next_action": "No further action needed.",
            "round_summary_markdown": "# Review Summary\n\n- Demo objective complete.\n",
            "completion_summary_markdown": "Demo complete.",
        })),
    )
    return backend


def cmd_run(args: argparse.Namespace) -> int:
    runner = _load_backend()

    def on_event(event: dict) -> None:
        if args.quiet:
            return
        kind = event.get("type", "?")
        text = event.get("text", "")
        sys.stderr.write(f"[{kind}] {text}\n")
        sys.stderr.flush()

    config = SkillLoopConfig(
        scientist_model=args.scientist_model,
        engineer_model=args.engineer_model,
        reviewer_model=args.reviewer_model,
        max_rounds=args.max_rounds,
        check_commands=list(args.check or []),
        skill_writeback=args.skill_writeback,
        distill_on_miss=args.distill_on_miss,
    )
    loop = SkillLoop(
        skills_dir=Path(args.skills_dir),
        scientist_runner=runner,
        engineer_runner=runner,
        reviewer_runner=runner,
        config=config,
        on_event=None if args.quiet else on_event,
    )
    workdir = Path(args.workdir) if args.workdir else Path.cwd()
    outcome = loop.run(args.task, workdir=workdir)

    print(json.dumps({
        "status": outcome.status,
        "rounds": outcome.round_count,
        "skill_used": outcome.skill_used,
        "skill_distilled": outcome.skill_distilled,
        "reason": outcome.reason,
        "final_message": outcome.final_message,
    }, ensure_ascii=False, indent=2))
    return 0 if outcome.successful else 1


def cmd_list_skills(args: argparse.Namespace) -> int:
    from ..skills.store import SkillStore
    store = SkillStore(Path(args.skills_dir))
    summaries = store.list_summaries()
    if not summaries:
        print("(no skills)")
        return 0
    for s in summaries:
        print(f"- {s['name']}  ({s.get('category') or '-'})  {s['description']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # No subcommand → drop straight into `go` (one-command-to-chat).
    if not args.cmd:
        from .go_app import cmd_go
        go_args = argparse.Namespace(
            cmd="go",
            objective=None,
            state_dir="~/.argus-skill/mission-state",
            skills_dir="/home/argustest/argus-skill/skills",
            plan_mode="auto",
            max_rounds=20,
            check=[],
            workdir=None,
            attach_only=False,
            shutdown_timeout=90,
            quiet=False,
            color=None,
        )
        return cmd_go(go_args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "list-skills":
        return cmd_list_skills(args)
    if args.cmd == "daemon":
        from .daemon_app import cmd_daemon
        return cmd_daemon(args, runner_factory=_load_backend)
    if args.cmd == "daemon-status":
        from .daemon_app import cmd_daemon_status
        return cmd_daemon_status(args)
    if args.cmd == "daemon-stop":
        from .daemon_app import cmd_daemon_stop
        return cmd_daemon_stop(args)
    if args.cmd == "daemon-inject":
        from .daemon_app import cmd_daemon_inject
        return cmd_daemon_inject(args)
    if args.cmd == "daemon-run":
        from .daemon_app import cmd_daemon_run
        return cmd_daemon_run(args)
    if args.cmd == "chat":
        from .chat_app import cmd_chat
        return cmd_chat(args)
    if args.cmd == "mission":
        from .mission_app import cmd_mission
        return cmd_mission(args)
    if args.cmd == "go":
        from .go_app import cmd_go
        return cmd_go(args)
    parser.print_help()
    return 2
