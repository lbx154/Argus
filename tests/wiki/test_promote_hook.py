"""After a mission the Reviewer accepted, tagged Wiki pages reach the shared roots."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_reason: str = ""
    stop_kind: str | None = None
    recoverable: bool = False
    rounds: int = 1
    final_review_status: str = ""
    final_review_source: str = ""
    final_review_reason: str = ""
    final_message: str = ""
    summary: str = ""
    final_output: str = ""


class _FixedOutcomeRunner:
    def __init__(self, outcome: _Outcome) -> None:
        self._outcome = outcome
        self.kwargs: dict[str, Any] = {}

    def execute(self, **kwargs: Any) -> _Outcome:
        self.kwargs = kwargs
        return self._outcome


def _supervisor(tmp_path, outcome: _Outcome) -> tuple[LifeSupervisor, _Sink]:
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_FixedOutcomeRunner(outcome),
        sink=sink,
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01),
    )
    return supervisor, sink


def _page(title: str, audience: str) -> str:
    return f"---\ntitle: {title}\ndescription: About {title}\naudience: {audience}\n---\n\nBody.\n"


def _seed_wiki(workdir: Path) -> None:
    wiki = workdir / ".autors" / "proj" / "wiki"
    (wiki / "pages").mkdir(parents=True, exist_ok=True)
    (wiki / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "pages" / "protocol.md").write_text(_page("Protocol", "vertical"), encoding="utf-8")
    (wiki / "pages" / "hosts.md").write_text(_page("Hosts", "global"), encoding="utf-8")
    (wiki / "pages" / "local.md").write_text(
        "---\ntitle: Local\ndescription: Stays here\n---\n\nBody.\n", encoding="utf-8"
    )


def test_reviewer_accepted_mission_shares_tagged_pages(tmp_path) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        _Outcome(success=True, status="done", final_review_status="done", final_review_source="reviewer"),
    )
    workdir = supervisor._project_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    _seed_wiki(workdir)
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Write it down",
            objective="Record the protocol",
            manager_decision={"vertical": "research"},
        )
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    shared = supervisor._budget_global_root() / "wiki"
    assert (shared / "_shared_verticals" / "research" / "pages" / "protocol.md").is_file()
    assert (shared / "_global" / "pages" / "hosts.md").is_file()
    assert not (shared / "_global" / "pages" / "local.md").exists()
    index = (shared / "_shared_verticals" / "research" / "INDEX.md").read_text(encoding="utf-8")
    assert "- [Protocol](pages/protocol.md) — About Protocol" in index


def test_mission_without_reviewer_acceptance_shares_nothing(tmp_path) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        _Outcome(success=True, status="done", final_review_status="done", final_review_source="self"),
    )
    workdir = supervisor._project_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    _seed_wiki(workdir)
    supervisor.memory.backlog.add(
        BacklogItem.new(title="Write it down", objective="Record the protocol", manager_decision={"vertical": "research"})
    )

    supervisor.tick()

    assert not (supervisor._budget_global_root() / "wiki").exists()


def test_promotion_failure_never_fails_the_mission(tmp_path, monkeypatch) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        _Outcome(success=True, status="done", final_review_status="done", final_review_source="reviewer"),
    )
    workdir = supervisor._project_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    _seed_wiki(workdir)
    supervisor.memory.backlog.add(
        BacklogItem.new(title="Write it down", objective="Record the protocol", manager_decision={"vertical": "research"})
    )

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("argus.wiki.promote.promote_wiki_pages", fail)

    result = supervisor.tick()

    assert result is not None and result["success"] is True
