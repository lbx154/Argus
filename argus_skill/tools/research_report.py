"""Append explicit, structured research facts to the current session event log."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from ..core.event_catalog import EventType, normalize_event_envelope
from ..life.event_log import JsonlEventSink


class ReportError(ValueError):
    pass


def add_report_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    report = subparsers.add_parser(
        "report",
        help="Append a structured research fact to the current Argus session",
    )
    report.add_argument(
        "--session",
        default="",
        help="Session id; defaults to ARGUS_SKILL_SESSION_ID / agent event log",
    )
    report_sub = report.add_subparsers(dest="report_cmd", required=True)

    metric = report_sub.add_parser("metric", help="Report an observed metric")
    metric.add_argument("--id", dest="metric_id", default="")
    metric.add_argument("--name", required=True)
    metric.add_argument("--baseline", type=float, default=None)
    metric.add_argument("--value", type=float, required=True)
    metric.add_argument("--unit", default="")
    metric.add_argument(
        "--direction",
        choices=("maximize", "minimize", "target"),
        default="maximize",
    )
    metric.add_argument("--evidence", required=True)
    metric.add_argument("--experiment-id", default="")
    metric.add_argument("--hypothesis-id", default="")
    metric.add_argument("--branch-id", default="")
    metric.add_argument("--item-id", default="")
    metric.add_argument("--round", dest="round_index", type=int, default=None)
    metric.add_argument("--primary", action="store_true")

    hypothesis = report_sub.add_parser(
        "hypothesis",
        help="Propose a structured research hypothesis",
    )
    hypothesis.add_argument("--id", dest="hypothesis_id", default="")
    hypothesis.add_argument("--title", required=True)
    hypothesis.add_argument("--statement", required=True)
    hypothesis.add_argument("--branch-id", default="")
    hypothesis.add_argument("--parent-branch-id", default=None)
    hypothesis.add_argument("--evidence", action="append", default=[])
    hypothesis.add_argument("--item-id", default="")
    hypothesis.add_argument("--round", dest="round_index", type=int, default=None)

    experiment = report_sub.add_parser("experiment", help="Report experiment state")
    experiment_sub = experiment.add_subparsers(dest="experiment_cmd", required=True)
    experiment_start = experiment_sub.add_parser("start", help="Mark an experiment started")
    _add_experiment_identity_args(experiment_start)
    experiment_start.add_argument("--title", required=True)
    experiment_start.add_argument("--summary", default="")
    experiment_complete = experiment_sub.add_parser(
        "complete",
        help="Mark an experiment completed, failed, or cancelled",
    )
    _add_experiment_identity_args(experiment_complete)
    experiment_complete.add_argument(
        "--status",
        choices=("completed", "failed", "cancelled"),
        default="completed",
    )
    experiment_complete.add_argument("--summary", default="")
    experiment_complete.add_argument("--duration-seconds", type=float, default=None)
    experiment_complete.add_argument("--evidence", action="append", default=[])

    artifact = report_sub.add_parser("artifact", help="Register a research artifact")
    artifact.add_argument("--id", dest="artifact_id", default="")
    artifact.add_argument("--path", required=True)
    artifact.add_argument(
        "--kind",
        choices=("text", "image", "pdf", "data", "code", "binary"),
        required=True,
    )
    artifact.add_argument("--title", default="")
    artifact.add_argument("--why", default="")
    artifact.add_argument("--experiment-id", default="")
    artifact.add_argument("--branch-id", default="")
    artifact.add_argument("--item-id", default="")
    artifact.add_argument("--round", dest="round_index", type=int, default=None)

    verify = report_sub.add_parser(
        "verify-metric",
        help="Reviewer acceptance/rejection of a previously reported metric",
    )
    verify.add_argument("--id", dest="metric_id", required=True)
    verify.add_argument("--status", choices=("accepted", "rejected"), required=True)
    verify.add_argument("--reason", dest="reviewer_reason", required=True)
    verify.add_argument("--evidence", default="")
    verify.add_argument("--round", dest="round_index", type=int, default=None)


def _add_experiment_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", dest="experiment_id", required=True)
    parser.add_argument("--hypothesis-id", default="")
    parser.add_argument("--branch-id", default="")
    parser.add_argument("--item-id", default="")
    parser.add_argument("--round", dest="round_index", type=int, default=None)


def _session_root(session_id: str = "") -> Path:
    explicit = str(session_id or os.environ.get("ARGUS_SKILL_SESSION_ID") or "").strip()
    if explicit:
        root = core_paths.global_root() / "projects" / explicit
        if root.is_dir():
            return root
        raise ReportError(f"unknown Argus session: {explicit}")
    session_root = str(os.environ.get("ARGUS_SKILL_SESSION_ROOT") or "").strip()
    if session_root:
        root = Path(session_root).expanduser()
        if root.is_dir():
            return root
    event_log = str(os.environ.get("ARGUS_SKILL_AGENT_IO_LOG") or "").strip()
    if event_log:
        root = Path(event_log).expanduser().parent
        if root.is_dir():
            return root
    raise ReportError(
        "cannot resolve the current Argus session; run inside a mission or pass --session"
    )


def _workspace_root() -> Path:
    configured = str(os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "").strip()
    return (Path(configured).expanduser() if configured else Path.cwd()).resolve()


def _evidence_path(raw: str, *, required: bool = True) -> str:
    text = str(raw or "").strip()
    if not text:
        if required:
            raise ReportError("evidence path is required")
        return ""
    workspace = _workspace_root()
    candidate = Path(text).expanduser()
    resolved = (workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise ReportError(f"evidence must stay inside project workspace: {text}") from exc
    if not resolved.exists():
        raise ReportError(f"evidence does not exist: {relative}")
    return relative.as_posix()


def _common_fields(args: argparse.Namespace) -> dict[str, Any]:
    fields: dict[str, Any] = {"agent_layer": "engineer"}
    for name in ("item_id", "round_index", "branch_id", "hypothesis_id", "experiment_id"):
        value = getattr(args, name, None)
        if value is not None and value != "":
            fields[name] = value
    return fields


def build_report_event(args: argparse.Namespace) -> dict[str, Any]:
    common = _common_fields(args)
    cmd = args.report_cmd
    if cmd == "metric":
        metric_id = args.metric_id or f"metric-{uuid.uuid4().hex[:12]}"
        return {
            "type": EventType.RESEARCH_METRIC_REPORTED,
            **common,
            "metric_id": metric_id,
            "name": args.name,
            "baseline": args.baseline,
            "value": args.value,
            "unit": args.unit,
            "direction": args.direction,
            "evidence": _evidence_path(args.evidence),
            "primary": bool(args.primary),
        }
    if cmd == "hypothesis":
        hypothesis_id = args.hypothesis_id or f"hyp-{uuid.uuid4().hex[:12]}"
        return {
            "type": EventType.RESEARCH_HYPOTHESIS_PROPOSED,
            **common,
            "hypothesis_id": hypothesis_id,
            "title": args.title,
            "statement": args.statement,
            "parent_branch_id": args.parent_branch_id,
            "evidence": [_evidence_path(path) for path in args.evidence],
        }
    if cmd == "experiment":
        if args.experiment_cmd == "start":
            return {
                "type": EventType.RESEARCH_EXPERIMENT_STARTED,
                **common,
                "experiment_id": args.experiment_id,
                "title": args.title,
                "summary": args.summary,
            }
        return {
            "type": EventType.RESEARCH_EXPERIMENT_COMPLETED,
            **common,
            "experiment_id": args.experiment_id,
            "status": args.status,
            "summary": args.summary,
            "duration_seconds": args.duration_seconds,
            "evidence": [_evidence_path(path) for path in args.evidence],
        }
    if cmd == "artifact":
        path = _evidence_path(args.path)
        artifact_id = args.artifact_id or f"artifact-{uuid.uuid4().hex[:12]}"
        return {
            "type": EventType.RESEARCH_ARTIFACT_REGISTERED,
            **common,
            "artifact_id": artifact_id,
            "path": path,
            "kind": args.kind,
            "title": args.title or Path(path).name,
            "why": args.why,
        }
    if cmd == "verify-metric":
        return {
            "type": EventType.RESEARCH_METRIC_VERIFIED,
            **common,
            "agent_layer": "reviewer",
            "metric_id": args.metric_id,
            "status": args.status,
            "reviewer_reason": args.reviewer_reason,
            "evidence": _evidence_path(args.evidence, required=False),
        }
    raise ReportError(f"unsupported report command: {cmd}")


def run_report(args: argparse.Namespace) -> int:
    try:
        session_root = _session_root(getattr(args, "session", ""))
        event = normalize_event_envelope(build_report_event(args))
        validation = event.get("event_validation")
        if validation:
            raise ReportError("; ".join(validation.get("errors") or ["invalid event"]))
        JsonlEventSink(None, life_dir=session_root, verbosity="full").append(event)
    except ReportError as exc:
        sys.stderr.write(f"argus-skill report: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


__all__ = [
    "ReportError",
    "add_report_parser",
    "build_report_event",
    "run_report",
]
