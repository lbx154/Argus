"""Project fingerprint: maps a working directory to a stable identifier
under ``~/.argus-skill/projects/<fingerprint>/``.

Resolution rules (see plan.md §2.4):

1. If ``cwd`` is inside a git work-tree AND the work-tree has a
   ``remote.origin.url`` configured, the label is the normalized remote
   URL. Same remote → same project, regardless of which clone the user is
   sitting in.
2. Otherwise, the label is the absolute cwd. Same absolute path → same
   project.

The fingerprint is the label itself as one directory name: every character
other than letters, digits and a few plain marks is percent-encoded, so it
reads back as the label and two labels never share a directory.

Normalization handles common variants of the same remote so that
``git@github.com:foo/bar.git`` and ``https://github.com/foo/bar``
collapse to one fingerprint.

This module has zero LLM / network dependencies. It only shells out to
``git`` and only inside the user's existing cwd. All failures fall back
silently to the path branch.
"""
from __future__ import annotations

import json
import logging
import os
import re
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

__all__ = [
    "ProjectIdentity",
    "is_state_directory_name",
    "migrate_legacy_project_state",
    "project_fingerprint",
    "normalize_git_remote",
    "resolve_legacy_project_state",
    "resolve_project_root",
]


log = logging.getLogger(__name__)
# Characters a fingerprint keeps as they are; any other ASCII character, "."
# included so a name never starts or ends with one, is percent-encoded.
# Printable non-ASCII text (a Chinese folder name) is kept.
_KEPT = frozenset(string.ascii_letters + string.digits + "-_~@+,=")
# The longest directory name common filesystems hold.
_MAX_NAME_BYTES = 255
# Project state directories this process already checked for an older name.
_MIGRATION_CHECKED: set[tuple[str, str]] = set()
# 迁移专用，下个版本删除: ``{old name: new name}`` of every migrated directory,
# beside them; the leading dot keeps it out of every project scan.
LEGACY_NAMES = ".legacy-names.json"


def resolve_project_root(explicit: str | Path | None = None) -> Path:
    """Resolve the active execution root without creating it."""
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get("ARGUS_SKILL_PROJECT_ROOT")
    return Path(configured) if configured else Path.cwd()


@dataclass(frozen=True)
class ProjectIdentity:
    """Computed identity of a project rooted at ``cwd``.

    Attributes:
        fingerprint: the label as one directory name under ``projects/``
            (see :func:`_directory_name`).
        source: ``"git-remote"`` or ``"cwd-path"`` — explains how the
            fingerprint was derived.
        label: Human-readable label suitable for display in the cockpit.
            For git, this is the normalized remote URL; for cwd, this is the
            absolute path string.
        cwd: The absolute working directory the identity was computed
            from. Always populated for diagnostics.
    """

    fingerprint: str
    source: str
    label: str
    cwd: str


