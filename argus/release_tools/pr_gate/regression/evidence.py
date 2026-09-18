"""One-file local evidence envelope and deterministic gate policy."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath

import jsonschema

from argus.release_tools.pr_gate import owned_process

if __package__:
    from .runtime import safe_file
    from .scope import analyze_scope
    from .snapshot import REPORT_NAME, GateError
    from .validation import validate_report
else:
    from runtime import safe_file
    from scope import analyze_scope
    from snapshot import REPORT_NAME, GateError
    from validation import validate_report

ASSETS = Path(__file__).resolve().parent
SKILL_PATH = ASSETS / "SKILL.md"
POLICY_VERSION = "local-staged-regression/v2"
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_FILES = 256
SOURCE_FILES = (
    "cli.py", "snapshot.py", "runner.py", "evidence.py", "scope.py", "oracle.py",
    "runtime.py", "validation.py", "origin_guard/sitecustomize.py",
    "local-report.schema.json", "entry.py", "probe.py", "egress_proxy.py",
    "report.schema.json", "probe-receipt.schema.json", "probe_contract.py",
    "Dockerfile", "requirements.txt",
)
_CREDENTIAL = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}"
    rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def policy_identity() -> dict[str, str]:
    files = {name: sha256((ASSETS / name).read_bytes()) for name in SOURCE_FILES}
    files["owned_process.py"] = sha256(Path(owned_process.__file__).read_bytes())
    return {
        "version": POLICY_VERSION,
        "skill_sha256": sha256(SKILL_PATH.read_bytes()),
        "implementation_sha256": sha256(json.dumps(files, sort_keys=True).encode()),
    }


def canonical_evidence_path(value: str, *, work: Path | None = None) -> str:
    path = PurePosixPath(value)
    if work is not None and path.is_absolute() and path.is_relative_to(work.as_posix()):
        path = path.relative_to(work.as_posix())
    elif path.is_absolute() and path.is_relative_to("/work"):
        path = path.relative_to("/work")
    if (
        path.is_absolute() or ".." in path.parts or "\\" in value
        or not path.parts or path.parts[0] not in {"evidence", "base-probes", "candidate-probes"}
        or len(path.parts) < 2
    ):
        raise GateError("Evidence paths must remain under the assigned evidence/probe directories.")
    return str(path)


def _schema() -> dict:
    return json.loads((ASSETS / "report.schema.json").read_text())


def _check_sensitive(data: bytes) -> None:
    if _CREDENTIAL.search(data):
        raise GateError("Credential-shaped text found in evidence; inspect private artifacts before sharing.")


def collect(
    work: Path, metadata: dict[str, object], source_inventory: dict,
) -> tuple[dict, str, list[dict]]:
    raw_path = safe_file(work, "report.json")
    if raw_path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise GateError("The model report exceeds the evidence size limit.")
    analysis = copy.deepcopy(json.loads(raw_path.read_text()))
    jsonschema.validate(analysis, _schema())
    for test in analysis["tests"]:
        test["receipt_path"] = canonical_evidence_path(test["receipt_path"], work=work)
    markdown_path = safe_file(work, "REPORT.md")
    if markdown_path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise GateError("The explanatory report exceeds the evidence size limit.")
    explanation = markdown_path.read_text()
    files = []
    total = len(explanation.encode()) + len(json.dumps(analysis).encode())
    roots = [
        (work / "evidence", "evidence"),
        (work / "base/tests/_regression_probe", "base-probes"),
        (work / "candidate/tests/_regression_probe", "candidate-probes"),
    ]
    for source, prefix in roots:
        if source.is_symlink() or not source.resolve().is_relative_to(work.resolve()):
            raise GateError("Evidence directory must not be a symlink.")
        if any(parent.is_symlink() for parent in source.parents if parent.is_relative_to(work)):
            raise GateError("Evidence directory has a symlink parent.")
        if not source.exists():
            continue
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if "__pycache__" in relative.parts or ".pytest_cache" in relative.parts:
                continue
            if path.is_symlink():
                raise GateError("Symlink evidence is not supported.")
            if not path.is_file():
                continue
            path = safe_file(work, path.relative_to(work).as_posix())
            total += path.stat().st_size
            if total > MAX_EVIDENCE_BYTES or len(files) >= MAX_FILES:
                raise GateError("Evidence exceeds 4 MiB/256 files; retain smaller reproducible observations.")
            data = path.read_bytes()
            _check_sensitive(data)
            files.append({
                "path": f"{prefix}/{relative.as_posix()}",
                "sha256": sha256(data),
                "base64": base64.b64encode(data).decode("ascii"),
            })
    _check_sensitive((json.dumps(analysis) + explanation).encode())
    validate_embedded(analysis, explanation, files, metadata, source_inventory)
    return analysis, explanation, files


def validate_embedded(
    analysis: dict, explanation: str, files: list, metadata: dict, source_inventory: dict,
) -> list[str]:
    if len(files) > MAX_FILES:
        raise GateError("Too many embedded evidence files.")
    total = len(explanation.encode()) + len(json.dumps(analysis).encode())
    if total > MAX_EVIDENCE_BYTES:
        raise GateError("Analysis/explanation exceeds the evidence size limit.")
    _check_sensitive((json.dumps(analysis) + explanation).encode())
    seen = set()
    with tempfile.TemporaryDirectory(prefix="pr-gate-evidence-") as temporary:
        work = Path(temporary)
        (work / "report.json").write_text(json.dumps(analysis))
        (work / "REPORT.md").write_text(explanation)
        for item in files:
            path = canonical_evidence_path(item["path"])
            if path in seen:
                raise GateError("Duplicate evidence path.")
            seen.add(path)
            try:
                data = base64.b64decode(item["base64"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise GateError("Invalid evidence encoding.") from exc
            total += len(data)
            if total > MAX_EVIDENCE_BYTES:
                raise GateError("Embedded evidence exceeds the size limit.")
            if sha256(data) != item["sha256"]:
                raise GateError("Embedded evidence digest mismatch.")
            _check_sensitive(data)
            destination = work / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        incomplete_probes: list[str] = []
        validate_report(
            work, _schema(), dict(metadata, local_gate=True),
            incomplete_probes=incomplete_probes, source_inventory=source_inventory,
        )
        return incomplete_probes


def gate_policy(report: dict, source_inventory: dict) -> dict[str, str]:
    execution = report["execution"]
    expected_scope = analyze_scope(
        report["analysis"], source_inventory, unchanged=execution["status"] == "unchanged",
    )
    if report["scope"] != expected_scope:
        raise GateError("Reported inspection scope does not match the source trees.")
    if execution["status"] not in {"completed", "unchanged"}:
        return {"status": "INCOMPLETE", "reason": execution.get("error", "Analysis did not complete.")}
    analysis = report["analysis"]
    if not isinstance(analysis, dict):
        raise GateError("Completed execution has no analysis report.")
    if execution["status"] == "completed" and execution.get("source_audit") is not True:
        raise GateError("Completed execution lacks source-preservation evidence.")
    incomplete_probes = validate_embedded(
        analysis, report["explanation"], report["evidence"], report["snapshots"], source_inventory,
    )
    if execution["status"] == "unchanged":
        binding = report["binding"]
        if binding["base_tree"] != binding["candidate_tree"] or analysis["tests"] or report["evidence"]:
            raise GateError("Invalid unchanged-source short circuit.")
    if any(finding["status"] == "CONFIRMED_REGRESSION" for finding in analysis["findings"]):
        return {"status": "BLOCKED", "reason": "A paired regression candidate requires repair or maintainer review."}
    if analysis["analysis_mode"] == "incomplete":
        return {"status": "INCOMPLETE", "reason": "The analyzer explicitly reported incomplete analysis."}
    if incomplete_probes:
        return {"status": "INCOMPLETE", "reason": "Probe observations are incomplete: " + ", ".join(incomplete_probes)}
    if expected_scope["issues"] or expected_scope["unresolved_paths"]:
        return {"status": "INCOMPLETE", "reason": "Changed-source inspection coverage is incomplete or invalid."}
    if (
        analysis["verdict"] != "NO_REGRESSION_FOUND"
        or analysis["escalation_required"]
        or any(finding["status"] == "UNKNOWN" for finding in analysis["findings"])
    ):
        return {"status": "INCOMPLETE", "reason": "Required behavioral evidence remains unresolved."}
    return {"status": "PASSED", "reason": "No regression found in the reported scope; not proof of global safety."}


def read_report(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_REPORT_BYTES:
        raise GateError("Report must be a regular file no larger than 8 MiB.")
    report = json.loads(path.read_text())
    jsonschema.validate(report, json.loads((ASSETS / "local-report.schema.json").read_text()))
    return report


def verify_artifacts(report: dict, source_inventory: dict) -> dict[str, str]:
    if report["policy"] != policy_identity():
        raise GateError("Report is stale: Skill, policy, or gate implementation changed.")
    verdict = gate_policy(report, source_inventory)
    if verdict != report["gate"]:
        raise GateError("Claimed gate result does not match its evidence.")
    return verdict


def write_report(root: Path, report: dict) -> Path:
    path = root / REPORT_NAME
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GateError("Refusing to replace a non-regular report file.")
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > MAX_REPORT_BYTES:
        raise GateError("Submitted report exceeds 8 MiB.")
    _check_sensitive(encoded)
    jsonschema.validate(report, json.loads((ASSETS / "local-report.schema.json").read_text()))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".pr-gate-", dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
