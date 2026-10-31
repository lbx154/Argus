"""Natural-language configuration intent handling."""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from ..apps._life_actions import append_note
from .front_door import _maybe_name_session

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
    mem: Any,
    chat_state: dict[str, Any],
    *,
    backend: str | None = None,
) -> None:
    """Drop the Manager runner after a Manager backend/model knob change."""
    runner = chat_state.pop("manager_runner", None)
    chat_state.pop("manager_runner_workdir", None)
    from ._session_ops import reset_manager_session

    reset_manager_session(mem.project_root)
    if backend:
        chat_state["backend"] = backend
    if runner is None:
        return
    try:
        reset = getattr(runner, "reset_chat_session", None)
        if callable(reset):
            reset()
    except Exception:  # noqa: BLE001 - config is already durable; recover next turn
        pass


def _save_operator_available(available: bool) -> None:
    """Save the one "can Argus ask me now" switch the operator just set.

    Running workers read it from config.json, so it is not pinned into the
    environment they inherit.
    """
    from ..core.autonomy import OPERATOR_AVAILABLE_KNOB
    from ..core.knob_store import write_persisted_knob

    if write_persisted_knob(OPERATOR_AVAILABLE_KNOB, "1" if available else "0"):
        os.environ.pop(OPERATOR_AVAILABLE_KNOB, None)


def record_manager_handoff(
    mem: Any, text: str, chat_state: dict[str, Any], handoff: Any, *, root_task_id: str, credential_imported: bool,
) -> None:
    """Keep what the Manager's call says beyond its reply: the question policy, the topic, a lasting rule."""
    if "operator_question_policy" in handoff.payload:
        _save_operator_available(handoff.get("operator_question_policy") == "allow")
    if handoff.get("name"):
        _maybe_name_session(chat_state, text, suggested_name=handoff.get("name"), replacing=True)
    intake = handoff.get("intake")
    # A message carrying a credential was already stored by the vault import.
    if not intake or credential_imported:
        return
    from ..core.operator_context import IntakeDecision, persist_intake_decision

    running = [item for item in mem.backlog.active() if item.status == "running"]
    mission_id = str(max(running, key=lambda item: float(item.started_ts or 0)).id) if running else root_task_id
    recorded = persist_intake_decision(
        mem.project_root,
        text,
        IntakeDecision(
            kind=intake["type"],
            scope=intake["scope"],
            applies_to_roles=tuple(dict.fromkeys(intake.get("roles", ()))) or "all",
            preference_kind=intake.get("preference_kind", "workflow"),
            preference_value=intake.get("preference_value", ""),
            target_revision=intake.get("revoke_revision", 0),
        ),
        source="manager",
        mission_id=mission_id,
        global_root=mem.global_root,
    )
    if recorded is not None and intake["type"] == "standing_directive":
        chat_state["_standing_notice"] = str(recorded.text or text).strip()


_MODEL_SENTINELS = frozenset({"auto", "inherit", "default"})


def _target_backends(entries: list[Any]) -> list[str]:
    """The backend this model change will run on (every role shares one).

    A backend change in the same message wins over the current backend, so
    ``SET backend - copilot; SET model ALL x`` vets ``x`` against Copilot.
    """
    from ..core.knobs import resolve_role_backend

    backend = next(
        (str(other.value) for other in entries if str(getattr(other, "knob", "")) == "backend"),
        "",
    )
    if not backend:
        try:
            backend = resolve_role_backend("", default="codex")
        except Exception:  # noqa: BLE001 - an unreadable store never blocks a change
            backend = ""
    backend = backend.strip().lower()
    return [backend] if backend else []


