"""Keep a native desktop CLI selection separate from deployment defaults.

Only activated by the native startup handshake. The selected CLI owns its
provider and authentication unless this backend has an explicit model pin.
No account material is stored here, and legacy shared settings stay intact.
"""
from __future__ import annotations

from typing import Mapping, MutableMapping

SELECTED_BACKEND = "ARGUS_DESKTOP_SELECTED_BACKEND"
MODEL_KEYS = (
    "ARGUS_SKILL_MODEL", "ARGUS_SKILL_MANAGER_MODEL", "ARGUS_SKILL_PLAN_MODEL",
    "ARGUS_SKILL_ENGINEER_MODEL", "ARGUS_SKILL_REVIEWER_MODEL", "ARGUS_SKILL_OBSERVER_MODEL",
    "ARGUS_SKILL_LIGHT_MODEL",
)


def profile_key(backend: str, name: str) -> str:
    return f"ARGUS_DESKTOP_{backend.upper()}_{name.removeprefix('ARGUS_SKILL_')}"


def prepare_selection(env: MutableMapping[str, str], saved: Mapping[str, str]) -> str:
    backend = str(env.get("ARGUS_SKILL_RUNNER_BACKEND", "")).strip().lower()
    if not env.get("ARGUS_DESKTOP_LAUNCH_NONCE") or not backend:
        return ""
    from ..agent_cli.runner_backend import normalize_runner_backend

    backend = normalize_runner_backend(backend)
    env[SELECTED_BACKEND] = backend
    env["ARGUS_SKILL_RUNNER_BACKEND"] = backend
    env["ARGUS_SKILL_LIFE_BACKEND"] = backend
    previous = str(saved.get("ARGUS_SKILL_RUNNER_BACKEND") or saved.get("ARGUS_SKILL_LIFE_BACKEND") or "").strip().lower()
    same_backend = previous == backend
    legacy_trial = saved.get("ARGUS_SKILL_COPILOT_TRIAL") == "1"
    for name in MODEL_KEYS:
        # A namespaced pin survives Pi -> DSH -> Pi. Legacy pins migrate by
        # association, not by deleting/relabeling another backend's settings.
        value = saved.get(profile_key(backend, name))
        if value is None and same_backend and not legacy_trial:
            value = saved.get(name)
        default = "auto" if name == "ARGUS_SKILL_MODEL" else "inherit"
        env[name] = str(value or default).strip() or default
    # Never inherit a retired desktop trial or foreign API-auth profile.
    env["ARGUS_SKILL_COPILOT_TRIAL"] = "0"
    env["ARGUS_SKILL_BACKEND_AUTH_MODE"] = "subscription_cli"
    return backend
