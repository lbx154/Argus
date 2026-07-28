"""Installed ``argus`` entrypoint: launch the bundled Ink cockpit."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_PYTHON_ADMIN_COMMANDS = frozenset({"wiki", "learn"})

_PYTHON_ADMIN_FLAGS = frozenset(
    {
        "-h",
        "--help",
        "--version",
        "--daemon",
        "--daemon-fg",
        "--daemon-stop",
        "--status",
        "--daemon-runbook",
        "--config-help",
        "--config-snapshot",
        "--gc",
        "--watch",
        "--follow",
        "--web",
        "--notify",
        "--init-identity",
        "--setup",
        "--doctor",
        "--model-api-status",
        "--init-model-api",
        "--install-ppt-master",
        "--ppt-master-status",
        "--approve-publication",
        "--list-pending-publications",
        "--skill-stats",
        "--skill-stats-json",
        "--skill-cleanse",
        "--export-builtin-skills",
        "--evidence-chain-check",
        "--anti-mediocrity-check",
        "--lifecycle-status",
        "--lifecycle-resume",
        "--lifecycle-archive",
    }
)

_PYTHON_PRE_ACTION_VALUE_OPTIONS = frozenset(
    {
        "--life-dir",
        "--gc-days",
        "--objective",
        "--web-host",
        "--web-port",
        "--notify-stage",
        "--backend",
        "--auth-mode",
        "--skills-dir",
        "--project-root",
        "--proposed-condition",
        "--baseline-condition",
    }
)

_PYTHON_PRE_ACTION_OPTIONAL_VALUE_OPTIONS = frozenset({"--resume"})

_PYTHON_PRE_ACTION_BOOL_OPTIONS = frozenset(
    {
        "--drain",
        "--force",
        "--gc-dry-run",
        "--no-daemon",
        "--new",
        "--continue",
        "--continuous",
        "--resume-continuous",
        "--bounded",
        "--non-interactive",
        "--accept-house-rules",
        "--allow-prerelease",
        "--set-git-global",
        "--configure-codex",
        "--apply",
    }
)


def _bundle_path() -> Path | None:
    explicit = os.environ.get("ARGUS_TUI_BUNDLE")
    candidates = [
        Path(explicit).expanduser() if explicit else None,
        # Wheel layout (force-included by pyproject.toml).
        Path(__file__).resolve().parents[1] / "_frontend" / "tui" / "bundle" / "argus.mjs",
        # Source/editable checkout layout.
        Path(__file__).resolve().parents[2] / "frontend" / "tui" / "bundle" / "argus.mjs",
    ]
    return next((path for path in candidates if path is not None and path.is_file()), None)


def _node_major(node: str) -> int | None:
    try:
        completed = subprocess.run(
            [node, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"v?(\d+)", completed.stdout or completed.stderr or "")
    return int(match.group(1)) if match else None


def _run_python_admin(argv: list[str]) -> int:
    from .cli._core import main as cli_main

    return cli_main(argv)


def _uses_python_admin(argv: list[str]) -> bool:
    i = 0
    while i < len(argv):
        arg = argv[i]
        option = arg.split("=", 1)[0] if arg.startswith("--") else arg
        if option in _PYTHON_ADMIN_COMMANDS | _PYTHON_ADMIN_FLAGS:
            return True
        if option in _PYTHON_PRE_ACTION_VALUE_OPTIONS:
            if "=" not in arg and (i + 1 >= len(argv) or argv[i + 1].startswith("-")):
                return True
            i += 1 if "=" in arg else 2
            continue
        if option in _PYTHON_PRE_ACTION_OPTIONAL_VALUE_OPTIONS:
            if "=" not in arg and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        if option in _PYTHON_PRE_ACTION_BOOL_OPTIONS:
            i += 1
            continue
        return False
    return False


def _tui_local_identity() -> dict[str, object]:
    from ..core.runtime_identity import source_root
    from ..release import release_identity

    return release_identity(source_root())


def _export_tui_local_identity() -> None:
    identity = _tui_local_identity()
    values = {
        "ARGUS_TUI_LOCAL_RELEASE_ID": identity.get("release_id"),
        "ARGUS_TUI_LOCAL_SOURCE_DIGEST": identity.get("runtime_source_digest"),
    }
    for name, value in values.items():
        text = str(value or "").strip()
        if text:
            os.environ[name] = text
        else:
            os.environ.pop(name, None)


def _configure_tui_backend_bin() -> None:
    if os.environ.get("ARGUS_BINARY_DISTRIBUTION", "").strip() == "1":
        os.environ.setdefault("ARGUS_SKILL_BIN", sys.executable)
        return
    if os.environ.get("ARGUS_SKILL_BIN", "").strip():
        return
    sibling = Path(sys.executable).parent / "argus-skill"
    if sibling.is_file():
        os.environ["ARGUS_SKILL_BIN"] = str(sibling)


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    if _uses_python_admin(forwarded):
        return _run_python_admin(forwarded)
    from ..life.special_prompts import describe_special_prompt_gate

    ok, detail = describe_special_prompt_gate()
    if not ok:
        sys.stderr.write(f"argus: {detail}\n")
        return 2
    bundle = _bundle_path()
    if bundle is None:
        sys.stderr.write(
            "argus: bundled Ink TUI is missing. Reinstall from a current release.\n"
        )
        return 2
    node = shutil.which("node")
    if node is None:
        sys.stderr.write("argus: Ink TUI requires Node.js 18 or newer.\n")
        return 2
    major = _node_major(node)
    if major is None or major < 18:
        found = "unknown" if major is None else str(major)
        sys.stderr.write(
            f"argus: Ink TUI requires Node.js 18 or newer (found {found}).\n"
        )
        return 2
    _configure_tui_backend_bin()
    _export_tui_local_identity()
    if os.environ.get("ARGUS_BINARY_DISTRIBUTION", "").strip() == "1":
        # The TUI must own the real frozen backend process, not an npm wrapper
        # that would leave argus-core orphaned when the ownership PID is stopped.
        os.environ["ARGUS_BINARY_MODE"] = "cli"
    os.execv(node, [node, str(bundle), *forwarded])
    return 0  # pragma: no cover - execv replaces the process


__all__ = ["main"]
