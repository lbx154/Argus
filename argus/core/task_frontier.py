"""Durable semantic progress for one long-running mission.

A frontier records what the mission currently knows and owes.  It does not turn
progress into a score: a better artifact, retired risk, reduced uncertainty,
information-gaining failure, or bounded repair debt are different transitions.
The Reviewer describes the transition in ordinary named lines; this module owns
the persisted state and the regression-envelope check.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

TASK_FRONTIER_VERSION = 1
FRONTIER_CHANGES = frozenset({
    "artifact_improved",
    "risk_reduced",
    "uncertainty_reduced",
    "information_gain",
    "bounded_regression",
    "recovered",
    "unchanged_failure",
    "expanding_regression",
    "unexplained_regression",
})
_PROGRESS_CHANGES = frozenset({
    "artifact_improved",
    "risk_reduced",
    "uncertainty_reduced",
    "information_gain",
    "bounded_regression",
    "recovered",
})
_REGRESSION_CHANGES = frozenset({
    "bounded_regression",
    "expanding_regression",
    "unexplained_regression",
})


# Stop-loss bounds against a runaway state file. The production path currently
# feeds no data into them: the Reviewer prompt deliberately does not request
# the FRONTIER_* lines, so every persisted frontier still has
# transition_count == 0 (magic-hyperparameters audit 2026-09-05).
# Items accepted per field from one Reviewer-authored transition.
FRONTIER_TRANSITION_ITEM_LIMIT = 40
# Items retained per cumulative field across transitions. Runtime merges and
# from_mapping loads must both keep the newest tail so state does not drift
# across a restart.
FRONTIER_CUMULATIVE_FIELD_LIMIT = 80
# Transition records retained in history.
FRONTIER_HISTORY_LIMIT = 100


def _texts(
    value: Any, *, limit: int | None = FRONTIER_TRANSITION_ITEM_LIMIT
) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return list(dict.fromkeys(
        text
        for item in value
        if (text := str(item or "").strip())
    ))[:limit]


def _merge(
    existing: list[str],
    incoming: list[str],
    *,
    limit: int = FRONTIER_CUMULATIVE_FIELD_LIMIT,
) -> list[str]:
    return list(dict.fromkeys([*existing, *incoming]))[-limit:]


def _cumulative_texts(value: Any) -> list[str]:
    """Load one cumulative field, keeping the same newest tail _merge keeps."""
    return _merge([], _texts(value, limit=None))


@dataclass(frozen=True)
class RegressionEnvelope:
    cause: str = ""
    scope: str = ""
    budget: str = ""
    recovery_test: str = ""
    exit_trigger: str = ""

    @property
    def complete(self) -> bool:
        return all((
            self.cause.strip(),
            self.scope.strip(),
            self.budget.strip(),
            self.recovery_test.strip(),
            self.exit_trigger.strip(),
        ))

    @classmethod
    def from_mapping(cls, value: Any) -> "RegressionEnvelope":
        row = value if isinstance(value, Mapping) else {}
        return cls(
            cause=str(row.get("cause") or "").strip()[:1000],
            scope=str(row.get("scope") or "").strip()[:1000],
            budget=str(row.get("budget") or "").strip()[:1000],
            recovery_test=str(row.get("recovery_test") or "").strip()[:1000],
            exit_trigger=str(row.get("exit_trigger") or "").strip()[:1000],
        )


@dataclass
class TaskFrontier:
    mission_id: str
    objective: str
    invariants: list[str] = field(default_factory=list)
    current_hypothesis: str = ""
    artifacts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    resolved_obligations: list[str] = field(default_factory=list)
    new_obligations: list[str] = field(default_factory=list)
    regressed_obligations: list[str] = field(default_factory=list)
    remaining_work: list[str] = field(default_factory=list)
    proxy_changes: list[str] = field(default_factory=list)
    uncertainty: str = ""
    next_decision_point: str = ""
    active_regression: dict[str, str] = field(default_factory=dict)
    unchanged_failure_streak: int = 0
    transition_count: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0

    @classmethod
    def initial(
        cls,
        *,
        mission_id: str,
        objective: str,
        invariants: list[str] | None = None,
        hypothesis: str = "",
        remaining_work: list[str] | None = None,
        uncertainty: str = "",
        next_decision_point: str = "",
    ) -> "TaskFrontier":
        return cls(
            mission_id=str(mission_id),
            objective=str(objective or "").strip(),
            invariants=_texts(invariants or []),
            current_hypothesis=str(hypothesis or "").strip(),
            remaining_work=_texts(remaining_work or []),
            uncertainty=str(uncertainty or "").strip(),
            next_decision_point=str(next_decision_point or "").strip(),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskFrontier":
        return cls(
            mission_id=str(value.get("mission_id") or ""),
            objective=str(value.get("objective") or ""),
            invariants=_texts(value.get("invariants")),
            current_hypothesis=str(value.get("current_hypothesis") or ""),
            artifacts=_cumulative_texts(value.get("artifacts")),
            evidence=_cumulative_texts(value.get("evidence")),
            resolved_obligations=_cumulative_texts(value.get("resolved_obligations")),
            new_obligations=_cumulative_texts(value.get("new_obligations")),
            regressed_obligations=_cumulative_texts(value.get("regressed_obligations")),
            remaining_work=_cumulative_texts(value.get("remaining_work")),
            proxy_changes=_cumulative_texts(value.get("proxy_changes")),
            uncertainty=str(value.get("uncertainty") or ""),
            next_decision_point=str(value.get("next_decision_point") or ""),
            active_regression={
                str(key): str(item)
                for key, item in (value.get("active_regression") or {}).items()
            } if isinstance(value.get("active_regression"), dict) else {},
            unchanged_failure_streak=max(
                0, int(value.get("unchanged_failure_streak") or 0)
            ),
            transition_count=max(0, int(value.get("transition_count") or 0)),
            history=[
                dict(item)
                for item in (value.get("history") or [])[-FRONTIER_HISTORY_LIMIT:]
                if isinstance(item, dict)
            ],
            updated_at=float(value.get("updated_at") or 0.0),
        )

    def apply(self, report: Mapping[str, Any], *, round_index: int) -> dict[str, Any]:
        """Apply one Reviewer-authored semantic transition and return its record."""
        change = str(report.get("change") or "").strip().lower()
        if change not in FRONTIER_CHANGES:
            return {}
        envelope = RegressionEnvelope.from_mapping(report.get("regression"))
        if change == "bounded_regression" and not envelope.complete:
            change = "unexplained_regression"

        resolved = _texts(report.get("resolved_obligations"))
        new = _texts(report.get("new_obligations"))
        regressed = _texts(report.get("regressed_obligations"))
        remaining = _texts(report.get("remaining_work"))
        artifacts = _texts(report.get("artifacts"))
        evidence = _texts(report.get("evidence"))
        proxies = _texts(report.get("proxy_changes"))

        self.resolved_obligations = _merge(self.resolved_obligations, resolved)
        self.new_obligations = _merge(self.new_obligations, new)
        self.regressed_obligations = _merge(self.regressed_obligations, regressed)
        if remaining:
            self.remaining_work = remaining
        self.artifacts = _merge(self.artifacts, artifacts)
        self.evidence = _merge(self.evidence, evidence)
        self.proxy_changes = _merge(self.proxy_changes, proxies)

        hypothesis = str(report.get("hypothesis") or "").strip()
        if hypothesis:
            self.current_hypothesis = hypothesis[:2000]
        uncertainty = str(report.get("uncertainty") or "").strip()
        if uncertainty:
            self.uncertainty = uncertainty[:2000]
        decision = str(report.get("next_decision_point") or "").strip()
        if decision:
            self.next_decision_point = decision[:2000]

        if change == "unchanged_failure":
            self.unchanged_failure_streak += 1
        elif change in _PROGRESS_CHANGES:
            self.unchanged_failure_streak = 0

        self.active_regression = (
            {key: value for key, value in asdict(envelope).items() if value}
            if change in _REGRESSION_CHANGES
            else {}
        )
        self.transition_count += 1
        record = {
            "round": max(1, int(round_index)),
            "change": change,
            "summary": str(report.get("summary") or "").strip()[:2000],
            "resolved_obligations": resolved,
            "new_obligations": new,
            "regressed_obligations": regressed,
            "remaining_work": remaining,
            "proxy_changes": proxies,
            "uncertainty": uncertainty[:2000],
            "next_decision_point": decision[:2000],
            "regression": dict(self.active_regression),
            "recorded_at": time.time(),
        }
        self.history = [*self.history, record][-FRONTIER_HISTORY_LIMIT:]
        self.updated_at = record["recorded_at"]
        return record

    @property
    def disposition(self) -> str:
        if self.history and self.history[-1].get("change") in {
            "expanding_regression",
            "unexplained_regression",
        }:
            return "replan"
        if self.unchanged_failure_streak >= 2:
            return "diagnose_or_replan"
        return "continue"

    def to_jsonable(self) -> dict[str, Any]:
        return {"schema_version": TASK_FRONTIER_VERSION, **asdict(self)}


def load_task_frontier(path: Path | str) -> TaskFrontier | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return TaskFrontier.from_mapping(payload)


def save_task_frontier(path: Path | str, frontier: TaskFrontier) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(frontier.to_jsonable(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)


__all__ = [
    "FRONTIER_CHANGES",
    "FRONTIER_CUMULATIVE_FIELD_LIMIT",
    "FRONTIER_HISTORY_LIMIT",
    "FRONTIER_TRANSITION_ITEM_LIMIT",
    "RegressionEnvelope",
    "TASK_FRONTIER_VERSION",
    "TaskFrontier",
    "load_task_frontier",
    "save_task_frontier",
]
