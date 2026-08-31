"""Standalone Argus bootstrap diagnostics using only the Python standard library."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def _finding(code, name, ok, detail, fix=""):
    return {
        "code": code,
        "name": name,
        "ok": bool(ok),
        "detail": str(detail),
        "fix": "" if ok else str(fix),
    }


def _checkout(path):
    candidate = Path(path).expanduser().resolve()
    return candidate if (candidate / "pyproject.toml").is_file() and (candidate / "argus_skill").is_dir() else None


def _find_checkout(explicit):
    if explicit:
        return _checkout(explicit)
    candidates = [
        os.environ.get("ARGUS_DESKTOP_REPO_ROOT", ""),
        Path(__file__).resolve().parent,
        Path.cwd(),
        Path.home() / "Argus",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        found = _checkout(candidate)
        if found is not None:
            return found
    return None


def _venv_python(root):
    if root is None:
        return None
    relative = Path(".venv/Scripts/python.exe") if os.name == "nt" else Path(".venv/bin/python")
    candidate = root / relative
    return candidate if candidate.is_file() else None


def _command_version(executable, flag="--version"):
    try:
        command = (
            [str(executable), "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]
            if Path(str(executable)).stem.casefold() == "powershell"
            else [str(executable), flag]
        )
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    text = (result.stdout or result.stderr).strip().splitlines()
    return result.returncode == 0, text[0] if text else f"exit {result.returncode}"


def run_bootstrap_doctor(root=None):
    checkout = _find_checkout(root)
    findings = []
    findings.append(_finding(
        "ARGUS-HOST-001",
        "host",
        True,
        f"{platform.system()} {platform.release()} {platform.machine()}",
    ))
    python_ok = sys.version_info >= (3, 11)
    findings.append(_finding(
        "ARGUS-PYTHON-001",
        "bootstrap Python",
        python_ok,
        f"{platform.python_version()} at {sys.executable}",
        "install Python 3.11 or newer using the platform's official installer",
    ))
    findings.append(_finding(
        "ARGUS-INSTALL-001",
        "source checkout",
        checkout is not None,
        str(checkout) if checkout is not None else "Argus source checkout not found",
        "pass --root <Argus checkout>, or restore the checkout before running full Doctor",
    ))

    checkout_runtime = _venv_python(checkout)
    runtime = checkout_runtime or Path(sys.executable)
    try:
        result = subprocess.run(
            [str(runtime), "-c", "import argus_skill; print(argus_skill.__version__)"],
            cwd=str(checkout) if checkout is not None else None,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        import_ok = result.returncode == 0
        detail = (result.stdout or result.stderr).strip() or f"exit {result.returncode}"
    except (OSError, subprocess.SubprocessError) as exc:
        import_ok = False
        detail = f"{type(exc).__name__}: {exc}"
    findings.append(_finding(
        "ARGUS-PYTHON-002",
        "Argus Python environment",
        checkout_runtime is not None or import_ok,
        (
            str(checkout_runtime) if checkout_runtime is not None
            else f"current interpreter is usable without checkout .venv (bootstrap fallback): {runtime}"
            if import_ok else "checkout .venv is missing and current interpreter cannot import Argus"
        ),
        "recreate .venv with Python 3.11+ and reinstall the checkout",
    ))
    findings.append(_finding(
        "ARGUS-PYTHON-003",
        "Argus Core import",
        import_ok,
        detail,
        "run the selected Python with `-m pip install -e .` after reviewing the environment",
    ))

    commands = [("ARGUS-GIT-001", "git"), ("ARGUS-NODE-001", "node")]
    if os.name == "nt":
        commands.append(("ARGUS-POWERSHELL-001", "powershell"))
    for code, name in commands:
        executable = shutil.which(name)
        ok, detail = _command_version(executable) if executable else (False, f"{name} not found on PATH")
        findings.append(_finding(
            code,
            name,
            bool(executable) and ok,
            detail,
            f"install a supported {name} release and ensure it is on PATH",
        ))

    if checkout is not None:
        assets = {
            "Web": checkout / "frontend" / "web" / "dist" / "index.html",
            "TUI": checkout / "frontend" / "tui" / "bundle" / "argus.mjs",
        }
        missing = [label for label, path in assets.items() if not path.is_file()]
        findings.append(_finding(
            "ARGUS-WEB-001",
            "frontend assets",
            not missing,
            "Web and TUI assets present" if not missing else f"missing: {', '.join(missing)}",
            "restore a complete release checkout or rebuild the declared frontend assets",
        ))
        desktop = checkout / "desktop-tauri"
        if (desktop / "package.json").is_file():
            tauri_cli = desktop / "node_modules" / "@tauri-apps" / "cli" / "tauri.js"
            rust_manifest = desktop / "src-tauri" / "Cargo.toml"
            ready = tauri_cli.is_file() and rust_manifest.is_file()
            findings.append(_finding(
                "ARGUS-DESKTOP-001",
                "Tauri Desktop runtime",
                True,
                (
                    "Tauri development runtime present"
                    if ready
                    else "Tauri dependencies not installed (optional for CLI/Web)"
                ),
            ))

    web_host = os.environ.get("ARGUS_SKILL_WEB_HOST", "127.0.0.1")
    try:
        web_port = int(os.environ.get("ARGUS_SKILL_WEB_PORT", "8799"))
    except ValueError:
        web_port = 8799
    try:
        with socket.create_connection((web_host, web_port), timeout=0.4):
            web_status = "listener reachable"
    except OSError:
        web_status = "no listener (normal when Argus is stopped)"
    findings.append(_finding(
        "ARGUS-PORT-001",
        "Web endpoint",
        True,
        f"{web_host}:{web_port} — {web_status}",
    ))

    return {
        "schema_version": 1,
        "mode": "bootstrap",
        "ok": all(item["ok"] for item in findings),
        "target_host": platform.node(),
        "findings": findings,
    }


def _run_repair_command(command, *, cwd, timeout):
    try:
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "applied" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "detail": (completed.stderr or completed.stdout or "").strip()[-4000:],
    }


def run_bootstrap_repair(root, *, install=False, desktop=False):
    """Execute only explicit bootstrap actions from this closed registry."""
    checkout = _find_checkout(root)
    if checkout is None:
        return [{
            "id": "restore_checkout",
            "risk": "manual",
            "status": "manual_required",
            "detail": "a complete checkout is required before bootstrap repair",
        }]
    actions = []
    runtime = _venv_python(checkout)
    if install:
        if runtime is None:
            created = _run_repair_command(
                [sys.executable, "-m", "venv", str(checkout / ".venv")],
                cwd=checkout,
                timeout=180,
            )
            actions.append({"id": "create_venv", "risk": "consent", **created})
            runtime = _venv_python(checkout)
        if runtime is None:
            actions.append({
                "id": "install_editable",
                "risk": "consent",
                "status": "failed",
                "detail": "virtual-environment interpreter is unavailable",
            })
        else:
            installed = _run_repair_command(
                [runtime, "-m", "pip", "install", "-e", str(checkout)],
                cwd=checkout,
                timeout=600,
            )
            actions.append({"id": "install_editable", "risk": "consent", **installed})
    if desktop:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        package = checkout / "desktop-tauri" / "package.json"
        if not npm or not package.is_file():
            actions.append({
                "id": "install_desktop",
                "risk": "consent",
                "status": "failed",
                "detail": "npm or desktop-tauri/package.json is unavailable",
            })
        else:
            installed = _run_repair_command(
                [npm, "ci"], cwd=package.parent, timeout=900,
            )
            actions.append({"id": "install_desktop", "risk": "consent", **installed})
    return actions


def _render(report):
    lines = ["argus-doctor — bootstrap diagnostics", ""]
    for item in report["findings"]:
        lines.append(f"{'✓' if item['ok'] else '✗'} {item['code']} {item['name']}: {item['detail']}")
        if item["fix"]:
            lines.append(f"    fix: {item['fix']}")
    if report.get("repairs"):
        lines.extend(["", "repairs:"])
        for action in report["repairs"]:
            lines.append(
                f"  {action.get('status', 'unknown')}: "
                f"{action.get('id', 'unknown')} ({action.get('risk', 'manual')})"
            )
    lines.append("")
    lines.append("all bootstrap checks passed" if report["ok"] else "bootstrap issues found")
    return "\n".join(lines)


def main(argv=None):
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="argus-doctor")
    parser.add_argument("--root", help="Argus source checkout to inspect")
    parser.add_argument("--json", action="store_true", help="print machine-readable findings")
    parser.add_argument(
        "--repair-install",
        action="store_true",
        help="recreate the checkout venv when missing and reinstall editable Argus",
    )
    parser.add_argument(
        "--repair-desktop",
        action="store_true",
        help="run the locked Tauri Desktop npm install",
    )
    parser.add_argument("--yes", action="store_true", help="authorize bootstrap mutations")
    args = parser.parse_args(argv)
    if (args.repair_install or args.repair_desktop) and not args.yes:
        sys.stderr.write("argus-doctor: bootstrap repair requires --yes\n")
        return 3
    repairs = run_bootstrap_repair(
        args.root,
        install=args.repair_install,
        desktop=args.repair_desktop,
    ) if (args.repair_install or args.repair_desktop) else []
    report = run_bootstrap_doctor(args.root)
    if repairs:
        report["repairs"] = repairs
        report["repair_ok"] = all(item.get("status") == "applied" for item in repairs)
        report["ok"] = bool(report["ok"] and report["repair_ok"])
    output = (
        json.dumps(report, ensure_ascii=False, indent=2)
        if args.json
        else _render(report)
    )
    buffer = getattr(sys.stdout, "buffer", None)
    if os.name == "nt" and buffer is not None:
        buffer.write((output + "\n").encode("utf-8"))
        buffer.flush()
    else:
        print(output)
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
