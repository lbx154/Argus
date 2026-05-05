#!/usr/bin/env python3
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
import os
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
    # 1. Shell prompt → user types `argus-skill`
    emit(PROMPT_HOST, 0.5)
    type_out("argus-skill", per=0.05)
    line("", 0.3)

    # 2. Objective prompt
    line(s("🎯 mission objective", "cyan", "bold")
         + s(" (single line or paste multi-line; Ctrl-C to abort):", "grey"))
    emit("> ", 0.4)
    type_out(
        "在 /tmp/argus-test-palette 用 Python 实现 hex↔rgb 调色板 CLI; "
        "≥6 个 pytest 用例必须全过",
        per=0.018,
    )
    line("", 0.4)
    line(s("📝 collected 1 line into objective", "grey"))

    line(s("✅ mission mission_20260505T085108Z", "green") +
         s("   spawning daemon (log: …/daemon.log) …", "grey"))
    _pause(0.5)

    # 3. Branded startup banner — straight from the real renderer.
    banner = render_startup_banner(
        theme=THEME,
        version="0.1.0",
        mode="mission",
        mission_id="mission_20260505T085108Z",
        mission_status="running",
        plan_mode="auto",
        max_rounds=20,
        objective="在 /tmp/argus-test-palette 用 Python 实现 hex↔rgb 调色板 CLI; ≥6 个 pytest 用例必须全过",
        state_dir="/home/u/.argus-skill/mission-state",
        daemon_pid=1974513,
    )
    block(banner.replace("\n", "\r\n"), dt_after=0.8)

    # 4. Engineer / reviewer round-loop events — fed through the actual renderer.
    EVENT_SCRIPT: list[tuple[dict, float]] = [
        ({"type": "loop.started", "max_rounds": 20,
          "text": "loop started — max_rounds=20, plan_mode=auto"}, 0.4),
        ({"type": "match.info",
          "text": "querying matcher (gpt-5.4-mini) against 3/3 candidates"}, 0.5),
        ({"type": "match.info",
          "text": "matcher: no high-fit match — will distill"}, 0.4),
        ({"type": "distill.start",
          "text": "distilling skill via gpt-5.4"}, 0.6),
        ({"type": "distill.done",
          "text": "distilled (5275 chars, 0 tok)"}, 0.5),
        ({"type": "round.started", "round_index": 1,
          "text": "round 1 starting…"}, 0.4),
        ({"type": "round.main.completed", "round_index": 1,
          "summary": "Implemented palette.py + 6 pytest cases.",
          "text": "round 1: main agent finished\n"
                  "   ↳ Added hex_to_rgb / rgb_to_hex with input validation; 6 tests pass."}, 0.7),
        ({"type": "round.checks.completed", "round_index": 1,
          "checks": [{"name": "pytest -q", "passed": True, "exit": 0}]}, 0.5),
        ({"type": "round.review.completed", "round_index": 1,
          "status": "continue",
          "reason": "looks good, but no test for non-hex input.",
          "next_action": "add a malformed-input test"}, 0.6),
        ({"type": "plan.completed", "round_index": 1,
          "plan_mode": "auto", "follow_up_required": True,
          "main_instruction": "add malformed-input test",
          "review_instruction": "verify ValueError is raised on bad hex",
          "next_explore": "negative-rgb edge cases"}, 0.5),
        ({"type": "round.started", "round_index": 2,
          "text": "round 2 starting…"}, 0.5),
        ({"type": "round.main.completed", "round_index": 2,
          "summary": "Added malformed-hex test; 7 tests pass.",
          "text": "round 2: main agent finished\n"
                  "   ↳ Added 'invalid_hex' test; pytest -q → 7 passed."}, 0.7),
        ({"type": "round.review.completed", "round_index": 2,
          "status": "done",
          "reason": "all checks passed; coverage is sufficient."}, 0.5),
        ({"type": "plan.completed", "round_index": 2,
          "plan_mode": "auto", "follow_up_required": False}, 0.4),
        ({"type": "final.report.ready",
          "path": "/home/u/.argus-skill/.../loop_state/final_report.md",
          "generated_by": "main-agent"}, 0.4),
        ({"type": "loop.completed", "success": True,
          "text": "loop done — success\n"
                  "   ↳ Reviewer marked done, checks passed, planner did not require a follow-up phase."}, 0.5),
        ({"type": "mission.completed",
          "text": "mission mission_20260505T085108Z: success=True rounds=2 reason=Reviewer marked done."}, 0.4),
        ({"type": "mission.idle",
          "text": "mission idle — daemon is alive; type /run, /show, or /exit"}, 0.6),
    ]

    for evt, dt in EVENT_SCRIPT:
        block(render_event_for_terminal(evt, theme=THEME).replace("\n", "\r\n"),
              dt_after=dt)

    # 5. Show interactive: user types /status, then /show review, then /exit.
    emit(PROMPT_REPL, 0.6)
    type_out("/status", per=0.05)
    line("", 0.3)
    status_text = (
        "mission mission_20260505T085108Z   done   round 2/20   phase=idle\n"
        "   objective: 在 /tmp/argus-test-palette 用 Python 实现 hex↔rgb 调色板 CLI\n"
        "   plan_mode: auto\n"
        "   last review: ✅ done — all checks passed; coverage is sufficient.\n"
        "   last plan: round 2 plan (auto) (no more follow-up)\n"
        "   last main: Added 'invalid_hex' test; pytest -q → 7 passed.\n"
        "   recent:\n"
        "     12:35:01 round 2 main agent finished\n"
        "     12:35:02 review ✅ done\n"
        "     12:35:03 mission completed"
    )
    block(render_event_for_terminal({"type": "status.report", "text": status_text},
                                    theme=THEME).replace("\n", "\r\n"),
          dt_after=0.7)

    emit(PROMPT_REPL, 0.4)
    type_out("/show review", per=0.05)
    line("", 0.3)
    show_text = (
        "── round_2_summary ──\n"
        "verdict: done\n"
        "reason: all checks passed; coverage is sufficient.\n"
        "evidence: pytest -q → 7 passed; cli_examples → both correct."
    )
    block(render_event_for_terminal(
        {"type": "command.ack", "show_kind": "review", "text": show_text},
        theme=THEME).replace("\n", "\r\n"),
        dt_after=0.7)

    emit(PROMPT_REPL, 0.4)
    type_out("/exit", per=0.05)
    line("", 0.3)
    line(s("bye — daemon (pid=1974513) keeps running", "grey"), 0.2)
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
