"""Operator-facing ARGUS_* knob registry + ``--config-help`` rendering.

A single discoverable list of the knobs an operator actually TUNES — backend,
models, reasoning effort, budget, lifecycle, telemetry — each with its default and
a one-line doc, so steering Argus stops being a grep-the-source exercise (the
audit found ~120 knobs with ~15 documented). ``argus --config-help`` prints
this with the CURRENT effective value of each.

Scope: the operator control surface, NOT every internal/test/handoff knob. Add a
knob here when an operator would reasonably set it. Defaults are documentation —
the authoritative default still lives at each read-site; keep them in sync.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from ..agent_cli.runner_backend import SUPPORTED_BACKENDS
from .hosting import hosting


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
    Knob(
        "ARGUS_SKILL_COPILOT_HOME", "(shared account)",
        "dedicated Copilot account directory; configure with --setup --backend copilot "
        "--copilot-home PATH, then restart running Argus processes",
        "backend",
    ),
    Knob(
        "ARGUS_SKILL_COPILOT_TOKEN_FROM_ENV", "0",
        "1 = authenticate Copilot children with COPILOT_GITHUB_TOKEN from Argus's "
        "environment (headless/container hosts without a logged-in home); only that "
        "token is passed, GH_TOKEN/GITHUB_TOKEN and git credentials stay stripped",
        "backend",
    ),
    Knob(
        "ARGUS_SKILL_FRAMEWORK_PYTHON_ON_PATH", "1",
        "1 = daemon child shells find Argus's own interpreter first on PATH; 0 = keep "
        "the host PATH (hosts whose work needs their own python3, e.g. task "
        "containers); ARGUS_SKILL_PYTHON still names the Argus interpreter",
        "backend",
    ),
    Knob("ARGUS_SKILL_PI_SESSION_DIR", "(~/.argus-skill/pi-sessions)", "Argus-owned Pi session storage, separate from interactive Pi history", "backend"),
    Knob("ARGUS_SKILL_PI_PROVIDER", "(unset — Pi resolves the id itself)", "provider prefix for bare model ids on the Pi backend; set it only to disambiguate an id two authenticated Pi catalogs both carry", "backend", cockpit=True),
    Knob("ARGUS_SKILL_OPENCODE_PROVIDER", "(unset — model is dropped)", "provider prefix for bare model ids on the OpenCode backend; `opencode run --model` needs provider/id, so without this the configured model has no effect", "backend", cockpit=True),
    # --- models ---
    Knob("ARGUS_SKILL_MODEL", "auto", "shared model override; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_MODEL_CHOICES", "(unset)", "comma-separated model ids the configured provider serves, offered by the model picker; for backends that publish no model list (e.g. Codex behind a relay)", "models"),
    Knob("ARGUS_SKILL_MANAGER_MODEL", "auto", "model for the Manager; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_MODEL", "auto", "model for the L1 engineer; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_MAP_TIMEOUT_SECONDS", "600", "map-only generation deadline in seconds (1-3600); does not limit research turns", "map", cockpit=True),
    Knob(
        "ARGUS_SKILL_LIGHT_MODEL", "auto",
        "model for every light route (front-door classification, /plan preview, "
        "prompt rewrite, map text, reflection and knowledge upkeep); auto is "
        "gpt-6.1-sol on Copilot and the Manager model elsewhere",
        "models", cockpit=True,
    ),
    Knob("ARGUS_SKILL_REVIEWER_MODEL", "auto", "model for the L2 reviewer; auto uses the selected backend's default", "models", cockpit=True),
    Knob("ARGUS_SKILL_OBSERVER_MODEL", "auto", "model for the Observer, who watches whether the work flows and tells the Manager when it does not; auto follows the Reviewer model", "models", cockpit=True),
    Knob("ARGUS_SKILL_PLAN_MODEL", "auto", "model for the L4 planner; auto uses the selected backend's default", "models", cockpit=True),
    # --- reasoning effort ---
    Knob("ARGUS_SKILL_MANAGER_REASONING_EFFORT", "high", "manager reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_PLANNER_REASONING_EFFORT", "high", "planner reasoning effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_LIGHT_REASONING_EFFORT", "low", "reasoning effort for every light route", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_SELF_REASONING_EFFORT", "medium", "Manager chat turn effort; unset: medium, high while ruling on a team stage question", "reasoning"),
    Knob("ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", "medium", "direct-task first-round Engineer effort; unset: medium for bounded work, high for research/paper; later rounds use the Engineer effort", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high", "engineer reasoning effort: low|medium|high|xhigh; set, it applies to every round", "reasoning", cockpit=True),
    Knob("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "medium", "reviewer reasoning effort; unset: medium for bounded work, high for research/paper/staged", "reasoning", cockpit=True),
    # Capacity limits are explicit resource admission, not work clocks.
    # --- budget ---
    Knob("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "0", "concurrent provider processes across all backends and projects; 0 disables", "budget"),
    Knob("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "45", "seconds a provider call queues for a busy concurrency slot before it is refused; 0 refuses at once", "budget"),
    Knob("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", str(DEFAULT_MAX_ACTIVE_DAEMONS), "host-wide active daemon cap", "budget", cockpit=True),
    # --- mission / lifecycle ---
    Knob(
        "ARGUS_SKILL_OPERATOR_AVAILABLE",
        "true",
        "whether Argus can ask you now. false: the Manager holds the operator's authority, settles questions of meaning itself and decides every parked operator question in your place, recording each decision for the run report. --no-operator turns it off for one run; telling the Manager not to ask (or to ask again) saves it for every run",
        "mission",
        cockpit=True,
    ),
    Knob(
        "ARGUS_SKILL_RUNTIME_CANARY_COOLDOWN_MIN",
        "30",
        "minutes after a runtime-failure circuit opens before the first timed canary window; each window that does not close the circuit doubles the wait; 0 opens windows only when a fact changes (backend, model, operator input)",
        "mission",
    ),
    Knob("ARGUS_SKILL_MAX_ROUNDS", "0", "optional engineer-round cap per mission; disabled by default", "mission"),
    Knob("ARGUS_SKILL_PROVIDER_TURN_CAP", "0", "optional per-call interaction allowance for Engineer/Reviewer; disabled by default (0). If explicitly enabled, save a checkpoint and continue in a fresh session", "mission"),
    Knob("ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", "600", "model stream inactivity before a diagnostic warning (0=off)", "mission"),
    Knob("ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS", "1800", "seconds a model turn may make no progress (no model stream event, no CPU time in its commands) before it is reported as likely stalled; an Engineer turn is then reported to the Manager once, which decides whether to keep waiting or restart it (0=off)", "mission"),
    Knob("ARGUS_SKILL_CHECKPOINT_PERSIST", "true", "persist the reviewer checkpoint across missions/restarts", "mission"),
    Knob("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "off", "compatibility gate for explicitly operator-approved source promotions such as generated data-domain verticals", "lifecycle"),
    Knob("ARGUS_SKILL_METRICS_MAX_BYTES", "16777216", "rotate metrics.jsonl after this many bytes", "telemetry"),
    Knob("ARGUS_SKILL_METRICS_RETENTION_DAYS", "7", "delete rotated metrics archives older than this many days", "telemetry"),
    Knob("ARGUS_SKILL_METRICS_MAX_ARCHIVES", "14", "maximum number of rotated metrics archives to retain", "telemetry"),
    Knob("ARGUS_SKILL_SAFE_MODE", "off", "extra-conservative guardrails", "lifecycle", cockpit=True),
    Knob(
        "ARGUS_SKILL_REDACT_SECRETS", "0",
        "1 = mask credential-shaped text in events, transcripts, model-visible text, "
        "config views and changed artifacts; off by default because masked text "
        "cannot be verified (secret detection for training capture is unaffected)",
        "lifecycle",
    ),
    # --- learning (what Argus keeps from missions and answers) ---
    Knob(
        "ARGUS_SKILL_LEARNING", "1",
        "learning on/off: after a mission or a researched answer Argus keeps what is "
        "worth keeping (lessons, facts, procedures, Skills, shared principles), keeps "
        "the project wiki, and recalls what other projects on this host learned; "
        "off does none of this",
        "learning", cockpit=True,
    ),
    Knob("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "off", "bypass the capability-vault preflight on daemon start", "lifecycle"),
    Knob("ARGUS_SKILL_SOURCE_ROOT", "(unset)", "expected deployment source root: when set, daemon/WebAPI startup refuses a process whose loaded package resolves to a different checkout (rewritten-launcher guard)", "lifecycle"),
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
    Knob("ARGUS_SKILL_LETTER_INTERVAL_HOURS", "8", "hours between Argus's letters to the operator, written to the transcript and LETTERS.md; 0 turns the letters off", "telemetry"),
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
#: Every role runs on the one shared backend; the legacy name is still read.
_BACKEND_KNOBS = ("ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_LIFE_BACKEND")
_EFFORT_KNOBS = frozenset(
    {
        "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
        "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
        "ARGUS_SKILL_SELF_REASONING_EFFORT",
        "ARGUS_SKILL_LIGHT_REASONING_EFFORT",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    }
)
_TOGGLE_KNOBS = frozenset(
    {
        "ARGUS_SKILL_LEARNING",
        "ARGUS_SKILL_SAFE_MODE",
        "ARGUS_SKILL_REDACT_SECRETS",
        "ARGUS_SKILL_SHOW_REASONING",
        "ARGUS_SKILL_ENABLE_TELEGRAM",
        "ARGUS_SKILL_ENABLE_FEISHU",
        "ARGUS_SKILL_OPERATOR_AVAILABLE",
    }
)
_NON_NEGATIVE_INT_KNOBS = frozenset({"ARGUS_SKILL_MAX_ACTIVE_DAEMONS"})
# A path knob names one absolute filesystem location. ``~`` expands at persist
# time; a relative path would silently depend on whichever cwd the reading
# process was started from. Existence is deliberately NOT required: the
# source-root preflight compares resolved paths and fail-closes, so a
# configured root that never materializes can only refuse startup, never
# admit the wrong checkout.
_PATH_KNOBS = frozenset({"ARGUS_SKILL_SOURCE_ROOT"})
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
    *,
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> str:
    """The agent CLI path: the environment's, else the saved one when it was
    saved for the backend now requested."""
    env_map = env if env is not None else os.environ
    explicit = str(env_map.get("ARGUS_SKILL_RUNNER_BIN", "") or "").strip()
    if explicit:
        return explicit
    if persisted is None:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()
    saved = str(persisted.get("ARGUS_SKILL_RUNNER_BIN", "") or "").strip()
    if not saved:
        return ""
    from ..agent_cli.runner_backend import normalize_runner_backend

    def first_backend(source: Mapping[str, str]) -> str:
        text = next((str(source.get(k) or "").strip() for k in _BACKEND_KNOBS if source.get(k)), "")
        return normalize_runner_backend(text) if text else ""

    requested = (
        normalize_runner_backend(backend) if str(backend or "").strip() else first_backend(env_map)
    )
    return saved if not requested or requested == first_backend(persisted) else ""


def normalize_cockpit_knob_value(name: str, value: str) -> str:
    """Validate and canonicalize a value before persisting it from the cockpit."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("config value cannot be empty")
    if name == "ARGUS_SKILL_MAP_TIMEOUT_SECONDS":
        maximum = 3600
        try:
            bounded_integer = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}") from exc
        if not 1 <= bounded_integer <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        return str(bounded_integer)
    if name in _NON_NEGATIVE_INT_KNOBS:
        try:
            number = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a non-negative integer") from exc
        if number < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return str(number)
    if name in _PATH_KNOBS:
        from pathlib import Path

        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{name} must be an absolute path; got {raw!r}")
        return str(path)
    if name == "ARGUS_SKILL_RUNNER_BACKEND":
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


