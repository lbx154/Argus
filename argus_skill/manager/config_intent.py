"""Natural-language configuration intent handling."""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from ..apps._life_actions import append_note
from .front_door import _accepts_keyword, _ensure_manager_runner

_ROLE_BACKEND_ENVS: dict[str, str] = {
    "manager": "ARGUS_SKILL_MANAGER_BACKEND",
    "planner": "ARGUS_SKILL_PLANNER_BACKEND",
    "engineer": "ARGUS_SKILL_ENGINEER_BACKEND",
    "reviewer": "ARGUS_SKILL_REVIEWER_BACKEND",
}
_ROLE_EFFORT_ENVS: dict[str, str] = {
    "manager": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "planner": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "engineer": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
}
_ROLE_MODEL_ENVS: dict[str, str] = {
    "manager": "ARGUS_SKILL_ENGINEER_MODEL",
    "planner": "ARGUS_SKILL_PLAN_MODEL",
    "engineer": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer": "ARGUS_SKILL_REVIEWER_MODEL",
}


def _front_door_classify(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    accepts_keyword: Callable[[Any, str], bool] | None = None,
) -> "tuple[Any, str | None, str]":
    """ONE merged LLM call for the Manager front-door: returns
    ``(ConfigIntent | None, control | None, route)``.

    Replaces the old sequential config-intent + route classify (two copilot
    cold-starts → one) — see ``Manager.classify_front_door`` /
    ``life.router.classify_front_door``. Fail-soft: no runner, no manager, or any
    error → ``(None, None, "complex")`` so the message flows through the normal
    task path unchanged (never swallow real work on a classify hiccup)."""
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None or not hasattr(mgr, "classify_front_door"):
            return None, None, "complex"
        accepts = accepts_keyword or _accepts_keyword
        if root_task_id is None or not accepts(
            mgr.classify_front_door,
            "root_task_id",
        ):
            decision = mgr.classify_front_door(text)
        else:
            decision = mgr.classify_front_door(
                text,
                root_task_id=root_task_id,
            )
        if isinstance(decision, tuple) and len(decision) == 3:
            intent, control, route = decision
        else:
            intent, route = decision
            control = None
        return (
            intent,
            control if control in {"abort", "no_dispatch"} else None,
            route if route in ("simple", "complex") else "complex",
        )
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        return None, None, "complex"


def _maybe_handle_config_intent(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    on_confirm: Any = None,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    accepts_keyword: Callable[[Any, str], bool] | None = None,
    apply_intent: Callable[..., bool] | None = None,
) -> bool:
    """Recognize + apply a natural-language change to one of Argus's OWN runtime
    knobs (a role's backend/model/effort, a budget cap, or the safe_mode/
    show_reasoning/telegram toggles) BEFORE it becomes work.

    One low-reasoning LLM call decides intent (Manager.classify_config_intent →
    life.router.classify_config_intent) — there is NO keyword/regex matching, so
    a request phrased any way is caught and a bare mention of a model/backend is
    not misread as a switch. Fail-soft: no runner, a classify error, or a NONE
    verdict all return False, and the text flows on to the normal chat/task path.
    Returns True iff it applied a change (and the turn is done).

    ``on_confirm(line)`` is an optional sink for confirmation lines."""
    runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
    mgr = getattr(runner, "manager", None) if runner is not None else None
    if mgr is None or not hasattr(mgr, "classify_config_intent"):
        return False
    try:
        accepts = accepts_keyword or _accepts_keyword
        if root_task_id is None or not accepts(
            mgr.classify_config_intent,
            "root_task_id",
        ):
            intent = mgr.classify_config_intent(text)
        else:
            intent = mgr.classify_config_intent(
                text,
                root_task_id=root_task_id,
            )
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        return False
    if intent is None:
        return False
    apply = apply_intent or _apply_config_intent
    return apply(mem, intent, chat_state, on_confirm=on_confirm)


