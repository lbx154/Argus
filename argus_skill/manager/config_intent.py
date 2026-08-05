"""Natural-language configuration intent handling."""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from ..apps._life_actions import append_note
from .front_door import (
    _accepts_keyword,
    _ensure_manager_runner,
    _maybe_name_session,
)

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
    "manager": "ARGUS_SKILL_MANAGER_MODEL",
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
    active_mission: bool = False,
) -> "tuple[Any, str | None, str]":
    """ONE merged LLM call for the Manager front-door: returns
    ``(ConfigIntent | None, control | None, route)``.

    TEAM lifetime is cached from the same call as ``bounded`` or ``standing``;
    no second classifier call is needed. Classifier output is never treated as
    an operator-facing reply: every SELF message reaches
    the real Manager model.
    Fail-soft: no runner, no manager, or any error → ``(None, None, "complex")``
    so the message flows through the normal task path unchanged (never swallow
    real work on a classify hiccup)."""
    suggested_names: list[str] = []
    lifetime_decisions: list[str] = []
    greeting_replies: list[str] = []
    steering_directives: list[str] = []
    authorization_decisions: list[tuple[str, ...]] = []
    chat_state.pop("_frontdoor_lifetime", None)
    chat_state.pop("_frontdoor_greeting_reply", None)
    chat_state.pop("_frontdoor_failure", None)
    chat_state.pop("_frontdoor_steering_directive", None)
    chat_state.pop("_frontdoor_authorization", None)
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None or not hasattr(mgr, "classify_front_door"):
            chat_state["_frontdoor_failure"] = "classifier unavailable"
            return None, None, "complex"
        accepts = accepts_keyword or _accepts_keyword
        kwargs: dict[str, Any] = {}
        if root_task_id is not None and accepts(
            mgr.classify_front_door,
            "root_task_id",
        ):
            kwargs["root_task_id"] = root_task_id
        if accepts(mgr.classify_front_door, "name_sink"):
            kwargs["name_sink"] = suggested_names.append
        if accepts(mgr.classify_front_door, "lifetime_sink"):
            kwargs["lifetime_sink"] = lifetime_decisions.append
        if accepts(mgr.classify_front_door, "greeting_sink"):
            kwargs["greeting_sink"] = greeting_replies.append
        if accepts(mgr.classify_front_door, "steering_sink"):
            kwargs["steering_sink"] = steering_directives.append
        if accepts(mgr.classify_front_door, "authorization_sink"):
            kwargs["authorization_sink"] = authorization_decisions.append
        if accepts(mgr.classify_front_door, "active_mission"):
            kwargs["active_mission"] = bool(active_mission)
        decision = mgr.classify_front_door(text, **kwargs)
        if isinstance(decision, tuple) and len(decision) == 4:
            intent, control, route, suggested_name = decision
            if suggested_name:
                suggested_names.append(str(suggested_name))
        elif isinstance(decision, tuple) and len(decision) == 3:
            intent, control, route = decision
        else:
            intent, route = decision
            control = None
        normalized_route = route if route in ("simple", "complex") else "complex"
        if normalized_route == "complex":
            lifetime = next(
                (
                    str(value).strip().lower()
                    for value in lifetime_decisions
                    if str(value).strip().lower() in {"bounded", "standing"}
                ),
                "standing",
            )
            chat_state["_frontdoor_lifetime"] = lifetime
        elif intent is None and control not in {"abort", "no_dispatch", "steer"}:
            greeting_reply = next(
                (
                    str(value).strip()
                    for value in greeting_replies
                    if str(value).strip()
                ),
                "",
            )
            if greeting_reply:
                chat_state["_frontdoor_greeting_reply"] = greeting_reply
        if control == "steer":
            directive = next(
                (
                    str(value).strip()
                    for value in steering_directives
                    if str(value).strip()
                ),
                "",
            )
            if directive:
                chat_state["_frontdoor_steering_directive"] = directive
        if authorization_decisions:
            actions = [
                str(value).strip().lower()
                for value in authorization_decisions[0]
                if str(value).strip()
            ]
            if actions:
                chat_state["_frontdoor_authorization"] = actions
        return (
            intent,
            control if control in {"abort", "no_dispatch", "steer"} else None,
            normalized_route,
        )
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        chat_state["_frontdoor_failure"] = "classifier failed"
        return None, None, "complex"
    finally:
        _maybe_name_session(
            chat_state,
            text,
            suggested_name=next(
                (name for name in suggested_names if str(name).strip()),
                "",
            ),
        )


def _apply_config_intent(
    mem: Any, intent: Any, chat_state: dict[str, Any], *, on_confirm: Any = None
) -> bool:
    """Apply a parsed ConfigIntent and persist it to its authoritative file.

    Backend/model/effort and the host-global budget use knob_store.
    """
    from ..core.knob_store import write_persisted_knobs

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

    def _set(values: dict[str, str]) -> bool:
        if not write_persisted_knobs(values):
            _confirm("Could not persist configuration; nothing changed.")
            return False
        os.environ.update(values)
        return True

    knob = intent.knob
    roles = list(intent.roles)

    if knob == "backend":
        from ..agent_cli.runner_backend import normalize_runner_backend

        value = normalize_runner_backend(intent.value)
        if roles:
            if not _set({_ROLE_BACKEND_ENVS[role]: value for role in roles}):
                return True
            _confirm(f"Set {' / '.join(r.title() for r in roles)} CLI backend to {value}.")
        else:
            if not _set({"ARGUS_SKILL_RUNNER_BACKEND": value}):
                return True
            _confirm(f"Set Argus default CLI backend to {value} "
                     "(roles without their own backend follow).")
        chat_state.pop("manager_runner", None)
        return True

    if knob == "model":
        value = intent.value
        if roles:
            if not _set({_ROLE_MODEL_ENVS[role]: value for role in roles}):
                return True
            _confirm(f"Set {' / '.join(r.title() for r in roles)} model to {value}.")
        else:
            if not _set({"ARGUS_SKILL_MODEL": value}):
                return True
            _confirm(f"Set Argus default model to {value} "
                     "(roles without their own model follow).")
        chat_state.pop("manager_runner", None)
        return True

    if knob == "effort":
        value = intent.value.strip().lower()
        target = roles or list(_ROLE_EFFORT_ENVS)
        # A reasoning-effort knob is a silent no-op on a non-reasoning model —
        # reject with a grounded explanation instead of pretending to apply it.
        from ..core.role_config import resolve_role_config

        rcfg = {r: resolve_role_config(r, env=os.environ) for r in target}
        applicable = [r for r in target if rcfg[r].effort is not None]
        if not applicable:
            models = ", ".join(sorted({rcfg[r].model for r in target}))
            _confirm(f"Current model ({models}) is non-reasoning — reasoning effort "
                     "does not apply, so I left it unchanged.")
            return True
        if not _set({_ROLE_EFFORT_ENVS[role]: value for role in applicable}):
            return True
        _confirm(f"Set {' / '.join(r.title() for r in applicable)} reasoning effort to {value}.")
        chat_state.pop("manager_runner", None)
        return True

    # The host-global cap and provider quotas share the knob_store write path.
    quota_knobs = {
        "global_daily_cap": "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
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
        if not _set({env_var: value}):
            return True
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
        if not _set({env_var: val}):
            return True
        _confirm(f"Set {env_var} = {val} ({'on' if on else 'off'}).")
        return True

    return False

__all__ = [
    "_apply_config_intent",
    "_front_door_classify",
]
