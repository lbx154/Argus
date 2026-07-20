from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from argus_skill.tools.research_platform import doctor


def _write_spec(root: Path, *, command: list[str]) -> None:
    (root / "research").mkdir()
    (root / "code").mkdir()
    (root / "code" / "runner.py").write_text("print('runner-ready')\n")
    (root / "research" / "PLATFORM_SPEC.json").write_text(
        json.dumps({
            "schema_version": 1,
            "platform_id": "test-platform",
            "required_artifacts": [{"path": "code/runner.py", "kind": "runner"}],
            "probes": [{
                "name": "real-smoke",
                "command": command,
                "timeout_seconds": 10,
            }],
        })
    )


def test_doctor_executes_real_platform_probe(tmp_path: Path) -> None:
    _write_spec(tmp_path, command=[sys.executable, "code/runner.py"])
    result = doctor(project_root=tmp_path)
    assert result["status"] == "PASS_RESEARCH_PLATFORM"
    assert result["classification"] == "platform_ready"
    assert result["scientific_evidence"] is False
    assert result["probes"][0]["stdout_tail"].strip() == "runner-ready"
    stored = json.loads((tmp_path / "research" / "PLATFORM_STATUS.json").read_text())
    assert stored["spec_sha256"] == result["spec_sha256"]


def test_failed_probe_is_platform_failure_not_method_failure(tmp_path: Path) -> None:
    _write_spec(
        tmp_path,
        command=[sys.executable, "-c", "import sys; print('missing-runtime'); sys.exit(7)"],
    )
    result = doctor(project_root=tmp_path)
    assert result["status"] == "FAIL_RESEARCH_PLATFORM"
    assert result["classification"] == "platform_failure"
    assert result["repair_owner"] == "engineer"
    assert result["scientific_evidence"] is False
    assert result["probes"][0]["exit_code"] == 7


def test_cli_runs_real_smoke_and_returns_zero(tmp_path: Path) -> None:
    _write_spec(tmp_path, command=[sys.executable, "code/runner.py"])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.tools.research_platform",
            "doctor",
            "--project-root",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "PASS_RESEARCH_PLATFORM"
