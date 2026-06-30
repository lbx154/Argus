from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..core.models import CheckResult

# A failing acceptance check's FULL output (nvcc/CUTLASS build log, traceback,
# numeric-mismatch diff) is written to disk and the prompt carries only the
# absolute path — codex greps/seds the file on demand (give-access, not
# pre-chew). These bounds are the INLINE FALLBACK, used only when there is no
# workdir to persist a log file.
CHECK_OUTPUT_HEAD_CHARS = 4_000
CHECK_OUTPUT_TAIL_CHARS = 16_000


def head_tail_window(
    text: str,
    *,
    head: int = CHECK_OUTPUT_HEAD_CHARS,
    tail: int = CHECK_OUTPUT_TAIL_CHARS,
) -> str:
    """Bounded head+tail view of *text*.

    Compiler/test errors live at the head (build banner, pytest collection) and
    the tail (traceback, assertion diff, pytest summary), so keeping both ends
    preserves failure-localization while dropping the repetitive middle. Used
    only for the inline fallback when a check's full log could not be persisted
    to disk; the normal path hands codex the on-disk path instead.
    """
    text = (text or "").strip()
    if len(text) <= head + tail:
        return text
    elided = len(text) - head - tail
    return (
        text[:head].strip()
        + f"\n…[{elided} chars elided — full log on disk; grep/sed/less it]…\n"
        + text[-tail:].strip()
    )


def run_checks(
    commands: list[str],
    timeout_seconds: int,
    *,
    cwd: str | None = None,
    artifacts_dir: Path | str | None = None,
) -> list[CheckResult]:
    """Run acceptance-check commands.

    For a FAILING check, when ``artifacts_dir`` is provided, the full merged
    stdout/stderr is written to ``artifacts_dir/NN-<cmd>.log`` and the result's
    ``output_path`` points at it; prompts then carry only that path. ``output_tail``
    still holds the full output in memory for local analysers (gate-failure
    extraction, failure-signature / missing-tool detectors) — it is NOT what gets
    rendered into a codex prompt.
    """
    results: list[CheckResult] = []
    argus_python = sys.executable
    artifacts_path = Path(artifacts_dir) if artifacts_dir is not None else None
    for idx, command in enumerate(commands):
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
        failed = completed.returncode != 0
        output_path = ""
        if failed and artifacts_path is not None and merged.strip():
            output_path = _persist_log(artifacts_path, idx, resolved, merged)
        results.append(
            CheckResult(
                command=resolved,
                exit_code=completed.returncode,
                passed=not failed,
                output_tail=merged.strip(),
                output_path=output_path,
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
        if item.output_path:
            # Path-only: codex reads the full log on demand.
            lines.append(
                f"  full log: {item.output_path}  "
                "(grep/sed/less this file for the complete output)"
            )
        elif item.output_tail:
            # Fallback (no persisted log): inline a bounded head+tail window so
            # the error is never lost, but never dump the full blob.
            lines.append(f"  tail: {head_tail_window(item.output_tail)}")
    return "\n".join(lines)


def all_checks_passed(results: list[CheckResult]) -> bool:
    return all(item.passed for item in results)


def _merge_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return stdout + "\n" + stderr
    return stdout or stderr or ""


def _persist_log(artifacts_dir: Path, idx: int, command: str, merged: str) -> str:
    """Write the full check output to disk; return its absolute path (or "")."""
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = artifacts_dir / f"{idx:02d}-{_slug(command)}.log"
        path.write_text(merged, encoding="utf-8", errors="replace")
        return str(path.resolve())
    except OSError:
        # Persisting is best-effort; on failure we leave output_path empty and
        # the caller falls back to an inline bounded window.
        return ""


def _slug(command: str, *, max_len: int = 48) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", command.strip()).strip("-")
    return (slug[:max_len].rstrip("-") or "check")
