"""Natural-language config intent and front-door role overlay."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from ..apps._life_actions import append_note
from .front_door import _accepts_keyword, _ensure_manager_runner
from .repl_ops import _ROLE_BACKEND_ENVS, _ROLE_EFFORT_ENVS, _ROLE_MODEL_ENVS


def _render_live_role_overlay(
    life_dir: Path | str, theme: Any, *, active_role: str, label: str,
) -> str:
    """A truthful "roles · activity" snapshot for the SELF quick-reply spinner
    (the ``LiveStatus`` block in ``_free_text_cmd``), marking ``active_role``
    active directly from the SAME phase signal the spinner itself is driven
    by — NOT from ``events.jsonl``.

    The SELF quick-reply path (Manager answers a simple chat turn itself, no
    Planner/Engineer/Reviewer hand-off) deliberately never journals its
    progress to ``events.jsonl`` (see ``_Capture``/``_simple_quick_reply`` —
    avoids mission-log noise for a one-line "你好"). That means
    ``role_activity()`` — the ONLY data source the pre-turn panel
    (``read_message_prompt_toolkit`` / ``read_message_with_live_cockpit``)
    reads — has no way to know this turn is happening at all, so that panel
    keeps showing every role "idle" for the entire live turn: not just stale,
    a direct, visible self-contradiction with the correctly-labeled spinner
    right below it (live-confirmed: "Manager idle" shown while "Manager ·
    SELF: ... 6s" spun beneath it, prompting "你不要只做摆设" — don't just
    make this decorative). This builds a SEPARATE, correct snapshot for that
    narrow window by overriding just one role's entry in an otherwise-real
    ``role_activity()`` read (so Planner/Engineer/Reviewer still show their
    true last-known state, not a blanket fake "idle")."""
    from ..cli.roles_status import (
        ROLES,
        RoleActivity,
        format_roles_panel,
        resolve_all_roles,
        role_activity,
    )

    try:
        activities = dict(role_activity(life_dir))
    except Exception:  # noqa: BLE001
        activities = {}
    for r in ROLES:
        activities.setdefault(
            r, RoleActivity(role=r, active=False, label="idle", status="idle", age_s=None),
        )
    role = (active_role or "").strip().lower()
    if role in activities:
        activities[role] = RoleActivity(
            role=role, active=True, label=label, status="running", age_s=0.0,
        )
    configs = resolve_all_roles(env=os.environ)
    width = theme.live_width() if theme is not None and hasattr(theme, "live_width") else 80
    return format_roles_panel(theme, configs, activities, width=width)


def _front_door_classify(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    accepts_keyword: Callable[[Any, str], bool] | None = None,
) -> "tuple[Any, str]":
    """ONE merged LLM call for the cockpit front-door: returns
    ``(ConfigIntent | None, route)`` where route is ``"simple"``/``"complex"``.

    Replaces the old sequential config-intent + route classify (two copilot
    cold-starts → one) — see ``Manager.classify_front_door`` /
    ``life.router.classify_front_door``. Fail-soft: no runner, no manager, or any
    error → ``(None, "complex")`` so the message flows through the normal
    task path unchanged (never swallow real work on a classify hiccup)."""
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None or not hasattr(mgr, "classify_front_door"):
            return None, "complex"
        accepts = accepts_keyword or _accepts_keyword
        if root_task_id is None or not accepts(
            mgr.classify_front_door,
            "root_task_id",
        ):
            intent, route = mgr.classify_front_door(text)
        else:
            intent, route = mgr.classify_front_door(
                text,
                root_task_id=root_task_id,
            )
        return intent, (route if route in ("simple", "complex") else "complex")
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        return None, "complex"


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

    ``on_confirm(line)`` — optional sink for the confirmation line(s). When given
    (the web/TUI cockpit front-door), the confirmation is handed to it INSTEAD of
    printed to stdout, so a non-REPL surface can show it as a chat reply. Default
    ``None`` keeps the line-REPL's print behaviour byte-for-byte."""
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
                on_confirm(line)  # cockpit: surface as a chat reply, not stdout
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
    "_render_live_role_overlay",
]
