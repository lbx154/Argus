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
    # --- team Curator (resident teammate-pool + leaderboard agent) ---
    Knob("ARGUS_SKILL_CURATOR_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the team Curator", "backend"),
    Knob("ARGUS_SKILL_CURATOR_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the team Curator", "backend"),
    Knob("ARGUS_SKILL_CURATOR_MODEL", "gpt-5.5", "model for the team Curator's distill", "models"),
    Knob("ARGUS_SKILL_CURATOR_REASONING_EFFORT", "high", "team Curator distill reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_CURATOR_DISTILL_INTERVAL_S", "1260", "min seconds between Curator leaderboard distills", "mission"),
    # --- team teammates (per-teammate forced-grounding, time-box, leaderboard) ---
    Knob("ARGUS_TEAMMATE_FORCE_RESEARCH", "off", "force ONE web_search grounding pass before each teammate mission (opt-in)", "team"),
    Knob("ARGUS_TEAMMATE_RESEARCH_PROMPT", "(built-in, domain-neutral)", "override the forced-research prompt template ({objective} placeholder)", "team"),
    Knob("ARGUS_TEAMMATE_FORCE_PROFILE", "off", "force ONE profiling pass before each teammate mission (opt-in; needs PROFILE_CMD)", "team"),
    Knob("ARGUS_TEAMMATE_PROFILE_CMD", "(unset)", "operator profiling command; its stdout is prepended to the objective (ARGUS_OBJECTIVE exported)", "team"),
    Knob("ARGUS_TEAMMATE_PROFILE_HEADER", "(built-in, domain-neutral)", "override the framing line prepended above the profile output", "team"),
    Knob("ARGUS_TEAMMATE_PROFILE_REQUIRE_SUBSTR", "(unset → accept any non-empty)", "require this substring in the profile output or it is discarded", "team"),
    Knob("ARGUS_TEAMMATE_PAPER_MISSION", "(inherit lead default)", "force the EMNLP paper gates on|off for each teammate", "team"),
    Knob("ARGUS_TEAMMATE_TIMEOUT_S", "5400", "wall-clock seconds before a teammate mission is time-boxed", "team"),
    Knob("ARGUS_TEAMMATE_MAX_ROUNDS", "200", "max engineer rounds per teammate mission", "team"),
    Knob("ARGUS_TEAMMATE_RESULT_FILE", "(unset)", "path the mission writes {metric,mechanism} to → the leaderboard shard", "team"),
    Knob("ARGUS_LEADERBOARD_LOWER_IS_BETTER", "off (higher-is-better)", "global leaderboard direction; a task's lower_is_better overrides it per target", "team"),
    # --- models ---
    Knob("ARGUS_SKILL_MODEL", "gpt-5.5", "shared default model for roles without a role-specific model", "models"),
    Knob("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.5", "model for the L1 engineer", "models"),
    Knob("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.5", "model for the L2 reviewer", "models"),
    Knob("ARGUS_SKILL_PLAN_MODEL", "gpt-5.5", "model for the L4 planner", "models"),
    Knob("ARGUS_SKILL_MATCHER_MODEL", "gpt-5.5", "model for skill matching", "models"),
    # --- reasoning effort ---
    Knob("ARGUS_SKILL_MANAGER_REASONING_EFFORT", "xhigh", "manager reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_PLANNER_REASONING_EFFORT", "xhigh", "planner reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh", "engineer reasoning effort: low|medium|high|xhigh", "reasoning"),
    Knob("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "xhigh", "reviewer reasoning effort", "reasoning"),
    # --- budget ---
    Knob("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0", "USD cap per mission", "budget"),
    Knob("ARGUS_SKILL_DAILY_CAP_USD", "180.0", "USD cap per local day", "budget"),
    # --- mission / lifecycle ---
    Knob("ARGUS_SKILL_VERTICAL", "(unset → research; see LANES #1)", "force a vertical: nanochat|nanogpt_speedrun|kernelbench|speedrun|research", "mission"),
    Knob("ARGUS_SKILL_MAX_ROUNDS", "500", "max engineer rounds per mission", "mission"),
    Knob("ARGUS_SKILL_SHIFT_ROUND_LIMIT", "8", "rounds before a session-roll re-seeds from checkpoint (0=off)", "mission"),
    Knob("ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S", "120", "bounded wait for the shared Manager session lock before failing open to a no-session call", "mission"),
    Knob("ARGUS_SKILL_CHECKPOINT_PERSIST", "true", "persist the reviewer checkpoint across missions/restarts", "mission"),
    Knob("ARGUS_SKILL_DAEMON_AUTO_RESTART", "0", "blue/green self-handoff on source change (default OFF)", "lifecycle"),
    Knob("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "off", "let end-of-mission skill tidy-up git-commit distilled skills to the argus repo (default OFF — never auto-commits the operator's working tree)", "lifecycle"),
    Knob("ARGUS_SKILL_PER_MISSION_DISTILL", "off", "distill a reusable skill from EACH mission's process data at completion (LLM classify + write). Default OFF — distillation otherwise runs only on clean daemon shutdown; enabling adds a per-mission cost across all live daemons", "lifecycle"),
    Knob("ARGUS_SKILL_SAFE_MODE", "off", "extra-conservative guardrails", "lifecycle"),
    Knob("ARGUS_SKILL_ENGINEER_SANDBOX", "off", "codex sandbox for builder roles (engineer/reviewer/planner/subagent): set 'workspace-write' to confine writes to the project workdir + a writable allowlist (excludes ~/.argus-skill, the package, ~/.codex) and scrub VCS creds, instead of --dangerously-bypass. Default OFF — verify on the box (network/cache/B200) before enabling", "lifecycle"),
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


def resolve_role_model(
    route: str,
    *,
    role_env: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve a role model using Argus's runtime model precedence.

    Precedence is role-specific override -> ``ARGUS_SKILL_MODEL`` ->
    persisted switch (``core.knob_store`` — a prior ``/backend``/``/config``
    or natural-language "把模型换成 X" switch, so it survives a restart of
    this process AND is what the next daemon boot reads too) -> route
    default. Every execution path should use this helper rather than reading
    the vault directly, so a persisted switch is honored EVERYWHERE
    consistently, not just in whichever process happened to make it.
    """
    env_map = env if env is not None else os.environ
    if role_env:
        explicit = str(env_map.get(role_env, "") or "").strip()
        if explicit:
            return explicit
    shared = str(env_map.get("ARGUS_SKILL_MODEL", "") or "").strip()
    if shared:
        return shared
    from .knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    if role_env:
        persisted_role = persisted.get(role_env, "").strip()
        if persisted_role:
            return persisted_role
    persisted_shared = persisted.get("ARGUS_SKILL_MODEL", "").strip()
    if persisted_shared:
        return persisted_shared
    from ..tools.capability_vault import resolve_route_model

    return resolve_route_model(route, env_map)


def resolve_role_backend(role: str, *, env: Mapping[str, str] | None = None) -> str:
    """Resolve a role's agent-CLI backend (codex / claude / copilot / memory)
    using Argus's runtime precedence.

    Precedence: role-specific override (``ARGUS_SKILL_<ROLE>_BACKEND``) ->
    shared ``ARGUS_SKILL_RUNNER_BACKEND`` -> shared ``ARGUS_SKILL_LIFE_BACKEND``
    -> persisted switch (the same three vars, same order — a prior
    ``/backend`` switch or natural-language "engineer 用 claude") -> ``codex``.
    Returns the RAW value (unnormalized); callers that need the canonical
    codex/claude/copilot spelling should pass it through
    ``agent_cli.runner_backend.normalize_runner_backend``, same as every
    existing caller of this precedence already does.
    """
    env_map = env if env is not None else os.environ
    candidates = [v for v in (
        f"ARGUS_SKILL_{role.upper()}_BACKEND" if role else "",
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_LIFE_BACKEND",
    ) if v]
    for var in candidates:
        val = str(env_map.get(var, "") or "").strip()
        if val:
            return val
    from .knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    for var in candidates:
        val = persisted.get(var, "").strip()
        if val:
            return val
    return "codex"


def resolve_role_reasoning_effort(
    role_env: str, *, env: Mapping[str, str] | None = None, default: str = "xhigh",
) -> str:
    """Resolve a role's reasoning effort using Argus's runtime precedence:
    role-specific env override -> persisted switch (a prior ``/config``
    switch or natural-language "engineer 用 high 强度") -> ``default``.
    """
    env_map = env if env is not None else os.environ
    if role_env:
        explicit = str(env_map.get(role_env, "") or "").strip()
        if explicit:
            return explicit
    if role_env:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs().get(role_env, "").strip()
        if persisted:
            return persisted
    return default
