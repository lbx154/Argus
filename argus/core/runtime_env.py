from __future__ import annotations

import os
import shlex
import stat as stat_module
import sys
from collections.abc import MutableMapping
from pathlib import Path

from .paths import global_root

_ALLOWED_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CONFIG_DIR",
    "DISABLE_AUTOUPDATER",
    "ARGUS_SKILL_RUNNER_BIN",
})


def _path_key(value: str) -> str:
    """Return a platform-aware comparison key without changing the PATH entry."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(value)))


def configure_framework_python_env(
    env: MutableMapping[str, str] | None = None,
    *,
    executable: str | os.PathLike[str] | None = None,
    prepend_python_path: bool = False,
) -> MutableMapping[str, str]:
    """Expose the owning Argus interpreter and make bare ``python`` prefer it.

    Console-script launchers and unactivated Windows virtual environments can
    run Argus with a correct interpreter while leaving an unrelated Anaconda or
    system Python first on ``PATH``. Always expose the explicit interpreter.
    Daemon callers may also request the historical PATH prepend needed by agent
    child shells; ordinary CLI commands leave executable selection untouched.
    """
    target_env = env if env is not None else os.environ
    framework_python = str(
        target_env.get("ARGUS_SKILL_PYTHON") or executable or sys.executable
    ).strip()
    target_env["ARGUS_SKILL_PYTHON"] = framework_python
    # A child pip must never silently fall back to a user install: with the
    # system site unwritable it rewrites the ~/.local/bin launchers and plants
    # a user-site ``.pth`` that every later interpreter auto-runs (2026-09-05
    # incident). Pinned here because the legacy dangerous_yolo spawn — the 7x24
    # maintenance default — inherits this env untouched (its ``_child_env``
    # returns ``None``); ``sandbox.sandboxed_child_env`` re-pins it for the
    # sandboxed path.
    target_env["PIP_USER"] = "0"
    if os.name == "nt":
        target_env.setdefault("PYTHONUTF8", "1")
        target_env.setdefault("PYTHONIOENCODING", "utf-8")

    if not prepend_python_path:
        return target_env

    preferred: list[str] = []
    python_path = Path(framework_python).expanduser()
    if python_path.parent != Path(".") or python_path.is_absolute():
        preferred.append(str(python_path.resolve().parent))

    existing = str(target_env.get("PATH") or "").split(os.pathsep)
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in [*preferred, *existing]:
        clean = entry.strip()
        if not clean:
            continue
        key = _path_key(clean)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    target_env["PATH"] = os.pathsep.join(ordered)
    return target_env


def load_backend_runtime_env(
    env: MutableMapping[str, str] | None = None,
    *,
    root: Path | str | None = None,
) -> dict[str, str]:
    """Load trusted backend exports without executing the env file as shell."""
    target_env = env if env is not None else os.environ
    runtime_root = Path(root) if root is not None else global_root()
    path = runtime_root / "runtime" / "claude.env"
    try:
        stat = path.stat()
        if (
            not stat_module.S_ISREG(stat.st_mode)
            or path.is_symlink()
            or (
                os.name != "nt"
                and stat.st_mode & 0o022
            )
        ):
            return {}
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    loaded: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or key not in _ALLOWED_KEYS or key in target_env:
            continue
        try:
            parts = shlex.split(raw_value, posix=True)
        except ValueError:
            continue
        if len(parts) != 1:
            continue
        target_env[key] = parts[0]
        loaded[key] = parts[0]
    return loaded


__all__ = ["configure_framework_python_env", "load_backend_runtime_env"]