def _apply_config_intent(
    mem: Any, intent: Any, chat_state: dict[str, Any], *, on_confirm: Any = None
) -> bool:
    """Apply a parsed ConfigIntent: set the env var(s), persist via knob_store
    (so a running daemon reads the switch immediately), confirm, and ground the
    Manager with a note. Returns True iff a change was applied."""
    from ..core.knob_store import write_persisted_knob

    theme = chat_state.get("theme")

    def _confirm(line: str) -> None:
        if callable(on_confirm):
            try:
                on_confirm(line)
            except Exception:  # noqa: BLE001 — a UI sink must never break the apply
                pass
        else:
            print(("  " + theme.cyan("argus") + theme.dim(" ↳ ") + line)
                  if theme is not None else line, flush=True)
        try:
            append_note(mem, line)
        except Exception:  # noqa: BLE001 — a grounding nicety, never fatal
            pass

    def _set(env_var: str, value: str) -> None:
        os.environ[env_var] = value
        write_persisted_knob(env_var, value)

    knob = intent.knob
    roles = list(intent.roles)

    if knob == "backend":
        from ..agent_cli.runner_backend import normalize_runner_backend

        value = normalize_runner_backend(intent.value)
        if roles:
            for role in roles:
                _set(_ROLE_BACKEND_ENVS[role], value)
            _confirm(f"Set {' / '.join(r.title() for r in roles)} CLI backend to {value}.")
        else:
            _set("ARGUS_SKILL_RUNNER_BACKEND", value)
            _confirm(f"Set Argus default CLI backend to {value} "
                     "(roles without their own backend follow).")
        chat_state.pop("manager_runner", None)
        return True

    if knob == "model":
        value = intent.value
        if roles:
            for env_var in {_ROLE_MODEL_ENVS[role] for role in roles}:
                _set(env_var, value)
            _confirm(f"Set {' / '.join(r.title() for r in roles)} model to {value}.")
        else:
            _set("ARGUS_SKILL_MODEL", value)
            _confirm(f"Set Argus default model to {value} "
                     "(roles without their own model follow).")
        chat_state.pop("manager_runner", None)
        return True

    if knob == "effort":
        value = intent.value.strip().lower()
        target = roles or list(_ROLE_EFFORT_ENVS)
        # A reasoning-effort knob is a silent no-op on a non-reasoning model —
        # reject with a grounded explanation instead of pretending to apply it.
        from ..cli.roles_status import resolve_role_config

        rcfg = {r: resolve_role_config(r, env=os.environ) for r in target}
        applicable = [r for r in target if rcfg[r].effort is not None]
        if not applicable:
            models = ", ".join(sorted({rcfg[r].model for r in target}))
            _confirm(f"Current model ({models}) is non-reasoning — reasoning effort "
                     "does not apply, so I left it unchanged.")
            return True
        for role in applicable:
            _set(_ROLE_EFFORT_ENVS[role], value)
        _confirm(f"Set {' / '.join(r.title() for r in applicable)} reasoning effort to {value}.")
        chat_state.pop("manager_runner", None)
        return True

    if knob in ("per_mission_cap", "daily_cap"):
        m = re.search(r"\d+(?:\.\d+)?", intent.value)
        if m is None:
            return False
        env_var = ("ARGUS_SKILL_PER_MISSION_CAP_USD" if knob == "per_mission_cap"
                   else "ARGUS_SKILL_DAILY_CAP_USD")
        _set(env_var, m.group(0))
        _confirm(f"Set {env_var} = {m.group(0)}.")
        return True

    quota_knobs = {
        "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
        "codex_daily_requests": "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
        "copilot_daily_requests": "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
        "copilot_daily_premium": "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
    }
    if knob in quota_knobs:
        m = re.search(r"\d+(?:\.\d+)?", intent.value)
        if m is None:
            return False
        env_var = quota_knobs[knob]
        from ..core.knobs import normalize_cockpit_knob_value

        value = normalize_cockpit_knob_value(env_var, m.group(0))
        _set(env_var, value)
        _confirm(f"Set {env_var} = {value}.")
        return True

    if knob in ("safe_mode", "show_reasoning", "telegram"):
        env_var = {
            "safe_mode": "ARGUS_SKILL_SAFE_MODE",
            "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
            "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
        }[knob]
        v = intent.value.strip().lower()
        on = v in ("on", "1", "true", "yes", "enable", "enabled",
                   "开", "打开", "开启", "启用")
        off = v in ("off", "0", "false", "no", "disable", "disabled",
                    "关", "关闭", "关掉", "停用", "禁用")
        if on == off:  # neither recognized, or contradictory — don't guess
            return False
        val = "1" if on else "0"
        _set(env_var, val)
        _confirm(f"Set {env_var} = {val} ({'on' if on else 'off'}).")
        return True

    return False

__all__ = [
    "_apply_config_intent",
    "_front_door_classify",
    "_maybe_handle_config_intent",
]
