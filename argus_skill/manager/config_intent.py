"""Natural-language configuration intent handling."""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from ..apps._life_actions import append_note
from .front_door import (
    _accepts_parameter,
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


def _invalidate_manager_runner(
    chat_state: dict[str, Any],
    *,
    backend: str | None = None,
) -> None:
    """Close stale warm clients after a Manager backend/model knob change."""
    runner = chat_state.pop("manager_runner", None)
    chat_state.pop("manager_runner_workdir", None)
    chat_state.pop("_manager_acp_prewarmed", None)
    chat_state["last_thread_id"] = None
    if backend:
        chat_state["backend"] = backend
    if runner is None:
        return
    try:
        for candidate in (
            getattr(runner, "_backend", None),
            getattr(runner, "manager_backend", None),
        ):
            close_acp = getattr(candidate, "close_acp_clients", None)
            if callable(close_acp):
                close_acp()
        reset = getattr(runner, "reset_chat_session", None)
        if callable(reset):
            reset()
    except Exception:  # noqa: BLE001 - config is already durable; recover next turn
        pass


def _front_door_classify(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    accepts_parameter: Callable[[Any, str], bool] | None = None,
    active_mission: bool = False,
) -> "tuple[Any, str | None, str]":
    """ONE merged LLM call for the Manager front-door: returns
    ``(ConfigIntent | None, control | None, route)``.

    TEAM lifetime is cached from the same call as ``bounded_increment``,
    ``bounded``, or ``standing``; final topology also considers Manager's
    direct/staged workflow decision. Classifier output is never treated as an
    operator-facing reply: every SELF message reaches the real Manager model.
    Fail-soft: no runner, no manager, or any error → ``(None, None, "complex")``
    so the message flows through the normal task path unchanged (never swallow
    real work on a classify hiccup)."""
    suggested_names: list[str] = []
    lifetime_decisions: list[str] = []
    self_mode_decisions: list[str] = []
    fast_replies: list[str] = []
    greeting_replies: list[str] = []
    steering_directives: list[str] = []
    authorization_decisions: list[tuple[str, ...]] = []
    classifier_failures: list[str] = []
    chat_state.pop("_frontdoor_lifetime", None)
    chat_state.pop("_frontdoor_self_mode", None)
    chat_state.pop("_frontdoor_fast_reply", None)
    chat_state.pop("_frontdoor_greeting_reply", None)
    chat_state.pop("_frontdoor_failure", None)
    chat_state.pop("_frontdoor_steering_directive", None)
    chat_state.pop("_frontdoor_authorization", None)
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None or not hasattr(mgr, "classify_front_door"):
            reason = str(chat_state.pop("manager_runner_error", "") or "").strip()
            chat_state["_frontdoor_failure"] = (
                f"classifier unavailable: {reason}" if reason
                else "classifier unavailable"
            )
            return None, None, "complex"
        accepts = accepts_parameter or _accepts_parameter
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
        if accepts(mgr.classify_front_door, "self_mode_sink"):
            kwargs["self_mode_sink"] = self_mode_decisions.append
        if accepts(mgr.classify_front_door, "reply_sink"):
            kwargs["reply_sink"] = fast_replies.append
        if accepts(mgr.classify_front_door, "greeting_sink"):
            kwargs["greeting_sink"] = greeting_replies.append
        if accepts(mgr.classify_front_door, "steering_sink"):
            kwargs["steering_sink"] = steering_directives.append
        if accepts(mgr.classify_front_door, "authorization_sink"):
            kwargs["authorization_sink"] = authorization_decisions.append
        if accepts(mgr.classify_front_door, "active_mission"):
            kwargs["active_mission"] = bool(active_mission)
        if accepts(mgr.classify_front_door, "failure_sink"):
            kwargs["failure_sink"] = classifier_failures.append
        model_text = str(
            chat_state.get("_frontdoor_contextual_text") or text
        )
        decision = mgr.classify_front_door(model_text, **kwargs)
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
        if classifier_failures:
            chat_state["_frontdoor_failure"] = classifier_failures[-1]
        if normalized_route == "simple":
            existing_thread = bool(chat_state.get("last_thread_id"))
            self_mode = next(
                (
                    str(value).strip().lower()
                    for value in self_mode_decisions
                    if str(value).strip().lower() in {
                        "reply",
                        "inspect",
                        "micro",
                        "implement",
                        "debug",
                        "review",
                        "synthesize",
                    }
                ),
                "inspect",
            )
            if existing_thread and self_mode == "reply":
                self_mode = "inspect"
            chat_state["_frontdoor_self_mode"] = self_mode
            fast_reply = next(
                (str(value).strip() for value in fast_replies if str(value).strip()),
                "",
            )
            if not existing_thread and self_mode == "reply" and fast_reply:
                chat_state["_frontdoor_fast_reply"] = fast_reply
        lifetime = next(
            (
                str(value).strip().lower()
                for value in lifetime_decisions
                if str(value).strip().lower() in {
                    "bounded_increment", "bounded", "standing",
                }
            ),
            "",
        )
        if normalized_route == "complex":
            lifetime = lifetime or "bounded"
            chat_state["_frontdoor_lifetime"] = lifetime
        elif control == "steer" and lifetime:
            chat_state["_frontdoor_lifetime"] = lifetime
        elif intent is None and control not in {"abort", "pause", "no_dispatch", "steer"}:
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
            control if control in {"abort", "pause", "no_dispatch", "steer"} else None,
            normalized_route,
        )
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        chat_state["_frontdoor_failure"] = "classifier failed"
        return None, None, "complex"
    finally:
        named = ""
        if not greeting_replies:
            named = _maybe_name_session(
                chat_state,
                text,
                suggested_name=next(
                    (name for name in suggested_names if str(name).strip()),
                    "",
                ),
            )
        if named and locals().get("normalized_route") == "simple":
            chat_state["_provisional_session_name"] = named


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

    intents = list(intent) if isinstance(intent, (tuple, list)) else []
    if intents:
        from ..core.knobs import normalize_cockpit_knob_value

        updates: dict[str, str] = {}
        confirmations: list[str] = []
        manager_backend: str | None = None
        quota_knobs = {
            "global_daily_cap": "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
            "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
            "codex_daily_requests": "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
            "copilot_daily_requests": "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
            "copilot_daily_premium": "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
        }
        toggle_knobs = {
            "safe_mode": "ARGUS_SKILL_SAFE_MODE",
            "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
            "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
        }
        try:
            for entry in intents:
                knob = str(entry.knob)
                roles = list(entry.roles)
                if knob == "backend":
                    names = (
                        [_ROLE_BACKEND_ENVS[role] for role in roles]
                        if roles else [
                            "ARGUS_SKILL_RUNNER_BACKEND",
                            *_ROLE_BACKEND_ENVS.values(),
                        ]
                    )
                    value = normalize_cockpit_knob_value(names[0], entry.value)
                    updates.update({name: value for name in names})
                    if not roles or "manager" in roles:
                        manager_backend = value
                    confirmations.append(
                        f"Set {' / '.join(r.title() for r in roles) if roles else 'all Argus roles'} "
                        f"CLI backend to {value}."
                    )
                elif knob == "model":
                    names = (
                        [_ROLE_MODEL_ENVS[role] for role in roles]
                        if roles else ["ARGUS_SKILL_MODEL", *_ROLE_MODEL_ENVS.values()]
                    )
                    value = normalize_cockpit_knob_value(names[0], entry.value)
                    updates.update({name: value for name in names})
                    confirmations.append(
                        f"Set {' / '.join(r.title() for r in roles) if roles else 'all Argus roles'} "
                        f"model to {value}."
                    )
                elif knob == "effort":
                    target = roles or list(_ROLE_EFFORT_ENVS)
                    value = normalize_cockpit_knob_value(
                        _ROLE_EFFORT_ENVS[target[0]], entry.value
                    )
                    updates.update({_ROLE_EFFORT_ENVS[role]: value for role in target})
                    confirmations.append(
                        f"Set {' / '.join(r.title() for r in target)} reasoning effort "
                        f"to {value}."
                    )
                elif knob in quota_knobs:
                    match = re.search(r"\d+(?:\.\d+)?", str(entry.value))
                    if match is None:
                        raise ValueError(f"{knob} has no numeric value")
                    env_var = quota_knobs[knob]
                    value = normalize_cockpit_knob_value(env_var, match.group(0))
                    updates[env_var] = value
                    confirmations.append(f"Set {env_var} = {value}.")
                elif knob in toggle_knobs:
                    env_var = toggle_knobs[knob]
                    value = normalize_cockpit_knob_value(env_var, entry.value)
                    updates[env_var] = value
                    confirmations.append(
                        f"Set {env_var} = {value} ({'on' if value == '1' else 'off'})."
                    )
                else:
                    raise ValueError(f"unsupported config knob: {knob}")
        except (KeyError, ValueError) as exc:
            _confirm(f"Could not apply configuration; nothing changed: {exc}")
            return True
        if not _set(updates):
            return True
        for line in confirmations:
            _confirm(line)
        _invalidate_manager_runner(chat_state, backend=manager_backend)
        return True

    knob = intent.knob
    roles = list(intent.roles)

    if knob == "backend":
        from ..core.knobs import normalize_cockpit_knob_value

        names = [_ROLE_BACKEND_ENVS[r] for r in roles] if roles else [
            "ARGUS_SKILL_RUNNER_BACKEND",
            *_ROLE_BACKEND_ENVS.values(),
        ]
        try:
            value = normalize_cockpit_knob_value(names[0], intent.value)
        except ValueError as exc:
            _confirm(f"Could not apply backend; nothing changed: {exc}")
            return True
        if roles:
            if not _set({_ROLE_BACKEND_ENVS[role]: value for role in roles}):
                return True
            _confirm(f"Set {' / '.join(r.title() for r in roles)} CLI backend to {value}.")
        else:
            if not _set({name: value for name in names}):
                return True
            _confirm(f"Set all Argus roles' CLI backend to {value}.")
        _invalidate_manager_runner(
            chat_state,
            backend=(value if not roles or "manager" in roles else None),
        )
        return True

    if knob == "model":
        from ..core.knobs import normalize_cockpit_knob_value

        names = [_ROLE_MODEL_ENVS[r] for r in roles] if roles else [
            "ARGUS_SKILL_MODEL",
            *_ROLE_MODEL_ENVS.values(),
        ]
        try:
            values = {n: normalize_cockpit_knob_value(n, intent.value) for n in names}
        except ValueError:
            # An unparsed instruction must never reach _set(): it would also land
            # in os.environ, be inherited by every child process, and outrank the
            # knob store for the rest of this process's life.
            _confirm(
                f"“{intent.value}” is not a model id, so I left the model unchanged. "
                "Name the model on its own, e.g. “engineer change to claude-opus-5”."
            )
            return True
        if not _set(values):
            return True
        model_value = values[names[0]]
        if roles:
            _confirm(f"Set {' / '.join(r.title() for r in roles)} model to {model_value}.")
        else:
            _confirm(f"Set all Argus roles' model to {model_value}.")
        _invalidate_manager_runner(chat_state)
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
        _invalidate_manager_runner(chat_state)
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

        normalized_value = normalize_cockpit_knob_value(env_var, m.group(0))
        if not _set({env_var: normalized_value}):
            return True
        _confirm(f"Set {env_var} = {normalized_value}.")
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
