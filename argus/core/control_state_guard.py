"""Refuse Argus control-state writes from a role process, for its own project.

Argus's campaign switch (``continuous.json``), daemon stop/drain requests and
daemon lifecycle commands decide whether the daemon keeps working. Role
processes (the agent CLIs Argus launches for the Engineer, Reviewer, Planner,
Manager and teammates, and the task commands those roles start) run as the same
OS user. In one bounded run an Engineer imported Argus's own state API from the
Argus interpreter it was given, set ``continuous.json`` to ``enabled=false``,
and the daemon exited as if the campaign had finished.

What this module does:

* Every role process carries a marker naming its role and the state roots of
  the project it works for (:func:`mark_role_process_env`). Those roots come
  from the orchestrating process: the daemon and the life supervisor register
  the state root they run (:func:`orchestrate_state_root`), and a role started
  by a role inherits its parent's roots.
* Argus's control-state writers call :func:`refuse_role_control_write` with the
  root they are about to change. Inside a role process, a write to one of that
  role's own project roots is refused with :class:`RoleControlStateWriteDenied`;
  the refusal is recorded once per attempt as a runtime incident the daemon
  publishes, so the Manager and operator see it.
* Everything else works as before: a role may run Argus against an unrelated
  project or a scratch project of its own (a nested Argus, a benchmark
  subagent). A daemon or web server started from inside a role clears the
  marker for exactly its own state root (:func:`release_role_marker_for`),
  so the orchestrator it starts can manage its own campaign; its parent's
  project stays protected.

What it does not do: it closes the observed path, a role calling Argus's API,
not a determined attacker. A role that edits its own environment (drops the
marker) or writes the raw JSON file is not stopped here. Real protection needs
OS-level isolation of role processes (a separate user, a sandbox without write
access to the state root).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

log = logging.getLogger(__name__)

#: Present (non-empty) in the environment of every role process Argus starts.
ROLE_PROCESS_ENV = "ARGUS_SKILL_ROLE_PROCESS"
#: ``os.pathsep``-separated resolved state roots the role process works for.
ROLE_PROTECTED_ROOTS_ENV = "ARGUS_SKILL_ROLE_PROTECTED_ROOTS"

INCIDENT_DETECTOR = "control_state_guard"
INCIDENT_INVARIANT = "control_state_changed_by_orchestrator_only"

# State roots this process orchestrates; its role children may not change them.
_ORCHESTRATED: dict[str, None] = {}


class RoleControlStateWriteDenied(PermissionError):
    """A role process tried to change its own project's orchestrator control state."""


def _key(path: Path | str) -> str:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        resolved = Path(os.path.abspath(os.path.expanduser(str(path))))
    return os.path.normcase(str(resolved))


def _split(value: str) -> list[str]:
    return [part for part in str(value or "").split(os.pathsep) if part.strip()]


def role_process(env: Mapping[str, str] | None = None) -> str:
    """Return the role marker of this process, or ``""`` outside a role."""
    source = os.environ if env is None else env
    return str(source.get(ROLE_PROCESS_ENV) or "").strip()


def protected_roots(env: Mapping[str, str] | None = None) -> list[str]:
    """The state roots this role process may not change (normalised)."""
    source = os.environ if env is None else env
    if not role_process(source):
        return []
    return list(dict.fromkeys(_key(part) for part in _split(source.get(ROLE_PROTECTED_ROOTS_ENV, ""))))


def orchestrate_state_root(*roots: Path | str | None) -> None:
    """Record that this process runs the campaign in ``roots``.

    Role processes it starts later carry these roots and are refused writes to
    them. Idempotent; ``None`` entries are ignored.
    """
    for root in roots:
        if root is not None and str(root).strip():
            _ORCHESTRATED[_key(root)] = None


