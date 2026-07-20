"""Validate an Engineer-authored, project-local research platform.

Argus deliberately does not prescribe Python packages, datasets, evaluators, or
experiment runners.  The Engineer writes ``research/PLATFORM_SPEC.json`` for the
current project; this tool executes its real smoke probes without a shell and
persists one typed report.  A failed probe is a platform failure, not scientific
evidence about the research idea.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus_skill.core.secret_guard import redact_secrets_text

SCHEMA_VERSION = 1
DEFAULT_SPEC = Path("research/PLATFORM_SPEC.json")
DEFAULT_REPORT = Path("research/PLATFORM_STATUS.json")
_MAX_CAPTURE_CHARS = 8_000


class ResearchPlatformError(RuntimeError):
    """Raised when the platform specification itself is invalid."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchPlatformError(f"cannot read platform spec {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchPlatformError("platform spec must be a JSON object")
    return value


def _project_path(root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    candidate = path if path.is_absolute() else root / path
    return candidate.resolve()


def _safe_text(value: str) -> str:
    text = redact_secrets_text(str(value or ""))
    return text[-_MAX_CAPTURE_CHARS:]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _validate_command(raw: Any, *, probe_name: str) -> list[str]:
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(part, str) or not part for part in raw)
    ):
        raise ResearchPlatformError(
            f"probe {probe_name!r} command must be a non-empty string array"
        )
    return list(raw)


def _validate_env(raw: Any, *, probe_name: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw.items()
    ):
        raise ResearchPlatformError(
            f"probe {probe_name!r} env must map strings to strings"
        )
    return dict(raw)


def validate_spec(spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ResearchPlatformError(
            f"platform spec schema_version must be {SCHEMA_VERSION}"
        )
    raw_artifacts = spec.get("required_artifacts", [])
    raw_probes = spec.get("probes", [])
    if not isinstance(raw_artifacts, list) or not isinstance(raw_probes, list):
        raise ResearchPlatformError("required_artifacts and probes must be arrays")
    artifacts: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_artifacts):
        if not isinstance(raw, dict) or not str(raw.get("path") or "").strip():
            raise ResearchPlatformError(f"required_artifacts[{index}] needs path")
        artifacts.append({
            "path": str(raw["path"]),
            "kind": str(raw.get("kind") or "artifact"),
        })
    probes: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_probes):
        if not isinstance(raw, dict):
            raise ResearchPlatformError(f"probes[{index}] must be an object")
        name = str(raw.get("name") or "").strip()
        if not name or name in names:
            raise ResearchPlatformError(f"probe name missing or duplicated: {name!r}")
        names.add(name)
        timeout = int(raw.get("timeout_seconds", 120))
        if timeout < 1 or timeout > 86_400:
            raise ResearchPlatformError(
                f"probe {name!r} timeout_seconds must be in [1, 86400]"
            )
        probes.append({
            "name": name,
            "command": _validate_command(raw.get("command"), probe_name=name),
            "env": _validate_env(raw.get("env"), probe_name=name),
            "timeout_seconds": timeout,
            "expected_exit_codes": [
                int(code) for code in raw.get("expected_exit_codes", [0])
            ],
        })
    if not probes:
        raise ResearchPlatformError("platform spec needs at least one real probe")
    return artifacts, probes


def doctor(
    *,
    project_root: Path,
    spec_path: Path = DEFAULT_SPEC,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    root = project_root.resolve()
    spec_file = _project_path(root, spec_path)
    report_file = _project_path(root, report_path)
    spec = _read_object(spec_file)
    artifacts, probes = validate_spec(spec)
    started = time.time()
    artifact_rows: list[dict[str, Any]] = []
    for item in artifacts:
        path = _project_path(root, item["path"])
        artifact_rows.append({
            **item,
            "exists": path.exists(),
            "resolved_path": str(path),
        })
    probe_rows: list[dict[str, Any]] = []
    for probe in probes:
        probe_started = time.time()
        env = dict(os.environ)
        env.update(probe["env"])
        try:
            result = subprocess.run(
                probe["command"],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=probe["timeout_seconds"],
            )
            exit_code: int | None = int(result.returncode)
            timed_out = False
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            timed_out = True
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
        probe_rows.append({
            "name": probe["name"],
            "command": probe["command"],
            "timeout_seconds": probe["timeout_seconds"],
            "expected_exit_codes": probe["expected_exit_codes"],
            "exit_code": exit_code,
            "timed_out": timed_out,
            "passed": (
                not timed_out and exit_code in probe["expected_exit_codes"]
            ),
            "duration_seconds": round(time.time() - probe_started, 3),
            "stdout_tail": _safe_text(stdout),
            "stderr_tail": _safe_text(stderr),
        })
    passed = all(row["exists"] for row in artifact_rows) and all(
        row["passed"] for row in probe_rows
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "platform_id": str(spec.get("platform_id") or root.name),
        "status": "PASS_RESEARCH_PLATFORM" if passed else "FAIL_RESEARCH_PLATFORM",
        "classification": "platform_ready" if passed else "platform_failure",
        "scientific_evidence": False,
        "repair_owner": None if passed else "engineer",
        "project_root": str(root),
        "spec_path": str(spec_file),
        "spec_sha256": hashlib.sha256(spec_file.read_bytes()).hexdigest(),
        "required_artifacts": artifact_rows,
        "probes": probe_rows,
        "started_at_unix": started,
        "duration_seconds": round(time.time() - started, 3),
    }
    _atomic_json(report_file, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus_skill.tools.research_platform")
    parser.add_argument("command", choices=("doctor",))
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        payload = doctor(
            project_root=args.project_root,
            spec_path=args.spec,
            report_path=args.report,
        )
    except ResearchPlatformError as exc:
        print(json.dumps({"status": "INVALID_PLATFORM_SPEC", "error": str(exc)}))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS_RESEARCH_PLATFORM" else 1


if __name__ == "__main__":
    raise SystemExit(main())
