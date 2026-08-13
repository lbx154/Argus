"""Standalone Argus bootstrap diagnostics using only the Python standard library."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
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
        result = subprocess.run(
            [str(executable), flag],
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

    runtime = _venv_python(checkout)
    findings.append(_finding(
        "ARGUS-PYTHON-002",
        "Argus virtual environment",
        runtime is not None,
        str(runtime) if runtime is not None else "checkout .venv Python is missing",
        "recreate .venv with Python 3.11+ and reinstall the checkout",
    ))
    if runtime is not None:
        try:
            result = subprocess.run(
                [str(runtime), "-c", "import argus_skill; print(argus_skill.__version__)"],
                cwd=str(checkout),
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
            "ARGUS-PYTHON-003",
            "Argus Core import",
            import_ok,
            detail,
            "run the checkout's Python with `-m pip install -e .` after reviewing the environment",
        ))

    for code, name in (("ARGUS-GIT-001", "git"), ("ARGUS-NODE-001", "node")):
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

    return {
        "schema_version": 1,
        "mode": "bootstrap",
        "ok": all(item["ok"] for item in findings),
        "target_host": platform.node(),
        "findings": findings,
    }


def _render(report):
    lines = ["argus-doctor — bootstrap diagnostics", ""]
    for item in report["findings"]:
        lines.append(f"{'✓' if item['ok'] else '✗'} {item['code']} {item['name']}: {item['detail']}")
        if item["fix"]:
            lines.append(f"    fix: {item['fix']}")
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
    args = parser.parse_args(argv)
    report = run_bootstrap_doctor(args.root)
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
