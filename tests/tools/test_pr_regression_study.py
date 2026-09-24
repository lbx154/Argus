import json
import subprocess
import sys
from pathlib import Path

import pytest

from argus.release_tools.pr_gate.regression.egress_proxy import allowed_destination
from argus.release_tools.pr_gate.regression.runtime import WORKER_FILES, safe_file
from argus.release_tools.pr_gate.regression.study import freeze_runner, sample
from argus.release_tools.pr_gate.regression.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]


def report_fixture(tmp_path):
    metadata = {"pr_number": 1, "base_sha": "a" * 40, "candidate_sha": "b" * 40}
    report = dict(metadata, **{
        "schema_version": "pr-regression-study/v1",
        "verdict": "NO_REGRESSION_FOUND",
        "analysis_mode": "short_circuit",
        "summary": "Explanatory text only; no runtime consumer identified.",
        "inspected_paths": ["README.md"],
        "short_circuit_reason": "Updated an external documentation link.",
        "findings": [],
        "tests": [],
        "limitations": ["Static scope only; not universal safety."],
        "escalation_required": False,
    })
    schema = json.loads((ROOT / "argus/release_tools/pr_gate/regression/report.schema.json").read_text())
    (tmp_path / "REPORT.md").write_text("Bounded static report.")
    return metadata, report, schema


def test_temporal_sampling_is_deterministic_and_covers_whole_history():
    population = [
        {"pr_number": n, "merged_at": f"{n:04}"}
        for n in range(1, 126)
    ]
    first = sample(population, size=50, seed=20260916)
    assert first == sample(population[::-1], size=50, seed=20260916)
    assert len({row["pr_number"] for row in first}) == 50
    assert all(sum(row["temporal_stratum"] == i for row in first) == 10 for i in range(1, 6))


@pytest.mark.parametrize("script", ["run.py", "study_summary.py"])
def test_frozen_runner_is_self_contained_without_an_argus_install(tmp_path, script):
    freeze_runner(tmp_path)
    assert (tmp_path / "SKILL.md").is_file()
    assert all((tmp_path / "runner" / name).is_file() for name in WORKER_FILES)
    result = subprocess.run(
        [sys.executable, "-c", """
import runpy
import sys

class RejectArgus:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "argus" or fullname.startswith("argus."):
            raise AssertionError("Frozen study must not import installed Argus")

sys.meta_path.insert(0, RejectArgus())
sys.path.insert(0, sys.argv[1])
sys.argv = [sys.argv[2], "--help"]
runpy.run_path(sys.argv[0], run_name="__main__")
""", str(tmp_path / "runner"), str(tmp_path / "runner" / script)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_short_circuit_needs_no_test_case(tmp_path):
    metadata, report, schema = report_fixture(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps(report))
    assert validate_report(tmp_path, schema, metadata)["tests"] == []


def test_regression_requires_paired_witness(tmp_path):
    metadata, report, schema = report_fixture(tmp_path)
    report.update(verdict="REGRESSION", analysis_mode="targeted")
    report["findings"] = [{
        "id": "H1", "status": "CONFIRMED_REGRESSION", "hypothesis": "Lost stop signal",
        "property_source": "Existing operation contract", "source_locations": ["runner.py:5"],
        "trigger_reached": True, "base_property_holds": True, "candidate_property_holds": False,
        "explanation": "A hypothesis without an execution receipt.", "probe_ids": [],
    }]
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="paired reached witness"):
        validate_report(tmp_path, schema, metadata)


def test_unresolved_hypothesis_is_not_a_pass(tmp_path):
    metadata, report, schema = report_fixture(tmp_path)
    report["analysis_mode"] = "static"
    report["findings"] = [{
        "id": "H1", "status": "UNKNOWN", "hypothesis": "Changed prompt behavior",
        "property_source": "Task completion contract", "source_locations": ["SKILL.md:5"],
        "trigger_reached": None, "base_property_holds": None, "candidate_property_holds": None,
        "explanation": "Requires a live model.", "probe_ids": [],
    }]
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Unresolved hypotheses"):
        validate_report(tmp_path, schema, metadata)


def test_host_never_follows_worker_artifact_symlinks(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    private = tmp_path / "not-mounted.txt"
    private.write_text("not available to worker")
    (work / "report.json").symlink_to(private)
    with pytest.raises(ValueError, match="escaping"):
        safe_file(work, "report.json")


def test_container_absolute_evidence_path_maps_only_inside_assigned_root(tmp_path):
    evidence = tmp_path / "evidence/probe"
    evidence.mkdir(parents=True)
    receipt = evidence / "receipt.json"
    receipt.write_text("{}")
    assert safe_file(tmp_path, "/work/evidence/probe/receipt.json") == receipt
    for unsafe in ("/etc/passwd", "/work/../../etc/passwd", "../other/receipt.json",
                   "/work-neighbor/evidence/probe/receipt.json"):
        with pytest.raises(ValueError, match="escaping"):
            safe_file(tmp_path, unsafe)


def test_uncertainty_can_preserve_a_supported_local_regression(tmp_path):
    import hashlib

    metadata, report, schema = report_fixture(tmp_path)
    evidence = tmp_path / "evidence/probe"
    evidence.mkdir(parents=True)
    receipt = dict(metadata, id="probe")
    for label in ("base", "candidate"):
        log = evidence / f"{label}.log"
        log.write_text(f"{label} observation")
        receipt[label] = {
            "command": "fixture-only", "cwd": f"/work/{label}",
            "exit_code": 0 if label == "base" else 1, "seconds": 0.01,
            "log": f"evidence/probe/{label}.log",
            "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "timed_out": False,
        }
    (evidence / "receipt.json").write_text(json.dumps(receipt))
    report.update(verdict="UNCERTAIN", analysis_mode="targeted", short_circuit_reason=None)
    report["tests"] = [{
        "probe_id": "probe", "receipt_path": "/work/evidence/probe/receipt.json",
        "interpretation": "Local witness, with other model-dependent behavior unresolved.",
    }]
    report["findings"] = [{
        "id": "H1", "status": "CONFIRMED_REGRESSION", "hypothesis": "Lost stop signal",
        "property_source": "Existing operation contract", "source_locations": ["runner.py:5"],
        "trigger_reached": True, "base_property_holds": True, "candidate_property_holds": False,
        "explanation": "Local reached observation.", "probe_ids": ["probe"],
    }]
    (tmp_path / "report.json").write_text(json.dumps(report))
    assert validate_report(tmp_path, schema, metadata)["verdict"] == "UNCERTAIN"
    report["verdict"] = "NO_REGRESSION_FOUND"
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="contradicts"):
        validate_report(tmp_path, schema, metadata)


@pytest.mark.parametrize("authority", [
    "127.0.0.1:443", "169.254.169.254:443", "host.docker.internal:443",
    "github.com:22", "github.com.evil.invalid:443", "api.github.com",
])
def test_proxy_rejects_nonservice_and_private_destinations(authority):
    assert allowed_destination(authority) is None


def test_proxy_allows_copilot_https_only():
    assert allowed_destination("api.individual.githubcopilot.com:443") == "api.individual.githubcopilot.com"