def _vet_model_value(
    value: str,
    backends: list[str],
    model_catalog: Callable[[str], Any] | None,
    chat_state: dict[str, Any],
) -> tuple[str, str | None]:
    """Check a requested model id against each target backend's own model list.

    Returns ``(value_to_write, refusal)``. A refusal names the backend, lists
    its closest models and asks the operator to confirm; repeating the same id
    on the next request confirms it (a list can lag the provider). A backend
    with no list available does not block the change.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() in _MODEL_SENTINELS or not callable(model_catalog):
        return raw, None
    from ..core.knobs import normalize_cockpit_knob_value

    try:
        raw = normalize_cockpit_knob_value("ARGUS_SKILL_MODEL", raw)
    except ValueError:
        return raw, None  # not a model id at all: the apply path explains that
    canonical = raw
    for backend in backends:
        try:
            catalog = [str(m).strip() for m in (model_catalog(backend) or ()) if str(m).strip()]
        except Exception:  # noqa: BLE001 - an unreadable list never blocks a change
            catalog = []
        if not catalog:
            continue
        by_lower = {m.lower(): m for m in catalog}
        if raw.lower() in by_lower:
            canonical = by_lower[raw.lower()]
            continue
        if chat_state.get("_pending_model_confirm") == raw.lower():
            continue
        chat_state["_pending_model_confirm"] = raw.lower()
        import difflib

        close = difflib.get_close_matches(raw.lower(), list(by_lower), n=5, cutoff=0.3)
        candidates = [by_lower[m] for m in close] or catalog[:5]
        return raw, (
            f"“{raw}” is not in the {backend} backend's model list, so I left the "
            f"model unchanged. Closest available: {', '.join(candidates)}. Reply "
            f"with one of these, or ask for “{raw}” again to use it anyway."
        )
    if chat_state.get("_pending_model_confirm") == raw.lower():
        chat_state.pop("_pending_model_confirm", None)
    return canonical, None



_CONFIRM_ZH: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^Set all Argus roles' CLI backend to (.+)\.$"), r"已把所有角色的后端改为 \1。"),
    (re.compile(r"^Set all Argus roles' model to (.+)\.$"), r"已把所有角色的模型改为 \1，下一次调用起生效。"),
    (re.compile(r"^Set (.+) CLI backend to (.+)\.$"), r"已把 \1 的后端改为 \2。"),
    (re.compile(r"^Set (.+) model to (.+)\.$"), r"已把 \1 的模型改为 \2，下一次调用起生效。"),
    (re.compile(r"^Set (.+) reasoning effort to (.+)\.$"), r"已把 \1 的推理强度改为 \2。"),
    (re.compile(r"^Set (\S+) = (.+)\.$"), r"已设置 \1 = \2。"),
    (re.compile(r"^Could not persist configuration; nothing changed\.$"), "设置没能保存，没有任何改动。"),
    (re.compile(r"^Could not apply (?:configuration|backend); nothing changed: (.+)$"), r"设置没能生效，没有任何改动：\1"),
)


def localize_config_confirmation(line: str, *, chinese: bool) -> str:
    """The operator's language for a confirmation the apply path wrote in English.

    Lines without a known shape (refusals already phrased for the operator,
    model ids) pass through unchanged.
    """
    if not chinese:
        return line
    for pattern, template in _CONFIRM_ZH:
        if pattern.match(line):
            return pattern.sub(template, line)
    return line

def _apply_config_intent(
    mem: Any,
    intents: Any,
    chat_state: dict[str, Any],
    *,
    on_confirm: Any = None,
    model_catalog: Callable[[str], Any] | None = None,
) -> bool:
    """Apply parsed ConfigIntents and persist them to their authoritative file.

    Backend/model/effort and the host-global budget use knob_store.
    ``model_catalog(backend)`` returns that backend's model ids; a model id
    outside it is not written until the operator confirms it.
    """
    from ..core.desktop_selection import MODEL_KEYS, SELECTED_BACKEND, profile_key
    from ..core.knob_store import write_persisted_knobs

    desktop_backend = os.environ.get(SELECTED_BACKEND, "")
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
            # Tagged so the feed can skip it: the chat reply already says it.
            append_note(mem, line, tags=["config"])
        except Exception:  # noqa: BLE001 — a grounding nicety, never fatal
            pass

    def _set(values: dict[str, str]) -> bool:
        persisted = {
            profile_key(desktop_backend, name) if desktop_backend and name in MODEL_KEYS else name: value
            for name, value in values.items()
            if not (desktop_backend and name == "ARGUS_SKILL_RUNNER_BACKEND")
        }
        if not write_persisted_knobs(persisted):
            _confirm("Could not persist configuration; nothing changed.")
            return False
        os.environ.update(values)
        return True

    entries = list(intents)
    if desktop_backend and any(
        entry.knob == "backend" and str(entry.value).strip().lower() != desktop_backend
        for entry in entries
    ):
        _confirm("请在桌面设置中切换后端，以同时更新 CLI 路径和运行上下文；当前配置未更改。")
        return True
    vetted: list[Any] = []
    refusals: list[str] = []
    for entry in entries:
        if str(getattr(entry, "knob", "")) == "model":
            model_value, refusal = _vet_model_value(
                entry.value, _target_backends(entries), model_catalog, chat_state
            )
            if refusal is not None:
                refusals.append(refusal)  # the other settings still apply
                continue
            if model_value != entry.value:
                entry = type(entry)(knob=entry.knob, roles=entry.roles, value=model_value)
        vetted.append(entry)
    for refusal in refusals:
        _confirm(refusal)
    if not vetted:
        return True
    from ..core.knobs import normalize_cockpit_knob_value

    updates: dict[str, str] = {}
    confirmations: list[str] = []
    manager_backend: str | None = None
    quota_knobs = {
        "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    }
    toggle_knobs = {
        "safe_mode": "ARGUS_SKILL_SAFE_MODE",
        "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
        "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
    }
    try:
        for entry in vetted:
            knob = str(entry.knob)
            roles = list(entry.roles)
            if knob == "backend":
                value = normalize_cockpit_knob_value(
                    "ARGUS_SKILL_RUNNER_BACKEND", entry.value
                )
                updates["ARGUS_SKILL_RUNNER_BACKEND"] = value
                manager_backend = value
                confirmations.append(f"Set all Argus roles' CLI backend to {value}.")
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
    _invalidate_manager_runner(mem, chat_state, backend=manager_backend)
    return True

__all__ = [
    "_apply_config_intent",
    "record_manager_handoff",
]
