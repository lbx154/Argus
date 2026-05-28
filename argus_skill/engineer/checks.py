from __future__ import annotations

import subprocess
import sys

from ..core.models import CheckResult

CHECK_OUTPUT_TAIL_CHARS = 60000


def run_checks(
    commands: list[str],
    timeout_seconds: int,
    *,
    cwd: str | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    argus_python = sys.executable
    for command in commands:
        resolved = command.replace("{argus_python}", argus_python)
        completed = subprocess.run(
            resolved,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd,
        )
        merged = _merge_output(completed.stdout, completed.stderr)
        results.append(
            CheckResult(
                command=resolved,
                exit_code=completed.returncode,
                passed=completed.returncode == 0,
                output_tail=_tail_text(merged, max_chars=CHECK_OUTPUT_TAIL_CHARS),
            )
        )
    return results


def summarize_checks(results: list[CheckResult]) -> str:
    if not results:
        return "No acceptance checks configured."
    lines: list[str] = []
    for item in results:
        status = "PASS" if item.passed else "FAIL"
        lines.append(f"- [{status}] `{item.command}` (exit={item.exit_code})")
        if item.output_tail:
            lines.append(f"  tail: {item.output_tail}")
    return "\n".join(lines)


def all_checks_passed(results: list[CheckResult]) -> bool:
    return all(item.passed for item in results)


def _merge_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return stdout + "\n" + stderr
    return stdout or stderr or ""


def _tail_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text.strip()
    return text[-max_chars:].strip()
