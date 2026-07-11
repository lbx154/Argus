from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


def _make_supervisor(tmp_path: Path) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    mem.init()

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
            telemetry_dir=tmp_path / "life",
        ),
    )


def test_final_submission_cert_persists_after_journal_tail_ages_out(tmp_path: Path):
    sup = _make_supervisor(tmp_path)
    sup._persist_final_submission_certification(title="final submission")
    events = tmp_path / "life" / "events.jsonl"
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

    assert sup._final_submission_cert_path().exists()
    assert sup._journal_has_full_paper_gate_success() is True