def learning_enabled(*, env: Mapping[str, str] | None = None) -> bool:
    """The one learning switch, ``ARGUS_SKILL_LEARNING`` (on by default)."""
    value = resolve_knob("ARGUS_SKILL_LEARNING", "1", env=env).value
    return value.strip().lower() not in _FALSE_VALUES


def redact_knob_value(name: str, value: str, *, source: str) -> str:
    """Hide configured secrets on operator-facing config surfaces.

    Follows ``ARGUS_SKILL_REDACT_SECRETS`` like every other Argus redaction.
    """
    from .secret_guard import secret_redaction_enabled

    if not secret_redaction_enabled():
        return value
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
    hosted_trial = hosting().enabled(env_map) and str(
        backend or resolve_role_backend(route, env=env_map, default="codex")
    ).strip().lower() == "copilot"
    # Trial is a custom catalog. Normalize automatic choices before admission,
    # logging and execution, not only in the CLI command builder (upstream's
    # trial_model_options). Explicit choices still reach the validation guard.
    auto_model = hosting().client_model if hosted_trial else ""
    if role_env:
        explicit = str(env_map.get(role_env, "") or "").strip()
        if explicit:
            return auto_model if explicit.lower() in _AUTO_MODEL_SENTINELS else explicit
    shared = str(env_map.get("ARGUS_SKILL_MODEL", "") or "").strip()
    if shared:
        return auto_model if shared.lower() in _AUTO_MODEL_SENTINELS else shared
    from .knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    if role_env:
        persisted_role = persisted.get(role_env, "").strip()
        if persisted_role:
            return (
                auto_model
                if persisted_role.lower() in _AUTO_MODEL_SENTINELS
                else persisted_role
            )
    persisted_shared = persisted.get("ARGUS_SKILL_MODEL", "").strip()
    if persisted_shared:
        return (
            auto_model
            if persisted_shared.lower() in _AUTO_MODEL_SENTINELS
            else persisted_shared
        )
    if hosted_trial:
        return hosting().client_model
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

    Every role runs on the one shared backend. Precedence:
    ``ARGUS_SKILL_RUNNER_BACKEND`` -> legacy ``ARGUS_SKILL_LIFE_BACKEND`` ->
    persisted switch (the same two names, same order — a prior ``/backend``
    switch or a natural-language one) -> ``default``. ``role`` only names the
    caller in errors; a retired per-role ``ARGUS_SKILL_<ROLE>_BACKEND`` is
    ignored.

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
    candidates = _BACKEND_KNOBS
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
    if normalized == "copilot":
        return not hosting().enabled(env)
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


