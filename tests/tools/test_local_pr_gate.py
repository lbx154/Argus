from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from argus.release_tools.pr_gate.regression import cli as local_gate
from argus.release_tools.pr_gate.regression import entry
from argus.release_tools.pr_gate.regression import evidence as local_evidence
from argus.release_tools.pr_gate.regression import runner as local_runner
from argus.release_tools.pr_gate.regression.snapshot import (
    REPORT_NAME,
    GateError,
    capture,
    git,
    materialize,
)

IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def commit(repo: Path, message: str) -> None:
    git(repo, "commit", "--quiet", "-m", message, env=IDENTITY)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--quiet", "--template=")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    (root / "app.py").write_text("VALUE = 1\n")
    (root / "README.md").write_text("# Fixture\n")
    git(root, "add", ".")
    commit(root, "base")
    git(root, "checkout", "--quiet", "-b", "feature")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return root


def stage_change(repo):
    (repo / "app.py").write_text("VALUE = 2\n")
    git(repo, "add", "app.py")


def fake_analysis(
    root, metadata, *, isolation, model, timeout,
    verdict="NO_REGRESSION_FOUND", confirmed=False, absolute=False,
):
    """Exercise the real snapshot/receipt envelope, not a real regression oracle."""
    local_runner.stage_native(root, metadata)
    work = root / "work"
    inspected = [
        f"{side}/{path}" for path in metadata["changed_paths"]
        for side in ("base", "candidate") if (work / side / path).is_file()
    ]
    mode = "targeted" if confirmed or verdict == "UNCERTAIN" else "short_circuit"
    report = {
        "schema_version": "pr-regression-study/v1",
        "pr_number": 0,
        "base_sha": metadata["base_sha"], "candidate_sha": metadata["candidate_sha"],
        "verdict": verdict,
        "analysis_mode": mode,
        "summary": "Fixture analysis for runner contract tests.",
        "inspected_paths": inspected,
        "change_coverage": [{
            "paths": metadata["changed_paths"],
            "status": "negligible" if mode == "short_circuit" else "analyzed",
            "reason": "Fixture coverage for testing the report pipeline.",
            "evidence_refs": inspected,
        }],
        "short_circuit_reason": None if confirmed or verdict == "UNCERTAIN" else "Fixture consumer inspection.",
        "findings": [], "tests": [],
        "limitations": ["Fixture output is not a model assessment."],
        "escalation_required": verdict == "UNCERTAIN",
    }
    if confirmed:
        directory = work / "evidence/pair"
        directory.mkdir(parents=True)
        receipt = {key: metadata[key] for key in ("base_sha", "candidate_sha")}
        receipt["id"] = "pair"
        driver = b"# Retained fixture reproducer.\n"
        oracle_copy = directory / "oracle.py"
        oracle_copy.write_bytes(driver)
        oracle = {"tests/_regression_probe/test_example.py": {
            "sha256": hashlib.sha256(driver).hexdigest(),
            "evidence_path": "evidence/pair/oracle.py",
        }}
        receipt["comparison"] = {
            "schema_version": "local-probe-comparison/v2",
            "command_argv": ["python", "tests/_regression_probe/test_example.py"],
            "compatible": True, "issues": [],
            "oracle_files": {"base": oracle, "candidate": oracle},
        }
        for label in ("base", "candidate"):
            log = directory / f"{label}.log"
            log.write_text(f"Fixture observation: {label}\n")
            receipt[label] = {
                "command": "python tests/_regression_probe/test_example.py",
                "exit_code": 0 if label == "base" else 1,
                "cwd": str(work / label),
                "timed_out": False, "seconds": 0.01,
                "log": f"evidence/pair/{label}.log",
                "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            }
            code = (work / label / "app.py").read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(code)).encode() + b"\0" + code).hexdigest()
            source_origin = directory / f"{label}.origin.json"
            source_origin.write_text(json.dumps({
                "schema_version": "probe-source-origin/v1", "status": "verified",
                "source_root": str(work / label), "context_id": f"fixture-{label}",
                "main_pid": 10 if label == "base" else 11,
                "guard_loaded": True, "completed": True, "issues": [],
                "files": [{"path": "app.py", "git_blob": blob, "lf_git_blob": blob}],
            }))
            receipt[label]["process_pid"] = 10 if label == "base" else 11
            receipt[label]["source_origin"] = {
                "path": f"evidence/pair/{label}.origin.json",
                "sha256": hashlib.sha256(source_origin.read_bytes()).hexdigest(),
                "status": "verified",
            }
            probes = work / label / "tests/_regression_probe"
            probes.mkdir(parents=True)
            (probes / "test_example.py").write_bytes(driver)
        (directory / "receipt.json").write_text(json.dumps(receipt))
        report["tests"] = [{
            "probe_id": "pair",
            "receipt_path": str(directory / "receipt.json") if absolute else "evidence/pair/receipt.json",
            "interpretation": "Fixture paired observation.",
        }]
        report["findings"] = [{
            "id": "F1", "status": "CONFIRMED_REGRESSION",
            "hypothesis": "Fixture reached violation", "property_source": "Fixture contract",
            "source_locations": ["app.py:1"], "trigger_reached": True,
            "base_property_holds": True, "candidate_property_holds": False,
            "explanation": "Fixture only.", "probe_ids": ["pair"],
        }]
    (work / "report.json").write_text(json.dumps(report))
    (work / "REPORT.md").write_text("Fixture explanation.\n")
    local_runner.audit_native(work, metadata)
    return {"status": "completed", "isolation": isolation, "source_audit": True,
            "model": model, "copilot_exit_code": 0}


