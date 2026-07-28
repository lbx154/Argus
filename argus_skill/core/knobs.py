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

import math
import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Knob:
    name: str
    default: str
    doc: str
    group: str
    # True ⇒ an operator can change this FROM THE COCKPIT (a natural-language
    # switch or /config), so it shows up as NL-editable in the /config settings
    # view. Single source of truth for the cockpit-editable surface — see
    # cockpit_editable_names().
    cockpit: bool = False


@dataclass(frozen=True)
class ResolvedKnob:
    """One knob after applying env -> persisted -> default precedence."""

    value: str
    source: str


@dataclass(frozen=True)
class BudgetCaps:
    """The sole host-global runtime budget cap."""

    global_daily_cap_usd: float


BUDGET_KNOB_DEFAULTS: dict[str, str] = {
    "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "20000.0",
}

# Daemon count is not provider concurrency: every backend still obeys its own
# host-wide call/concurrency guard. Keep this high enough for independent
# long-running projects while those lower-level guards control actual load.
DEFAULT_MAX_ACTIVE_DAEMONS = 64


#: The operator control surface. Defaults verified against read-sites 2026-06-26.
KNOBS: tuple[Knob, ...] = (
    # --- backend / runner ---
    Knob(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
        "agent backend: codex | copilot | claude | opencode | memory (test only)",
        "backend",
    ),
    Knob("ARGUS_SKILL_RUNNER_BIN", "(agent CLI on PATH)", "absolute path to the agent CLI binary", "backend"),
    Knob("ARGUS_SKILL_ENGINEER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the engineer", "backend", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the reviewer", "backend", cockpit=True),
    Knob("ARGUS_SKILL_PLANNER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the planner", "backend", cockpit=True),
    Knob("ARGUS_SKILL_MANAGER_BACKEND", "(=LIFE_BACKEND)", "per-role backend override for the manager", "backend", cockpit=True),
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
    Knob("ARGUS_TEAMMATE_PAPER_MISSION", "(inherit lead default)", "force the paper gates on|off for each teammate", "team"),
    Knob("ARGUS_TEAMMATE_TIMEOUT_S", "5400", "wall-clock seconds before a teammate mission is time-boxed", "team"),
    Knob("ARGUS_TEAMMATE_MAX_ROUNDS", "200", "max engineer rounds per teammate mission", "team"),
    Knob("ARGUS_TEAMMATE_RESULT_FILE", "(unset)", "path the mission writes {metric,mechanism} to → the leaderboard shard", "team"),
    Knob("ARGUS_LEADERBOARD_LOWER_IS_BETTER", "off (higher-is-better)", "global leaderboard direction; a task's lower_is_better overrides it per target", "team"),
    # --- models ---
    Knob("ARGUS_SKILL_MODEL", "gpt-5.5", "shared default model for roles without a role-specific model", "models", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.5", "model for the L1 engineer", "models", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.5", "model for the L2 reviewer", "models", cockpit=True),
    Knob("ARGUS_SKILL_PLAN_MODEL", "gpt-5.5", "model for the L4 planner", "models", cockpit=True),
    Knob("ARGUS_SKILL_PLAN_PREVIEW_MODEL", "auto", "interactive /plan model: gpt-5.4-mini on codex/copilot, planner model on claude; set an id to override", "models"),
    Knob("ARGUS_SKILL_REWRITE_MODEL", "gpt-5.5", "interactive prompt rewrite model", "models"),
    Knob("ARGUS_SKILL_MANAGER_REPLY_MODEL", "inherit", "operator-facing Manager SELF model; inherit uses the configured Manager/shared route model", "models", cockpit=True),
    Knob("ARGUS_SKILL_FRONTDOOR_MODEL", "auto", "cheap front-door classification model: gpt-5.4-mini on codex/copilot, Manager model otherwise", "models"),
    Knob("ARGUS_SKILL_MATCHER_MODEL", "gpt-5.5", "model for skill matching", "models"),
    # --- reasoning effort ---
    Knob("ARGUS_SKILL_MANAGER_REASONING_EFFORT", "xhigh", "manager reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_PLANNER_REASONING_EFFORT", "xhigh", "planner reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_SELF_REASONING_EFFORT", "xhigh", "foreground Manager SELF chat/read-only reply effort", "reasoning"),
    Knob("ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT", "low", "interactive /plan preview effort; execution planning keeps the planner setting", "reasoning"),
    Knob("ARGUS_SKILL_REWRITE_REASONING_EFFORT", "high", "interactive prompt rewrite reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", "high", "direct-task first-round Engineer effort; later rounds use the Engineer effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh", "engineer reasoning effort: low|medium|high|xhigh", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high", "reviewer reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_ADAPTER_REASONING_EFFORT", "low", "matched-skill adaptation reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_MAINTENANCE_REASONING_EFFORT", "low", "post-task skill-maintenance reasoning effort", "reasoning"),
    # --- budget ---
    Knob("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", BUDGET_KNOB_DEFAULTS["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"], "host-global daily USD cap across all projects", "budget", cockpit=True),
    Knob("ARGUS_SKILL_COST_CONTROL", "on", "host-global settled-cost admission and reconciliation", "budget"),
    Knob("ARGUS_SKILL_UNPRICED_COST_POLICY", "block", "handling for unresolved call cost: block | allow", "budget", cockpit=True),
    Knob("ARGUS_SKILL_COPILOT_GUARD", "on", "cross-project Copilot premium/call/concurrency circuit breaker", "budget"),
    Knob("ARGUS_SKILL_CODEX_GUARD", "on", "cross-project Codex daily-call circuit breaker", "budget"),
    Knob("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "300", "host-wide Codex provider-call cap per local day", "budget", cockpit=True),
    Knob("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "10000", "host-wide Copilot premium-request cap per local day", "budget", cockpit=True),
    Knob("ARGUS_SKILL_COPILOT_DAILY_CALL_CAP", "10000", "host-wide Copilot provider-call cap per local day", "budget", cockpit=True),
    Knob("ARGUS_SKILL_COPILOT_HOURLY_CALL_CAP", "10000", "host-wide Copilot provider-call cap per rolling hour", "budget"),
    Knob("ARGUS_SKILL_COPILOT_MAX_CONCURRENCY", "10000", "maximum concurrent Copilot calls across all Argus projects", "budget"),
    Knob("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", str(DEFAULT_MAX_ACTIVE_DAEMONS), "host-wide active daemon cap", "budget", cockpit=True),
    Knob("ARGUS_SKILL_SUBAGENT_FAMILY_FAILURE_STREAK_LIMIT", "3", "consecutive unresolved subagent-job failures (same experiment family) before the L4 planner circuit-breaks further retries", "budget"),
    Knob("ARGUS_SKILL_SUBAGENT_FAMILY_FAILURE_WINDOW_HOURS", "72.0", "trailing window (hours) the subagent family failure streak is computed over", "budget"),
    # --- mission / lifecycle ---
    Knob("ARGUS_SKILL_MAX_ROUNDS", "500", "max engineer rounds per mission", "mission"),
    Knob("ARGUS_SKILL_NEAREST_TRANSFER_MIN_SCORE", "0.12", "minimum semantic score for injecting a full nearest-skill fallback", "mission"),
    Knob("ARGUS_SKILL_NEAREST_TRANSFER_ENABLED", "0", "inject a low-confidence nearest Skill when the LLM matcher finds no high-fit match (default OFF)", "mission"),
    Knob("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "1", "ask every completed task to retain only verified durable learning (default ON)", "mission"),
    Knob("ARGUS_SKILL_FORCE_POST_TASK_LEARNING", "0", "force every task to create/update a skill; selective learning is default", "mission"),
    Knob("ARGUS_SKILL_ENGINEER_FILE_READ_BUDGET", "12", "soft first-pass relevant-file inspection budget", "mission"),
    Knob("ARGUS_SKILL_ENGINEER_TEST_RUN_BUDGET", "3", "soft focused verification-run budget before the final verifier", "mission"),
    Knob("ARGUS_SKILL_BOUNDED_DAG_MODEL", "auto", "compact model for decomposing Manager bounded tasks into backlog DAG nodes", "mission"),
    Knob("ARGUS_SKILL_BOUNDED_DAG_REASONING_EFFORT", "low", "reasoning effort for bounded DAG decomposition", "mission"),
    Knob("ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS", "0", "optional wall-clock cap for one Engineer turn; disabled by default", "mission"),
    Knob("ARGUS_SKILL_SCIENTIST_TURN_MAX_SECONDS", "0", "optional wall-clock cap for one Scientist skill-distillation turn; disabled by default", "mission"),
    Knob("ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", "600", "model stream inactivity before a diagnostic warning (0=off)", "mission"),
    Knob("ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS", "1800", "model stream inactivity before likely-stalled alerting (0=off)", "mission"),
    Knob("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", "2700", "model stream inactivity before terminating only the current provider process group (0=off)", "mission"),
    Knob("ARGUS_SKILL_SHIFT_ROUND_LIMIT", "1", "compatibility knob; autonomous Engineer/Reviewer sessions are always fresh", "mission"),
    Knob("ARGUS_SKILL_THREAD_TOKEN_LIMIT", "0", "compatibility knob; autonomous role threads are never resumed", "mission"),
    Knob("ARGUS_SKILL_DECISION_PROGRESS_TIMEOUT_SECONDS", "1800", "safe round-boundary seconds without reviewer-classified decision/evidence progress (0=off)", "mission"),
    Knob("ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S", "120", "bounded wait for the shared Manager session lock before failing open to a no-session call", "mission"),
    Knob("ARGUS_SKILL_CHECKPOINT_PERSIST", "true", "persist the reviewer checkpoint across missions/restarts", "mission"),
    Knob("ARGUS_SKILL_COMPACT_CONTINUATION_PROMPTS", "true", "send the full Engineer task/skill contract only on round 1; later rounds use reviewer guidance plus CHECKPOINT.md", "mission"),
    Knob("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "off", "compatibility gate for explicitly operator-approved source promotions such as generated data-domain verticals", "lifecycle"),
    Knob("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", "on", "Manager-promote changed reviewed Skills into shared global/vertical runtime layers after each successful mission", "lifecycle"),
    Knob("ARGUS_SKILL_SKILL_OPS", "on", "compatibility replay for legacy reviewer skill_ops; current roles edit the project layer directly", "lifecycle"),
    Knob("ARGUS_SKILL_WIKI", "on", "enable the shared direct-edit project knowledge wiki", "lifecycle"),
    Knob("ARGUS_SKILL_AUTO_INIT_WIKI", "on", "bootstrap a project wiki before the first SkillLoop mission", "lifecycle"),
    Knob("ARGUS_SKILL_AUTO_COMPACT", "off", "run LLM skill/wiki compaction after every mission (default OFF; use explicit maintenance)", "lifecycle"),
    Knob("ARGUS_SKILL_HISTORY_HOT_VERSIONS", "20", "uncompressed skill versions retained per skill before lossless gzip", "lifecycle"),
    Knob("ARGUS_SKILL_WIKI_RETIRED_HOT_VERSIONS", "20", "uncompressed wiki tombstones retained per page before lossless gzip", "lifecycle"),
    Knob("ARGUS_SKILL_METRICS_MAX_BYTES", "16777216", "rotate metrics.jsonl after this many bytes", "telemetry"),
    Knob("ARGUS_SKILL_METRICS_RETENTION_DAYS", "7", "delete rotated metrics archives older than this many days", "telemetry"),
    Knob("ARGUS_SKILL_METRICS_MAX_ARCHIVES", "14", "maximum number of rotated metrics archives to retain", "telemetry"),
    Knob("ARGUS_SKILL_AGENT_IO_MODE", "full", "agent I/O persistence: full saves prompt and every raw stream frame exactly once plus a summary; compact stores summary only", "telemetry"),
    Knob("ARGUS_SKILL_SAFE_MODE", "off", "extra-conservative guardrails", "lifecycle", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_SANDBOX", "off", "codex sandbox for builder roles (engineer/reviewer/planner/subagent): set 'workspace-write' to confine writes to the project workdir + a writable allowlist (excludes ~/.argus-skill, the package, ~/.codex) and scrub VCS creds, instead of --dangerously-bypass. Default OFF — verify on the box (network/cache/B200) before enabling", "lifecycle"),
    Knob("ARGUS_SKILL_MEASURED_MODE", "off", "measured-mode evaluation gating", "lifecycle"),
    Knob("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "off", "bypass the capability-vault preflight on daemon start", "lifecycle"),
    Knob("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", "off", "refuse daemon/WebAPI startup when source and built release artifacts differ", "lifecycle"),
    # --- telemetry / notify ---
    Knob("ARGUS_SKILL_ENABLE_TELEGRAM", "off", "enable the Telegram inbound/outbound bridge", "telemetry", cockpit=True),
    Knob("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", "(unset)", "Telegram bot token", "telemetry"),
    Knob("ARGUS_SKILL_TELEGRAM_CHAT_ID", "(unset)", "Telegram chat id to notify", "telemetry"),
    Knob("ARGUS_SKILL_SHOW_REASONING", "0", "stream the agent's reasoning to the cockpit", "telemetry", cockpit=True),
)

_BACKEND_KNOBS = frozenset(
    {
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_ENGINEER_BACKEND",
        "ARGUS_SKILL_REVIEWER_BACKEND",
        "ARGUS_SKILL_PLANNER_BACKEND",
        "ARGUS_SKILL_MANAGER_BACKEND",
    }
)
_EFFORT_KNOBS = frozenset(
    {
        "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
        "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
        "ARGUS_SKILL_SELF_REASONING_EFFORT",
        "ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    }
)
_TOGGLE_KNOBS = frozenset(
    {
        "ARGUS_SKILL_COST_CONTROL",
        "ARGUS_SKILL_SKILL_OPS",
        "ARGUS_SKILL_WIKI",
        "ARGUS_SKILL_AUTO_INIT_WIKI",
        "ARGUS_SKILL_CROSS_PROJECT_PROPAGATION",
        "ARGUS_SKILL_NEAREST_TRANSFER_ENABLED",
        "ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING",
        "ARGUS_SKILL_REQUIRE_RELEASE_MATCH",
        "ARGUS_SKILL_SAFE_MODE",
        "ARGUS_SKILL_SHOW_REASONING",
        "ARGUS_SKILL_ENABLE_TELEGRAM",
    }
)
_NON_NEGATIVE_INT_KNOBS = frozenset(
    {
        "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
        "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
        "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    }
)
_NON_NEGATIVE_FLOAT_KNOBS = frozenset({
    "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
})
_SENSITIVE_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH")
_TRUE_VALUES = frozenset(
    {"1", "true", "yes", "on", "enable", "enabled", "开", "开启", "打开", "启用"}
)
_FALSE_VALUES = frozenset(
    {"0", "false", "no", "off", "disable", "disabled", "关", "关闭", "关掉", "停用", "禁用"}
)


def resolve_knob(
    name: str,
    default: str,
    *,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> ResolvedKnob:
    """Resolve one operator knob with the canonical precedence.

    Explicit process environment wins, then the persisted cockpit setting,
    then the caller-provided default. Passing a persisted map lets callers
    resolve many knobs with one disk read.
    """
    env_map = env if env is not None else os.environ
    explicit = str(env_map.get(name, "") or "").strip()
    if explicit:
        return ResolvedKnob(explicit, "env")
    if persisted is None:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()
    saved = str(persisted.get(name, "") or "").strip()
    if saved:
        return ResolvedKnob(saved, "persisted")
    return ResolvedKnob(default, "default")


def resolve_runner_bin_setting(
    role: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> str:
    """Resolve role/shared runner paths with env-before-persisted precedence."""
    env_map = env if env is not None else os.environ
    if persisted is None:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()
    role_name = str(role or "").strip().upper()
    role_key = f"ARGUS_SKILL_{role_name}_RUNNER_BIN" if role_name else ""
    for source, key in (
        (env_map, role_key),
        (env_map, "ARGUS_SKILL_RUNNER_BIN"),
        (persisted, role_key),
        (persisted, "ARGUS_SKILL_RUNNER_BIN"),
    ):
        if not key:
            continue
        value = str(source.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _parse_budget_value(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number; got {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number; got {raw!r}")
    return value


def _migrate_legacy_budget_into_config(
    project_state_dir: object | None,
    global_root: object | None,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """One-time: preserve a pre-existing host-global budget in config.json."""
    from .knob_store import read_persisted_knobs, write_persisted_knobs

    persisted = read_persisted_knobs()
    env_map = env if env is not None else os.environ

    def _have(name: str) -> bool:
        return bool(str(env_map.get(name, "") or "").strip()) or name in persisted

    name = "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"
    if _have(name):
        return

    import json as _json
    from pathlib import Path

    def _load(path: object) -> dict | None:
        try:
            data = _json.loads(Path(str(path)).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def _fmt(value: float) -> str:
        return repr(int(value)) if float(value).is_integer() else repr(float(value))

    groot = global_root
    if groot is None and project_state_dir is not None:
        p = Path(str(project_state_dir)).expanduser()
        groot = p.parent.parent if p.parent.name == "projects" else None
    if groot is None:
        return
    payload = _load(Path(str(groot)).expanduser() / "global_budget.json")
    if payload is None or "global_daily_cap_usd" not in payload:
        return
    try:
        value = float(payload["global_daily_cap_usd"])
    except (TypeError, ValueError):
        return
    if value != float(BUDGET_KNOB_DEFAULTS[name]):
        write_persisted_knobs({name: _fmt(value)})


def resolve_budget_caps(
    *,
    project_state_dir: object | None = None,
    global_root: object | None = None,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> BudgetCaps:
    """Resolve budget caps from the knob layer — ``config.json`` is the single source.

    Precedence is ``env`` > persisted ``config.json`` > default. The retired
    ``global_budget.json`` is read ONCE (via
    ``_migrate_legacy_budget_into_config``) only to migrate a pre-existing
    operator budget into config.json so an upgrade never silently resets caps;
    ``project_state_dir``/``global_root`` are used solely to locate that file.
    """
    if persisted is None:
        _migrate_legacy_budget_into_config(project_state_dir, global_root, env=env)
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()

    def _value(name: str) -> float:
        resolved = resolve_knob(
            name,
            BUDGET_KNOB_DEFAULTS[name],
            env=env,
            persisted=persisted,
        )
        return _parse_budget_value(name, resolved.value)

    return BudgetCaps(
        global_daily_cap_usd=_value("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"),
    )


def normalize_cockpit_knob_value(name: str, value: str) -> str:
    """Validate and canonicalize a value before persisting it from the cockpit."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("config value cannot be empty")
    if name == "ARGUS_SKILL_UNPRICED_COST_POLICY":
        policy = raw.lower()
        if policy not in {"block", "allow"}:
            raise ValueError(f"{name} must be block or allow")
        return policy
    if name in BUDGET_KNOB_DEFAULTS:
        number = _parse_budget_value(name, raw.removeprefix("$"))
        return f"{number:g}"
    if name in _NON_NEGATIVE_INT_KNOBS:
        try:
            number = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a non-negative integer") from exc
        if number < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return str(number)
    if name in _NON_NEGATIVE_FLOAT_KNOBS:
        number = _parse_budget_value(name, raw)
        return f"{number:g}"
    if name in _BACKEND_KNOBS:
        backend = raw.lower()
        if backend == "opencod":
            backend = "opencode"
        if backend not in {"codex", "claude", "copilot", "opencode"}:
            raise ValueError(f"{name} must be codex, claude, copilot, or opencode")
        return backend
    if name in _EFFORT_KNOBS:
        effort = raw.lower()
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"{name} must be low, medium, high, xhigh, or max")
        return effort
    if name in _TOGGLE_KNOBS:
        toggle = raw.lower()
        if toggle in _TRUE_VALUES:
            return "1"
        if toggle in _FALSE_VALUES:
            return "0"
        raise ValueError(f"{name} must be an on/off value")
    return raw


def redact_knob_value(name: str, value: str, *, source: str) -> str:
    """Hide configured secrets on operator-facing config surfaces."""
    if source != "default" and any(marker in name.upper() for marker in _SENSITIVE_MARKERS):
        return "<redacted>" if value else ""
    return value


def cockpit_editable_names() -> frozenset[str]:
    """The env-var names an operator can change FROM THE COCKPIT — the single
    source of truth for the cockpit-editable config surface (the ``cockpit=True``
    flags above). The ``/config`` settings view marks exactly these rows as
    NL-editable, so the surface lives in one place instead of a hand-maintained
    parallel list that drifts."""
    return frozenset(knob.name for knob in KNOBS if knob.cockpit)


def format_config_help(env: Mapping[str, str] | None = None) -> str:
    """Render the knob registry grouped, with each knob's CURRENT effective value."""
    env_map = env if env is not None else os.environ
    from .knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    out: list[str] = [
        "Argus operator control knobs (ARGUS_*). Default shown in (), current value "
        "uses env -> persisted cockpit setting -> default precedence.",
        "This is the operator control surface — internal/test knobs are not listed.",
        "",
    ]
    last_group = None
    for k in KNOBS:
        if k.group != last_group:
            out.append(f"[{k.group}]")
            last_group = k.group
        resolved = resolve_knob(k.name, k.default, env=env_map, persisted=persisted)
        display_value = redact_knob_value(k.name, resolved.value, source=resolved.source)
        cur_str = (
            "(default)"
            if resolved.source == "default"
            else f"= {display_value} ({resolved.source})"
        )
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
    """Resolve a role's agent-CLI backend (codex / claude / copilot / opencode / memory)
    using Argus's runtime precedence.

    Precedence: role-specific override (``ARGUS_SKILL_<ROLE>_BACKEND``) ->
    shared ``ARGUS_SKILL_RUNNER_BACKEND`` -> shared ``ARGUS_SKILL_LIFE_BACKEND``
    -> persisted switch (the same three vars, same order — a prior
    ``/backend`` switch or natural-language "engineer 用 claude") -> ``codex``.
    Returns the RAW value (unnormalized); callers that need the canonical
    canonical backend spelling should pass it through
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


def resolve_manager_reply_model(*, env: Mapping[str, str] | None = None) -> str:
    """Resolve the high-quality operator-facing Manager SELF model."""
    env_map = env if env is not None else os.environ
    configured = resolve_knob(
        "ARGUS_SKILL_MANAGER_REPLY_MODEL",
        "inherit",
        env=env_map,
    ).value.strip()
    if configured.lower() not in {"", "auto", "inherit", "default"}:
        return configured
    return resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
        env=env_map,
    )


def resolve_manager_classify_model(*, env: Mapping[str, str] | None = None) -> str:
    """Resolve the cheap stateless front-door classification model."""
    env_map = env if env is not None else os.environ
    configured = resolve_knob(
        "ARGUS_SKILL_FRONTDOOR_MODEL",
        "auto",
        env=env_map,
    ).value.strip()
    if configured.lower() not in {"", "auto", "inherit", "default"}:
        return configured
    from ..agent_cli.runner_backend import normalize_runner_backend

    backend = normalize_runner_backend(resolve_role_backend("manager", env=env_map))
    if backend in {"codex", "copilot"}:
        return "gpt-5.4-mini"
    return resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
        env=env_map,
    )


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
