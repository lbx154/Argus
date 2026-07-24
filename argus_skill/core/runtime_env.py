from __future__ import annotations

import os
import shlex
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
        if not path.is_file() or stat.st_mode & 0o022:
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


__all__ = ["load_backend_runtime_env"]
