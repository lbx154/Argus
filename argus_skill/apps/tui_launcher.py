"""Installed ``argus`` entrypoint: launch the bundled Ink cockpit."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_PYTHON_ADMIN_COMMANDS = frozenset({"doctor", "repair", "update", "wiki", "learn"})

_PYTHON_ADMIN_FLAGS = frozenset(
    {
        "-h",
        "--help",
        "-doctor",
        "--version",
        "--update",
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
        "--pair-plan",
        "--answer",
        "--ask",
        "--notify",
        "--init-identity",
        "--setup",
        "--doctor",
        "--model-api-status",
        "--init-model-api",
        "--install-ppt-master",
        "--ppt-master-status",
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
        "--mission-width",
        "--web-host",
        "--host",
        "--web-port",
        "--port",
        "--answer-item",
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
        "--fix-safe",
        "--json",
        "--deep",
        "--verify",
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


def _configure_windows_console_encoding(*, platform_name: str | None = None) -> None:
    """Keep the Python admin CLI usable on legacy Windows code pages.

    The CLI deliberately renders status glyphs and multilingual diagnostics.
    A normal zh-CN PowerShell process still exposes CP936 text streams, where
    writing one of those glyphs raises ``UnicodeEncodeError`` before the actual
    command can report its result.  Reconfigure only the Windows console-facing
    streams; child processes already receive an explicit UTF-8 environment.
    """
    if (os.name if platform_name is None else platform_name) != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


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


def _node_version(node: str) -> tuple[int, int, int] | None:
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
    match = re.search(
        r"v?(\d+)\.(\d+)(?:\.(\d+))?",
        completed.stdout or completed.stderr or "",
    )
    if match is None:
        return None
    major, minor, patch = (int(part or 0) for part in match.groups())
    return major, minor, patch


def _run_python_admin(argv: list[str]) -> int:
    from .cli._core import main as cli_main

    return cli_main(argv)


def _headless_stdin_error() -> str:
    """Explain that the cockpit needs a terminal, or ``""`` when it has one.

    Ink puts stdin in raw mode, so a piped, redirected or cron-launched
    `argus` used to die inside the bundle with a JavaScript stack trace and a
    link to Ink's README — after already announcing that it was starting the
    backend. The surfaces that do work without a terminal are named here
    because that is the question the operator actually has.
    """
    if os.environ.get("ARGUS_SKILL_ALLOW_HEADLESS_TUI", "").strip() == "1":
        return ""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return ""
    except (AttributeError, OSError, ValueError):
        pass
    return (
        "argus: the cockpit needs an interactive terminal and stdin is not "
        "one. Use `argus --web` for the browser cockpit, `argus --watch` for "
        "a live read-only view, `argus --status` for a one-shot summary, or "
        "`argus --daemon` to run unattended."
    )


def _uses_python_admin(argv: list[str]) -> bool:
    # `argus --web` is a cockpit surface: it needs the TUI's automatic port
    # selection and browser launch. Keep the legacy raw WebAPI spelling on the
    # Python path only when its backend-specific options are present.
    if "--web" in argv and any(
        arg == option or arg.startswith(f"{option}=")
        for arg in argv
        for option in ("--web-host", "--host", "--web-port", "--port")
    ):
        return True
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
    backend_name = "argus-skill.exe" if os.name == "nt" else "argus-skill"
    sibling = Path(sys.executable).parent / backend_name
    if sibling.is_file():
        os.environ["ARGUS_SKILL_BIN"] = str(sibling)


def _configure_tui_life_dir(argv: list[str]) -> list[str]:
    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--life-dir":
            os.environ["ARGUS_SKILL_HOME"] = str(
                Path(argv[index + 1]).expanduser().resolve()
            )
            index += 2
            continue
        if argument.startswith("--life-dir="):
            os.environ["ARGUS_SKILL_HOME"] = str(
                Path(argument.split("=", 1)[1]).expanduser().resolve()
            )
            index += 1
            continue
        forwarded.append(argument)
        index += 1
    return forwarded


def _needs_foreground_spawn() -> bool:
    """Whether this platform must wait for the cockpit instead of exec-ing it.

    Windows has no real exec: ``os.execv`` starts the child and exits the
    parent, so the shell prints its next prompt while the Ink cockpit still
    owns the console and both compete for the keyboard.
    """
    return os.name == "nt"


def main(argv: list[str] | None = None) -> int:
    _configure_windows_console_encoding()
    forwarded = list(sys.argv[1:] if argv is None else argv)
    if _uses_python_admin(forwarded):
        return _run_python_admin(forwarded)
    forwarded = _configure_tui_life_dir(forwarded)
    headless = _headless_stdin_error()
    if headless:
        sys.stderr.write(f"{headless}\n")
        return 2
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
        sys.stderr.write("argus: Ink TUI requires Node.js 22.12 or newer.\n")
        return 2
    node_version = _node_version(node)
    if node_version is None or node_version < (22, 12, 0):
        found = (
            "unknown"
            if node_version is None
            else ".".join(str(part) for part in node_version)
        )
        sys.stderr.write(
            f"argus: Ink TUI requires Node.js 22.12 or newer (found {found}).\n"
        )
        return 2
    _configure_tui_backend_bin()
    _export_tui_local_identity()
    if os.environ.get("ARGUS_BINARY_DISTRIBUTION", "").strip() == "1":
        # The TUI must own the real frozen backend process, not an npm wrapper
        # that would leave argus-core orphaned when the ownership PID is stopped.
        os.environ["ARGUS_BINARY_MODE"] = "cli"
    if _needs_foreground_spawn():
        # Windows has no real exec. os.execv() there starts the child and
        # returns/exits the parent immediately, so the shell prints its next
        # prompt while the Ink TUI keeps running on the *same* console. Two
        # processes then compete for the keyboard, the cursor position, and
        # stdout, which is why typed characters land below the input box —
        # letters and digits equally, nothing to do with input methods.
        # Run it in the foreground and exit with its status instead.
        try:
            completed = subprocess.run([node, str(bundle), *forwarded], check=False)
        except KeyboardInterrupt:
            return 130
        return int(completed.returncode or 0)
    os.execv(node, [node, str(bundle), *forwarded])
    return 0  # pragma: no cover - execv replaces the process on POSIX


__all__ = ["main"]
