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
import re
from dataclasses import dataclass
from typing import Mapping

from ..agent_cli.runner_backend import SUPPORTED_BACKENDS


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
        "ARGUS_SKILL_RUNNER_BACKEND",
        "codex",
        "shared agent backend: selected by setup; "
        + " | ".join(SUPPORTED_BACKENDS),
        "backend",
        cockpit=True,
    ),
    Knob(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
        "legacy shared-backend fallback: "
        + " | ".join(SUPPORTED_BACKENDS)
        + " | memory (test only)",
        "backend",
    ),
    Knob("ARGUS_SKILL_RUNNER_BIN", "(agent CLI on PATH)", "absolute path to the agent CLI binary", "backend"),
    Knob("ARGUS_SKILL_PI_SESSION_DIR", "(~/.argus-skill/pi-sessions)", "Argus-owned Pi session storage, separate from interactive Pi history", "backend"),
    Knob("ARGUS_SKILL_PI_PROVIDER", "(unset — Pi resolves the id itself)", "provider prefix for bare model ids on the Pi backend; set it only to disambiguate an id two authenticated Pi catalogs both carry", "backend", cockpit=True),
    Knob("ARGUS_SKILL_OPENCODE_PROVIDER", "(unset — model is dropped)", "provider prefix for bare model ids on the OpenCode backend; `opencode run --model` needs provider/id, so without this the configured model has no effect", "backend", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_BACKEND", "(=RUNNER_BACKEND)", "per-role backend override for the engineer", "backend", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_BACKEND", "(=RUNNER_BACKEND)", "per-role backend override for the reviewer", "backend", cockpit=True),
    Knob("ARGUS_SKILL_PLANNER_BACKEND", "(=RUNNER_BACKEND)", "per-role backend override for the planner", "backend", cockpit=True),
    Knob("ARGUS_SKILL_MANAGER_BACKEND", "(=RUNNER_BACKEND)", "per-role backend override for the manager", "backend", cockpit=True),
    Knob("ARGUS_SKILL_SUPERVISOR_BACKEND", "(=RUNNER_BACKEND)", "per-role backend override for the subagent supervisor", "backend", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the engineer", "backend"),
    Knob("ARGUS_SKILL_REVIEWER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the reviewer", "backend"),
    Knob("ARGUS_SKILL_PLANNER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the planner", "backend"),
    Knob("ARGUS_SKILL_MANAGER_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the manager", "backend"),
    Knob("ARGUS_SKILL_SUPERVISOR_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the subagent supervisor", "backend"),
    # --- team Curator (resident pool + leaderboard strategy) ---
    Knob("ARGUS_SKILL_CURATOR_BACKEND", "(=RUNNER_BACKEND)", "per-role backend override for the team Curator", "backend"),
    Knob("ARGUS_SKILL_CURATOR_RUNNER_BIN", "(=RUNNER_BIN)", "per-role CLI binary for the team Curator", "backend"),
    Knob("ARGUS_SKILL_IDEA_PANEL", "(off)", "opt-in cross-lab ideation: models that propose research ideas in parallel, cross-examine each other, then each name the one to run. Set 'backend' or 'backend:model', comma separated (e.g. 'codex,claude' or 'copilot:gpt-5.5,copilot:gemini-3.1-pro-preview' on a single subscription). Blind scoring over 32 candidates found a panel buys spread, not level: the best candidate of the batch and twice the weak ones. Unset, or with fewer than two usable seats, ideation is unchanged", "models", cockpit=True),
    Knob("ARGUS_SKILL_CURATOR_MODEL", "auto", "model for Curator strategy distillation; auto uses the selected backend's default", "models"),
    Knob("ARGUS_SKILL_CURATOR_REASONING_EFFORT", "high", "Curator distillation reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_CURATOR_DISTILL_INTERVAL_S", "1260", "minimum seconds between Curator strategy updates", "team"),
    # --- resident teammate pool, time-box, and deterministic leaderboard ---
    Knob("ARGUS_TEAMMATE_PAPER_MISSION", "(inherit lead default)", "force the paper gates on|off for each teammate", "team"),
    Knob("ARGUS_TEAMMATE_TIMEOUT_S", "0", "optional teammate wall-clock cap; disabled by default", "team"),
    Knob("ARGUS_TEAMMATE_MAX_ROUNDS", "0", "optional teammate engineer-round cap; disabled by default", "team"),
    Knob("ARGUS_TEAMMATE_RESULT_FILE", "(unset)", "path the mission writes {metric,mechanism} to → the leaderboard shard", "team"),
    Knob("ARGUS_LEADERBOARD_LOWER_IS_BETTER", "off (higher-is-better)", "global leaderboard direction; a task's lower_is_better overrides it per target", "team"),
    Knob("ARGUS_TEAM_MAX_WIDTH", "64", "hard safety ceiling for one campaign's requested teammate width", "team"),
    Knob("ARGUS_TEAM_MAX_ACTIVE_CAMPAIGNS", "8", "hard safety ceiling for active Team campaigns in one project", "team"),
    Knob("ARGUS_TEAM_MAX_TASKS_PER_FORMATION", "256", "hard safety ceiling for tasks admitted by one Team formation", "team"),
    Knob("ARGUS_TEAM_MAX_TOTAL_IN_FLIGHT", "32", "hard Curator ceiling across all live teammates in one daemon", "team"),
    Knob("ARGUS_SKILL_ALLOW_NESTED_TEAM", "off", "expert override permitting a teammate to form another Team", "team"),
    # --- models ---
    Knob("ARGUS_SKILL_MODEL", "auto", "shared model override; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_MANAGER_MODEL", "auto", "model for the Manager; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_MODEL", "auto", "model for the L1 engineer; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_MODEL", "auto", "model for the L2 reviewer; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_SUPERVISOR_MODEL", "auto", "model for supervised subagent health decisions; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_PLAN_MODEL", "auto", "model for the L4 planner; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_PLAN_PREVIEW_MODEL", "auto", "interactive /plan model: gpt-5.4-mini on codex/copilot, planner model otherwise; set an id to override", "models"),
    Knob("ARGUS_SKILL_REWRITE_MODEL", "auto", "interactive prompt rewrite model: gpt-5.5 on codex/copilot, Manager model otherwise; set an id to override", "models"),
    Knob("ARGUS_SKILL_MANAGER_REPLY_MODEL", "inherit", "operator-facing Manager SELF model; inherit uses the configured Manager/shared route model", "models", cockpit=True),
    Knob("ARGUS_SKILL_FRONTDOOR_MODEL", "auto", "cheap front-door classification model: gpt-5.4-mini on codex/copilot, Manager model otherwise", "models"),
    Knob("ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "low", "reasoning effort for the LLM-only front-door and STEER confirmation", "models"),
    # --- reasoning effort ---
    Knob("ARGUS_SKILL_MANAGER_REASONING_EFFORT", "high", "manager reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_PLANNER_REASONING_EFFORT", "high", "planner reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_SELF_REASONING_EFFORT", "high", "foreground Manager SELF chat/read-only reply effort", "reasoning"),
    Knob("ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT", "low", "interactive /plan preview effort; execution planning keeps the planner setting", "reasoning"),
    Knob("ARGUS_SKILL_REWRITE_REASONING_EFFORT", "high", "interactive prompt rewrite reasoning effort", "reasoning"),
    Knob("ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", "high", "direct-task first-round Engineer effort; later rounds use the Engineer effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh", "engineer reasoning effort: low|medium|high|xhigh", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high", "reviewer reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_SUPERVISOR_REASONING_EFFORT", "low", "subagent supervisor reasoning effort", "reasoning", cockpit=True),
    # Provider/cost caps are explicit resource-admission budgets, not work clocks.
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
    Knob(
        "ARGUS_SKILL_AUTONOMY_MODE",
        "pragmatic",
        "operator interruption policy: cautious | pragmatic | autonomous",
        "mission",
        cockpit=True,
    ),
    Knob("ARGUS_SKILL_MAX_ROUNDS", "0", "optional engineer-round cap per mission; disabled by default", "mission"),
    Knob("ARGUS_SKILL_ROUND_CHECKPOINT", "off", "record private git refs for Reviewer-recommended round checkpoints", "mission"),
    Knob("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "1", "enable selective project-layer Skill maintenance for all four roles (default ON)", "mission"),
    Knob("ARGUS_SKILL_BOUNDED_DAG_MODEL", "auto", "compact model for decomposing Manager bounded tasks into backlog DAG nodes: gpt-5.4-mini on codex/copilot, planner model otherwise", "mission"),
    Knob("ARGUS_SKILL_BOUNDED_DAG_REASONING_EFFORT", "low", "reasoning effort for bounded DAG decomposition", "mission"),
    Knob("ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS", "0", "optional wall-clock cap for one Engineer turn; disabled by default", "mission"),
    Knob("ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", "600", "model stream inactivity before a diagnostic warning (0=off)", "mission"),
    Knob("ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS", "1800", "model stream inactivity before likely-stalled alerting (0=off)", "mission"),
    Knob("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", "0", "optional model stream inactivity limit before terminating the current provider process group; disabled by default", "mission"),
    Knob("ARGUS_SKILL_DECISION_PROGRESS_TIMEOUT_SECONDS", "0", "optional round-boundary limit without reviewer-classified decision/evidence progress; disabled by default", "mission"),
    Knob("ARGUS_SKILL_CHECKPOINT_PERSIST", "true", "persist the reviewer checkpoint across missions/restarts", "mission"),
    Knob("ARGUS_SKILL_COMPACT_CONTINUATION_PROMPTS", "true", "send the full Engineer task/skill contract only on round 1; later rounds use reviewer guidance plus CHECKPOINT.md", "mission"),
    Knob("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "off", "compatibility gate for explicitly operator-approved source promotions such as generated data-domain verticals", "lifecycle"),
    Knob("ARGUS_SKILL_CROSS_PROJECT_PROPAGATION", "on", "Manager-promote changed reviewed Skills into shared global/vertical runtime layers after each successful mission", "lifecycle"),
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
    Knob("ARGUS_SKILL_ENGINEER_SANDBOX", "off", "codex sandbox for builder roles (engineer/reviewer/planner/subagent): set 'workspace-write' to confine writes to the project workdir + a writable allowlist (excludes ~/.argus-skill, the package, ~/.codex) and scrub VCS creds, instead of --dangerously-bypass. Default OFF — verify required network, cache, and remote accelerator access before enabling", "lifecycle"),
    Knob("ARGUS_SKILL_MEASURED_MODE", "off", "measured-mode evaluation gating", "lifecycle"),
    Knob("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "off", "bypass the capability-vault preflight on daemon start", "lifecycle"),
    Knob("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", "off", "refuse daemon/WebAPI startup when source and built release artifacts differ", "lifecycle"),
    # --- telemetry / notify ---
    Knob("ARGUS_SKILL_ENABLE_TELEGRAM", "off", "enable the Telegram inbound/outbound bridge", "telemetry", cockpit=True),
    Knob("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", "(unset)", "Telegram bot token", "telemetry"),
    Knob("ARGUS_SKILL_TELEGRAM_CHAT_ID", "(unset)", "Telegram chat id to notify", "telemetry"),
    Knob("ARGUS_SKILL_ENABLE_FEISHU", "off", "enable the Feishu/Lark bridge (WebSocket long connection; no public URL needed)", "telemetry", cockpit=True),
    Knob("ARGUS_SKILL_FEISHU_APP_ID", "(unset)", "Feishu app id (cli_...)", "telemetry"),
    Knob("ARGUS_SKILL_FEISHU_APP_SECRET", "(unset)", "Feishu app secret", "telemetry"),
    Knob("ARGUS_SKILL_FEISHU_CHAT_ID", "(unset)", "Feishu chat id to notify", "telemetry"),
    Knob("ARGUS_SKILL_FEISHU_ALLOWED_USERS", "(unset)", "comma-separated Feishu open_ids allowed to drive the daemon; unset allows everyone the bot can see", "telemetry"),
    Knob("ARGUS_SKILL_FEISHU_DOMAIN", "feishu", "Feishu open-platform host: 'feishu' (mainland), 'lark' (international), or a full URL", "telemetry"),
    Knob("ARGUS_SKILL_SHOW_REASONING", "0", "stream the agent's reasoning to the cockpit", "telemetry", cockpit=True),
)

# A model knob carries a bare model id (``gpt-5.6-sol``, ``copilot/opus-5``) or a
# sentinel (``auto``, ``inherit``). Free text reaches the agent CLI verbatim as
# ``--model <text>``, so every call by that role fails until it is unset.
_MODEL_KNOBS = frozenset(knob.name for knob in KNOBS if knob.group == "models")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,63}$")
_PROVIDER_KNOBS = frozenset(
    {
        "ARGUS_SKILL_PI_PROVIDER",
        "ARGUS_SKILL_OPENCODE_PROVIDER",
    }
)
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_BACKEND_KNOBS = frozenset(
    {
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_ENGINEER_BACKEND",
        "ARGUS_SKILL_REVIEWER_BACKEND",
        "ARGUS_SKILL_PLANNER_BACKEND",
        "ARGUS_SKILL_MANAGER_BACKEND",
        "ARGUS_SKILL_SUPERVISOR_BACKEND",
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
        "ARGUS_SKILL_SUPERVISOR_REASONING_EFFORT",
    }
)
_TOGGLE_KNOBS = frozenset(
    {
        "ARGUS_SKILL_COST_CONTROL",
        "ARGUS_SKILL_WIKI",
        "ARGUS_SKILL_AUTO_INIT_WIKI",
        "ARGUS_SKILL_CROSS_PROJECT_PROPAGATION",
        "ARGUS_SKILL_NEAREST_TRANSFER_ENABLED",
        "ARGUS_SKILL_ROUND_CHECKPOINT",
        "ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING",
        "ARGUS_SKILL_REQUIRE_RELEASE_MATCH",
        "ARGUS_SKILL_SAFE_MODE",
        "ARGUS_SKILL_SHOW_REASONING",
        "ARGUS_SKILL_ENABLE_TELEGRAM",
        "ARGUS_SKILL_ENABLE_FEISHU",
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
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> str:
    """Resolve a runner path without crossing persisted backend bindings."""
    env_map = env if env is not None else os.environ
    if persisted is None:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()
    role_name = str(role or "").strip().upper()
    role_key = f"ARGUS_SKILL_{role_name}_RUNNER_BIN" if role_name else ""
    for key in (role_key, "ARGUS_SKILL_RUNNER_BIN"):
        if not key:
            continue
        value = str(env_map.get(key, "") or "").strip()
        if value:
            return value

    from ..agent_cli.runner_backend import normalize_runner_backend

    def normalized(value: object) -> str:
        text = str(value or "").strip()
        return normalize_runner_backend(text) if text else ""

    requested_backend = normalized(backend)
    if not requested_backend:
        for key in (
            f"ARGUS_SKILL_{role_name}_BACKEND" if role_name else "",
            "ARGUS_SKILL_RUNNER_BACKEND",
            "ARGUS_SKILL_LIFE_BACKEND",
        ):
            requested_backend = normalized(env_map.get(key))
            if requested_backend:
                break

    shared_backend = normalized(
        persisted.get("ARGUS_SKILL_RUNNER_BACKEND")
        or persisted.get("ARGUS_SKILL_LIFE_BACKEND")
    )
    role_backend = normalized(
        persisted.get(f"ARGUS_SKILL_{role_name}_BACKEND")
        if role_name
        else ""
    ) or shared_backend
    for key, configured_backend in (
        (role_key, role_backend),
        ("ARGUS_SKILL_RUNNER_BIN", shared_backend),
    ):
        if not key:
            continue
        value = str(persisted.get(key, "") or "").strip()
        if value and (
            not requested_backend or requested_backend == configured_backend
        ):
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
    if name == "ARGUS_SKILL_AUTONOMY_MODE":
        mode = raw.lower()
        if mode not in {"cautious", "pragmatic", "autonomous"}:
            raise ValueError(
                f"{name} must be cautious, pragmatic, or autonomous"
            )
        return mode
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
        if backend not in {
            "codex",
            "claude",
            "copilot",
            "cursor",
            "opencode",
            "pi",
            "grok",
            "qoder",
            "dsh",
        }:
            raise ValueError(
                f"{name} must be " + ", ".join(SUPPORTED_BACKENDS)
            )
        return backend
    if name in _EFFORT_KNOBS:
        effort = raw.lower()
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"{name} must be low, medium, high, xhigh, or max")
        return effort
    if name in _PROVIDER_KNOBS:
        # A provider is one catalog name as the backend CLI spells it
        # (``deepseek``, ``anthropic``, ``copilot-forward``) — never a
        # provider/model pair, which would produce ``a/b/model`` downstream.
        provider = raw.strip("/")
        if not _PROVIDER_ID_RE.match(provider):
            raise ValueError(
                f"{name} must be a single provider id such as deepseek, "
                f"not {raw!r}"
            )
        return provider
    if name in _MODEL_KNOBS:
        # Natural-language cockpit requests often omit separators
        # (``gpt5.6sol``). Canonicalize only the unambiguous GPT family while
        # leaving arbitrary provider/model ids verbatim.
        compact_gpt = re.fullmatch(
            r"gpt[-_]?(\d+(?:\.\d+)?)(?:[-_]?(sol|mini|codex))?",
            raw,
            flags=re.IGNORECASE,
        )
        if compact_gpt:
            suffix = compact_gpt.group(2)
            raw = f"gpt-{compact_gpt.group(1)}" + (
                f"-{suffix.lower()}" if suffix else ""
            )
        if not _MODEL_ID_RE.match(raw):
            raise ValueError(
                f"{name} must be a bare model id such as gpt-5.6-sol, not free "
                f"text: {raw!r}"
            )
        return raw
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
    backend: str | None = None,
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
            return "" if explicit.lower() in _AUTO_MODEL_SENTINELS else explicit
    shared = str(env_map.get("ARGUS_SKILL_MODEL", "") or "").strip()
    if shared:
        return "" if shared.lower() in _AUTO_MODEL_SENTINELS else shared
    from .knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    if role_env:
        persisted_role = persisted.get(role_env, "").strip()
        if persisted_role:
            return (
                ""
                if persisted_role.lower() in _AUTO_MODEL_SENTINELS
                else persisted_role
            )
    persisted_shared = persisted.get("ARGUS_SKILL_MODEL", "").strip()
    if persisted_shared:
        return (
            ""
            if persisted_shared.lower() in _AUTO_MODEL_SENTINELS
            else persisted_shared
        )
    from ..agent_cli.runner_backend import BACKEND_MEMORY, normalize_runner_backend

    # default="codex": the backend is used here ONLY to choose between "name an
    # OpenAI catalog id" and "leave the model to the backend". Model lookup must
    # not hard-fail because no backend knob is set, and codex is what an
    # unconfigured runner would itself normalize to
    # (``normalize_runner_backend("")`` / ``AgentCliBackend``'s default), so this
    # stays consistent with the CLI that will really be spawned.
    requested = backend or resolve_role_backend(route, env=env_map, default="codex")
    if str(requested).strip().lower() == BACKEND_MEMORY:
        # The in-process backend has no provider and therefore no model catalog
        # to name an id from. Branch before normalizing: memory is not an
        # agent-CLI backend, so normalize_runner_backend rejects it.
        return ""
    backend_name = normalize_runner_backend(requested)
    if not backend_uses_openai_catalog(backend_name, env=env_map):
        return ""
    from ..tools.capability_vault import resolve_route_model

    return resolve_route_model(route, env_map)


class BackendResolutionError(RuntimeError):
    """No agent-CLI backend is configured for a role, and the caller named none.

    Raised by :func:`resolve_role_backend` when every layer of the documented
    precedence chain is empty. The caller is expected to do ONE of two things:
    pass an explicit ``default=`` (only at a site that can genuinely assume a
    backend — say why in a comment), or let this propagate so the operator sees
    it. The message names the role, every knob that was checked, and the
    knob-store file a value can be written to, so it is actionable without
    reading source.

    It used to be neither: the chain ended in a bare ``return "codex"``, so a
    daemon launched with ``--backend copilot`` — a CLI argument that was never
    exported to the environment — silently ran its roles on codex.
    """


def _role_backend_knobs(role: str) -> list[str]:
    """The env-var names checked for ``role``, most specific first.

    The persisted knob store is consulted for the SAME names in the SAME order,
    so this one list defines all four positions of the precedence chain.
    """
    return [
        name
        for name in (
            f"ARGUS_SKILL_{role.upper()}_BACKEND" if role else "",
            "ARGUS_SKILL_RUNNER_BACKEND",
            "ARGUS_SKILL_LIFE_BACKEND",
        )
        if name
    ]


def resolve_role_backend_with_source(
    role: str,
    *,
    env: Mapping[str, str] | None = None,
    default: str | None = None,
) -> tuple[str, str]:
    """Resolve a role's agent-CLI backend AND where the value came from.

    Returns ``(backend, source)``. The source vocabulary is the one
    ``core.backend_readiness.resolve_backend_profile`` already uses for exactly
    this purpose — ``env:<VAR>`` / ``persisted:<VAR>`` / ``default`` — so the
    cockpit can render provenance from either resolver without a second
    dialect.

    Precedence (unchanged): role-specific override
    (``ARGUS_SKILL_<ROLE>_BACKEND``) -> shared ``ARGUS_SKILL_RUNNER_BACKEND``
    -> shared ``ARGUS_SKILL_LIFE_BACKEND`` -> persisted switch (the same three
    vars, same order — a prior ``/backend`` switch or natural-language
    "engineer 用 claude") -> ``default``.

    ``default`` is REQUIRED to be a non-empty string if the caller wants a
    fallback; there is no implicit one. When the chain is empty and no default
    was named this raises :class:`BackendResolutionError` rather than guessing
    codex.

    Returns the RAW value (unnormalized); callers that need the canonical
    backend spelling should pass it through
    ``agent_cli.runner_backend.normalize_runner_backend``, same as every
    existing caller of this precedence already does.
    """
    env_map = env if env is not None else os.environ
    candidates = _role_backend_knobs(role)
    for var in candidates:
        val = str(env_map.get(var, "") or "").strip()
        if val:
            return val, f"env:{var}"
    from .knob_store import read_persisted_knobs

    # A corrupt knob store raises KnobStoreCorruptError, which is deliberately
    # allowed to propagate: "the operator's persisted /backend switch" and
    # "the file holding it is unreadable" must not resolve to the same answer.
    persisted = read_persisted_knobs()
    for var in candidates:
        val = str(persisted.get(var, "") or "").strip()
        if val:
            return val, f"persisted:{var}"
    fallback = str(default or "").strip()
    if fallback:
        return fallback, "default"
    from .paths import config_path

    raise BackendResolutionError(
        f"no agent-CLI backend is configured for role {role or '<shared>'!r}. "
        f"Checked, in order: {', '.join(candidates)} in this process's "
        f"environment, then the same names in the persisted knob store at "
        f"{config_path()}. Set one — e.g. run Argus with "
        f"`--backend copilot`, send `/backend copilot` from the cockpit, or "
        f"export ARGUS_SKILL_RUNNER_BACKEND=copilot before starting the "
        f"daemon. (A call site that can legitimately assume a backend must say "
        f"so by passing an explicit default= instead.)"
    )


def resolve_role_backend(
    role: str,
    *,
    env: Mapping[str, str] | None = None,
    default: str | None = None,
) -> str:
    """Resolve a role's agent-CLI backend
    (codex / claude / copilot / cursor / opencode / pi / grok / memory)
    using Argus's runtime precedence.

    Thin wrapper over :func:`resolve_role_backend_with_source` for the callers
    that do not need provenance. See that function for the precedence chain and
    for why an unconfigured resolve raises :class:`BackendResolutionError`
    instead of returning codex.
    """
    return resolve_role_backend_with_source(role, env=env, default=default)[0]


def resolve_manager_reply_model(
    *,
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
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
        backend=backend,
        env=env_map,
    )


#: Backends whose model catalog IS the OpenAI catalog, so Argus may name a
#: specific OpenAI id for its cheap control-plane routes without asking the
#: operator. ``copilot`` qualifies by construction.  Codex normally does too,
#: except when its own config selects a non-OpenAI custom provider such as
#: DeepSeek; :func:`backend_uses_openai_catalog` handles that exception.
_OPENAI_CATALOG_BACKENDS = frozenset({"codex", "copilot"})


def backend_uses_openai_catalog(
    backend: str,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Whether Argus may safely force an OpenAI model id for ``backend``.

    Codex CLI is also a generic OpenAI-compatible client.  A user can point it
    at a named custom provider in ``~/.codex/config.toml`` (for example
    ``model_provider = \"deepseek\"``).  In that mode forcing Argus's
    ``gpt-5.4-mini``/``gpt-5.5`` defaults makes every turn fail before the
    Manager can classify it.  Leave the model unset instead, allowing Codex to
    use its own configured default.  Explicit Argus model knobs still win in
    the callers above.
    """
    normalized = str(backend or "").strip().casefold()
    if normalized not in _OPENAI_CATALOG_BACKENDS:
        return False
    if normalized != "codex":
        return True
    try:
        from ..tools.capability_vault import read_codex_provider_config

        provider = read_codex_provider_config(env)
    except Exception:  # noqa: BLE001 — preserve legacy Codex behavior on unreadable config
        return True
    if provider is None:
        return True
    # ``codex`` is Codex's built-in OpenAI provider; ``openai`` is a common
    # equivalent spelling.  Any other named table has an operator-defined
    # catalog and must retain its configured default model.
    return provider.name.strip().casefold() in {"codex", "openai"}


#: Knob values that mean "decide for me" rather than naming a model.
_AUTO_MODEL_SENTINELS = frozenset({"", "auto", "inherit", "default"})


def resolve_cheap_route_model(
    *,
    knob: str,
    catalog_default: str,
    role: str,
    role_env: str,
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve one cheap control-plane route's model.

    Four routes want a small model rather than the role's full-strength one:
    Manager front-door classify, bounded-DAG decomposition, ``/plan`` preview,
    and interactive prompt rewrite. Each used to carry its own copy of this
    rule, and the copies agreed on the wrong thing — they counted ``pi`` as an
    OpenAI-catalog backend. A Pi fronting DeepSeek therefore asked its provider
    for ``gpt-5.4-mini`` and all four routes hard-failed, no matter how
    carefully the operator had configured Argus's documented model knobs.

    Precedence: an explicit knob value wins; an OpenAI-catalog backend gets
    ``catalog_default`` (each route passes its own historical id, so codex and
    copilot behaviour is unchanged); every other backend falls back to the
    role's own model — the only id an arbitrary provider is known to carry.

    中文：四条「廉价路由」原先各自硬编码 ``gpt-5.4-mini``，并把 ``pi`` 误当作
    OpenAI 目录后端；此处统一规则，非 OpenAI 目录的后端回落到角色 model。
    """
    env_map = env if env is not None else os.environ
    configured = resolve_knob(knob, "auto", env=env_map).value.strip()
    if configured.lower() not in _AUTO_MODEL_SENTINELS:
        return configured
    from ..agent_cli.runner_backend import BACKEND_MEMORY, normalize_runner_backend

    # default="codex": same reasoning as resolve_role_model above — this only
    # selects between the OpenAI catalog id and the role's own model, and a
    # cheap control-plane route must not fail to pick a model just because no
    # backend knob is set. memory branches out first, same as there.
    requested = backend or resolve_role_backend(role, env=env_map, default="codex")
    backend_name = (
        BACKEND_MEMORY
        if str(requested).strip().lower() == BACKEND_MEMORY
        else normalize_runner_backend(requested)
    )
    if backend_uses_openai_catalog(backend_name, env=env_map):
        return catalog_default
    return resolve_role_model(
        role,
        role_env=role_env,
        backend=backend_name,
        env=env_map,
    )


def resolve_manager_classify_model(
    *,
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the cheap stateless front-door classification model."""
    return resolve_cheap_route_model(
        knob="ARGUS_SKILL_FRONTDOOR_MODEL",
        catalog_default="gpt-5.4-mini",
        role="manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
        backend=backend,
        env=env,
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


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an integer knob, falling back to the default on anything unusable."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)
