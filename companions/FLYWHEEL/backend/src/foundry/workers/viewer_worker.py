"""Independent venue-calibrated Viewer JSONL/file-queue worker.

Protocol input is one JSON object per line. Without independently supplied
dimension scores and evidence references, the worker returns
``awaiting_evaluator`` rather than inventing a score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..services.evidence_snapshot import validate_evidence_snapshot
from ..services.viewer_queue import ViewerQueue

DEFAULT_DIMENSIONS = (
    "novelty", "significance", "technical_quality", "empirical_rigor",
    "clarity", "reproducibility", "venue_fit",
)


PopenFactory = Callable[..., subprocess.Popen[str]]


class IndependentEvaluatorProcess:
    """Run a separately configured JSON evaluator in a fresh work directory.

    The command is a protocol adapter (it may internally use Pi, Copilot,
    Codex, or another reviewer model), not the campaign's own Argus process.
    It receives one JSON request on stdin and must return one JSON object.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        work_root: Path,
        timeout: float = 600.0,
        popen_factory: PopenFactory = subprocess.Popen,
    ) -> None:
        if not argv or any(not str(part) for part in argv):
            raise ValueError("evaluator argv must be a non-empty explicit sequence")
        forbidden = ("token", "password", "secret", "api-key", "apikey")
        if any(any(word in str(part).lower() for word in forbidden) for part in argv):
            raise ValueError("credentials must not be placed in evaluator argv")
        self.argv = tuple(str(part) for part in argv)
        self.work_root = work_root.resolve()
        self.timeout = timeout
        self.popen_factory = popen_factory

    def evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(request.get("request_id") or "review"))[:80]
        workdir = self.work_root / request_id / str(uuid.uuid4())
        workdir.mkdir(parents=True, exist_ok=False)
        packet = {
            "protocol_version": 1,
            "mode": "independent_venue_review",
            "fresh_context": True,
            "request": dict(request),
            "evidence_access": {
                "mode": "read_only_references",
                "refs": list(request.get("evidence_refs") or []),
                "instruction": "Do not modify campaign artifacts; cite the exact refs used for every score.",
            },
            "required_output": {
                "independent_dimension_scores": {key: "number 1-10" for key in DEFAULT_DIMENSIONS},
                "blockers": ["string"],
                "evidence_refs": ["artifact reference actually inspected"],
                "report": "venue-calibrated review explanation",
            },
        }
        stdin_text = json.dumps(packet, ensure_ascii=False)
        started_at = datetime.now(UTC).isoformat()
        process = self.popen_factory(
            list(self.argv), cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, shell=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        pid = int(process.pid)
        campaign_pid = request.get("campaign_pid")
        if campaign_pid is not None and int(campaign_pid) == pid:
            process.kill()
            raise RuntimeError("viewer evaluator must not share the campaign process")
        try:
            stdout, stderr = process.communicate(stdin_text, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise RuntimeError(f"independent evaluator timed out after {self.timeout}s") from exc
        if process.returncode != 0:
            raise RuntimeError(
                f"independent evaluator exited {process.returncode}: {(stderr or '').strip()[:500]}"
            )
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("independent evaluator returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("independent evaluator result must be a JSON object")
        return {
            **result,
            "evaluator_provenance": {
                "mode": "separate_process",
                "backend_executable": self.argv[0],
                "argv": list(self.argv),
                "pid": pid,
                "campaign_pid": campaign_pid,
                "process_is_independent": campaign_pid is None or int(campaign_pid) != pid,
                "fresh_workdir": str(workdir),
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "exit_code": process.returncode,
                "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            },
        }


@dataclass(frozen=True)
class VenueReviewer:
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS

    def review(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("request_id") or "")
        venue = request.get("venue") or {}
        evidence_refs = request.get("evidence_refs") or []
        scores = request.get("independent_dimension_scores")
        base = {
            "protocol_version": 1,
            "request_id": request_id,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "venue": venue,
            "calibration": {
                "scale": "1-10 internal readiness scale; not acceptance probability",
                "anchors": {
                    "1-3": "fundamental flaw or no usable evidence",
                    "4-5": "interesting but below the target venue bar",
                    "6": "borderline; material gaps remain",
                    "7": "credible target-venue submission",
                    "8": "strong submission with clear contribution",
                    "9": "exceptional internal readiness; oral remains uncertain",
                    "10": "reserved; never inferred from self-report alone",
                },
                "oral_gate": "overall >= 8.5, novelty/significance >= 8, technical/rigor >= 7, zero blockers",
                "warning": "This score calibrates evidence readiness only. It cannot predict reviewer assignment, acceptance, or Oral selection.",
            },
        }
        if not isinstance(scores, Mapping):
            return {
                **base,
                "state": "awaiting_evaluator",
                "overall": None,
                "oral_readiness": "unknown",
                "blockers": ["independent evaluator did not provide dimension scores"],
                "detail": "No score was fabricated. Run the separate Viewer backend against the referenced artifacts.",
            }
        missing = [dimension for dimension in self.dimensions if dimension not in scores]
        invalid = [
            dimension for dimension in self.dimensions
            if dimension in scores and not self._valid_score(scores[dimension])
        ]
        blockers = list(request.get("blockers") or [])
        if not evidence_refs:
            blockers.append("no independently readable evidence references")
        if missing or invalid:
            blockers.extend([f"missing score: {value}" for value in missing])
            blockers.extend([f"invalid 1-10 score: {value}" for value in invalid])
            return {**base, "state": "invalid_input", "overall": None, "oral_readiness": "unknown", "blockers": blockers}
        normalized = {dimension: float(scores[dimension]) for dimension in self.dimensions}
        weights = self._weights(venue.get("rubric_weights") or request.get("rubric_weights") or {})
        overall = round(sum(normalized[key] * weights[key] for key in self.dimensions), 2)
        oral_ready = (
            overall >= 8.5 and normalized["novelty"] >= 8 and normalized["significance"] >= 8
            and normalized["technical_quality"] >= 7 and normalized["empirical_rigor"] >= 7
            and not blockers
        )
        return {
            **base,
            "state": "scored",
            "dimension_scores": normalized,
            "weights": weights,
            "overall": overall,
            "oral_readiness": "aspirational_gate_pass" if oral_ready else "not_yet",
            "blockers": blockers,
            "evidence_refs": list(evidence_refs),
        }

    def _weights(self, provided: Mapping[str, Any]) -> dict[str, float]:
        if provided:
            unknown = set(provided) - set(self.dimensions)
            if unknown:
                raise ValueError(f"unknown rubric dimensions: {sorted(unknown)}")
            weights = {key: float(provided.get(key, 0.0)) for key in self.dimensions}
            total = sum(weights.values())
            if total <= 0:
                raise ValueError("rubric weights must sum to a positive value")
            return {key: value / total for key, value in weights.items()}
        return {
            "novelty": 0.20, "significance": 0.16, "technical_quality": 0.18,
            "empirical_rigor": 0.18, "clarity": 0.08, "reproducibility": 0.10,
            "venue_fit": 0.10,
        }

    @staticmethod
    def _valid_score(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= float(value) <= 10


def process_viewer_request(
    request: Mapping[str, Any],
    evaluator: IndependentEvaluatorProcess | None = None,
) -> dict[str, Any]:
    try:
        enriched = dict(request)
        snapshot_state = enriched.get("evidence_snapshot_state")
        if snapshot_state is not None:
            snapshot = enriched.get("evidence_snapshot")
            digest = enriched.get("evidence_snapshot_sha256")
            if not isinstance(snapshot, Mapping) or not isinstance(digest, str):
                raise ValueError("verified evidence snapshot is required")
            verified_state, verified_refs = validate_evidence_snapshot(snapshot, digest)
            if verified_state == "empty":
                return {
                    "protocol_version": 1,
                    "request_id": str(request.get("request_id") or ""),
                    "state": "awaiting_evidence",
                    "overall": None,
                    "detail": "Verified evidence snapshot is empty; no score was produced.",
                    "evidence_snapshot_sha256": digest,
                    "evidence_refs": [],
                }
            # Evidence references are derived from authenticated snapshot
            # contents, never from caller-supplied rubric text.
            enriched["evidence_refs"] = list(verified_refs)
        evaluator_provenance = None
        evaluator_report = None
        if not isinstance(enriched.get("independent_dimension_scores"), Mapping) and evaluator is not None:
            external = evaluator.evaluate(enriched)
            enriched["independent_dimension_scores"] = external.get("independent_dimension_scores")
            enriched["blockers"] = external.get("blockers") or []
            enriched["evidence_refs"] = external.get("evidence_refs") or enriched.get("evidence_refs") or []
            evaluator_provenance = external.get("evaluator_provenance")
            evaluator_report = external.get("report")
        result = VenueReviewer().review(enriched)
        if evaluator_provenance:
            result["evaluator_provenance"] = evaluator_provenance
            result["independent_report"] = evaluator_report
        return result
    except (RuntimeError, TypeError, ValueError) as exc:
        return {
            "protocol_version": 1,
            "request_id": str(request.get("request_id") or ""),
            "state": "invalid_input",
            "overall": None,
            "error": str(exc),
        }


def _stdio(evaluator: IndependentEvaluatorProcess | None = None) -> int:
    for raw in sys.stdin:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("request must be a JSON object")
            result = process_viewer_request(value, evaluator)
        except (json.JSONDecodeError, ValueError) as exc:
            result = {"protocol_version": 1, "state": "invalid_input", "overall": None, "error": str(exc)}
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def _once(queue_dir: Path, evaluator: IndependentEvaluatorProcess | None = None) -> int:
    queue = ViewerQueue(queue_dir)
    pending = queue.next_request()
    if pending is None:
        return 0
    path, request = pending
    queue.complete(path, process_viewer_request(request, evaluator))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Argus Research Data Flywheel independent Viewer")
    parser.add_argument("--queue-dir", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--evaluator-command-json",
        help="JSON argv for a separate evaluator protocol adapter; never put credentials here",
    )
    parser.add_argument("--evaluator-work-root", type=Path)
    parser.add_argument("--evaluator-timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    evaluator = None
    if args.evaluator_command_json:
        command = json.loads(args.evaluator_command_json)
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            parser.error("--evaluator-command-json must be a JSON list of strings")
        work_root = args.evaluator_work_root or (
            (args.queue_dir / "evaluator-work") if args.queue_dir else Path.cwd() / ".viewer-work"
        )
        evaluator = IndependentEvaluatorProcess(command, work_root=work_root, timeout=args.evaluator_timeout)
    if args.queue_dir:
        return _once(args.queue_dir, evaluator)
    return _stdio(evaluator)


if __name__ == "__main__":
    raise SystemExit(main())
