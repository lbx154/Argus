"""Operator-facing ARGUS_* knob registry + ``--config-help`` rendering.

A single discoverable list of the knobs an operator actually TUNES — backend,
models, reasoning effort, budget, lifecycle, telemetry — each with its default and
a one-line doc, so steering Argus stops being a grep-the-source exercise (the
audit found ~120 knobs with ~15 documented). ``argus-skill --config-help`` prints
this with the CURRENT effective value of each.

Scope: the operator control surface, NOT every internal/test/handoff knob. Add a
knob here when an operator would reasonably set it. Defaults are documentation —
the authoritative default still lives at each read-site; keep them in sync.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Knob:
    name: str
    default: str
    doc: str
    group: str


#: The operator control surface. Defaults verified against read-sites 2026-06-26.
KNOBS: tuple[Knob, ...] = (
    # --- backend / runner ---
    Knob("ARGUS_SKILL_LIFE_BACKEND", "codex", "agent backend: codex | claude | memory (test only)", "backend"),
    Knob("ARGUS_SKILL_RUNNER_BIN", "(agent CLI on PATH)", "absolute path to the agent CLI binary", "backend"),
    Knob("ARGUS_SKILL_ENGINEER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the engineer", "backend"),
    Knob("ARGUS_SKILL_REVIEWER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the reviewer", "backend"),
    Knob("ARGUS_SKILL_PLANNER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the planner", "backend"),
    Knob("ARGUS_SKILL_MANAGER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the manager", "backend"),
    Knob("ARGUS_SKILL_ENGINEER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the engineer", "backend"),
    Knob("ARGUS_SKILL_REVIEWER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the reviewer", "backend"),
    Knob("ARGUS_SKILL_PLANNER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the planner", "backend"),
    Knob("ARGUS_SKILL_MANAGER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the manager", "backend"),
    # --- models ---
    Knob("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.5", "model for the L1 engineer", "models"),
    Knob("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.5", "model for the L2 reviewer", "models"),
    Knob("ARGUS_SKILL_PLAN_MODEL", "gpt-5.5", "model for the L4 planner", "models"),
    Knob("ARGUS_SKILL_MATCHER_MODEL", "gpt-5.5", "model for skill matching", "models"),
    # --- reasoning effort ---
    Knob("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high", "engineer reasoning effort: low|medium|high|xhigh", "reasoning"),
    Knob("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high", "reviewer reasoning effort", "reasoning"),
    # --- budget ---
    Knob("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0", "USD cap per mission", "budget"),
    Knob("ARGUS_SKILL_DAILY_CAP_USD", "180.0", "USD cap per local day", "budget"),
    # --- mission / lifecycle ---
    Knob("ARGUS_SKILL_VERTICAL", "(unset → research; see LANES #1)", "force a vertical: nanochat|nanogpt_speedrun|kernelbench|speedrun|research", "mission"),
    Knob("ARGUS_SKILL_MAX_ROUNDS", "500", "max engineer rounds per mission", "mission"),
    Knob("ARGUS_SKILL_SHIFT_ROUND_LIMIT", "8", "rounds before a session-roll re-seeds from checkpoint (0=off)", "mission"),
    Knob("ARGUS_SKILL_CHECKPOINT_PERSIST", "true", "persist the reviewer checkpoint across missions/restarts", "mission"),
    Knob("ARGUS_SKILL_DAEMON_AUTO_RESTART", "0", "blue/green self-handoff on source change (default OFF)", "lifecycle"),
    Knob("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "off", "let end-of-mission skill tidy-up git-commit distilled skills to the argus repo (default OFF — never auto-commits the operator's working tree)", "lifecycle"),
    Knob("ARGUS_SKILL_SAFE_MODE", "off", "extra-conservative guardrails", "lifecycle"),
    Knob("ARGUS_SKILL_MEASURED_MODE", "off", "measured-mode evaluation gating", "lifecycle"),
    Knob("ARGUS_SKILL_SIMULATED_OPERATOR", "off", "inject a simulated human operator (dev/test only)", "lifecycle"),
    Knob("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "off", "bypass the capability-vault preflight on daemon start", "lifecycle"),
    Knob("ARGUS_META_JUMP_FROZEN_THRESHOLD", "12", "frozen-floor attempts before the meta layer convenes a regime jump", "meta"),
    # --- telemetry / notify ---
    Knob("ARGUS_SKILL_ENABLE_TELEGRAM", "off", "enable the Telegram inbound/outbound bridge", "telemetry"),
    Knob("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", "(unset)", "Telegram bot token", "telemetry"),
    Knob("ARGUS_SKILL_TELEGRAM_CHAT_ID", "(unset)", "Telegram chat id to notify", "telemetry"),
    Knob("ARGUS_SKILL_SHOW_REASONING", "0", "stream the agent's reasoning to the cockpit", "telemetry"),
)


def format_config_help(env: Mapping[str, str] | None = None) -> str:
    """Render the knob registry grouped, with each knob's CURRENT effective value."""
    env = env if env is not None else os.environ
    out: list[str] = [
        "Argus operator control knobs (ARGUS_*). Default shown in (), current value "
        "is what's set in this environment.",
        "This is the operator control surface — internal/test knobs are not listed.",
        "",
    ]
    last_group = None
    for k in KNOBS:
        if k.group != last_group:
            out.append(f"[{k.group}]")
            last_group = k.group
        cur = env.get(k.name)
        cur_str = f"= {cur}" if cur not in (None, "") else "(default)"
        out.append(f"  {k.name}  (default: {k.default})  {cur_str}")
        out.append(f"      {k.doc}")
    return "\n".join(out) + "\n"
