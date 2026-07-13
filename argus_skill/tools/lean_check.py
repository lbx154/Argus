"""Structured, fail-closed Lean source checker used by the Math vertical."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

DIVISIBILITY_SMOKE_THEOREM = """\
import Mathlib

theorem dvd_linear_combination
    (a b c m n : Int) (hab : a ∣ b) (hac : a ∣ c) :
    a ∣ (m * b + n * c) := by
  rcases hab with ⟨kb, rfl⟩
  rcases hac with ⟨kc, rfl⟩
  refine ⟨m * kb + n * kc, ?_⟩
  ring
"""

_SYNTAX_PATTERNS = (
    "unexpected token",
    "unexpected identifier",
    "unexpected end",
    "parser error",
    "invalid syntax",
)
_AXIOM_AUDIT_MARKER = "ARGUS_AXIOM_AUDIT_FOUND:"
_AXIOM_AUDIT_PATH = Path(__file__).with_name("lean_axiom_audit.lean")


def audit_lean_tools() -> dict[str, dict[str, Any]]:
    """Return executable path/version facts without installing anything."""
    return {
        name: _tool_info(name)
        for name in ("lean", "lake", "elan")
    }


def run_lean_check(
    source: Path | str,
    *,
    timeout_seconds: float = 30.0,
    lean_bin: str | None = None,
    lake_bin: str | None = None,
    use_lake: bool = False,
) -> dict[str, Any]:
    """Compile one Lean file and return a JSON-serializable result."""
    path = Path(source).expanduser().resolve()
    started = time.monotonic()
    tools = audit_lean_tools()
    if lean_bin:
        tools["lean"] = _tool_info("lean", lean_bin)
    if lake_bin:
        tools["lake"] = _tool_info("lake", lake_bin)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _result(
            "syntax_error",
            path,
            tools=tools,
            stderr=f"cannot read source: {exc}",
            duration_ms=_duration_ms(started),
        )
    proof_holes = find_proof_holes(text)
    if proof_holes:
        return _result(
            "proof_hole",
            path,
            tools=tools,
            proof_holes=proof_holes,
            stderr="Lean source contains a proof hole or local assumption.",
            duration_ms=_duration_ms(started),
        )

    if use_lake:
        executable = _resolve_executable("lake", lake_bin)
        command = [executable, "env", "lean", str(path)] if executable else []
        tool = "lake"
        working_dir = _resolve_lake_workspace(path) or path.parent
    else:
        executable = _resolve_executable("lean", lean_bin)
        command = [executable, str(path)] if executable else []
        tool = "lean"
        working_dir = path.parent
    if not executable:
        return _result(
            "unavailable",
            path,
            tools=tools,
            tool=tool,
            cwd=working_dir,
            stderr=f"{tool} executable is unavailable.",
            duration_ms=_duration_ms(started),
        )
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            cwd=working_dir,
        )
        stdout, stderr = process.communicate(
            timeout=max(0.01, float(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        return _result(
            "timeout",
            path,
            tools=tools,
            tool=tool,
            command=command,
            cwd=working_dir,
            stdout=stdout or _text(exc.stdout),
            stderr=stderr or _text(exc.stderr),
            duration_ms=_duration_ms(started),
        )
    except OSError as exc:
        return _result(
            "unavailable",
            path,
            tools=tools,
            tool=tool,
            command=command,
            cwd=working_dir,
            stderr=str(exc),
            duration_ms=_duration_ms(started),
        )
    output = f"{stdout}\n{stderr}".lower()
    compiler_hole = any(
        marker in output
        for marker in (
            "declaration uses 'sorry'",
            "declaration uses sorry",
            "declaration contains 'sorry'",
        )
    )
    if compiler_hole:
        status = "proof_hole"
        proof_holes = [{"kind": "compiler_warning", "line": None}]
    elif process.returncode == 0:
        status = "success"
    elif any(pattern in output for pattern in _SYNTAX_PATTERNS):
        status = "syntax_error"
    else:
        status = "type_error"
    audit_command: list[str] = []
    audit_exit_code: int | None = None
    audit_stdout = ""
    audit_stderr = ""
    if status == "success":
        audit_command = (
            [executable, "env", "lean", "--run", str(_AXIOM_AUDIT_PATH), str(path)]
            if use_lake
            else [executable, "--run", str(_AXIOM_AUDIT_PATH), str(path)]
        )
        try:
            audit_process = subprocess.Popen(
                audit_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                cwd=working_dir,
            )
            audit_stdout, audit_stderr = audit_process.communicate(
                timeout=max(0.01, float(timeout_seconds)),
            )
            audit_exit_code = audit_process.returncode
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(audit_process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            audit_stdout, audit_stderr = audit_process.communicate()
            return _result(
                "timeout",
                path,
                tools=tools,
                tool=tool,
                command=command,
                cwd=working_dir,
                exit_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                audit_command=audit_command,
                audit_stdout=audit_stdout or _text(exc.stdout),
                audit_stderr=audit_stderr or _text(exc.stderr),
                duration_ms=_duration_ms(started),
            )
        except OSError as exc:
            return _result(
                "unavailable",
                path,
                tools=tools,
                tool=tool,
                command=command,
                cwd=working_dir,
                exit_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                audit_command=audit_command,
                audit_stderr=str(exc),
                duration_ms=_duration_ms(started),
            )
        audit_output = f"{audit_stdout}\n{audit_stderr}"
        audited_axioms = [
            line.split(_AXIOM_AUDIT_MARKER, 1)[1].strip()
            for line in audit_output.splitlines()
            if _AXIOM_AUDIT_MARKER in line
        ]
        if audited_axioms:
            status = "proof_hole"
            proof_holes = [
                {
                    "kind": "environment_axiom",
                    "line": None,
                    "declaration": name,
                }
                for name in audited_axioms
            ]
        elif audit_exit_code != 0:
            status = "unavailable"
    return _result(
        status,
        path,
        tools=tools,
        tool=tool,
        command=command,
        cwd=working_dir,
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        proof_holes=proof_holes,
        audit_command=audit_command,
        audit_exit_code=audit_exit_code,
        audit_stdout=audit_stdout,
        audit_stderr=audit_stderr,
        duration_ms=_duration_ms(started),
    )


def find_proof_holes(source: str) -> list[dict[str, Any]]:
    """Find real ``sorry``/``admit`` identifiers with a small Lean lexer."""
    holes: list[dict[str, Any]] = []
    index = 0
    line = 1
    block_depth = 0
    in_string = False
    escaped = False
    while index < len(source):
        current = source[index]
        pair = source[index : index + 2]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                index += 2
                continue
            if pair == "-/":
                block_depth -= 1
                index += 2
                continue
            if current == "\n":
                line += 1
            index += 1
            continue
        if in_string:
            if current == "\n":
                line += 1
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                in_string = False
            index += 1
            continue
        if pair == "--":
            newline = source.find("\n", index + 2)
            if newline < 0:
                break
            line += 1
            index = newline + 1
            continue
        if pair == "/-":
            block_depth = 1
            index += 2
            continue
        raw_end = _raw_string_end(source, index)
        if raw_end is not None:
            line += source.count("\n", index, raw_end)
            index = raw_end
            continue
        if current == "«":
            end = source.find("»", index + 1)
            if end < 0:
                line += source.count("\n", index)
                break
            line += source.count("\n", index, end + 1)
            index = end + 1
            continue
        if current == '"':
            in_string = True
            index += 1
            continue
        if current.isalpha() or current == "_":
            end = index + 1
            while end < len(source) and (
                source[end].isalnum() or source[end] in {"_", "'"}
            ):
                end += 1
            token = source[index:end]
            if token in {"sorry", "admit", "axiom", "constant"}:
                holes.append({"kind": token, "line": line})
            index = end
            continue
        if current == "\n":
            line += 1
        index += 1
    return holes


def _raw_string_end(source: str, index: int) -> int | None:
    if source[index] != "r":
        return None
    cursor = index + 1
    while cursor < len(source) and source[cursor] == "#":
        cursor += 1
    if cursor >= len(source) or source[cursor] != '"':
        return None
    delimiter = '"' + ("#" * (cursor - index - 1))
    end = source.find(delimiter, cursor + 1)
    return len(source) if end < 0 else end + len(delimiter)


def _tool_info(name: str, path_override: str | None = None) -> dict[str, Any]:
    path = _resolve_executable(name, path_override)
    if not path:
        return {"available": False, "path": None, "version": ""}
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        version = (result.stdout or result.stderr).strip()[:500]
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    return {"available": True, "path": path, "version": version}


def _resolve_executable(name: str, override: str | None = None) -> str | None:
    if override:
        candidate = Path(override).expanduser()
        if (
            (candidate.is_absolute() or candidate.parent != Path("."))
            and candidate.is_file()
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate.resolve())
        return shutil.which(override)

    on_path = shutil.which(name)
    if on_path:
        return on_path
    configured_home = os.environ.get("ELAN_HOME", "").strip()
    elan_home = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".elan"
    )
    candidate = elan_home / "bin" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    return None


def _resolve_lake_workspace(source: Path) -> Path | None:
    for directory in (source.parent, *source.parent.parents):
        if _is_lake_workspace(directory):
            return directory

    configured = os.environ.get("ARGUS_SKILL_MATHLIB_WORKSPACE", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(
        Path.home() / ".local" / "share" / "argus-skill" / "mathlib"
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if _is_lake_workspace(resolved):
            return resolved
    return None


def _is_lake_workspace(path: Path) -> bool:
    return (path / "lakefile.toml").is_file() or (path / "lakefile.lean").is_file()


def _result(
    status: str,
    source: Path,
    *,
    tools: dict[str, Any],
    tool: str = "",
    command: Sequence[str] = (),
    cwd: Path | str | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    proof_holes: list[dict[str, Any]] | None = None,
    audit_command: Sequence[str] = (),
    audit_exit_code: int | None = None,
    audit_stdout: str = "",
    audit_stderr: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "source": str(source),
        "tool": tool,
        "tools": tools,
        "command": list(command),
        "cwd": str(cwd or ""),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "proof_holes": list(proof_holes or []),
        "audit_command": list(audit_command),
        "audit_exit_code": audit_exit_code,
        "audit_stdout": audit_stdout,
        "audit_stderr": audit_stderr,
        "duration_ms": duration_ms,
    }


def _duration_ms(started: float) -> int:
    return max(0, int(round((time.monotonic() - started) * 1000)))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile and audit one Lean file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--lean-bin")
    parser.add_argument("--lake-bin")
    parser.add_argument("--lake", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_lean_check(
        args.source,
        timeout_seconds=args.timeout,
        lean_bin=args.lean_bin,
        lake_bin=args.lake_bin,
        use_lake=args.lake,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DIVISIBILITY_SMOKE_THEOREM",
    "audit_lean_tools",
    "find_proof_holes",
    "main",
    "run_lean_check",
]
