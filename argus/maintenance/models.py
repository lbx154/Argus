"""Typed data contracts shared by Doctor, Repair, CLI, and support exports."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Scope = Literal[
    "host", "install", "cli", "web", "desktop", "backend", "daemon", "project", "update"
]
Severity = Literal["info", "warning", "error", "critical"]
Risk = Literal["safe", "consent", "manual"]


@dataclass(frozen=True)
class DoctorFinding:
    code: str
    scope: Scope
    severity: Severity
    ok: bool
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    repair_action_ids: tuple[str, ...] = ()
    recommendation: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "scope": self.scope,
            "severity": self.severity,
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "evidence": dict(self.evidence),
            "repair_action_ids": list(self.repair_action_ids),
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True)
class DoctorReport:
    schema_version: int
    target_fingerprint: str
    generated_at: str
    findings: tuple[DoctorFinding, ...]

    @property
    def ok(self) -> bool:
        return all(
            item.ok or item.severity in {"info", "warning"}
            for item in self.findings
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "target_fingerprint": self.target_fingerprint,
            "generated_at": self.generated_at,
            "findings": [item.to_jsonable() for item in self.findings],
        }


@dataclass(frozen=True)
class RepairAction:
    id: str
    provider: str
    risk: Risk
    target: str
    precondition: dict[str, Any] = field(default_factory=dict)
    verify_codes: tuple[str, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "risk": self.risk,
            "target": self.target,
            "precondition": dict(self.precondition),
            "verify_codes": list(self.verify_codes),
        }


@dataclass(frozen=True)
class RepairPlanRef:
    plan_id: str
    path: Path
    status: str
    actions: tuple[RepairAction, ...]


@dataclass(frozen=True)
class RepairResult:
    plan_id: str
    status: str
    actions: tuple[dict[str, Any], ...]
    verification: dict[str, Any]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "status": self.status,
            "actions": [dict(item) for item in self.actions],
            "verification": dict(self.verification),
        }
