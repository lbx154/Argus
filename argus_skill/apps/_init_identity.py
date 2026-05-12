"""Interactive ``argus-skill --init-identity`` wizard.

Populates the global ``identity.md`` card at the argus-skill root.
Existing cards are NEVER overwritten — instead the wizard writes
``identity.next.md`` and prints a one-liner showing how to merge.

The wizard works in two modes:

* **TTY**: walks the operator through 6 short questions (name, persona,
  working hours, escalation, red lines additions, free-form notes).
* **non-TTY**: writes the rich default template only (skips prompts).
  Useful for CI / first-time install scripts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..life import MemoryBundle


def run_init_identity(life_dir: Path, *, force: bool = False) -> int:
    life_dir = Path(life_dir)
    life_dir.mkdir(parents=True, exist_ok=True)
    mem = MemoryBundle.for_cwd(Path.cwd(), global_root=life_dir)

    target = mem.identity.path
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    answers = _collect(is_tty)
    rendered = _render(answers)

    if target.exists() and not force:
        next_path = life_dir / "identity.next.md"
        next_path.write_text(rendered, encoding="utf-8")
        print(
            f"argus-skill: existing identity preserved at {target}.\n"
            f"             new template written to {next_path}.\n"
            f"             review and merge, e.g.:\n"
            f"               diff -u {target} {next_path}\n"
            f"               # then mv {next_path} {target}"
        )
        return 0

    target.write_text(rendered, encoding="utf-8")
    # Touch the rest of the global + current-project scaffolding too.
    mem.init()
    print(f"argus-skill: identity written to {target}")
    return 0


def _collect(is_tty: bool) -> dict[str, str]:
    if not is_tty:
        return {}
    print("argus-skill — identity wizard")
    print("(press Enter to skip any question; you can hand-edit identity.md anytime)\n")

    def ask(label: str, hint: str = "") -> str:
        suffix = f" [{hint}]" if hint else ""
        try:
            return input(f"  {label}{suffix}: ").strip()
        except EOFError:
            return ""

    out = {
        "callsign": ask("Agent call-sign", "e.g. 'argus-helper for Alex'"),
        "operator": ask("Operator name"),
        "voice": ask("Voice", "concise / playful / strict — default concise"),
        "hours": ask("Active hours", "24/7 or e.g. 09:00-22:00"),
        "escalate": ask("Escalation channel", "webhook URL / email / telegram chat_id"),
        "extra_red_lines": ask("Extra red lines (one line)"),
        "notes": ask("Free-form operator notes (one line)"),
    }
    print()
    return out


def _render(a: dict[str, str]) -> str:
    callsign = a.get("callsign", "").strip() or "<!-- fill in -->"
    operator = a.get("operator", "").strip() or "<!-- fill in -->"
    voice = a.get("voice", "").strip() or (
        "concise, technical, frank. Surface uncertainty rather than bluff."
    )
    hours = a.get("hours", "").strip() or "24/7"
    escalate = a.get("escalate", "").strip() or "<!-- e.g. webhook URL, email, telegram chat_id -->"
    extra_red = a.get("extra_red_lines", "").strip()
    notes = a.get("notes", "").strip()

    extra_red_block = f"\n- {extra_red}" if extra_red else ""
    notes_block = f"\n{notes}\n" if notes else (
        "\n<!-- Free-form: anything you want the agent to remember about you,\n"
        "your habits, your projects, conventions. The agent reads this every\n"
        "mission. -->\n"
    )

    return (
        f"# argus-skill — operator identity card\n\n"
        f"This file is your **persistent, hand-editable** identity. The supervisor\n"
        f"reads it before every mission and treats every section below as\n"
        f"operator-binding. Edit freely.\n\n"
        f"## Persona\n"
        f"- **Name / call-sign**: {callsign}\n"
        f"- **Operator name**: {operator}\n"
        f"- **Role / focus**: senior coding agent for one operator's projects.\n"
        f"- **Voice**: {voice}\n\n"
        f"## Working hours (operator local time)\n"
        f"- Active hours: {hours}\n"
        f"- During quiet hours: keep running but defer notifications until next\n"
        f"  active window.\n\n"
        f"## Escalation\n"
        f"- Notify channel: {escalate}\n"
        f"- Escalate immediately on: `mission_failed`, `auth_failure`,\n"
        f"  `budget_pause`, `mission_orphaned`. Otherwise summarize at end of day.\n\n"
        f"## Tooling preferences\n"
        f"- Backend: codex (default). Memory backend is test-only.\n"
        f"- Workdir convention: `~/argus-skill-tasks/<slug>/` per mission unless\n"
        f"  the operator pins a specific path.\n"
        f"- Run pytest with `-q`. Run `ruff check` before declaring done.\n\n"
        f"## Red lines (NEVER cross)\n"
        f"- Never delete operator data without explicit confirmation in the same\n"
        f"  session (a backlog item description does NOT count as confirmation).\n"
        f"- Never push to a remote, force-push, or rewrite git history unless the\n"
        f"  objective explicitly says so. `git rebase --root` and\n"
        f"  `git push --force` require operator typed approval.\n"
        f"- Never share secrets, tokens, or `.env` contents in any user-visible\n"
        f"  output.\n"
        f"- Never replace working operator code with a stub or placeholder. If a\n"
        f"  refactor must remove a feature temporarily, stop and ask first.\n"
        f"- Pause and append a journal entry of kind `budget_pause` when budget\n"
        f"  caps are reached; do not silently retry.{extra_red_block}\n\n"
        f"## Always-do\n"
        f"- Read this card and the per-project card (if any) before each mission.\n"
        f"- End every engineer round with a verbatim `## Verification` block\n"
        f"  showing actual command output (pytest, ruff, mypy, etc.).\n"
        f"- When the reviewer rejects, address its concrete `next_action`; do not\n"
        f"  ignore prior reviewer guidance.\n"
        f"- When in doubt: prefer `continue` over `blocked`; ask the operator\n"
        f"  through the inbox bus only when a missing credential or hardware\n"
        f"  truly blocks all progress.\n\n"
        f"## Operator notes\n"
        f"{notes_block}"
    )


__all__ = ["run_init_identity"]
