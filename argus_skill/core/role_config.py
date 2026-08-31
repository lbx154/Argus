"""Canonical per-role backend, model, and reasoning-effort resolution.

This is runtime configuration shared by Manager prompts, WebAPI snapshots,
and terminal surfaces; it is not CLI presentation logic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

# Public role order (front-to-back through a mission's lifecycle).
ROLES: tuple[str, ...] = ("manager", "planner", "engineer", "reviewer")

_BACKEND_LABEL = {
    "codex": "Codex",
    "claude": "Claude Code",
    "copilot": "Copilot",
    "cursor": "Cursor CLI",
    "opencode": "OpenCode",
    "pi": "Pi",
    "grok": "Grok Build",
    "qoder": "Qoder",
    "dsh": "DeepSeek Harness",
    "memory": "memory",
}

# Which capability-vault route and explicit model override each role owns.
_ROLE_ROUTE = {
    "manager": "manager",
    "planner": "planner",
    "engineer": "engineer",
    "reviewer": "reviewer",
    "curator": "curator",
}
_ROLE_MODEL_ENV = {
    "manager": "ARGUS_SKILL_MANAGER_MODEL",
    "planner": "ARGUS_SKILL_PLAN_MODEL",
    "engineer": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer": "ARGUS_SKILL_REVIEWER_MODEL",
    "curator": "ARGUS_SKILL_CURATOR_MODEL",
}
_ROLE_EFFORT_ENV = {
    "manager": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "planner": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "engineer": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "curator": "ARGUS_SKILL_CURATOR_REASONING_EFFORT",
}
_ROLE_EFFORT_DEFAULT = {
    "manager": "high",
    "planner": "high",
    "engineer": "xhigh",
    "reviewer": "high",
    "curator": "high",
}

_ROLE_DESC = {
    "manager": "front door · triages chat/tasks, approves skills",
    "planner": "queues new work when backlog is empty, final-gate routing",
    "engineer": "L1 execution · writes code / runs commands",
    "reviewer": "L2 acceptance · done / continue / blocked",
    "curator": "skill-pool upkeep · distill / write-back",
}


# ── config resolution ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleConfig:
    role: str
    backend: str  # codex / claude / copilot / cursor / opencode / pi / grok / qoder / dsh / memory
    backend_label: str  # Codex / Claude Code / Copilot / Cursor CLI / OpenCode / Pi / Grok
    model: str
    effort: str | None  # None → not a reasoning model (effort N/A)
    desc: str


def _normalize_backend(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if raw == "memory":
        return "memory"
    try:
        from ..agent_cli.runner_backend import normalize_runner_backend

        return normalize_runner_backend(raw or None)
    except Exception:  # noqa: BLE001 — never fail the display
        return raw or "codex"


def _resolve_backend(role: str, env: Mapping[str, str]) -> str:
    from .knobs import resolve_role_backend

    # default="codex": read-only display resolver. `/roles` and the cockpit
    # role panel must still render on a host where nothing is configured, and
    # codex is what the execution paths that CAN assume a backend also assume,
    # so the panel keeps matching what a run would really do. Provenance for
    # the daemon's own roles is carried by life.<role>.backend_resolved.
    requested = resolve_role_backend(role, env=env, default="codex")
    normalized = _normalize_backend(requested)
    if normalized == "memory":
        return normalized
    from ..agent_cli.runner_backend import resolve_available_runner

    configured = (
        str(env.get(f"ARGUS_SKILL_{role.upper()}_RUNNER_BIN", "") or "").strip()
        or str(env.get("ARGUS_SKILL_RUNNER_BIN", "") or "").strip()
    )
    effective, _runner_bin = resolve_available_runner(
        requested,
        configured or None,
    )
    return effective


def runner_backend_label(env: Mapping[str, str] | None = None) -> str:
    """Display label of the *current* runner backend, resolved from
    ``ARGUS_SKILL_RUNNER_BACKEND`` →
    ``ARGUS_SKILL_LIFE_BACKEND`` → a persisted ``/backend`` switch → ``codex``.

    Used by user-facing copy (status phrases, the Manager chat identity) so the
    single-worker SELF path names the backend the operator actually configured
    instead of a hardcoded "Codex". Fail-soft to "Codex" so a resolution hiccup
    never breaks the line it decorates.
    """
    env = env if env is not None else os.environ
    try:
        backend = _resolve_backend("manager", env)
        return _BACKEND_LABEL.get(backend, backend or "Codex")
    except Exception:  # noqa: BLE001 — display copy must never crash
        return "Codex"


def _resolve_model(role: str, env: Mapping[str, str]) -> str:
    from .knobs import resolve_role_model

    try:
        return resolve_role_model(
            _ROLE_ROUTE.get(role, "text"),
            role_env=_ROLE_MODEL_ENV.get(role, ""),
            env=env,
        )
    except Exception:  # noqa: BLE001
        return "gpt-5.5"


def is_reasoning_model(model: str) -> bool:
    """True when ``model`` supports a reasoning-effort knob (gpt-5.x / o-series).

    A non-reasoning model (e.g. a plain chat model) has no effort setting, so
    the display shows ``—`` rather than a misleading value.
    """
    m = (model or "").strip().lower()
    if not m:
        return False
    if m.startswith("gpt-5") or m.startswith("gpt5"):
        return True
    if re.match(r"^o[1-9]", m):  # o1 / o3 / o4 …
        return True
    return "reason" in m


def _resolve_effort(role: str, model: str, env: Mapping[str, str]) -> str | None:
    if not is_reasoning_model(model):
        return None
    from .knobs import resolve_role_reasoning_effort

    role_env = _ROLE_EFFORT_ENV.get(role, "")
    default_effort = _ROLE_EFFORT_DEFAULT.get(role, "xhigh")
    if role == "manager":
        # Manager triage reuses the engineer effort; Manager._core also
        # checks its own knob first. Check manager's own knob (env, then a persisted
        # switch) before falling back to engineer's (same two layers), so an
        # explicit manager-specific switch on EITHER layer still wins.
        val = resolve_role_reasoning_effort(role_env, env=env, default="")
        if val:
            return val
        return resolve_role_reasoning_effort(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
            env=env,
            default=default_effort,
        )
    return resolve_role_reasoning_effort(role_env, env=env, default=default_effort)


def resolve_role_config(role: str, *, env: Mapping[str, str] | None = None) -> RoleConfig:
    env = env if env is not None else os.environ
    backend = _resolve_backend(role, env)
    model = _resolve_model(role, env)
    effort = _resolve_effort(role, model, env)
    return RoleConfig(
        role=role,
        backend=backend,
        backend_label=_BACKEND_LABEL.get(backend, backend or "codex"),
        model=model,
        effort=effort,
        desc=_ROLE_DESC.get(role, ""),
    )


def resolve_all_roles(
    roles: Sequence[str] = ROLES, *, env: Mapping[str, str] | None = None
) -> list[RoleConfig]:
    return [resolve_role_config(r, env=env) for r in roles]


__all__ = [
    "ROLES",
    "RoleConfig",
    "is_reasoning_model",
    "resolve_all_roles",
    "resolve_role_config",
    "runner_backend_label",
]