def project_fingerprint(cwd: str | Path | None = None) -> ProjectIdentity:
    """Compute a :class:`ProjectIdentity` for ``cwd`` (default: current cwd).

    Always returns an identity; never raises on the happy path.
    """
    cwd_path = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    remote = _git_remote_origin(cwd_path)
    if remote:
        normalized = normalize_git_remote(remote)
        return ProjectIdentity(
            fingerprint=_directory_name(normalized),
            source="git-remote",
            label=normalized,
            cwd=str(cwd_path),
        )
    label = str(cwd_path)
    return ProjectIdentity(
        fingerprint=_directory_name(label),
        source="cwd-path",
        label=label,
        cwd=str(cwd_path),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _directory_name(label: str) -> str:
    """``label`` as one directory name that reads back as ``label``.

    On a case-insensitive filesystem two paths differing only in case name the
    same directory, and so the same project, as they do on disk. A label too
    long for one directory name is refused rather than shortened.
    """
    name = "".join(
        char if char in _KEPT or (not char.isascii() and char.isprintable())
        else "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))
        for char in label
    )
    if len(name.encode("utf-8")) > _MAX_NAME_BYTES:
        raise ValueError(f"project path is too long to name its state directory: {label[:60]!r}...")
    return name


def is_state_directory_name(name: str) -> bool:
    """Whether ``name`` is a project state directory's name: an escaped label."""
    try:
        return "%" in name and _directory_name(unquote(name)) == name
    except ValueError:
        return False


def migrate_legacy_project_state(states_root: Path, name: str) -> Path:
    """迁移专用，下个版本删除 (migration only; delete in the next release).

    Returns the state directory of ``name``. Releases before this one named a
    project's directory ``sha1(label)[:12]``; when only that one exists it is
    recorded in :data:`LEGACY_NAMES` and renamed to ``name`` in one atomic
    step, unless a daemon still runs in it, which keeps it until a lookup after
    the daemon stops. When both exist, the new one is used and the old one is
    left untouched.
    """
    import hashlib

    from .project_gc import _project_is_live

    target = states_root / name
    key = (str(states_root), name)
    if key in _MIGRATION_CHECKED:
        return target
    legacy = states_root / hashlib.sha1(
        unquote(name).encode("utf-8"), usedforsecurity=False,
    ).hexdigest()[:12]
    if not legacy.is_dir():
        pass
    elif target.exists():
        log.warning("project state %s is kept; the older copy %s was left untouched", target, legacy)
    elif _project_is_live(legacy):
        return legacy
    else:
        try:
            _record_legacy_name(states_root, legacy.name, name)
        except (OSError, ValueError):  # unrecorded, a saved old path would lose it; try again later
            log.warning("project state %s was not migrated: its new name could not be recorded", legacy)
            return legacy
        try:
            os.rename(legacy, target)
        except OSError:  # another process migrated it, or created the new one, first
            pass
        else:
            log.info("migrated project state %s to %s", legacy, target)
    _MIGRATION_CHECKED.add(key)
    return target


def _record_legacy_name(states_root: Path, legacy: str, name: str) -> None:
    """迁移专用，下个版本删除: add ``legacy -> name`` to :data:`LEGACY_NAMES`."""
    from .atomic_replace import replace
    from .file_lock import exclusive_file_lock

    table = states_root / LEGACY_NAMES
    temporary = table.with_name(f"{LEGACY_NAMES}.{os.getpid()}.tmp")
    with (
        open(table.with_suffix(".lock"), "a+b") as handle,
        exclusive_file_lock(handle, lock_name="legacy project names"),
    ):
        try:
            names = json.loads(table.read_text(encoding="utf-8"))
        except FileNotFoundError:
            names = {}
        names[legacy] = name
        try:
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(names, output, ensure_ascii=False, indent=1)
                output.flush()
                os.fsync(output.fileno())
            replace(temporary, table)
        finally:
            temporary.unlink(missing_ok=True)


def resolve_legacy_project_state(path: Path) -> Path:
    """迁移专用，下个版本删除 (migration only; delete in the next release).

    ``path``, or, when it does not exist and names a directory a migration
    renamed, the directory it was renamed to: a path saved before the
    migration still finds its project and never recreates an empty one.
    """
    path = Path(path)
    if path.exists():
        return path
    try:
        name = json.loads((path.parent / LEGACY_NAMES).read_text(encoding="utf-8")).get(path.name)
    except (OSError, ValueError, AttributeError):
        return path
    return path.parent / name if isinstance(name, str) and name else path


def _git_env() -> dict[str, str]:
    """Return an env mapping with malformed ``GIT_CONFIG_*`` stripped.

    Git treats an incomplete ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_*`` /
    ``GIT_CONFIG_VALUE_*`` tuple as an error. We remove only that config
    injection family so unrelated overrides like ``GIT_DIR`` or
    ``GIT_WORK_TREE`` still reach the subprocess unchanged.
    """
    env = os.environ.copy()
    raw_count = env.get("GIT_CONFIG_COUNT")
    if raw_count is None:
        return env
    try:
        count = int(raw_count)
    except ValueError:
        count = -1

    valid = count >= 0 and all(
        env.get(f"GIT_CONFIG_KEY_{idx}") is not None
        and env.get(f"GIT_CONFIG_VALUE_{idx}") is not None
        for idx in range(count)
    )
    if valid:
        return env

    for key in list(env):
        if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    return env


def _git_remote_origin(cwd: Path) -> str | None:
    """Return ``remote.origin.url`` if ``cwd`` is inside a git work-tree.

    Uses ``git -C <cwd> config --get remote.origin.url``. Returns
    ``None`` for non-git dirs, missing remote, missing git binary,
    or any other error — fingerprint resolution always falls
    back to cwd-hash in that case.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "config", "--get", "remote.origin.url"],
            env=_git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    url = (completed.stdout or "").strip()
    return url or None


_SCP_LIKE = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")


def normalize_git_remote(url: str) -> str:
    """Collapse common variants of the same git remote to one canonical form.

    Examples (all → ``github.com/foo/bar``):

    * ``https://github.com/foo/bar.git``
    * ``https://github.com/foo/bar/``
    * ``git@github.com:foo/bar.git``
    * ``ssh://git@github.com/foo/bar``

    Any URL we can't parse is returned lower-cased + stripped,
    preserving determinism so the fingerprint is still stable.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    cleaned = raw

    # If the URL has an explicit scheme, strip it first; otherwise try
    # the SCP-like ``user@host:path`` form. Doing the SCP test BEFORE
    # the scheme strip would misread ``ssh://git@github.com:22/...`` as
    # ``host=github.com`` ``path=22/...`` and lose the port.
    scheme_stripped = False
    for scheme in ("https://", "http://", "ssh://", "git://", "git+ssh://"):
        if cleaned.lower().startswith(scheme):
            cleaned = cleaned[len(scheme):]
            scheme_stripped = True
            break

    if not scheme_stripped:
        m = _SCP_LIKE.match(cleaned)
        if m:
            host = m.group("host")
            path = m.group("path")
            cleaned = f"{host}/{path}"

    # Strip ``user@`` prefix on the host part (after scheme strip, the
    # remainder may be ``git@host[:port]/path``).
    if "@" in cleaned and "/" in cleaned and cleaned.index("@") < cleaned.index("/"):
        cleaned = cleaned.split("@", 1)[1]

    cleaned = cleaned.strip("/")
    if cleaned.lower().endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned.lower()
