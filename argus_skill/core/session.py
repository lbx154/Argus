"""Session model — Copilot/Codex/Claude-Code-style daemons.

Historically a "project" was keyed by the cwd/git-remote fingerprint, so
re-running ``argus-skill`` in the same directory always REUSED the same
project + daemon. That made "start a fresh run" impossible without juggling
``ARGUS_SKILL_HOME``.

The session model inverts the default:

* ``argus-skill`` (default ``--new``) → a BRAND-NEW session: a fresh
  ``session id`` keys ``projects/<id>/`` with its own daemon + memory.
* ``argus-skill --resume [<id>]`` → reuse a previous session (a picker when
  no id is given).
* ``argus-skill --continue`` → reuse the most-recently-active session.

Each session writes ``projects/<id>/session.json`` so the resume picker can
show ``id · name · age · backlog``. The Manager fills ``display_name`` from
the first task (see :mod:`argus_skill.manager`). Legacy cwd-fingerprint
projects (no ``session.json``) are still listable/resumable by their id.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths as core_paths

SESSION_META_FILE = "session.json"
_SESSION_PREFIX = "s-"


def new_session_id() -> str:
    """A short, unique, path-safe session id, e.g. ``s-3f9a1c20``."""
    return _SESSION_PREFIX + secrets.token_hex(4)


@dataclass
class SessionMeta:
    id: str
    display_name: str = ""
    created: float = 0.0
    last_active: float = 0.0
    cwd: str = ""
    objective: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMeta":
        return cls(
            id=str(d.get("id", "")),
            display_name=str(d.get("display_name", "") or ""),
            created=float(d.get("created", 0.0) or 0.0),
            last_active=float(d.get("last_active", 0.0) or 0.0),
            cwd=str(d.get("cwd", "") or ""),
            objective=str(d.get("objective", "") or ""),
        )


def _meta_path(global_root: Path | None, sid: str) -> Path:
    root = global_root if global_root is not None else core_paths.global_root()
    return Path(root) / "projects" / sid / SESSION_META_FILE


def read_session_meta(global_root: Path | None, sid: str) -> SessionMeta | None:
    try:
        raw = _meta_path(global_root, sid).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        return SessionMeta.from_dict(json.loads(raw))
    except Exception:  # noqa: BLE001
        return None


def write_session_meta(global_root: Path | None, meta: SessionMeta) -> None:
    p = _meta_path(global_root, meta.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(meta.to_json() + "\n", encoding="utf-8")
    tmp.replace(p)


def touch_session(
    global_root: Path | None,
    sid: str,
    *,
    display_name: str | None = None,
    objective: str | None = None,
    now: float | None = None,
) -> None:
    """Bump last_active (and optionally name/objective). Fail-soft."""
    now = time.time() if now is None else now
    meta = read_session_meta(global_root, sid) or SessionMeta(id=sid, created=now)
    meta.last_active = now
    if display_name is not None and not meta.display_name:
        meta.display_name = display_name
    if objective is not None and not meta.objective:
        meta.objective = objective
    try:
        write_session_meta(global_root, meta)
    except OSError:
        pass


def project_exists(global_root: Path | None, sid: str) -> bool:
    root = global_root if global_root is not None else core_paths.global_root()
    return (Path(root) / "projects" / sid).is_dir()


def list_sessions(
    global_root: Path | None = None, *, include_empty: bool = True
) -> list[SessionMeta]:
    """All sessions (newest-active first).

    Includes legacy cwd-fingerprint projects with no session.json — synthesised
    from the dir mtime + continuous.json objective so they stay resumable.

    With ``include_empty=False`` the content-less litter that bare launches mint
    (no name, no objective, no backlog, no events) is hidden UNLESS it has a live
    daemon — so the resume picker shows real/running work, not 70 empty shells.
    """
    root = global_root if global_root is not None else core_paths.global_root()
    projects = Path(root) / "projects"
    if not projects.exists():
        return []
    out: list[SessionMeta] = []
    for d in projects.iterdir():
        if not d.is_dir():
            continue
        meta = read_session_meta(global_root, d.name)
        if meta is None:
            # Legacy project: synthesise minimal meta so it's resumable.
            try:
                mtime = d.stat().st_mtime
            except OSError:
                mtime = 0.0
            obj = ""
            try:
                cj = json.loads((d / "continuous.json").read_text(encoding="utf-8"))
                obj = str(cj.get("objective", "") or "")
            except Exception:  # noqa: BLE001
                pass
            meta = SessionMeta(id=d.name, created=mtime, last_active=mtime, objective=obj)
        if not include_empty and not _session_is_meaningful(d, meta):
            continue
        out.append(meta)
    out.sort(key=lambda m: m.last_active, reverse=True)
    return out


def _project_has_content(project_dir: Path) -> bool:
    """True if a project dir holds real work (backlog items or recorded events)."""
    for name in ("backlog.jsonl", "events.jsonl"):
        try:
            f = project_dir / name
            if f.exists() and f.stat().st_size > 2:
                return True
        except OSError:
            pass
    return False


def _session_is_meaningful(project_dir: Path, meta: "SessionMeta") -> bool:
    """A session is worth listing if it is named, has an objective, holds real
    work, or has a LIVE daemon — otherwise it is bare-launch litter."""
    if (meta.display_name or "").strip() or (meta.objective or "").strip():
        return True
    if _project_has_content(project_dir):
        return True
    try:
        from .daemon_lock import is_pid_running, read_daemon_pid

        for lock in ("daemon.pid", "repl.pid"):
            pid = read_daemon_pid(project_dir / lock)
            if pid is not None and is_pid_running(pid):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False



def most_recent_session(global_root: Path | None = None) -> str | None:
    sessions = list_sessions(global_root)
    return sessions[0].id if sessions else None


def live_daemon_sessions(global_root: Path | None = None) -> list[SessionMeta]:
    """Sessions/projects that currently have a LIVE daemon, newest-active first.

    A running daemon is the operator's actual work; the session model must not
    bury it. Used to (a) surface it on a fresh-session banner and (b) make
    ``--continue`` prefer it over an empty just-minted session. Liveness is the
    lightweight ``daemon.pid`` + pid-alive check (no daemon-layer import).
    """
    from .daemon_lock import is_pid_running, read_daemon_pid

    root = global_root if global_root is not None else core_paths.global_root()
    projects = Path(root) / "projects"
    out: list[SessionMeta] = []
    for meta in list_sessions(global_root):
        try:
            pid = read_daemon_pid(projects / meta.id / "daemon.pid")
        except Exception:  # noqa: BLE001
            pid = None
        if pid is not None and is_pid_running(pid):
            out.append(meta)
    return out  # already newest-active-first (list_sessions sorts)



class SessionResolutionError(ValueError):
    """Raised when a requested resume target does not exist."""


def resolve_session(
    *,
    global_root: Path | None,
    mode: str,
    session_id: str | None = None,
    cwd: str | Path | None = None,
    now: float | None = None,
) -> tuple[str, bool]:
    """Resolve the session id to operate on.

    ``mode``:
      * ``new``      → mint a fresh id, write its session.json, return (id, True).
      * ``resume``   → require an existing ``session_id`` (caller runs the
                       picker when it's None); return (id, False).
      * ``continue`` → the most-recently-active session; return (id, False).

    Returns ``(session_id, is_new)``. Raises :class:`SessionResolutionError`
    for a resume/continue target that does not exist.
    """
    now = time.time() if now is None else now
    if mode == "new":
        sid = new_session_id()
        write_session_meta(
            global_root,
            SessionMeta(id=sid, created=now, last_active=now,
                        cwd=str(Path(cwd).resolve()) if cwd else str(Path.cwd())),
        )
        return sid, True
    if mode == "continue":
        # Prefer a session with a LIVE daemon (the operator's actual running
        # work) over a more-recent but empty just-minted session — otherwise
        # `--continue` would attach to a litter session and the real daemon
        # stays buried. Fall back to plain most-recent when none are live.
        live = live_daemon_sessions(global_root)
        sid = live[0].id if live else most_recent_session(global_root)
        if not sid:
            raise SessionResolutionError("no previous session to --continue")
        return sid, False
    if mode == "resume":
        if not session_id:
            raise SessionResolutionError("resume requires a session id (use the picker)")
        if not project_exists(global_root, session_id):
            raise SessionResolutionError(f"no session {session_id!r} to resume")
        return session_id, False
    raise SessionResolutionError(f"unknown session mode {mode!r}")
