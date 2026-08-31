"""PyInstaller entry point for the frozen Tauri desktop backend."""

from __future__ import annotations

import errno
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any

_VERIFY_FROZEN_RUNTIME = "--verify-frozen-runtime"


def _install_windows_signal_zero_guard(*, platform_name: str | None = None) -> None:
    """Make ``os.kill(pid, 0)`` a non-destructive liveness probe on Windows.

    CPython maps non-console signals to ``TerminateProcess`` on Windows, including
    signal zero. POSIX-oriented Argus tools and agent-authored diagnostics commonly
    use signal zero only to ask whether a PID exists; without this guard the frozen
    interpreter can silently terminate a daemon or its owning Web backend.
    """
    host_platform = os.name if platform_name is None else platform_name
    if host_platform != "nt" or getattr(os.kill, "__argus_signal_zero_guard__", False):
        return

    from argus_skill.core.daemon_lock import is_pid_running

    original_kill = os.kill

    def guarded_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if is_pid_running(int(pid)):
                return
            raise ProcessLookupError(errno.ESRCH, os.strerror(errno.ESRCH))
        original_kill(pid, sig)

    setattr(guarded_kill, "__argus_signal_zero_guard__", True)
    os.kill = guarded_kill  # type: ignore[assignment]


def verify_runtime_providers() -> dict[str, Any]:
    """Import every registered dynamic provider and return a JSON-safe report."""
    from argus_skill.domains import BUILTIN_DOMAINS, load_domain
    from argus_skill.skills.vertical_select import VERTICALS
    from argus_skill.verticals._base import load_vertical

    loaded_verticals: list[str] = []
    loaded_domains: list[str] = []
    failures: list[dict[str, str]] = []

    for name in VERTICALS:
        try:
            load_vertical(name)
            loaded_verticals.append(name)
        except Exception as exc:  # noqa: BLE001 - report every broken provider
            failures.append(
                {
                    "provider_type": "vertical",
                    "name": name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    for name in BUILTIN_DOMAINS:
        try:
            load_domain(name)
            loaded_domains.append(name)
        except Exception as exc:  # noqa: BLE001 - report every broken provider
            failures.append(
                {
                    "provider_type": "domain",
                    "name": name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    return {
        "check": "argus-frozen-runtime-providers",
        "ok": not failures,
        "frozen": bool(getattr(sys, "frozen", False)),
        "verticals": {
            "expected": list(VERTICALS),
            "loaded": loaded_verticals,
        },
        "domains": {
            "expected": list(BUILTIN_DOMAINS),
            "loaded": loaded_domains,
        },
        "failures": failures,
    }


def _emit_report(report: dict[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def _system_exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(str(exc.code), file=sys.stderr, flush=True)
    return 1


def _python_compat_entrypoint(argv: list[str]) -> tuple[bool, int]:
    """Implement the Python invocation subset exposed by ARGUS_SKILL_PYTHON.

    A one-folder PyInstaller build has no standalone ``python.exe``. Argus
    prompts and tools nevertheless rely on ``$ARGUS_SKILL_PYTHON -m ...``.
    The frozen backend therefore acts as the owning interpreter for in-package
    modules, ``-c`` snippets, and explicit Python script paths.
    """
    args = list(argv)
    while args and args[0] in {"-B", "-I", "-s", "-S", "-u"}:
        args.pop(0)
    original_argv = sys.argv[:]
    original_path = sys.path[:]
    try:
        if len(args) >= 2 and args[0] == "-m":
            module = args[1].strip()
            if module != "argus_skill" and not module.startswith("argus_skill."):
                print(
                    f"argus-backend: refusing non-Argus frozen module {module!r}",
                    file=sys.stderr,
                    flush=True,
                )
                return True, 2
            sys.argv = [module, *args[2:]]
            runpy.run_module(module, run_name="__main__", alter_sys=True)
            return True, 0
        if len(args) >= 2 and args[0] == "-c":
            sys.argv = ["-c", *args[2:]]
            namespace = {"__name__": "__main__", "__package__": None}
            exec(compile(args[1], "<string>", "exec"), namespace, namespace)
            return True, 0
        if args and not args[0].startswith("-"):
            script = Path(args[0]).expanduser()
            if script.is_file() and script.suffix.casefold() in {".py", ".pyw"}:
                resolved_script = script.resolve()
                sys.argv = [str(resolved_script), *args[1:]]
                # Match ``python path/to/script.py``: sibling imports resolve
                # from the script directory rather than the backend cwd.
                sys.path.insert(0, str(resolved_script.parent))
                runpy.run_path(str(resolved_script), run_name="__main__")
                return True, 0
    except SystemExit as exc:
        return True, _system_exit_code(exc)
    finally:
        sys.argv = original_argv
        sys.path[:] = original_path
    return False, 0


def _entrypoint() -> int:
    _install_windows_signal_zero_guard()
    if sys.argv[1:] == [_VERIFY_FROZEN_RUNTIME]:
        report = verify_runtime_providers()
        _emit_report(report, error=False)
        return 0 if report["ok"] else 1

    # Also fail fast during normal frozen startup.  This protects builds made
    # without the standard PowerShell build gate from starting a healthy-looking
    # web service whose TEAM routing providers are absent.
    if getattr(sys, "frozen", False):
        report = verify_runtime_providers()
        if not report["ok"]:
            _emit_report(report, error=True)
            return 1

    handled, code = _python_compat_entrypoint(sys.argv[1:])
    if handled:
        return code

    from argus_skill.apps.cli import main

    return int(main())


if __name__ == "__main__":
    sys.exit(_entrypoint())
