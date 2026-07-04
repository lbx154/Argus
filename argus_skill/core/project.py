"""Project fingerprint: maps a working directory to a stable identifier
under ``~/.argus-skill/projects/<fingerprint>/``.

Resolution rules (see plan.md §2.4):

1. If ``cwd`` is inside a git work-tree AND the work-tree has a
   ``remote.origin.url`` configured, the fingerprint is
   ``sha1(normalized_remote_url)[:12]``. Same remote → same project,
   regardless of which clone the user is sitting in.
2. Otherwise, the fingerprint is ``sha1(absolute_cwd)[:12]``. Same
   absolute path → same project.

Normalization handles common variants of the same remote so that
``git@github.com:foo/bar.git`` and ``https://github.com/foo/bar``
collapse to one fingerprint.

This module has zero LLM / network dependencies. It only shells out to
``git`` and only inside the user's existing cwd. All failures fall back
silently to the path-hash branch.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ProjectIdentity",
    "project_fingerprint",
    "normalize_git_remote",
]


_FINGERPRINT_LEN = 12


@dataclass(frozen=True)
class ProjectIdentity:
    """Computed identity of a project rooted at ``cwd``.

    Attributes:
        fingerprint: 12-char sha1 prefix used as the dirname under
            ``projects/``.
        source: ``"git-remote"`` or ``"cwd-path"`` — explains how the
            fingerprint was derived.
        label: Human-readable label suitable for display in the REPL.
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
            fingerprint=_hash(normalized),
            source="git-remote",
            label=normalized,
            cwd=str(cwd_path),
        )
    label = str(cwd_path)
    return ProjectIdentity(
        fingerprint=_hash(label),
        source="cwd-path",
        label=label,
        cwd=str(cwd_path),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:_FINGERPRINT_LEN]


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
    timeouts, or any other error — fingerprint resolution always falls
    back to cwd-hash in that case.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "config", "--get", "remote.origin.url"],
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
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