def mark_role_process_env(
    env: Mapping[str, str] | None,
    role: str = "agent",
) -> dict[str, str]:
    """Return a child environment that carries the role-process marker.

    ``None`` means "inherit this process's environment". The child is protected
    from the roots this process orchestrates and from any its own role parent
    passed down; the environment is otherwise unchanged.
    """
    marked = dict(os.environ if env is None else env)
    inherited = _split(marked.get(ROLE_PROTECTED_ROOTS_ENV, "")) if role_process(marked) else []
    roots = list(dict.fromkeys([*(_key(part) for part in inherited), *_ORCHESTRATED]))
    marked[ROLE_PROCESS_ENV] = str(role or "agent").strip() or "agent"
    if roots:
        marked[ROLE_PROTECTED_ROOTS_ENV] = os.pathsep.join(roots)
    else:
        marked.pop(ROLE_PROTECTED_ROOTS_ENV, None)
    return marked


def release_role_marker_for(root: Path | str, env: dict[str, str] | None = None) -> None:
    """Clear the role marker for ``root``, for an orchestrator started inside a role.

    A daemon or web server a role starts runs its own campaign: ``root`` itself,
    and only that root, is dropped from the protected list (the whole marker
    goes when none is left), so that orchestrator can manage its own state
    while its parent's project stays protected. A project root nested under
    ``root`` (the parent's project under a shared global root) stays protected.
    """
    target = os.environ if env is None else env
    if not role_process(target):
        return
    released = _key(root)
    remaining = [part for part in protected_roots(target) if part != released]
    if remaining:
        target[ROLE_PROTECTED_ROOTS_ENV] = os.pathsep.join(remaining)
        return
    target.pop(ROLE_PROTECTED_ROOTS_ENV, None)
    target.pop(ROLE_PROCESS_ENV, None)


def refuse_role_control_write(what: str, root: Path | str | None) -> None:
    """Raise when a role process tries to change its own project's control state.

    ``root`` is the state root (or project directory) the write targets. Only
    a root the role works for is refused; any other project is left alone.
    """
    if root is None or not role_process():
        return
    target = _key(root)
    if target not in protected_roots():
        return
    _record_refusal(target, what)
    raise RoleControlStateWriteDenied(
        f"{what} is Argus orchestrator control state for the project this role "
        "works on, and a role cannot change it. Control changes go through the "
        "operator or the Manager: state what you need and why in your run "
        "summary instead."
    )


def _role_phrase(role: str) -> str:
    role = role or "agent"
    return f"{'An' if role[:1].lower() in 'aeiou' else 'A'} {role} role process"


def _lead_lower(text: str) -> str:
    """``The continuous ...`` -> ``the continuous ...``; keeps ``Argus`` and acronyms."""
    head = text.split(" ", 1)[0]
    if head[:1].isupper() and head[1:].islower():
        return text[:1].lower() + text[1:]
    return text


def _record_refusal(root: str, what: str) -> None:
    """One runtime incident per refused attempt; never breaks the refusal."""
    try:
        from .runtime_incidents import RuntimeIncidentStore

        RuntimeIncidentStore(root).record_unresolved(
            detector=INCIDENT_DETECTOR,
            invariant=INCIDENT_INVARIANT,
            subject_kind="control_state",
            subject_id=str(what)[:120],
            severity="error",
            observed={"role": role_process(), "control": str(what)[:200], "pid": os.getpid()},
            reason=(
                f"{_role_phrase(role_process())} tried a control-state change and was "
                f"refused: {_lead_lower(str(what))}. Control changes go through the "
                "operator or the Manager."
            ),
            escalation_after=1,
        )
    except Exception:  # noqa: BLE001 - reporting must never weaken the refusal
        log.exception("failed to record a refused control-state write in %s", root)


def roots_of(paths: Iterable[Path | str | None]) -> list[str]:
    """Normalised keys for ``paths`` (a test and diagnostics helper)."""
    return [_key(path) for path in paths if path is not None]


__all__ = [
    "INCIDENT_DETECTOR",
    "INCIDENT_INVARIANT",
    "ROLE_PROCESS_ENV",
    "ROLE_PROTECTED_ROOTS_ENV",
    "RoleControlStateWriteDenied",
    "mark_role_process_env",
    "orchestrate_state_root",
    "protected_roots",
    "refuse_role_control_write",
    "release_role_marker_for",
    "role_process",
    "roots_of",
]
