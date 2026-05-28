from __future__ import annotations

import subprocess

from .models import CheckResult


def run_checks(commands: list[str], timeout_seconds: int) -> list[CheckResult]:
    results: list[CheckResult] = []
    for command in commands:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        merged = _merge_output(completed.stdout, completed.stderr)
        results.append(
            CheckResult(
                command=command,
                exit_code=completed.returncode,
                passed=completed.returncode == 0,
                output_tail=_tail_text(merged, max_chars=1800),
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