def test_capture_does_not_change_index_or_refs(repo):
    stage_change(repo)
    index = (repo / ".git/index").read_bytes()
    head = git(repo, "rev-parse", "HEAD")
    snap = capture(repo)
    assert snap.base_tree != snap.candidate_tree
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "rev-parse", "HEAD") == head
    assert not (repo / REPORT_NAME).exists()


def test_materialize_keeps_unreferenced_filtered_trees_and_excludes_only_report(repo, tmp_path):
    stage_change(repo)
    (repo / REPORT_NAME).write_text("old report")
    (repo / "pr-regression-report-helper.py").write_text("VALUE = 3\n")
    git(repo, "add", REPORT_NAME, "pr-regression-report-helper.py")
    snap = capture(repo)
    root = tmp_path / "run"
    root.mkdir()
    identities = materialize(snap, root)
    for label in ("base", "candidate"):
        files = git(root / "repository.git", "ls-tree", "-r", "--name-only", identities[f"{label}_sha"]).decode().splitlines()
        assert REPORT_NAME not in files
    assert "pr-regression-report-helper.py" in files
    assert git(root / "repository.git", "show", identities["candidate_sha"] + ":app.py") == b"VALUE = 2\n"


def test_no_changes_short_circuit_without_any_analyzer(repo):
    def forbidden(*args, **kwargs):
        raise AssertionError("No Copilot/Docker invocation should occur")
    report, cache = local_gate.check(repo, analyzer=forbidden)
    assert report["gate"]["status"] == "PASSED"
    assert report["execution"]["isolation"] == "none"
    assert cache is None
    assert local_gate.verify(repo)["status"] == "PASSED"


def test_one_report_stays_fresh_after_code_and_evidence_commit(repo):
    stage_change(repo)
    report, cache = local_gate.check(repo, analyzer=fake_analysis)
    assert report["gate"]["status"] == "PASSED"
    assert cache is not None and not (cache / "repository.git").exists()
    assert not (cache / "work/base").exists()
    git(repo, "add", REPORT_NAME)
    commit(repo, "code and evidence")
    assert local_gate.verify(repo)["status"] == "PASSED"


@pytest.mark.parametrize("staged", [False, True])
def test_source_edit_makes_report_nonpassing(repo, staged):
    stage_change(repo)
    local_gate.check(repo, analyzer=fake_analysis)
    (repo / "app.py").write_text("VALUE = 3\n")
    if staged:
        git(repo, "add", "app.py")
    with pytest.raises(GateError, match="stale|Stage"):
        local_gate.verify(repo)


