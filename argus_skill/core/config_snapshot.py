"""Resolved Argus runtime hyperparameter snapshot.

This is the operator-facing "what is Argus currently configured to use?"
surface: role backend/model/reasoning effort plus the curated ARGUS_* knobs.
It is intentionally generated from the same resolvers used by the cockpit, so a
snapshot matches `/roles` and `--config-help` instead of becoming a parallel
configuration story.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..cli.roles_status import ROLES, resolve_all_roles
from .knobs import KNOBS


_ROLE_MODEL_ENV: dict[str, str] = {
    "manager": "ARGUS_SKILL_ENGINEER_MODEL",
    "planner": "ARGUS_SKILL_PLAN_MODEL",
    "engineer": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer": "ARGUS_SKILL_REVIEWER_MODEL",
    "curator": "ARGUS_SKILL_CURATOR_MODEL",
}
_ROLE_EFFORT_ENV: dict[str, str] = {
    "manager": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "planner": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "engineer": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "curator": "ARGUS_SKILL_CURATOR_REASONING_EFFORT",
}
_SENSITIVE_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH")


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _is_set(env: Mapping[str, str], name: str) -> bool:
    return bool(str(env.get(name, "") or "").strip())


def _source_from_env(
    env: Mapping[str, str],
    candidates: Sequence[str],
    *,
    default: str,
) -> str:
    for name in candidates:
        if _is_set(env, name):
            return name
    return default


def _backend_source(role: str, env: Mapping[str, str]) -> str:
    return _source_from_env(
        env,
        (
            f"ARGUS_SKILL_{role.upper()}_BACKEND",
            "ARGUS_SKILL_RUNNER_BACKEND",
            "ARGUS_SKILL_LIFE_BACKEND",
        ),
        default="default: codex",
    )


def _model_source(role: str, env: Mapping[str, str]) -> str:
    role_env = _ROLE_MODEL_ENV.get(role, "")
    candidates = tuple(v for v in (role_env, "ARGUS_SKILL_MODEL") if v)
    return _source_from_env(
        env,
        candidates,
        default="capability vault / default: gpt-5.5",
    )


def _effort_source(role: str, effort: str | None, env: Mapping[str, str]) -> str:
    if effort is None:
        return "not applicable for this model"
    role_env = _ROLE_EFFORT_ENV.get(role, "")
    if role_env and _is_set(env, role_env):
        return role_env
    if role == "manager" and _is_set(env, "ARGUS_SKILL_ENGINEER_REASONING_EFFORT"):
        return "ARGUS_SKILL_ENGINEER_REASONING_EFFORT"
    return "default: xhigh"


def _redacted_value(name: str, value: str, *, set_in_env: bool) -> str:
    if set_in_env and any(marker in name.upper() for marker in _SENSITIVE_MARKERS):
        return "<redacted>" if value else ""
    return value


def build_config_snapshot(
    *,
    env: Mapping[str, str] | None = None,
    generated_at_utc: str | None = None,
    roles: Sequence[str] = ROLES,
) -> dict[str, Any]:
    """Return a JSON-serializable snapshot of current Argus runtime settings."""
    env_map = env if env is not None else os.environ
    role_rows = []
    for cfg in resolve_all_roles(roles, env=env_map):
        role_rows.append(
            {
                "role": cfg.role,
                "backend": cfg.backend,
                "backend_label": cfg.backend_label,
                "backend_source": _backend_source(cfg.role, env_map),
                "model": cfg.model,
                "model_source": _model_source(cfg.role, env_map),
                "reasoning_effort": cfg.effort,
                "reasoning_effort_source": _effort_source(cfg.role, cfg.effort, env_map),
                "description": cfg.desc,
            }
        )

    knob_rows = []
    for knob in KNOBS:
        raw = str(env_map.get(knob.name, "") or "").strip()
        set_in_env = bool(raw)
        value = raw if set_in_env else knob.default
        knob_rows.append(
            {
                "name": knob.name,
                "group": knob.group,
                "value": _redacted_value(knob.name, value, set_in_env=set_in_env),
                "source": "env" if set_in_env else "default",
                "default": knob.default,
                "doc": knob.doc,
            }
        )

    return {
        "schema_version": 1,
        "generated_at_utc": generated_at_utc or _now_utc(),
        "roles": role_rows,
        "operator_knobs": knob_rows,
        "how_to_change": [
            "switch the model to <name>",
            "把模型换成 <name>",
            "把backend换成 <name>",
            "effort 设为 <low|medium|high|xhigh>",
            "/backend",
            "/config",
        ],
    }


def format_config_snapshot_markdown(snapshot: Mapping[str, Any]) -> str:
    """Render a human-readable Markdown snapshot."""
    lines: list[str] = [
        "# Argus Runtime Settings Snapshot",
        "",
        f"- Generated: `{snapshot.get('generated_at_utc', '')}`",
        f"- Schema: `{snapshot.get('schema_version', 1)}`",
        "",
        "## Role Hyperparameters",
        "",
        "| Role | Backend | Model | Effort | Sources |",
        "| --- | --- | --- | --- | --- |",
    ]
    for role in snapshot.get("roles", []):
        effort = role.get("reasoning_effort")
        effort_text = str(effort) if effort not in (None, "") else "n/a"
        source = (
            f"backend: {role.get('backend_source')}; "
            f"model: {role.get('model_source')}; "
            f"effort: {role.get('reasoning_effort_source')}"
        )
        lines.append(
            "| {role} | {backend} | `{model}` | `{effort}` | {source} |".format(
                role=role.get("role", ""),
                backend=role.get("backend_label", role.get("backend", "")),
                model=role.get("model", ""),
                effort=effort_text,
                source=source,
            )
        )

    lines.extend(
        [
            "",
            "## Operator Knobs",
            "",
            "| Group | Name | Value | Source | Default |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for knob in snapshot.get("operator_knobs", []):
        lines.append(
            "| {group} | `{name}` | `{value}` | {source} | `{default}` |".format(
                group=knob.get("group", ""),
                name=knob.get("name", ""),
                value=knob.get("value", ""),
                source=knob.get("source", ""),
                default=knob.get("default", ""),
            )
        )

    lines.extend(
        [
            "",
            "## Change From Argus",
            "",
        ]
    )
    for item in snapshot.get("how_to_change", []):
        lines.append(f"- `{item}`")
    lines.append("")
    return "\n".join(lines)


def write_config_snapshot(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    generated_at_utc: str | None = None,
) -> Path:
    """Write a Markdown or JSON config snapshot and return the output path."""
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    snapshot = build_config_snapshot(env=env, generated_at_utc=generated_at_utc)
    if out.suffix.lower() == ".json":
        out.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        out.write_text(format_config_snapshot_markdown(snapshot), encoding="utf-8")
    return out
