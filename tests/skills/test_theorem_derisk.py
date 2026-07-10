from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from argus_skill.skills.checklist_store import apply_checklist_ops
from argus_skill.skills.research_derisk import selected_derisk_kind
from argus_skill.skills.theorem_derisk import (
    EXPECTED_ISOMORPHISM_COUNTS,
    EXPECTED_LABELED_COUNTS,
    EXPECTED_ORDERS,
    REQUIRED_LEMMA_IDS,
    validate_for_gate,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lemma_rows() -> list[dict[str, str]]:
    return [
        {"id": lemma_id, "verdict": "pass", "evidence": "proof lines", "reason": "sound"}
        for lemma_id in REQUIRED_LEMMA_IDS
    ]


def _write_good_project(root: Path) -> Path:
    research = root / "research"
    research.mkdir(parents=True)
    apply_checklist_ops(
        root,
        [
            {
                "op": "add",
                "stage": "research",
                "id": "research.signal_derisk",
                "statement": "Use research/THEOREM_DERISK.json; no performance metrics.",
                "evidence_hint": "research/THEOREM_DERISK_LOG.txt",
            }
        ],
    )

    proof = research / "RESEARCH_BRIEF.md"
    proof.write_text("self-contained proof\n", encoding="utf-8")
    enumeration = research / "friendship_graph_enumeration_n7.json"
    enumeration.write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "n": order,
                        "qualifying_labeled_graphs": count,
                        "isomorphism_classes": classes,
                    }
                    for order, count, classes in zip(
                        EXPECTED_ORDERS,
                        EXPECTED_LABELED_COUNTS,
                        EXPECTED_ISOMORPHISM_COUNTS,
                        strict=True,
                    )
                ]
            }
        ),
        encoding="utf-8",
    )

    repo = root / "code" / "references" / "mathlib4"
    source = repo / "Archive" / "Wiedijk100Theorems" / "FriendshipGraphs.lean"
    source.parent.mkdir(parents=True)
    source.write_text("theorem friendship_theorem := by trivial\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://example.test/mathlib4.git"],
        check=True,
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.test",
    }
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "fixture"],
        check=True,
        env=env,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    audit = research / "INDEPENDENT_PROOF_AUDIT.json"
    audit.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verdict": "pass",
                "lemmas": _lemma_rows(),
                "blocking_issues": [],
            }
        ),
        encoding="utf-8",
    )
    (research / "INDEPENDENT_PROOF_AUDIT_LOG.txt").write_text(
        "independent lemma audit: PASS\n", encoding="utf-8"
    )
    (research / "THEOREM_DERISK_LOG.txt").write_text(
        "raw theorem gate audit\n", encoding="utf-8"
    )

    derisk = research / "THEOREM_DERISK.json"
    derisk.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_type": "finite_graph_theorem",
                "verdict": "pass",
                "performance_metrics_applicable": False,
                "no_performance_metrics": True,
                "proof": {
                    "path": "research/RESEARCH_BRIEF.md",
                    "sha256": _hash(proof),
                    "lemmas": _lemma_rows(),
                },
                "independent_audit": {
                    "path": "research/INDEPENDENT_PROOF_AUDIT.json",
                    "log_path": "research/INDEPENDENT_PROOF_AUDIT_LOG.txt",
                    "verdict": "pass",
                },
                "enumeration_audit": {
                    "path": "research/friendship_graph_enumeration_n7.json",
                    "sha256": _hash(enumeration),
                    "orders": EXPECTED_ORDERS,
                    "qualifying_labeled_counts": EXPECTED_LABELED_COUNTS,
                    "isomorphism_class_counts": EXPECTED_ISOMORPHISM_COUNTS,
                    "non_probative": True,
                },
                "source_provenance": {
                    "original": {
                        "url": "https://www.renyi.hu/~p_erdos/1966-06.pdf",
                        "theorem": 6,
                    },
                    "formalization": {
                        "path": (
                            "code/references/mathlib4/Archive/Wiedijk100Theorems/"
                            "FriendshipGraphs.lean"
                        ),
                        "sha256": _hash(source),
                        "repo_path": "code/references/mathlib4",
                        "origin": "https://example.test/mathlib4.git",
                        "commit": commit,
                    },
                },
                "log_path": "research/THEOREM_DERISK_LOG.txt",
                "commands": [
                    "python -m argus_skill.skills.theorem_derisk validate "
                    "--project-root . --derisk research/THEOREM_DERISK.json"
                ],
            }
        ),
        encoding="utf-8",
    )
    return derisk


def test_good_theorem_derisk_passes(tmp_path: Path) -> None:
    derisk = _write_good_project(tmp_path)
    reject, concern = validate_for_gate(tmp_path, derisk)
    assert reject is False
    assert concern == ""


def test_performance_metric_fields_are_rejected(tmp_path: Path) -> None:
    derisk = _write_good_project(tmp_path)
    raw = json.loads(derisk.read_text(encoding="utf-8"))
    raw["baseline_metric"] = 0.0
    derisk.write_text(json.dumps(raw), encoding="utf-8")
    reject, concern = validate_for_gate(tmp_path, derisk)
    assert reject is True
    assert "performance_metrics_forbidden" in concern


def test_enumeration_overclaim_is_rejected(tmp_path: Path) -> None:
    derisk = _write_good_project(tmp_path)
    raw = json.loads(derisk.read_text(encoding="utf-8"))
    raw["enumeration_audit"]["non_probative"] = False
    derisk.write_text(json.dumps(raw), encoding="utf-8")
    reject, concern = validate_for_gate(tmp_path, derisk)
    assert reject is True
    assert "enumeration_overclaim" in concern


def test_dispatcher_uses_only_planner_selected_theorem_gate(tmp_path: Path) -> None:
    assert selected_derisk_kind(tmp_path) == "signal"
    _write_good_project(tmp_path)
    assert selected_derisk_kind(tmp_path) == "theorem"