def test_new_untracked_source_is_not_silently_excluded(repo):
    (repo / "new.py").write_text("NEW = True\n")
    with pytest.raises(GateError, match="Stage"):
        capture(repo)


def test_target_base_advance_invalidates_report_even_without_content_change(repo):
    stage_change(repo)
    local_gate.check(repo, analyzer=fake_analysis)
    old = git(repo, "rev-parse", "main").decode().strip()
    tree = git(repo, "rev-parse", "main^{tree}").decode().strip()
    new = git(repo, "commit-tree", tree, "-p", old, input=b"advance\n", env=IDENTITY).decode().strip()
    git(repo, "update-ref", "refs/heads/main", new)
    with pytest.raises(GateError, match="stale"):
        local_gate.verify(repo)


def test_report_symlink_is_rejected_without_following_it(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("keep")
    (repo / REPORT_NAME).symlink_to(outside)
    with pytest.raises(GateError, match="regular"):
        capture(repo)
    assert outside.read_text() == "keep"


def test_report_exclusion_cannot_be_widened(repo):
    local_gate.check(repo)
    path = repo / REPORT_NAME
    report = json.loads(path.read_text())
    report["binding"]["excluded_paths"] = ["*"]
    path.write_text(json.dumps(report))
    assert local_gate.main(["verify", "--repo", str(repo)]) == 2


def test_policy_change_invalidates_evidence(repo, monkeypatch):
    stage_change(repo)
    local_gate.check(repo, analyzer=fake_analysis)
    old = local_evidence.policy_identity()
    monkeypatch.setattr(local_evidence, "policy_identity", lambda: dict(old, skill_sha256="0" * 64))
    with pytest.raises(GateError, match="Skill"):
        local_gate.verify(repo)


def test_uncertain_report_with_local_witness_blocks_and_embeds_receipts(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        return fake_analysis(root, metadata, **kwargs, verdict="UNCERTAIN", confirmed=True, absolute=True)
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["analysis"]["verdict"] == "UNCERTAIN"
    assert report["gate"]["status"] == "BLOCKED"
    assert {item["path"] for item in report["evidence"]} >= {
        "evidence/pair/base.log", "evidence/pair/candidate.log",
        "evidence/pair/receipt.json", "base-probes/test_example.py",
    }
    assert report["analysis"]["tests"][0]["receipt_path"] == "evidence/pair/receipt.json"
    assert local_gate.verify(repo)["status"] == "BLOCKED"


def test_unresolved_coverage_stays_incomplete(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        return fake_analysis(root, metadata, **kwargs, verdict="UNCERTAIN")
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert local_gate.main(["verify", "--repo", str(repo)]) == 2


def test_failed_analyzer_produces_nonpassing_report(repo):
    stage_change(repo)
    def fail(*args, **kwargs):
        raise GateError("Missing dependency in this fixture")
    report, _ = local_gate.check(repo, analyzer=fail)
    assert report["analysis"] is None
    assert report["execution"]["status"] == "error"
    assert report["gate"]["status"] == "INCOMPLETE"


def test_timed_out_no_regression_observations_cannot_pass(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        work = root / "work"
        report = json.loads((work / "report.json").read_text())
        report["verdict"] = "NO_REGRESSION_FOUND"
        report["findings"][0].update(status="NOT_REPRODUCED", candidate_property_holds=True)
        (work / "report.json").write_text(json.dumps(report))
        path = work / "evidence/pair/receipt.json"
        receipt = json.loads(path.read_text())
        for label in ("base", "candidate"):
            receipt[label].update(timed_out=True, exit_code=-9)
        path.write_text(json.dumps(receipt))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert local_gate.verify(repo)["status"] == "INCOMPLETE"


def test_explicit_incomplete_analysis_mode_cannot_pass(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        path = root / "work/report.json"
        report = json.loads(path.read_text())
        report["analysis_mode"] = "incomplete"
        report["short_circuit_reason"] = None
        path.write_text(json.dumps(report))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert local_gate.verify(repo)["status"] == "INCOMPLETE"


def test_signal_exit_cannot_be_disguised_as_complete_probe(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        work = root / "work"
        report = json.loads((work / "report.json").read_text())
        report["verdict"] = "NO_REGRESSION_FOUND"
        report["findings"][0].update(status="NOT_REPRODUCED", candidate_property_holds=True)
        (work / "report.json").write_text(json.dumps(report))
        path = work / "evidence/pair/receipt.json"
        receipt = json.loads(path.read_text())
        receipt["candidate"].update(
            exit_code=-9, timed_out=False, interrupted=False,
            output_limit_exceeded=False, cleanup_complete=True, execution_error=None,
        )
        path.write_text(json.dumps(receipt))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert "Signal-terminated" in report["execution"]["error"]


def test_every_findings_probe_reference_must_exist(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        path = root / "work/report.json"
        report = json.loads(path.read_text())
        report["verdict"] = "NO_REGRESSION_FOUND"
        report["findings"][0].update(
            status="NOT_REPRODUCED", candidate_property_holds=True, probe_ids=["missing"],
        )
        report["tests"] = []
        path.write_text(json.dumps(report))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert "missing probe" in report["execution"]["error"]


def test_valid_local_witness_still_blocks_with_other_timed_out_probes(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="UNCERTAIN")
        work = root / "work"
        receipt = json.loads((work / "evidence/pair/receipt.json").read_text())
        receipt["id"] = "unavailable"
        receipt["candidate"]["timed_out"] = True
        (work / "evidence/other.json").write_text(json.dumps(receipt))
        report = json.loads((work / "report.json").read_text())
        report["tests"].append({
            "probe_id": "unavailable", "receipt_path": "evidence/other.json",
            "interpretation": "Other behavior could not be assessed.",
        })
        (work / "report.json").write_text(json.dumps(report))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "BLOCKED"
    assert local_gate.verify(repo)["status"] == "BLOCKED"


def test_malformed_receipt_cannot_pass(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        path = root / "work/evidence/pair/receipt.json"
        receipt = json.loads(path.read_text())
        del receipt["candidate"]["exit_code"]
        path.write_text(json.dumps(receipt))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"


def test_verification_never_executes_configured_clean_filter(repo, tmp_path):
    import shlex
    import sys

    local_gate.check(repo)
    marker = tmp_path / "filter-executed"
    code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    git(repo, "config", "filter.probe.clean", shlex.join([sys.executable, "-c", code]))
    (repo / ".git/info").mkdir(exist_ok=True)
    (repo / ".git/info/attributes").write_text("app.py filter=probe\n")
    (repo / "app.py").write_text("force an index comparison\n")
    with pytest.raises(GateError, match="clean/process"):
        local_gate.verify(repo)
    assert not marker.exists()


def test_partial_clones_fail_closed_without_lazy_fetch(repo, tmp_path):
    local_gate.check(repo)
    git(repo, "config", "remote.origin.promisor", "true")
    git(repo, "config", "remote.origin.url", "https://invalid.example/never-fetch")
    with pytest.raises(GateError, match="Partial/promisor"):
        local_gate.verify(repo)


def test_post_analysis_source_change_cannot_return_pass(repo):
    stage_change(repo)
    def mutate(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        (repo / "app.py").write_text("VALUE = 999\n")
        git(repo, "add", "app.py")
        return result
    with pytest.raises(GateError, match="stale"):
        local_gate.check(repo, analyzer=mutate)
    assert local_gate.main(["verify", "--repo", str(repo)]) == 2


def test_older_analysis_cannot_overwrite_newer_valid_report(repo):
    stage_change(repo)
    newer = {}

    def delayed(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        (repo / "app.py").write_text("VALUE = 3\n")
        git(repo, "add", "app.py")
        report, _ = local_gate.check(repo, analyzer=fake_analysis)
        newer["run_id"] = report["run_id"]
        newer["bytes"] = (repo / REPORT_NAME).read_bytes()
        return result

    with pytest.raises(GateError, match="not overwritten"):
        local_gate.check(repo, analyzer=delayed)
    assert (repo / REPORT_NAME).read_bytes() == newer["bytes"]
    assert local_gate.verify(repo)["status"] == "PASSED"


@pytest.mark.parametrize("missing_coverage", [False, True])
def test_nonexistent_or_uncovered_inspection_scope_cannot_pass(repo, missing_coverage):
    stage_change(repo)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        path = root / "work/report.json"
        report = json.loads(path.read_text())
        if missing_coverage:
            report.pop("change_coverage")
        else:
            report["inspected_paths"] = ["candidate/not-a-real-file.py"]
        path.write_text(json.dumps(report))
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert report["scope"]["unresolved_paths"] == ["app.py"]
    assert local_gate.verify(repo)["status"] == "INCOMPLETE"


@pytest.mark.parametrize("verdict", ["REGRESSION", "NO_REGRESSION_FOUND"])
def test_different_oracles_support_neither_false_block_nor_false_pass(repo, verdict):
    stage_change(repo)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        work = root / "work"
        path = work / "report.json"
        report = json.loads(path.read_text())
        report["verdict"] = verdict
        if verdict == "NO_REGRESSION_FOUND":
            report["findings"][0].update(status="NOT_REPRODUCED", candidate_property_holds=True)
        path.write_text(json.dumps(report))
        test = work / "candidate/tests/_regression_probe/test_example.py"
        test.write_text("# Different oracle expectation\n")
        copied = work / "evidence/pair/candidate-oracle.py"
        copied.write_bytes(test.read_bytes())
        receipt_path = work / "evidence/pair/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["comparison"]["oracle_files"]["candidate"]["tests/_regression_probe/test_example.py"] = {
            "sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
            "evidence_path": "evidence/pair/candidate-oracle.py",
        }
        receipt_path.write_text(json.dumps(receipt))
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert local_gate.verify(repo)["status"] == "INCOMPLETE"


def test_source_origin_hash_is_bound_to_the_actual_git_tree(repo):
    stage_change(repo)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        work = root / "work"
        path = work / "evidence/pair/candidate.origin.json"
        origin = json.loads(path.read_text())
        origin["files"][0].update(git_blob="0" * 40, lf_git_blob="0" * 40)
        path.write_text(json.dumps(origin))
        receipt_path = work / "evidence/pair/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["candidate"]["source_origin"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"


@pytest.mark.parametrize("invalid_record", [None, [], 42, "", {"files": [None]}, {"files": [{"path": 7}]}])
def test_malformed_origin_record_is_incomplete_not_an_exception(repo, invalid_record):
    stage_change(repo)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs, confirmed=True, verdict="REGRESSION")
        work = root / "work"
        report_path = work / "report.json"
        report = json.loads(report_path.read_text())
        report["verdict"] = "NO_REGRESSION_FOUND"
        report["findings"][0].update(status="NOT_REPRODUCED", candidate_property_holds=True)
        report_path.write_text(json.dumps(report))
        path = work / "evidence/pair/candidate.origin.json"
        path.write_text(json.dumps(invalid_record))
        receipt_path = work / "evidence/pair/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["candidate"]["source_origin"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert local_gate.main(["verify", "--repo", str(repo)]) == 2


def test_inspecting_only_old_source_does_not_cover_modified_candidate(repo):
    stage_change(repo)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        path = root / "work/report.json"
        report = json.loads(path.read_text())
        report["inspected_paths"] = ["base/app.py"]
        report["change_coverage"][0]["evidence_refs"] = ["base/app.py"]
        path.write_text(json.dumps(report))
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["scope"]["unresolved_paths"] == ["app.py"]
    assert report["gate"]["status"] == "INCOMPLETE"


@pytest.mark.parametrize("layout", ["root", "src"])
@pytest.mark.parametrize("regression", [False, True])
def test_standalone_probe_flows_into_verifiable_local_report(repo, layout, regression):
    if layout == "src":
        (repo / "src").mkdir()
        git(repo, "mv", "app.py", "src/app.py")
        commit(repo, "src layout base")
        git(repo, "update-ref", "refs/heads/main", "HEAD")
    source = "src/app.py" if layout == "src" else "app.py"
    (repo / source).write_text(f"VALUE = {2 if regression else 1}\n# Candidate revision\n")
    git(repo, "add", source)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        work = root / "work"
        driver = "tests/_regression_probe/test_value.py"
        for side in ("base", "candidate"):
            path = work / side / driver
            path.parent.mkdir(parents=True)
            path.write_text("import app\nprint(app.__file__)\nassert app.VALUE == 1\n")
        subprocess.run(
            [sys.executable, str(root / "runner/probe.py"), "--id", "real",
             "--command", shlex.join([sys.executable, driver])],
            cwd=work, env={
                "PATH": os.environ.get("PATH", ""),
                "PR_GATE_WORK_ROOT": str(work), "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=True, capture_output=True, text=True, timeout=30,
        )
        receipt = json.loads((work / "evidence/real/receipt.json").read_text())
        assert receipt["comparison"]["compatible"]
        for side in ("base", "candidate"):
            assert receipt[side]["source_origin"]["status"] == "verified"
            assert receipt[side]["exit_code"] == int(regression and side == "candidate")
        path = work / "report.json"
        report = json.loads(path.read_text())
        report.update(
            verdict="REGRESSION" if regression else "NO_REGRESSION_FOUND",
            analysis_mode="targeted", short_circuit_reason=None,
        )
        report["change_coverage"][0]["status"] = "analyzed"
        report["tests"] = [{
            "probe_id": "real", "receipt_path": "evidence/real/receipt.json",
            "interpretation": "The same VALUE == 1 assertion ran on both revisions.",
        }]
        report["findings"] = [{
            "id": "value", "status": "CONFIRMED_REGRESSION" if regression else "NOT_REPRODUCED",
            "hypothesis": "Candidate changes the fixture's required value.",
            "property_source": "Fixture contract: VALUE must remain 1.",
            "source_locations": [f"{source}:1"], "trigger_reached": True,
            "base_property_holds": True, "candidate_property_holds": not regression,
            "explanation": "Actual snapshot imports, common assertion, independent process state.",
            "probe_ids": ["real"],
        }]
        path.write_text(json.dumps(report))
        local_runner.audit_native(work, metadata)
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    expected = "BLOCKED" if regression else "PASSED"
    assert report["gate"]["status"] == expected, report["execution"]
    assert local_gate.verify(repo)["status"] == expected


@pytest.mark.parametrize("value,expected,declared,verdict,status", [
    (2, 2, False, "NO_REGRESSION_FOUND", "INCOMPLETE"),
    (1, 2, False, "REGRESSION", "INCOMPLETE"),
    (2, 2, True, "NO_REGRESSION_FOUND", "INCOMPLETE"),
    (1, 1, True, "NO_REGRESSION_FOUND", "PASSED"),
    (2, 1, True, "REGRESSION", "BLOCKED"),
])
def test_real_fixture_inputs_control_local_report_comparability(
    repo, value, expected, declared, verdict, status,
):
    fixture = "tests/fixtures/expected.json"
    path = repo / fixture
    path.parent.mkdir(parents=True)
    path.write_text('{"expected": 1}\n')
    (repo / "tests/test_value.py").write_text(
        "import json\nfrom pathlib import Path\nimport app\n\n"
        "def test_value():\n"
        "    expected = json.loads(Path('tests/fixtures/expected.json').read_text())\n"
        "    assert app.VALUE == expected['expected']\n"
    )
    git(repo, "add", "tests")
    commit(repo, "fixture baseline")
    git(repo, "update-ref", "refs/heads/main", "HEAD")
    (repo / "app.py").write_text(f"VALUE = {value}\n# Candidate revision\n")
    path.write_text(json.dumps({"expected": expected}) + "\n")
    git(repo, "add", "app.py", fixture)

    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        work = root / "work"
        command = [
            sys.executable, str(root / "runner/probe.py"), "--id", "fixture",
            "--command", shlex.join([sys.executable, "-m", "pytest", "-q", "tests/test_value.py"]),
        ]
        if declared:
            command += ["--oracle", fixture]
        subprocess.run(
            command, cwd=work, env={
                "PATH": os.environ.get("PATH", ""),
                "PR_GATE_WORK_ROOT": str(work), "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=True, capture_output=True, text=True, timeout=30,
        )
        receipt = json.loads((work / "evidence/fixture/receipt.json").read_text())
        if not declared:
            for side in ("base", "candidate"):
                origin = json.loads((work / receipt[side]["source_origin"]["path"]).read_text())
                assert origin["status"] == "incomplete"
                assert any(f"unfingerprinted_data_input: {fixture}" in issue for issue in origin["issues"])
        report_path = work / "report.json"
        report = json.loads(report_path.read_text())
        report.update(verdict=verdict, analysis_mode="targeted", short_circuit_reason=None)
        report["change_coverage"][0]["status"] = "analyzed"
        report["tests"] = [{
            "probe_id": "fixture", "receipt_path": "evidence/fixture/receipt.json",
            "interpretation": "Scripted interpretation exercises acceptance, not a model assessment.",
        }]
        if verdict == "REGRESSION":
            report["findings"] = [{
                "id": "value", "status": "CONFIRMED_REGRESSION",
                "hypothesis": "Candidate changes the required value.",
                "property_source": "Fixture contract: VALUE must remain 1.",
                "source_locations": ["app.py:1"], "trigger_reached": True,
                "base_property_holds": True, "candidate_property_holds": False,
                "explanation": "This claim must be rejected unless the paired oracle is valid.",
                "probe_ids": ["fixture"],
            }]
        report_path.write_text(json.dumps(report))
        local_runner.audit_native(work, metadata)
        return result

    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == status, report["execution"]
    assert local_gate.verify(repo)["status"] == status


def test_report_scope_cannot_be_rewritten_independently(repo):
    stage_change(repo)
    local_gate.check(repo, analyzer=fake_analysis)
    path = repo / REPORT_NAME
    report = json.loads(path.read_text())
    report["scope"]["inspected_files"][0]["oid"] = "0" * 40
    path.write_text(json.dumps(report))
    with pytest.raises(GateError, match="inspection scope"):
        local_gate.verify(repo)


def test_evidence_tampering_is_rejected(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        return fake_analysis(root, metadata, **kwargs, verdict="REGRESSION", confirmed=True)
    local_gate.check(repo, analyzer=analyzer)
    path = repo / REPORT_NAME
    report = json.loads(path.read_text())
    report["evidence"][0]["base64"] = base64.b64encode(b"edited").decode()
    path.write_text(json.dumps(report))
    with pytest.raises(GateError, match="digest"):
        local_gate.verify(repo)


def test_oversized_evidence_cannot_become_a_pass(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        result = fake_analysis(root, metadata, **kwargs)
        evidence = root / "work/evidence"
        evidence.mkdir(exist_ok=True)
        (evidence / "oversized.bin").write_bytes(b"x" * (local_evidence.MAX_EVIDENCE_BYTES + 1))
        return result
    report, _ = local_gate.check(repo, analyzer=analyzer)
    assert report["gate"]["status"] == "INCOMPLETE"
    assert report["evidence"] == []


def test_gate_status_cannot_be_changed_without_its_evidence(repo):
    stage_change(repo)
    def analyzer(root, metadata, **kwargs):
        return fake_analysis(root, metadata, **kwargs, verdict="UNCERTAIN")
    local_gate.check(repo, analyzer=analyzer)
    path = repo / REPORT_NAME
    report = json.loads(path.read_text())
    report["gate"] = {"status": "PASSED", "reason": "manually overwritten"}
    path.write_text(json.dumps(report))
    with pytest.raises(GateError, match="Claimed gate"):
        local_gate.verify(repo)


def test_renaming_staged_source_changes_its_fingerprint(repo):
    before = capture(repo).candidate_tree
    git(repo, "mv", "app.py", "renamed.py")
    assert capture(repo).candidate_tree != before


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", "evidence/../../escape", "evidence\\escape"])
def test_evidence_cannot_escape_its_unpack_root(path):
    with pytest.raises(GateError, match="Evidence paths"):
        local_evidence.canonical_evidence_path(path)


def test_prompt_reuses_paths_without_recursive_replacement(tmp_path):
    work = tmp_path / "work" / "nested" / "work"
    work.mkdir(parents=True)
    (work / "input.json").write_text('{"local_gate":true}')
    skill = tmp_path / "work" / "policy" / "SKILL.md"
    runner = tmp_path / "work" / "runner"
    prompt = entry.build_prompt(work=work, runner=runner, skill=skill)
    assert f"Skill at {skill}" in prompt
    assert f"python {runner}/probe.py" in prompt
    assert str(work / "input.json") in prompt


def test_native_command_does_not_enable_all_paths():
    command = entry.copilot_command(
        "/bin/copilot", "read the skill", model="fixed", effort="high", usage=Path("/tmp/usage.json"),
    )
    assert "--allow-all-paths" not in command
    assert "--disable-builtin-mcps" in command
    assert not any("task" in item for item in command if item.startswith("--available-tools="))


def test_native_home_and_state_do_not_inherit_running_argus_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", "/real/argus")
    monkeypatch.setenv("COPILOT_HOME", "/real/copilot")
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "/real/instructions")
    monkeypatch.setenv("GITHUB_TOKEN", "other-token")
    monkeypatch.setenv("BASH_ENV", "/real/startup.sh")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-private-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-cloud-secret")
    environment = local_runner.native_environment(tmp_path, "fixture-token")
    assert environment["ARGUS_SKILL_HOME"] == str(tmp_path / "home/argus")
    assert environment["COPILOT_HOME"] == str(tmp_path / "home/.copilot")
    assert environment["COPILOT_GITHUB_TOKEN"] == "fixture-token"
    assert not {
        "GITHUB_TOKEN", "BASH_ENV", "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
        "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
    } & environment.keys()
    assert os.environ["ARGUS_SKILL_HOME"] == "/real/argus"


def test_docker_is_only_an_explicit_choice(repo, monkeypatch):
    stage_change(repo)
    seen = []
    def analyzer(root, metadata, **kwargs):
        seen.append(kwargs["isolation"])
        return fake_analysis(root, metadata, **kwargs)
    monkeypatch.setattr(local_runner, "analyze", analyzer)
    assert local_gate.main(["check", "--repo", str(repo)]) == 0
    assert seen == ["native"]
    monkeypatch.setattr(local_runner, "analyze", lambda *_args, **_kwargs: pytest.fail("verify called analyzer"))
    assert local_gate.main(["verify", "--repo", str(repo)]) == 0


def test_requested_docker_does_not_silently_fall_back_to_native(repo, monkeypatch):
    stage_change(repo)
    which = local_runner.shutil.which
    monkeypatch.setattr(local_runner.shutil, "which", lambda name: None if name == "docker" else which(name))
    monkeypatch.setattr(local_runner, "native", lambda *_args, **_kwargs: pytest.fail("unexpected native fallback"))
    report, _ = local_gate.check(repo, isolation="docker")
    assert report["execution"]["isolation"] == "docker"
    assert report["gate"]["status"] == "INCOMPLETE"


def test_source_audit_catches_a_changed_snapshot_without_touching_original(repo, tmp_path):
    stage_change(repo)
    root = tmp_path / "audit-run"
    root.mkdir()
    (root / "work").mkdir()
    metadata = materialize(capture(repo), root)
    local_runner.stage_native(root, metadata)
    (root / "work/candidate/app.py").write_text("VALUE = 999\n")
    with pytest.raises(GateError, match="tracked source"):
        local_runner.audit_native(root / "work", metadata)
    assert (repo / "app.py").read_text() == "VALUE = 2\n"


@pytest.mark.skipif(os.name != "posix", reason="Git executable mode fixture")
def test_executable_mode_is_bound(repo):
    before = capture(repo).candidate_tree
    (repo / "app.py").chmod(0o755)
    git(repo, "add", "app.py")
    assert capture(repo).candidate_tree != before
