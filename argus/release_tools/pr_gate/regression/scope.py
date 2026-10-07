"""Bind scope declarations and comparative evidence to authoritative source trees."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path, PurePosixPath

if __package__:
    from .oracle import python_command
    from .runtime import digest, safe_file
else:
    from oracle import python_command
    from runtime import digest, safe_file


def changed_paths(inventory: dict) -> list[str]:
    return sorted(
        path for path in inventory["base"].keys() | inventory["candidate"].keys()
        if inventory["base"].get(path) != inventory["candidate"].get(path)
    )


def source_ref(value: str) -> tuple[str, str]:
    side, separator, path = value.partition("/")
    pure = PurePosixPath(path)
    if (
        not separator or side not in {"base", "candidate"} or not path
        or pure.is_absolute() or ".." in pure.parts or "\\" in path
        or str(pure) != path
    ):
        raise ValueError("Use source references such as candidate/path/to/file.py.")
    return side, path


def analyze_scope(analysis: dict | None, inventory: dict, *, unchanged: bool = False) -> dict:
    changed = changed_paths(inventory)
    if unchanged and not changed:
        return {"changed_paths": [], "unresolved_paths": [], "issues": [], "inspected_files": []}
    if analysis is None:
        return {"changed_paths": changed, "unresolved_paths": changed,
                "issues": ["Analysis is unavailable."], "inspected_files": []}
    issues, inspected = [], {}
    for reference in analysis["inspected_paths"]:
        try:
            side, path = source_ref(reference)
        except ValueError:
            issues.append(f"Invalid inspected source reference: {reference}")
            continue
        entry = inventory[side].get(path)
        if entry is None:
            issues.append(f"Inspected source does not exist: {reference}")
        else:
            inspected[reference] = {"reference": reference, **entry}
    coverage = analysis.get("change_coverage", [])
    covered = set()
    unresolved = set(changed)
    for group in coverage:
        paths = group["paths"]
        references = group["evidence_refs"]
        if not references or any(reference not in inspected for reference in references):
            issues.append("Coverage evidence must refer to actually declared, existing source files.")
            continue
        if not group["reason"].strip():
            issues.append("Coverage needs a nonempty explanation.")
            continue
        for path in paths:
            if path not in changed:
                issues.append(f"Coverage names a path outside the change: {path}")
                continue
            if path in covered:
                issues.append(f"Duplicate changed-path coverage: {path}")
            covered.add(path)
            required = [
                f"{side}/{path}" for side in ("base", "candidate") if path in inventory[side]
            ]
            if any(reference not in inspected for reference in required):
                issues.append(f"Changed source versions were not fully inspected: {path}")
                continue
            if analysis["analysis_mode"] == "short_circuit" and group["status"] != "negligible":
                issues.append(f"Short circuit does not justify negligible impact for {path}")
                continue
            if group["status"] != "unresolved":
                unresolved.discard(path)
    return {
        "changed_paths": changed, "unresolved_paths": sorted(unresolved),
        "issues": issues, "inspected_files": [inspected[key] for key in sorted(inspected)],
    }


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def comparison_issues(work: Path, receipt: dict, inventory: dict | None) -> list[str]:
    """Mechanical comparability only; never certify an oracle's intended meaning."""
    issues = []
    contract = receipt.get("comparison")
    if not isinstance(contract, dict) or contract.get("schema_version") != "local-probe-comparison/v2":
        return ["Missing local comparative-probe contract."]
    if contract.get("compatible") is not True or contract.get("issues"):
        issues.append("Probe producer could not establish oracle compatibility.")
    try:
        base_command = shlex.split(receipt["base"]["command"])
        candidate_command = shlex.split(receipt["candidate"]["command"])
    except ValueError:
        return ["Invalid comparative command syntax."]
    if base_command != candidate_command:
        issues.append("Base and candidate commands differ.")
    parsed, command_errors = python_command(receipt["base"]["command"])
    if command_errors or parsed != contract.get("command_argv"):
        issues.append("Recorded command is not a supported common Python probe.")
    maps = contract.get("oracle_files", {})
    if not isinstance(maps, dict) or set(maps) != {"base", "candidate"}:
        return issues + ["Missing paired oracle file inventories."]
    if not isinstance(maps["base"], dict) or not isinstance(maps["candidate"], dict):
        return issues + ["Invalid oracle inventories."]
    if set(maps["base"]) != set(maps["candidate"]):
        issues.append("Base and candidate use different oracle paths.")
    for side in ("base", "candidate"):
        for path, item in maps[side].items():
            if (
                not isinstance(item, dict) or not isinstance(path, str)
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
            ):
                issues.append("Invalid oracle input identity.")
                continue
            try:
                evidence = safe_file(work, item["evidence_path"])
                if not evidence.is_relative_to(work / "evidence"):
                    raise ValueError("Oracle evidence is outside the evidence directory.")
                data = evidence.read_bytes()
                if digest(evidence) != item["sha256"]:
                    issues.append(f"Oracle bytes do not match their receipt: {path}")
                counterpart = maps["candidate" if side == "base" else "base"].get(path)
                if not isinstance(counterpart, dict) or counterpart.get("sha256") != item["sha256"]:
                    issues.append(f"Oracle differs between versions: {path}")
                if path.startswith("tests/_regression_probe/"):
                    relative = path.removeprefix("tests/_regression_probe/")
                    supplied = safe_file(work, f"{side}-probes/{relative}")
                    if digest(supplied) != item["sha256"]:
                        issues.append(f"Executed oracle and submitted probe differ: {side}/{path}")
                elif inventory is not None:
                    expected = inventory[side].get(path)
                    oids = {_git_blob(data)}
                    if path.endswith((".py", ".toml", ".ini", ".cfg")):
                        oids.add(_git_blob(data.replace(b"\r\n", b"\n")))
                    if expected is None or expected["oid"] not in oids:
                        issues.append(f"Existing oracle is not bound to the source tree: {side}/{path}")
            except (KeyError, OSError, ValueError):
                issues.append(f"Missing or invalid oracle evidence: {side}/{path}")
    for side in ("base", "candidate"):
        observation = receipt[side]
        origin = observation.get("source_origin")
        if not isinstance(origin, dict):
            issues.append(f"Missing source-origin evidence: {side}")
            continue
        try:
            path = safe_file(work, origin["path"])
            if not path.is_relative_to(work / "evidence"):
                raise ValueError("Source-origin evidence is outside the evidence directory.")
            if digest(path) != origin["sha256"]:
                issues.append(f"Source-origin evidence hash mismatch: {side}")
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or not isinstance(record.get("files"), list):
                raise ValueError("Source-origin record must contain a source file list.")
            if (
                origin.get("status") != "verified" or record.get("status") != "verified"
                or record.get("schema_version") != "probe-source-origin/v1"
                or record.get("guard_loaded") is not True or record.get("completed") is not True
                or record.get("issues") != [] or not record["files"]
                or record.get("main_pid") != observation.get("process_pid")
                or type(record.get("main_pid")) is not int or record["main_pid"] <= 0
                or record.get("source_root") != observation["cwd"]
                or not isinstance(record.get("context_id"), str) or not record["context_id"]
            ):
                issues.append(f"Source execution was not fully attributed: {side}")
            if inventory is None:
                issues.append("Source tree inventory is unavailable.")
                continue
            for source in record["files"]:
                if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                    raise ValueError("Invalid observed source file identity.")
                relative = source["path"]
                expected = inventory[side].get(relative)
                oids = {source.get("git_blob")}
                if relative.endswith(".py"):
                    oids.add(source.get("lf_git_blob"))
                if expected is None or expected["oid"] not in oids:
                    issues.append(f"Executed source does not match the assigned tree: {side}/{relative}")
        except (OSError, ValueError, KeyError, TypeError):
            issues.append(f"Invalid source-origin record: {side}")
    return sorted(set(issues))
