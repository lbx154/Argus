from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.terminal_state import build_project_state_signature


def _make_supervisor(tmp_path: Path) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    mem.init()
    project = tmp_path / "project"
    project.mkdir()

    class _Sink:
        def handle_event(self, event: dict) -> None:
            pass

    class _Runner:
        pass

    return LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_state_dir=tmp_path / "life",
            project_worktree=project,
            artifact_root=project,
        ),
    )


def test_final_submission_cert_tracks_current_project_state(tmp_path: Path):
    sup = _make_supervisor(tmp_path)
    paper = tmp_path / "project" / "paper"
    paper.mkdir()
    source = paper / "main.tex"
    source.write_text("certified\n", encoding="utf-8")
    signature = sup._final_submission_signature()
    events = tmp_path / "life" / "events.jsonl"
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.mission.completed",
            "title": "final submission",
            "success": True,
            "final_submission_certified": True,
            "final_submission_signature": signature,
        }) + "\n")
    for idx in range(51):
        with events.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "life.mission.completed",
                        "title": f"later {idx}",
                        "success": True,
                        "final_submission_certified": False,
                    }
                )
                + "\n"
            )

    assert sup._journal_has_full_paper_gate_success() is True
    source.write_text("changed after review\n", encoding="utf-8")
    assert sup._journal_has_full_paper_gate_success() is False


def test_legacy_cert_is_valid_only_while_project_is_unchanged(tmp_path: Path):
    sup = _make_supervisor(tmp_path)
    source = tmp_path / "project" / "paper" / "main.tex"
    source.parent.mkdir()
    source.write_text("legacy certified\n", encoding="utf-8")
    certified_at = time.time()
    events = tmp_path / "life" / "events.jsonl"
    events.write_text(json.dumps({
        "type": "life.mission.completed",
        "title": "legacy final submission",
        "success": True,
        "final_submission_certified": True,
        "ts": certified_at,
    }) + "\n", encoding="utf-8")

    assert sup._journal_has_full_paper_gate_success() is True
    source.write_text("changed\n", encoding="utf-8")
    assert sup._journal_has_full_paper_gate_success() is False


def test_gate_can_match_older_certification_after_exact_revert(tmp_path: Path):
    sup = _make_supervisor(tmp_path)
    source = tmp_path / "project" / "paper" / "main.tex"
    source.parent.mkdir()
    events = tmp_path / "life" / "events.jsonl"

    source.write_text("state A\n", encoding="utf-8")
    signature_a = sup._final_submission_signature()
    source.write_text("state B\n", encoding="utf-8")
    signature_b = sup._final_submission_signature()
    events.write_text(
        "\n".join([
            json.dumps({
                "type": "life.mission.completed",
                "title": "cert A",
                "success": True,
                "final_submission_certified": True,
                "final_submission_signature": signature_a,
            }),
            json.dumps({
                "type": "life.mission.completed",
                "title": "cert B",
                "success": True,
                "final_submission_certified": True,
                "final_submission_signature": signature_b,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    source.write_text("state A\n", encoding="utf-8")

    assert sup._journal_has_full_paper_gate_success() is True


def test_project_signature_ignores_history_only_commits(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "paper.tex").write_text("same tree\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "paper.tex"], cwd=project, check=True)
    commit = [
        "git",
        "-c",
        "user.name=Argus Test",
        "-c",
        "user.email=argus@example.com",
        "commit",
        "-q",
    ]
    subprocess.run([*commit, "-m", "initial"], cwd=project, check=True)
    before = build_project_state_signature(project_root=project)
    subprocess.run(
        [*commit, "--allow-empty", "-m", "history only"],
        cwd=project,
        check=True,
    )

    assert build_project_state_signature(project_root=project) == before
