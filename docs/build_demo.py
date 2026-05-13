#!/usr/bin/env python3
# ruff: noqa: I001
"""Build the README demo for ``argus-skill``.

Writes ``docs/demo.cast`` (asciinema v2). When ``svg-term-cli`` is on
the PATH (or available via ``npx``), also re-renders ``docs/demo.svg``
which is what the README embeds.

This is a *scripted* replay — no daemon, no LLM, no network. The
output is deterministic, fast, and safe to commit. We re-use the
real argus-skill renderer (``argus_skill.cli.render`` /
``argus_skill.cli.branding``) so the recording stays in sync with the
actual UI on every theme change.

Run::

    python docs/build_demo.py            # writes cast + svg if tools available
    python docs/build_demo.py --skip-svg # cast only

"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure we import the in-tree argus_skill, not a pip-installed copy.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from argus_skill.cli.branding import render_startup_banner  # noqa: E402
from argus_skill.cli.render import render_event_for_terminal  # noqa: E402
from argus_skill.cli.theme import Theme  # noqa: E402


# ── ANSI helpers ─────────────────────────────────────────────────────────

C = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "italic": "\x1b[3m",
    "grey": "\x1b[90m",
    "cyan": "\x1b[36m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
}


def s(text: str, *styles: str) -> str:
    return "".join(C[x] for x in styles) + text + C["reset"]


# ── event recorder ───────────────────────────────────────────────────────

events: list[list] = []
_t = 0.0


def emit(out: str, dt: float = 0.04) -> None:
    """Append an asciinema 'output' event with a relative time delta."""
    global _t
    _t += dt
    events.append([round(_t, 3), "o", out])


def line(text: str = "", dt: float = 0.05) -> None:
    emit(text + "\r\n", dt)


def block(text: str, dt_after: float = 0.4) -> None:
    """Emit a multi-line block as a single chunk, then pause."""
    emit(text + "\r\n", 0.05)
    _pause(dt_after)


def _pause(dt: float) -> None:
    emit("", dt)  # zero-byte tick so the recorder advances time


def type_out(text: str, per: float = 0.04) -> None:
    """Simulate a human typing one character at a time."""
    for ch in text:
        emit(ch, per)


# ── script ───────────────────────────────────────────────────────────────

PROMPT_HOST = s("(base) argustest@dsp7", "green", "bold") + s(":~$ ", "bold")
PROMPT_REPL = "> "

THEME = Theme(enabled=True, width=96)


def main() -> int:
    # 1. Shell prompt → user starts the 7x24 daemon for a real repo.
    emit(PROMPT_HOST, 0.5)
    type_out("export ARGUS_SKILL_LIFE_BACKEND=codex ARGUS_SKILL_RUNNER_BIN=$(which codex)", per=0.018)
    line("", 0.3)
    emit(PROMPT_HOST, 0.2)
    type_out(
        'argus-skill --daemon --continuous --objective "Keep this repo production-ready: '
        'tests green, docs accurate, cockpit and Telegram UX reliable."',
        per=0.018,
    )
    line("", 0.3)
    line("argus-skill: daemon started (pid 3479643, life_dir=~/.argus-skill/projects/07197071cf43).")
    line(s("Tip: close SSH; the daemon keeps draining. Watch it from terminal or Telegram.", "grey"))
    _pause(0.5)

    # 2. Branded startup banner — straight from the real renderer.
    banner = render_startup_banner(
        theme=THEME,
        version="0.1.0",
        mode="life",
        mission_id="project_07197071cf43",
        mission_status="continuous",
        plan_mode="auto",
        auto_follow_up=True,
        max_rounds=500,
        objective="Keep this repo production-ready: tests green, docs accurate, cockpit and Telegram UX reliable.",
        state_dir="/home/u/.argus-skill/projects/07197071cf43",
        daemon_pid=3479643,
    )
    block(banner.replace("\n", "\r\n"), dt_after=0.8)

    # 3. A high-value remote use case: operator nudges from Telegram,
    # planner creates an operator-UX task, engineer/reviewer/critic close it.
    block(
        s("Telegram", "cyan", "bold")
        + "  you: 修一下 --follow 看不出当前任务的问题，并加回归\r\n"
        + "argus: 收到，我会把这当作一个新任务来做。\r\n"
        + "       中间如果在匹配技能、读代码或跑测试，我也会发进展。",
        dt_after=0.8,
    )

    emit(PROMPT_HOST, 0.4)
    type_out("argus-skill --follow", per=0.04)
    line("", 0.2)
    line("argus-skill: following ~/.argus-skill/projects/07197071cf43/events.jsonl  (Ctrl-C to stop)")

    line(s("📋 L4 planner", "cyan", "bold")
         + " queued high-impact task: "
         + s("Render mission title and objective in --follow lifecycle lines", "bold"))
    line("   impact: 4/5 · area=operator_ux · evidence=live tail is ambiguous across missions")
    _pause(0.6)

    EVENT_SCRIPT: list[tuple[dict, float]] = [
        ({"type": "loop.started", "max_rounds": 500,
          "text": "loop started — max_rounds=500, plan_mode=auto"}, 0.3),
        ({"type": "match.info",
          "text": "querying matcher (gpt-5.4-mini) against 8/29 candidates"}, 0.4),
        ({"type": "match.info",
          "text": "matcher picked: Live Operator Surface Enrichment (14,765 tok)"}, 0.4),
        ({"type": "round.started", "round_index": 1,
          "text": "engineer round 1 (resuming codex session)"}, 0.4),
        ({"type": "engineer.progress",
          "text": "I’m reading the --follow formatter and subprocess tests before changing only the rendering path."}, 0.5),
        ({"type": "engineer.progress",
          "text": "Read argus_skill/apps/cli.py and tests/daemon/test_cli_lifecycle_subprocess.py"}, 0.4),
        ({"type": "round.main.completed", "round_index": 1,
          "summary": "--follow now includes task context on start and completion.",
          "text": "round 1: main agent finished\n"
                  "   ↳ Added mission title/objective rendering and subprocess regression."}, 0.6),
        ({"type": "round.checks.completed", "round_index": 1,
          "checks": [{"name": "pytest -q tests/daemon/test_cli_lifecycle_subprocess.py -k follow", "passed": True, "exit": 0},
                     {"name": "ruff check . && mypy .", "passed": True, "exit": 0}]}, 0.5),
        ({"type": "round.review.completed", "round_index": 1,
          "status": "done",
          "reason": "real subprocess follow test proves start and completion lines show title + objective."}, 0.6),
        ({"type": "plan.completed", "round_index": 1,
          "plan_mode": "auto", "follow_up_required": False}, 0.4),
        ({"type": "loop.completed", "success": True,
          "text": "loop done — success\n"
                  "   ↳ Operator can now understand live mission context from --follow or Telegram."}, 0.5),
    ]

    for evt, dt in EVENT_SCRIPT:
        block(render_event_for_terminal(evt, theme=THEME).replace("\n", "\r\n"),
              dt_after=dt)

    block(
        "argus: ✅ 任务已完成 · --follow 现在会显示任务标题和 objective\r\n"
        "       已验证：pytest follow tests、ruff、mypy、full pytest",
        dt_after=0.8,
    )

    # 4. Wake-up status: the daemon has kept useful state.
    emit(PROMPT_REPL, 0.6)
    type_out("/status", per=0.05)
    line("", 0.3)
    status_text = (
        "daemon   : alive (pid 3479643, backend codex)\n"
        "budget   : per-mission $30.00 · daily $180.00 · remaining $172.53\n"
        "active   : 0 pending · 0 running\n"
        "history  : 51 done\n"
        "continuous: off — planner declared project done\n"
        "recent   : Make watch mode stream and terminate cleanly off-TTY ✅"
    )
    block(render_event_for_terminal({"type": "status.report", "text": status_text},
                                    theme=THEME).replace("\n", "\r\n"),
          dt_after=0.7)

    emit(PROMPT_REPL, 0.4)
    type_out("/help", per=0.04)
    line("", 0.2)
    block(
        "Useful commands: /add, /status, /backlog, /journal, /nudge, /stop, /skills, /exit\r\n"
        "One-shot cockpit: argus-skill --watch | --follow | --notify MSG | --daemon-stop",
        dt_after=0.6,
    )

    emit(PROMPT_REPL, 0.4)
    type_out("/exit", per=0.05)
    line("", 0.3)
    line(s("bye — daemon (pid=3479643) keeps running", "grey"), 0.2)
    line("")
    line(PROMPT_HOST, 0.4)

    # ── write asciinema cast ────────────────────────────────────────────
    here = Path(__file__).resolve().parent
    cast_path = here / "demo.cast"
    svg_path = here / "demo.svg"

    header = {
        "version": 2,
        "width": 96,
        "height": 28,
        "timestamp": 1745000000,
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "argus-skill demo",
    }
    with cast_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"wrote {cast_path}: {len(events)} events, "
          f"total duration {events[-1][0]:.1f}s")

    # ── render SVG via svg-term-cli ─────────────────────────────────────
    if "--skip-svg" in sys.argv:
        return 0

    svg_term = shutil.which("svg-term") or shutil.which("npx")
    if not svg_term:
        print("svg-term-cli not found; install with `npm i -g svg-term-cli` "
              "or rerun with --skip-svg.")
        return 0

    cmd = [svg_term]
    if svg_term.endswith("npx"):
        cmd += ["--yes", "svg-term-cli"]
    cmd += [
        "--in", str(cast_path),
        "--out", str(svg_path),
        "--window", "--width", str(header["width"]),
        "--height", str(header["height"]),
        "--padding", "14",
    ]
    print("rendering svg:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"wrote {svg_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