def _main_model_requested_auto(env: Mapping[str, str]) -> bool:
    """Whether this process's environment asks the Manager model to be automatic.

    Only the environment counts: the cockpit stores ``auto`` as the ordinary
    default. The role variable outranks the shared one, as in
    :func:`resolve_role_model`.
    """
    for name in ("ARGUS_SKILL_MANAGER_MODEL", "ARGUS_SKILL_MODEL"):
        value = str(env.get(name, "") or "").strip()
        if value:
            return value.lower() in _AUTO_MODEL_SENTINELS
    return False


#: What ``ARGUS_SKILL_LIGHT_MODEL=auto`` names on a backend that sells it.
LIGHT_MODEL_DEFAULT = "gpt-6.1-sol"


def resolve_light_model(
    *,
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """The one model for every light route.

    An explicit ``ARGUS_SKILL_LIGHT_MODEL`` wins, unless it is only a saved
    value while this process's environment names the Manager or shared model
    (a deployment default must not send a model id to a backend that never
    offered it). ``auto`` is :data:`LIGHT_MODEL_DEFAULT` on Copilot and the
    Manager model elsewhere: Codex may run on a ChatGPT account whose catalog
    lacks it, and Pi, Claude and the rest sell their own catalogs.
    """
    env_map = env if env is not None else os.environ
    requested = str(
        backend or resolve_role_backend("manager", env=env_map, default="codex")
    ).strip().lower()

    def manager_model() -> str:
        return resolve_role_model(
            "manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend=requested, env=env_map,
        )

    resolved = resolve_knob("ARGUS_SKILL_LIGHT_MODEL", "auto", env=env_map)
    configured = resolved.value.strip()
    if configured.lower() not in _AUTO_MODEL_SENTINELS:
        if resolved.source == "persisted" and any(
            str(env_map.get(name, "") or "").strip()
            for name in ("ARGUS_SKILL_MANAGER_MODEL", "ARGUS_SKILL_MODEL")
        ):
            return manager_model()
        return configured
    if (
        requested == "copilot"
        and backend_uses_openai_catalog(requested, env=env_map)
        and not _main_model_requested_auto(env_map)
    ):
        return LIGHT_MODEL_DEFAULT
    return manager_model()


def resolve_light_effort(*, env: Mapping[str, str] | None = None) -> str:
    """The one reasoning effort for every light route."""
    return resolve_knob("ARGUS_SKILL_LIGHT_REASONING_EFFORT", "low", env=env).value.strip() or "low"


def resolve_role_reasoning_effort(
    role_env: str, *, env: Mapping[str, str] | None = None, default: str = "high",
) -> str:
    """Resolve a role's reasoning effort using Argus's runtime precedence:
    role-specific env override -> persisted switch (a prior ``/config``
    switch or natural-language "engineer 用 high 强度") -> ``default``.
    """
    env_map = env if env is not None else os.environ
    if hosting().enabled(env_map):
        return hosting().reasoning_effort
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
