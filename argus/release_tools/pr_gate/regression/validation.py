"""Structural report, receipt and evidence validation, without executing probes."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

if __package__:
    from .probe_contract import signal_exit
    from .runtime import digest, safe_file
else:
    from probe_contract import signal_exit
    from runtime import digest, safe_file


def observation_incomplete(observation: dict) -> bool:
    return bool(
        observation["timed_out"] or observation.get("interrupted")
        or signal_exit(observation["exit_code"])
        or observation.get("output_limit_exceeded")
        or observation.get("cleanup_complete") is False
        or observation.get("execution_error")
    )


def validate_report(
    work: Path, schema: dict, metadata: dict, *, incomplete_probes: list[str] | None = None,
    source_inventory: dict | None = None,
) -> dict:
    report = json.loads(safe_file(work, "report.json").read_text())
    jsonschema.validate(report, schema)
    for key in ("pr_number", "base_sha", "candidate_sha"):
        if report[key] != metadata[key]:
            raise ValueError(f"Report {key} does not match the immutable input")
    safe_file(work, "REPORT.md")
    receipts = {}
    invalid_comparisons = set()
    receipt_schema = json.loads(Path(__file__).with_name("probe-receipt.schema.json").read_text())
    for test in report["tests"]:
        path = safe_file(work, test["receipt_path"])
        if not path.is_relative_to((work / "evidence").resolve()):
            raise ValueError("Evidence path escapes the assigned directory")
        receipt = json.loads(path.read_text())
        jsonschema.validate(receipt, receipt_schema)
        if receipt["id"] != test["probe_id"]:
            raise ValueError("Probe identity mismatch")
        if receipt["id"] in receipts:
            raise ValueError("Duplicate probe identity")
        for key in ("base_sha", "candidate_sha"):
            if receipt[key] != metadata[key]:
                raise ValueError("Probe source revision mismatch")
        for label in ("base", "candidate"):
            observation = receipt[label]
            if signal_exit(observation["exit_code"]) and not (
                observation["timed_out"] or observation.get("interrupted")
                or observation.get("output_limit_exceeded")
                or observation.get("cleanup_complete") is False
                or observation.get("execution_error")
            ):
                raise ValueError("Signal-terminated probe is not marked incomplete")
            log = safe_file(work, observation["log"])
            if not log.is_relative_to((work / "evidence").resolve()):
                raise ValueError("Probe log escapes its evidence directory")
            if digest(log) != observation["log_sha256"]:
                raise ValueError("Probe log digest mismatch")
        if metadata.get("local_gate"):
            if __package__:
                from .scope import comparison_issues
            else:
                from scope import comparison_issues
            if comparison_issues(work, receipt, source_inventory):
                invalid_comparisons.add(receipt["id"])
        receipts[test["probe_id"]] = receipt
        if incomplete_probes is not None and (
            receipt["id"] in invalid_comparisons
            or any(observation_incomplete(receipt[label]) for label in ("base", "candidate"))
        ):
            incomplete_probes.append(receipt["id"])
    confirmed = []
    for finding in report["findings"]:
        if any(identifier not in receipts for identifier in finding["probe_ids"]):
            raise ValueError("Finding references a missing probe")
        if finding["status"] == "CONFIRMED_REGRESSION":
            if not (
                finding["trigger_reached"] is True
                and finding["base_property_holds"] is True
                and finding["candidate_property_holds"] is False
                and finding["probe_ids"]
                and all(identifier in receipts for identifier in finding["probe_ids"])
            ):
                raise ValueError("Regression claim lacks a paired reached witness")
            if any(
                identifier in invalid_comparisons
                or any(observation_incomplete(receipts[identifier][label]) for label in ("base", "candidate"))
                for identifier in finding["probe_ids"]
            ):
                raise ValueError("An incomplete or incomparable probe cannot establish a regression")
            confirmed.append(finding)
    if report["verdict"] == "REGRESSION" and not confirmed:
        raise ValueError("Regression verdict has no confirmed finding")
    if report["verdict"] == "NO_REGRESSION_FOUND" and confirmed:
        raise ValueError("No-regression verdict contradicts a confirmed finding")
    if report["analysis_mode"] == "short_circuit" and (
        not report["short_circuit_reason"] or report["tests"]
        or report["verdict"] != "NO_REGRESSION_FOUND"
    ):
        raise ValueError("Invalid short-circuit report")
    if report["verdict"] == "NO_REGRESSION_FOUND" and any(
        finding["status"] == "UNKNOWN" for finding in report["findings"]
    ):
        raise ValueError("Unresolved hypotheses cannot silently become a pass")
    return report
