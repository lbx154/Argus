from __future__ import annotations

from pathlib import Path

from argus_skill.life.memory import JournalEntry, LifeMemory
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
    certified = JournalEntry.new(
        kind="mission_complete",
        title="final submission",
        summary="certified",
        extra={"final_submission_certified": True},
    )
    sup.memory.journal.append(certified)
    sup._persist_final_submission_cert_if_needed(certified)
    for idx in range(51):
        sup.memory.journal.append(
            JournalEntry.new(
                kind="mission_complete",
                title=f"later {idx}",
                summary="later",
                extra={"final_submission_certified": False},
            )
        )

    assert sup._final_submission_cert_path().exists()
    assert sup._journal_has_full_emnlp_gate_success() is True
