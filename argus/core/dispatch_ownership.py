"""Resolve execution ownership independently of logs and monetary policy.

Explicit runtime context and the inherited session root own project calls.
Standalone workdir-only calls resolve registered sessions (including legacy
fingerprints); ambiguity or conflicting ownership fails closed. Only generic
standalone callers may be unscoped when there is no project evidence. Project
entry points require an owner even for an as-yet unregistered workdir.
"""
from __future__ import annotations

import os
from pathlib import Path

from .paths import global_root, resolve_runtime_path
from .project import project_fingerprint
from .safety_io import loads_strict_json


class DispatchOwnershipError(RuntimeError):
    pass


def _state_path(path: Path, root: Path) -> Path:
    path = path.expanduser().absolute()
    # Do not follow a session-state alias to a different fence. Workspace
    # symlinks are fine: only state authority must not be redirected.
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise DispatchOwnershipError('symlink in execution state ownership')
    return path.resolve()


def _metadata(project: Path) -> dict | None:
    meta = project / 'session.json'
    if meta.is_symlink():
        raise DispatchOwnershipError('symlink in execution session metadata')
    try:
        value = loads_strict_json(meta.read_bytes())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get('id') != project.name:
        raise DispatchOwnershipError('invalid execution session identity')
    return value


def _matches(workdir: Path, owner_workdir: str) -> bool:
    owner = Path(owner_workdir).expanduser().resolve(strict=True)
    return workdir == owner or owner in workdir.parents


def resolve_dispatch_project(*, project: Path | None, root: Path | None,
                             working_dir: str | None,
                             require_project: bool = False) -> Path | None:
    """Return canonical authority or a secret-safe, non-accounting denial.

    Reviewer and supervisor callers resolve before entering the backend, so
    its result finalizer cannot protect their diagnostics. Keep this boundary
    shared by all ownership callers; never finalize uncertain admission here.
    """
    try:
        return _resolve_dispatch_project(project=project, root=root,
            working_dir=working_dir, require_project=require_project)
    except Exception as exc:
        from .secret_guard import known_secret_values, redact_secrets_text

        try:
            detail = redact_secrets_text(f'{type(exc).__name__}: {exc}',
                                         known_values=known_secret_values())
        except Exception:
            # Failure to collect/redact credentials must not expose either
            # the original error or the redactor's exception in a traceback.
            detail = 'diagnostic unavailable (secret redaction failed)'
        raise DispatchOwnershipError(f'execution ownership denied: {detail}') from None


def _resolve_dispatch_project(*, project: Path | None, root: Path | None,
                              working_dir: str | None,
                              require_project: bool) -> Path | None:
    root = (root or global_root()).expanduser().resolve()
    wd = Path(working_dir).expanduser().resolve(strict=True) if working_dir else None
    inherited = os.environ.get('ARGUS_SKILL_SESSION_ROOT', '').strip()
    inherited_id = os.environ.get('ARGUS_SKILL_SESSION_ID', '').strip()
    if inherited:
        inherited_path = _state_path(resolve_runtime_path(inherited, context='session root'), root)
        if inherited_path.parent != root / 'projects' or not inherited_path.is_dir():
            raise DispatchOwnershipError('unregistered inherited execution owner')
        if inherited_id and inherited_path.name != inherited_id:
            raise DispatchOwnershipError('conflicting inherited execution identity')
        if project is not None and _state_path(project, root) != inherited_path:
            raise DispatchOwnershipError('conflicting explicit and inherited execution owners')
        project = inherited_path
    elif inherited_id:
        raise DispatchOwnershipError('execution session id without session root')
    if project is not None:
        owner = _state_path(project, root)
        meta = _metadata(owner)
        bound = str((meta or {}).get('workdir') or (meta or {}).get('cwd') or '').strip()
        if wd is not None and bound and not _matches(wd, bound):
            raise DispatchOwnershipError('workdir does not belong to execution owner')
        # Explicit set_usage_context is the trusted legacy binding API. It may
        # name a non-collection root, or a state directory not yet initialized.
        # A different registered fingerprint is not such a legacy binding.
        if wd is not None and owner.parent == root / 'projects' and not bound:
            fingerprint = root / 'projects' / project_fingerprint(wd).fingerprint
            if fingerprint.is_dir() and fingerprint != owner:
                raise DispatchOwnershipError('conflicting registered execution owner')
        return owner
    if wd is not None:
        collection = root / 'projects'
        if collection.is_symlink():
            raise DispatchOwnershipError('symlink in execution state collection')
        fingerprint = collection / project_fingerprint(wd).fingerprint
        candidates = set()
        for entry in collection.iterdir() if collection.exists() else ():
            if not entry.is_dir() and not entry.is_symlink():
                continue
            owner = _state_path(entry, root)
            meta = _metadata(owner)
            bound = str((meta or {}).get('workdir') or (meta or {}).get('cwd') or '').strip()
            if bound:
                try:
                    matches = _matches(wd, bound)
                except FileNotFoundError:
                    # Retained metadata may outlive an unrelated workspace.
                    # Ignore only that proven non-owner, not malformed state,
                    # the current fingerprint, or a potentially owning path.
                    stale = Path(bound).expanduser().resolve(strict=False)
                    if owner == fingerprint or wd == stale or stale in wd.parents:
                        raise
                    continue
                if matches:
                    candidates.add(owner)
        if fingerprint.is_dir():
            owner = _state_path(fingerprint, root)
            meta = _metadata(owner)
            bound = str((meta or {}).get('workdir') or (meta or {}).get('cwd') or '').strip()
            if bound and not _matches(wd, bound):
                raise DispatchOwnershipError('fingerprint conflicts with session workdir')
            candidates.add(owner)
        if len(candidates) > 1:
            raise DispatchOwnershipError('ambiguous execution owner; bind canonical session explicitly')
        if candidates:
            return candidates.pop()
    if require_project:
        raise DispatchOwnershipError('project execution requires a registered or explicit owner')
    # Intentional standalone compatibility: no ambient cwd/logdir authority,
    # no inferred/new project registration, and no project-wide pause claim.
    return None
