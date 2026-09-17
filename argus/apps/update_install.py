"""Reject pip configuration that would redirect an in-place Argus update."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .update import CommandRunner


def _config_value(value: str) -> str:
    """Decode pip config list's repr values without interpreting configuration."""
    raw = value.strip()
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw
    return str(parsed) if parsed is not None else ""


def _user_enabled(value: str, setting: str) -> bool:
    from .update import UpdateError

    normalized = value.strip().lower()
    if normalized in {"", "0", "no", "false", "off", "n", "f"}:
        return False
    if normalized in {"1", "yes", "true", "on", "y", "t"}:
        return True
    raise UpdateError(f"{setting} has an invalid boolean value; correct it before updating")


def validate_pip_target(
    python_executable: str,
    cwd: Path,
    runner: CommandRunner,
    *,
    user_install: bool = False,
) -> None:
    """Ensure pip will install into this Python environment or its user site.

    Config inspection must not use the generic checked-command helper: its
    diagnostics include captured output, which can contain index credentials.
    Only the names of relevant settings are ever included in errors here.
    """
    from .update import UpdateError

    for name in ("PIP_TARGET", "PIP_PREFIX"):
        if os.environ.get(name, ""):
            raise UpdateError(
                f"{name} redirects pip outside the running environment; "
                "unset it before updating Argus"
            )

    environment_user = os.environ.get("PIP_USER", "").strip()
    if environment_user and _user_enabled(environment_user, "PIP_USER") and not user_install:
        raise UpdateError(
            "PIP_USER redirects pip to user site-packages, but Argus is running "
            "from another environment; unset it before updating"
        )

    result = runner([python_executable, "-m", "pip", "config", "list"], cwd, 30.0)
    if result.returncode != 0:
        raise UpdateError(
            "could not inspect pip configuration safely; resolve the pip configuration "
            "error before updating Argus"
        )

    user_settings: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip().lower()
        section, dot, option = key.rpartition(".")
        if (
            not dot
            or section not in {"global", "install", ":env:"}
            or option not in {"target", "prefix", "user"}
        ):
            continue
        decoded = _config_value(value)
        if option in {"target", "prefix"} and decoded:
            # Refuse target/prefix even if another config layer overrides it.
            # An explicit in-place update must not depend on that redirection.
            raise UpdateError(
                f"pip configuration '{key}' redirects installation outside the running "
                "environment; remove that setting before updating Argus"
            )
        if option == "user" and section in {"global", "install", ":env:"}:
            user_settings[section] = decoded

    # pip applies install-specific config after global config and environment
    # variables last. An explicit --user for a known user install remains valid.
    user_value = user_settings.get(":env:", user_settings.get("install", user_settings.get("global", "")))
    if environment_user:
        user_value = environment_user
    if _user_enabled(user_value, "pip user configuration") and not user_install:
        raise UpdateError(
            "pip user configuration redirects installation to user site-packages, "
            "but Argus is running from another environment; disable it before updating"
        )


__all__ = ["validate_pip_target"]
