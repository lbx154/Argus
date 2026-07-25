"""Generation-bound, mechanically verified freshness for repair missions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Literal

from .file_lock import exclusive_file_lock

FailureClassification = Literal["answer_change_required", "infrastructure_only"]
FailureSignatureStatus = Literal[
    "changed", "unchanged", "unavailable", "no_execution"
]
EXPECTATION_PATH = Path(".argus") / "repair-objective.json"


@contextmanager
def repair_state_lock(project_root: Path | str) -> Iterator[None]:
    """Serialize signed repair-expectation updates across CLI processes."""
    lock_path = repair_state_paths(project_root)[0].with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        with exclusive_file_lock(handle):
            yield


def _safe_project_path(project_root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe project-relative path: {value!r}")
    resolved = (project_root / Path(*relative.parts)).resolve()
    if resolved != project_root and project_root not in resolved.parents:
        raise ValueError(f"path escapes project root: {value!r}")
    return resolved


def hash_project_files(project_root: Path | str, paths: tuple[str, ...]) -> str:
    root = Path(project_root).resolve()
    digest = hashlib.sha256()
    if not paths:
        raise ValueError("answer paths must be non-empty")
    for value in sorted(set(paths)):
        path = _safe_project_path(root, value)
        if not path.is_file():
            raise FileNotFoundError(path)
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class FreshnessExpectation:
    generation: int
    iteration: int
    mission_id: str
    repair: bool
    answer_paths: tuple[str, ...] = ()
    prior_answer_hash: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if self.generation < 1 or self.iteration < 1:
            raise ValueError("generation and iteration must be positive")
        if not self.mission_id.strip():
            raise ValueError("mission_id must be non-empty")
        if self.repair and (not self.answer_paths or not self.prior_answer_hash):
            raise ValueError("repairs require answer_paths and prior_answer_hash")

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generation": self.generation,
            "iteration": self.iteration,
            "mission_id": self.mission_id,
            "repair": self.repair,
            "answer_paths": list(self.answer_paths),
            "prior_answer_hash": self.prior_answer_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_jsonable(cls, payload: dict[str, Any]) -> "FreshnessExpectation":
        paths = payload.get("answer_paths", [])
        if not isinstance(paths, list):
            raise ValueError("answer_paths must be a list")
        return cls(
            generation=int(payload["generation"]),
            iteration=int(payload["iteration"]),
            mission_id=str(payload["mission_id"]),
            repair=bool(payload["repair"]),
            answer_paths=tuple(str(path) for path in paths),
            prior_answer_hash=str(payload.get("prior_answer_hash") or ""),
            created_at=float(payload.get("created_at") or 0.0),
        )


@dataclass(frozen=True)
class RepairFreshnessEvidence:
    artifact_generation: int
    artifact_iteration: int
    current_answer_hash: str
    preflight_path: str
    preflight_hash: str
    regression_evidence: tuple[tuple[str, str], ...]
    failure_classification: FailureClassification
    official_failure_signature_status: FailureSignatureStatus = "unavailable"
    public_hypothesis_path: str = ""
    public_hypothesis_hash: str = ""

    @classmethod
    def from_jsonable(cls, payload: dict[str, Any]) -> "RepairFreshnessEvidence":
        regression = payload.get("regression_evidence", [])
        if not isinstance(regression, list):
            raise ValueError("regression_evidence must be a list")
        classification = payload.get("failure_classification")
        if classification not in {"answer_change_required", "infrastructure_only"}:
            raise ValueError("invalid failure_classification")
        signature_status = payload.get(
            "official_failure_signature_status", "unavailable"
        )
        if signature_status not in {
            "changed",
            "unchanged",
            "unavailable",
            "no_execution",
        }:
            raise ValueError("invalid official_failure_signature_status")
        return cls(
            artifact_generation=int(payload["artifact_generation"]),
            artifact_iteration=int(payload["artifact_iteration"]),
            current_answer_hash=str(payload.get("current_answer_hash") or ""),
            preflight_path=str(payload.get("preflight_path") or ""),
            preflight_hash=str(payload.get("preflight_hash") or ""),
            regression_evidence=tuple(
                (str(row["path"]), str(row["sha256"]))
                for row in regression
                if isinstance(row, dict) and "path" in row and "sha256" in row
            ),
            failure_classification=classification,
            official_failure_signature_status=signature_status,
            public_hypothesis_path=str(
                payload.get("public_hypothesis_path") or ""
            ),
            public_hypothesis_hash=str(
                payload.get("public_hypothesis_hash") or ""
            ),
        )


@dataclass(frozen=True)
class FreshnessGateResult:
    passed: bool
    issues: tuple[str, ...] = ()


def evaluate_repair_freshness(
    project_root: Path | str,
    expectation: FreshnessExpectation,
    evidence: RepairFreshnessEvidence | None,
) -> FreshnessGateResult:
    if not expectation.repair:
        return FreshnessGateResult(True)
    if evidence is None:
        return FreshnessGateResult(False, ("missing_repair_freshness_evidence",))
    root = Path(project_root).resolve()
    issues: list[str] = []
    if (evidence.artifact_generation, evidence.artifact_iteration) != (
        expectation.generation,
        expectation.iteration,
    ):
        issues.append("stale_artifact_generation")
    try:
        actual_hash = hash_project_files(root, expectation.answer_paths)
    except (FileNotFoundError, OSError, ValueError):
        actual_hash = ""
        issues.append("answer_artifact_unavailable")
    if not evidence.current_answer_hash or evidence.current_answer_hash != actual_hash:
        issues.append("answer_hash_mismatch")
    if (
        evidence.official_failure_signature_status == "no_execution"
        and evidence.failure_classification != "infrastructure_only"
    ):
        issues.append("no_execution_misclassified_as_rtl")

    try:
        preflight_path = _safe_project_path(root, evidence.preflight_path)
        preflight_bytes = preflight_path.read_bytes()
        preflight = json.loads(preflight_bytes)
        if not isinstance(preflight, dict):
            raise ValueError("preflight attestation must be a JSON object")
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        preflight_bytes = b""
        preflight = {}
        issues.append("preflight_attestation_unavailable")
    if (
        not evidence.preflight_hash
        or evidence.preflight_hash != hashlib.sha256(preflight_bytes).hexdigest()
    ):
        issues.append("preflight_hash_mismatch")
    try:
        preflight_generation = int(preflight.get("generation") or 0)
        preflight_iteration = int(preflight.get("iteration") or 0)
    except (TypeError, ValueError):
        preflight_generation = 0
        preflight_iteration = 0
        issues.append("invalid_preflight_metadata")
    if (preflight_generation, preflight_iteration) != (
        expectation.generation,
        expectation.iteration,
    ):
        issues.append("stale_preflight_generation")
    if str(preflight.get("repair_mission_id") or "") != expectation.mission_id:
        issues.append("stale_preflight_mission")
    if str(preflight.get("status") or "").strip().lower() != "pass":
        issues.append("preflight_not_passed")

    genuine_no_execution = (
        evidence.failure_classification == "infrastructure_only"
        and evidence.official_failure_signature_status == "no_execution"
    )
    unchanged_signature_requires_public_repair = (
        evidence.official_failure_signature_status == "unchanged"
        and not genuine_no_execution
    )
    validate_regressions = (
        evidence.failure_classification == "answer_change_required"
        or unchanged_signature_requires_public_repair
    )
    regression_payloads: list[dict[str, Any]] = []
    if evidence.failure_classification == "answer_change_required":
        if actual_hash and actual_hash == expectation.prior_answer_hash:
            issues.append("unchanged_answer_hash")
        if not evidence.regression_evidence:
            issues.append("missing_regression_evidence")
    if validate_regressions:
        for value, expected_hash in evidence.regression_evidence:
            try:
                path = _safe_project_path(root, value)
                regression_bytes = path.read_bytes()
                actual = hashlib.sha256(regression_bytes).hexdigest()
                if not expected_hash or not hmac.compare_digest(actual, expected_hash):
                    issues.append("regression_hash_mismatch")
                    break
                regression = json.loads(regression_bytes)
                if not isinstance(regression, dict):
                    raise ValueError("regression evidence must be a JSON object")
                if (
                    str(regression.get("status") or "").strip().lower() != "pass"
                    or int(regression.get("generation") or 0)
                    != expectation.generation
                    or int(regression.get("iteration") or 0)
                    != expectation.iteration
                    or str(regression.get("repair_mission_id") or "")
                    != expectation.mission_id
                ):
                    issues.append("regression_not_passed")
                    break
                regression_payloads.append(regression)
            except (OSError, ValueError, json.JSONDecodeError):
                issues.append("regression_hash_mismatch")
                break
    if unchanged_signature_requires_public_repair:
        try:
            hypothesis_path = _safe_project_path(
                root, evidence.public_hypothesis_path
            )
            hypothesis_bytes = hypothesis_path.read_bytes()
            hypothesis = json.loads(hypothesis_bytes)
            if not isinstance(hypothesis, dict):
                raise ValueError("public hypothesis must be a JSON object")
            hypothesis_hash = hashlib.sha256(hypothesis_bytes).hexdigest()
            if (
                not evidence.public_hypothesis_hash
                or hypothesis_hash != evidence.public_hypothesis_hash
                or str(hypothesis.get("provenance_scope") or "")
                != "public_only"
                or not bool(hypothesis.get("changed_from_prior"))
                or not str(hypothesis.get("hypothesis") or "").strip()
                or int(hypothesis.get("generation") or 0)
                != expectation.generation
                or int(hypothesis.get("iteration") or 0)
                != expectation.iteration
                or str(hypothesis.get("repair_mission_id") or "")
                != expectation.mission_id
            ):
                issues.append(
                    "unchanged_signature_requires_new_public_hypothesis"
                )
        except (OSError, ValueError, json.JSONDecodeError):
            issues.append("unchanged_signature_requires_new_public_hypothesis")
        if not any(
            regression.get("provenance_scope") == "public_only"
            and bool(regression.get("changed_from_prior"))
            for regression in regression_payloads
        ):
            issues.append("unchanged_signature_requires_new_public_test")
    return FreshnessGateResult(not issues, tuple(dict.fromkeys(issues)))


def _canonical_expectation(expectation: FreshnessExpectation) -> bytes:
    return json.dumps(
        expectation.to_jsonable(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def repair_state_paths(project_root: Path | str) -> tuple[Path, Path, Path]:
    from .paths import global_root

    fingerprint = hashlib.sha256(
        str(Path(project_root).resolve()).encode("utf-8")
    ).hexdigest()
    state = global_root() / "repair_expectations" / fingerprint
    return (
        state / "expectation.json",
        state / "private.ed25519",
        state / "public.ed25519",
    )


def write_freshness_expectation(
    project_root: Path | str,
    expectation: FreshnessExpectation,
) -> Path:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as exc:
        raise RuntimeError(
            "repair freshness signing requires argus-skill[signing]"
        ) from exc
    trusted_path, private_path, public_path = repair_state_paths(project_root)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    if private_path.exists():
        private = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
    else:
        private = Ed25519PrivateKey.generate()
        private_path.write_bytes(
            private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        private_path.chmod(0o600)
        public_path.write_bytes(
            private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
    payload = expectation.to_jsonable()
    payload["signature"] = base64.b64encode(
        private.sign(_canonical_expectation(expectation))
    ).decode("ascii")
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for path in (trusted_path, Path(project_root) / EXPECTATION_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return trusted_path


def load_freshness_expectation(project_root: Path | str) -> FreshnessExpectation:
    with repair_state_lock(project_root):
        return _load_freshness_expectation_unlocked(project_root)


def _load_freshness_expectation_unlocked(
    project_root: Path | str,
) -> FreshnessExpectation:
    trusted_path, _private_path, public_path = repair_state_paths(project_root)
    if not trusted_path.exists():
        raise FileNotFoundError(trusted_path)
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError(
            "repair freshness verification requires argus-skill[signing]"
        ) from exc
    payload = json.loads(trusted_path.read_text(encoding="utf-8"))
    expectation = FreshnessExpectation.from_jsonable(payload)
    try:
        signature = base64.b64decode(str(payload.get("signature") or ""), validate=True)
        Ed25519PublicKey.from_public_bytes(public_path.read_bytes()).verify(
            signature, _canonical_expectation(expectation)
        )
    except (ValueError, InvalidSignature):
        raise ValueError("repair expectation signature mismatch")
    return expectation


def load_repair_freshness_evidence(path: Path | str) -> RepairFreshnessEvidence:
    return RepairFreshnessEvidence.from_jsonable(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
