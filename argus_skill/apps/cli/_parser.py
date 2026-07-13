"""Argument parser construction for the unified ``argus-skill`` CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

_PUBLIC_HELP = """usage: argus

Start Argus:
  argus

Then type what you need in natural language.

Examples:
  "帮我优化这个项目"
  "继续上次的任务"
  "现在在干什么？"
  "暂停一下"
  "换成 copilot 后端"
  "把模型换成 claude-sonnet-5"

Argus has one user-facing mode: the cockpit. The Manager decides whether your
message is chat, status, resume, configuration, planning, or real work.
"""


class _ArgusArgumentParser(argparse.ArgumentParser):
    """Public help is product-facing; full flag help is an internal debug view."""

    def format_help(self) -> str:
        if os.environ.get("ARGUS_SKILL_DEBUG_HELP", "").strip() == "1":
            return super().format_help()
        return _PUBLIC_HELP


def build_parser() -> argparse.ArgumentParser:
    from ... import __version__
    from ...skills.builtins import DEFAULT_PROJECT_BUILTIN_SKILLS_DIR

    parser = _ArgusArgumentParser(
        prog="argus",
        description="Argus — one cockpit; describe what you need.",
        # Disable prefix abbreviation so a subcommand flag like
        # ``wiki ingest --init`` is not rejected as an ambiguous abbreviation
        # of top-level options (``--init-identity`` / ``--init-model-api``).
        # argparse pre-scans every option-like token against the top-level
        # parser before delegating to a subparser; with abbreviation enabled
        # that mis-classifies ``--init`` and exits 2 on Python <= 3.12.
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"argus-skill {__version__}",
    )

    daemon_grp = parser.add_argument_group("7×24 daemon")
    daemon_grp.add_argument(
        "--daemon",
        action="store_true",
        help="start a detached background worker that drains the backlog forever",
    )
    daemon_grp.add_argument(
        "--daemon-fg",
        action="store_true",
        help="run the worker in the foreground (for systemd / debugging)",
    )
    daemon_grp.add_argument(
        "--daemon-stop",
        action="store_true",
        help="send SIGTERM to the current project's daemon",
    )
    daemon_grp.add_argument(
        "--drain",
        action="store_true",
        help="with --daemon-stop: quiesce continuous mode and wait for the "
        "current mission to finish at its natural boundary before exiting "
        "(no mid-mission SIGKILL) — the safe stop for a code-reload restart",
    )
    daemon_grp.add_argument(
        "--force",
        action="store_true",
        help="with --daemon-stop: SIGKILL the daemon if it does not exit in time "
        "(interrupts a running mission / eval)",
    )
    daemon_grp.add_argument(
        "--status",
        action="store_true",
        help="print the current project daemon + backlog status and exit",
    )
    daemon_grp.add_argument(
        "--daemon-runbook",
        action="store_true",
        help="print the daemon-safe upgrade / restart playbook and exit",
    )
    daemon_grp.add_argument(
        "--config-help",
        action="store_true",
        help="print the operator-facing ARGUS_* control knobs (default + current value) and exit",
    )
    daemon_grp.add_argument(
        "--config-snapshot",
        nargs="?",
        const="argus_runtime_settings.md",
        default=None,
        metavar="PATH",
        help="write the resolved backend/model/effort + ARGUS_* knob snapshot "
             "to PATH (default: ./argus_runtime_settings.md; .json writes JSON)",
    )
    daemon_grp.add_argument(
        "--gc",
        action="store_true",
        help="garbage-collect stale projects (no live daemon + untouched for "
        "--gc-days) by moving them to ~/.argus-skill/projects_trash/, then exit",
    )
    daemon_grp.add_argument(
        "--gc-days",
        type=int,
        default=None,
        metavar="N",
        help="with --gc: retention window in days (default 30, or "
        "ARGUS_SKILL_PROJECT_RETENTION_DAYS)",
    )
    daemon_grp.add_argument(
        "--gc-dry-run",
        action="store_true",
        help="with --gc: list what WOULD be pruned without moving anything",
    )
    daemon_grp.add_argument(
        "--no-daemon",
        action="store_true",
        help="skip auto-spawning the background daemon when entering the cockpit",
    )
    daemon_grp.add_argument(
        "--life-dir",
        default=None,
        help="override the global argus-skill root (default: ~/.argus-skill)",
    )
    daemon_grp.add_argument(
        "--new",
        action="store_true",
        help="start a FRESH session (the default for a bare `argus-skill`): a new "
        "id keys its own daemon + memory, never reusing a previous run",
    )
    daemon_grp.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="ID",
        help="resume a previous session: `--resume` opens a picker of recent "
        "sessions; `--resume <id>` jumps straight to one",
    )
    daemon_grp.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="resume the most-recently-active session",
    )
    daemon_grp.add_argument(
        "--continuous",
        action="store_true",
        help="enable continuous planner mode (daemon generates new tasks "
             "when backlog is empty)",
    )
    daemon_grp.add_argument(
        "--objective",
        default="",
        help="continuous improvement objective (used with --continuous)",
    )
    daemon_grp.add_argument(
        "--resume-continuous",
        dest="resume_continuous",
        action="store_true",
        help="resume THIS project's persisted continuous campaign "
             "(<life_dir>/continuous.json) if one is armed. Off by default so a "
             "fresh/manual daemon never silently inherits a campaign it was not "
             "asked to run; supervisors (systemd / keepalive) pass this to "
             "auto-heal a restarted campaign daemon.",
    )
    daemon_grp.add_argument(
        "--bounded",
        action="store_true",
        help="treat the mission as a bounded one-shot goal: hard-stop once the "
             "planner certifies project_done (default: open-ended — the agent "
             "keeps generating new work forever)",
    )

    cockpit_grp = parser.add_argument_group("cockpit")
    cockpit_grp.add_argument(
        "--watch",
        action="store_true",
        help="open the live read-only cockpit for the current project",
    )
    cockpit_grp.add_argument(
        "--notify",
        metavar="MSG",
        help="append a nudge message to the supervisor's inbox (the next "
             "engineer round picks it up as operator guidance)",
    )
    cockpit_grp.add_argument(
        "--follow",
        action="store_true",
        help="stream daemon events to terminal in real-time "
             "(like tail -f, Ctrl-C to stop)",
    )
    cockpit_grp.add_argument(
        "--dashboard",
        action="store_true",
        help="serve a live HTML dashboard of ALL discovered daemons "
             "(auto-discovers every project under the life root(s); "
             "set ARGUS_SKILL_DASHBOARD_ROOTS to include custom --life-dir "
             "roots). Vertical-agnostic; Ctrl-C to stop.",
    )
    cockpit_grp.add_argument(
        "--dashboard-port",
        type=int,
        default=8787,
        help="port for --dashboard (default 8787)",
    )
    cockpit_grp.add_argument(
        "--web",
        action="store_true",
        help="serve the web/TUI backend API (argus-skill[web] extra) — the "
             "shared API that the React web UI (frontend/web) and the Ink "
             "terminal UI (frontend/tui) both talk to. Binds 127.0.0.1 by "
             "default; set ARGUS_SKILL_WEB_TOKEN to require a bearer token.",
    )
    cockpit_grp.add_argument(
        "--web-host",
        default="127.0.0.1",
        help="bind host for --web (default 127.0.0.1; use 0.0.0.0 to expose on "
             "the LAN — only with ARGUS_SKILL_WEB_TOKEN set)",
    )
    cockpit_grp.add_argument(
        "--web-port",
        type=int,
        default=8799,
        help="port for --web (default 8799)",
    )
    cockpit_grp.add_argument(
        "--init-identity",
        action="store_true",
        help="run the interactive identity-card wizard "
             "(never overwrites an existing card)",
    )

    capability_grp = parser.add_argument_group("capability config")
    capability_grp.add_argument(
        "--setup",
        action="store_true",
        help="run the interactive setup wizard (API + GPU configuration)",
    )
    capability_grp.add_argument(
        "--model-api-status",
        action="store_true",
        help="print the unified model/image API capability status without secrets",
    )
    capability_grp.add_argument(
        "--init-model-api",
        action="store_true",
        help="import OPENAI_* / Codex config into the private capability vault "
             "(~/.argus-skill/capabilities/model_api.json, mode 0600)",
    )

    skills_grp = parser.add_argument_group("skill admin")
    skills_grp.add_argument(
        "--skill-stats",
        action="store_true",
        help="print empirical skill effectiveness report (hit-rate, "
             "mean rounds with/without skill) and exit",
    )
    skills_grp.add_argument(
        "--skill-stats-json",
        action="store_true",
        help="render the skill-stats output as JSON instead of plain text",
    )
    skills_grp.add_argument(
        "--skill-cleanse",
        action="store_true",
        help="strip historic 'Memory context' boilerplate from existing skill "
             "task_history entries (idempotent migration)",
    )
    skills_grp.add_argument(
        "--export-builtin-skills",
        nargs="?",
        const=DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
        default=None,
        metavar="DIR",
        help="copy packaged built-in skill markdown into DIR for a project "
             "(default: ./argus_builtin_skills; preserves existing files)",
    )
    skills_grp.add_argument(
        "--apply",
        action="store_true",
        help="with --skill-cleanse: actually mutate disk "
             "(default is dry-run); with --export-builtin-skills: replace "
             "existing copied built-in files",
    )
    skills_grp.add_argument(
        "--skills-dir",
        default=None,
        help="override skills directory (default: global skills root)",
    )

    gates_grp = parser.add_argument_group("research-factory gates")
    gates_grp.add_argument(
        "--evidence-chain-check",
        action="store_true",
        help="run F4 evidence-chain validator on a project root and exit; "
             "prints broken chains and exits non-zero if any claim ↔ "
             "evidence ↔ bundle link is broken",
    )
    gates_grp.add_argument(
        "--anti-mediocrity-check",
        action="store_true",
        help="run F3 anti-mediocrity gates (baseline-reproduction, "
             "Δ-reward, benchmark-diversity) and exit; requires "
             "--proposed-condition and --baseline-condition to enable "
             "the comparison gates",
    )
    gates_grp.add_argument(
        "--lifecycle-status",
        action="store_true",
        help="print F5 project-lifecycle state derived from project memory "
             "(incubating/running/writing/quarantined/done/archived) and exit",
    )
    gates_grp.add_argument(
        "--lifecycle-resume",
        action="store_true",
        help="resume a quarantined project; clears persisted quarantine "
             "state in <life-dir>/lifecycle.json so the supervisor will "
             "dispatch missions again",
    )
    gates_grp.add_argument(
        "--lifecycle-archive",
        action="store_true",
        help="archive the project; supervisor will permanently refuse to "
             "dispatch missions until --lifecycle-resume is called",
    )
    gates_grp.add_argument(
        "--project-root",
        default=".",
        help="project root for --evidence-chain-check / "
             "--anti-mediocrity-check / --lifecycle-status (default cwd)",
    )
    gates_grp.add_argument(
        "--proposed-condition",
        default=None,
        help="condition name to evaluate against the baseline for "
             "--anti-mediocrity-check",
    )
    gates_grp.add_argument(
        "--baseline-condition",
        default=None,
        help="baseline condition name for --anti-mediocrity-check",
    )

    subparsers = parser.add_subparsers(dest="command")
    wiki_parser = subparsers.add_parser(
        "wiki",
        help="Per-project idea-wiki operations",
    )
    wiki_sub = wiki_parser.add_subparsers(dest="wiki_cmd", required=True)
    init_parser = wiki_sub.add_parser(
        "init",
        help="Initialize .autors/<project>/wiki/ from templates",
    )
    init_parser.add_argument(
        "project",
        help="Project slug (becomes .autors/<project>/wiki)",
    )
    init_parser.add_argument(
        "--base",
        type=Path,
        default=Path.cwd(),
        help="Base directory (default: cwd)",
    )
    ingest_parser = wiki_sub.add_parser(
        "ingest",
        help="Backfill sources/papers/ from paper/refs.bib (+ optional LIT_MATRIX.tsv)",
    )
    ingest_parser.add_argument(
        "--wiki",
        type=Path,
        required=True,
        help="Path to .autors/<project>/wiki/",
    )
    ingest_parser.add_argument(
        "--refs",
        type=Path,
        help="Path to refs.bib (default: <project-root>/paper/refs.bib if it exists)",
    )
    ingest_parser.add_argument(
        "--lit-matrix",
        type=Path,
        help=(
            "Path to LIT_MATRIX.tsv (default: "
            "<project-root>/research/LIT_MATRIX.tsv if it exists)"
        ),
    )
    ingest_parser.add_argument(
        "--ingested-by",
        default="wiki-curator@manual-backfill",
        help="Provenance string for the ingested_by frontmatter field",
    )
    ingest_parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize the wiki path before ingesting if it is missing",
    )
    migrate_parser = wiki_sub.add_parser(
        "migrate",
        help="Run one-shot wiki migrations such as sources/*.md -> sources/notes/",
    )
    migrate_parser.add_argument(
        "--wiki",
        type=Path,
        required=True,
        help="Path to .autors/<project>/wiki/",
    )

    query_parser = subparsers.add_parser(
        "query",
        help="Unified trajectory + skills + wiki search (Obelisk-style)",
    )
    query_parser.add_argument(
        "text",
        nargs="+",
        help="search text (whitespace-joined)",
    )
    query_parser.add_argument(
        "--top-k", type=int, default=5,
        help="max hits per layer (default 5)",
    )
    query_parser.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of human-readable text",
    )
    query_parser.add_argument(
        "--no-index", action="store_true",
        help="skip incremental re-index (faster; may miss recent rows)",
    )

    learn_parser = subparsers.add_parser(
        "learn",
        help="Ingest learning material so a learning mission can update Argus's "
             "own skill + wiki libraries from it",
    )
    learn_parser.add_argument(
        "--material", type=Path, action="append", required=True,
        help="Path to a learning material file (.md/.txt/.rst/.pdf). Repeatable.",
    )
    learn_parser.add_argument(
        "--project", default="learning",
        help="Wiki project slug for the learning knowledge base (default: learning)",
    )
    learn_parser.add_argument(
        "--base", type=Path, default=Path.cwd(),
        help="Workdir where the learning mission will run (default: cwd)",
    )
    learn_parser.add_argument(
        "--ingested-by", default="learn@manual",
        help="Provenance string for the ingested_by manifest field",
    )

    from ...tools.research_report import add_report_parser

    add_report_parser(subparsers)

    return parser
