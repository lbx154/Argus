"""The letter to the operator."""
from __future__ import annotations

import json
import time
from pathlib import Path

from argus.core.operator_presence import operator_presence
from argus.roles.prompts.letter import build_letter_prompt
from argus.verticals.research.notes import RESEARCH_NOTES_FILENAME


def test_presence_reads_the_operator_silence_from_transcript_and_inbox(tmp_path: Path) -> None:
    now = time.time()
    (tmp_path / "transcript.jsonl").write_text(
        json.dumps({"role": "operator", "text": "go", "ts": now - 5 * 3600}) + "\n"
        + json.dumps({"role": "argus", "text": "ok", "ts": now - 4 * 3600}) + "\n",
        encoding="utf-8",
    )
    presence = operator_presence(tmp_path, now=now)
    assert presence.silence_seconds is not None
    assert abs(presence.silence_seconds - 5 * 3600) < 5
    assert "5.0 hours ago" in presence.describe()

    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"ts": now - 600, "text": "note"}) + "\n", encoding="utf-8"
    )
    presence = operator_presence(tmp_path, now=now)
    assert presence.silence_seconds is not None and presence.silence_seconds < 700
    assert "10 minutes ago" in presence.describe()


def test_the_letter_prompt_speaks_the_operator_language_and_uses_only_facts() -> None:
    prompt = build_letter_prompt(
        facts={
            "chinese": True,
            "objective": "研究自由选择推理",
            "stage_line": "Stage: experiment.",
            "missions": "- 评测 16 个模型 (complete, $3.10)",
            "hours_since_operator_wrote": 7.2,
            "hours_since_last_letter": 8.0,
        }
    )
    assert "in Chinese" in prompt
    assert "about 7 hours" in prompt
    assert "评测 16 个模型" in prompt
    assert "do not invent" in prompt
    assert "Sign as Argus" in prompt


class _Item:
    def __init__(self, item_id: str, title: str, status: str, question: str = "") -> None:
        self.id = item_id
        self.title = title
        self.status = status
        self.pending_question = question
        self.deps = ()


class _Entry:
    def __init__(self, ts: float, kind: str, title: str, summary: str, cost: float) -> None:
        self.ts = ts
        self.kind = kind
        self.title = title
        self.summary = summary
        self.cost_usd = cost


class _Backlog:
    def __init__(self, items: list[_Item]) -> None:
        self._items = items

    def active(self) -> list[_Item]:
        return [
            item for item in self._items
            if item.status not in {"done", "failed", "aborted", "skipped", "superseded"}
        ]


class _Journal:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries

    def tail_settlements(self, n: int, **_: object) -> list[_Entry]:
        return list(self._entries)[-n:]


class _Memory:
    def __init__(self, root: Path, items: list[_Item], entries: list[_Entry]) -> None:
        self.root = root
        self.backlog = _Backlog(items)
        self.journal = _Journal(entries)


class _Manager:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self.labels: list[str] = []

    def write_prose(self, prompt: str, *, on_event=None, run_label: str = "", **_: object) -> str:
        self.prompts.append(prompt)
        self.labels.append(run_label)
        return self.reply


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> bool:
        self.events.append(dict(event))
        return True


def _supervisor_stub(tmp_path: Path, manager: _Manager, *, stage: str = "experiment"):
    from argus.life.supervisor._letters import LettersMixin

    life_dir = tmp_path / "life"
    workdir = tmp_path / "work"
    life_dir.mkdir()
    workdir.mkdir()
    (workdir / "OBJECTIVE.md").write_text("Study free-choice permission in LLMs.", encoding="utf-8")
    (workdir / RESEARCH_NOTES_FILENAME).write_text(
        "# Research notes — Experiment stage\n\n## Thesis\n\nThe signed margin helps.\n",
        encoding="utf-8",
    )

    class Stub(LettersMixin):
        def __init__(self) -> None:
            self.memory = _Memory(
                life_dir,
                [
                    _Item("t1", "Retrain with gamma 0.05", "pending"),
                    _Item("t2", "Evaluate held-out families", "running"),
                ],
                [_Entry(time.time() - 60, "mission_complete", "cycle5", "It held on Qwen only.", 2.5)],
            )
            self.sink = _Sink()
            self.config = type("Cfg", (), {"continuous_objective": ""})()
            self.status: list[str] = []
            self._manager = manager

        def _project_workdir(self) -> Path:
            return workdir

        def _current_pipeline_stage(self) -> str:
            return stage

        def _bound_manager(self) -> _Manager:
            return self._manager

        def _emit(self, event: dict) -> bool:
            return self.sink.handle_event(event)

        def _emit_status(self, text: str) -> None:
            self.status.append(text)

        def _render_campaign_tally(self) -> str:
            return "12 terminal missions"


    return Stub(), life_dir, workdir


def test_a_letter_is_written_published_and_kept_in_the_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LETTER_INTERVAL_HOURS", "8")
    manager = _Manager("Dear colleague, while you were away the evaluation finished.\n\nArgus")
    stub, life_dir, workdir = _supervisor_stub(tmp_path, manager)
    # The first call only starts the clock.
    assert stub._maybe_write_letter() is False
    assert manager.prompts == []
    assert stub._maybe_write_letter(force=True) is True
    assert manager.labels == ["manager-letter"]
    prompt = manager.prompts[0]
    assert "Study free-choice permission" in prompt
    assert "cycle5" in prompt and "It held on Qwen only." in prompt
    assert "Evaluate held-out families (running)" in prompt
    assert "Retrain with gamma 0.05" in prompt
    letters = (workdir / "LETTERS.md").read_text(encoding="utf-8")
    assert letters.startswith("# Letters from Argus")
    assert "while you were away" in letters
    transcript = (life_dir / "transcript.jsonl").read_text(encoding="utf-8")
    assert "while you were away" in transcript
    kinds = [event["type"] for event in stub.sink.events]
    assert "life.letter.written" in kinds
    # Not due again until the interval passes.
    assert stub._maybe_write_letter() is False
    state = json.loads((life_dir / "letters.json").read_text(encoding="utf-8"))
    assert state["count"] == 1


def test_only_news_earns_a_letter_and_a_failed_one_waits_for_more(
    tmp_path: Path, monkeypatch
) -> None:
    """A held campaign once asked the Manager for a letter on every held cycle."""
    from types import SimpleNamespace

    from argus.life.supervisor import _letters

    hour = 3600.0
    clock = SimpleNamespace(now=1_800_000_000.0)
    monkeypatch.setattr(_letters, "time", SimpleNamespace(
        time=lambda: clock.now, strftime=time.strftime, localtime=time.localtime,
    ))
    monkeypatch.setenv("ARGUS_SKILL_LETTER_INTERVAL_HOURS", "8")
    manager = _Manager("")
    stub, _life_dir, _workdir = _supervisor_stub(tmp_path, manager)
    start = clock.now
    stub.memory.journal._entries[:] = []
    assert stub._maybe_write_letter() is False  # starts the clock

    for hours in range(1, 9 * 24):  # nine quiet days, checked hourly
        clock.now = start + hours * hour
        stub._maybe_write_letter()
    assert manager.prompts == [], "nothing settled and nothing was decided"

    entry = _Entry(clock.now, "mission_complete", "cycle6", "It held on Llama too.", 1.0)
    stub.memory.journal._entries.append(entry)
    for hours in range(1, 9 * 24):  # the Manager fails to write it
        clock.now += hour
        stub._maybe_write_letter()
    assert len(manager.prompts) == 1, "the same facts are not retried"

    manager.reply = "Dear colleague, two models now agree.\n\nArgus"
    stub.memory.journal._entries.append(
        _Entry(clock.now, "mission_complete", "cycle7", "Mistral agrees.", 1.0)
    )
    clock.now += 8 * hour
    assert stub._maybe_write_letter() is True
    assert len(manager.prompts) == 2
    assert "cycle6" in manager.prompts[1] and "cycle7" in manager.prompts[1]


def test_letters_can_be_switched_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LETTER_INTERVAL_HOURS", "0")
    manager = _Manager("unused")
    stub, _, _ = _supervisor_stub(tmp_path, manager)
    assert stub._maybe_write_letter() is False
    assert stub._maybe_write_letter() is False
    assert manager.prompts == []


def test_letter_uses_current_review_and_work_instead_of_archived_blockers(tmp_path: Path) -> None:
    from argus.life.event_log import JsonlEventSink
    from argus.life.memory import BacklogItem, LifeMemory

    manager = _Manager("unused")
    stub, life_dir, workdir = _supervisor_stub(tmp_path, manager, stage="review")
    stub.memory = LifeMemory.open(life_dir)
    old = BacklogItem.new(title="Old contract check", objective="Review the prior task")
    old.status = "failed"
    old.pending_question = "Confirm the old receipt location."
    stub.memory.backlog.add(old)
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": "life.mission.completed",
        "item_id": old.id,
        "title": old.title,
        "status": "failed",
        "success": False,
        "summary": "The former acceptance contract could not be resolved.",
        "ts": time.time() - 30,
    })
    live = BacklogItem.new(title="Improve the paper", objective="Complete the causal experiment")
    live.status = "running"
    live.acceptance_check = "Independent ICLR strong accept on the actual revised paper."
    stub.memory.backlog.add(live)
    checkpoint = life_dir / "handoffs" / live.id / "CHECKPOINT.md"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        "The eight-family job is running. Its full results are not reviewed yet.",
        encoding="utf-8",
    )
    review_path = workdir / "paper" / "REVIEW.md"
    review_path.parent.mkdir()
    current_review = "The positive control is valid; finish the full comparison. Weak reject."
    review_path.write_text(current_review, encoding="utf-8")

    facts = stub._letter_facts(since=time.time() - 60, now=time.time())

    assert facts["latest_review"] == current_review
    assert "eight-family job is running" in facts["current_work"]
    assert live.acceptance_check in facts["current_work"]
    assert "Improve the paper (running)" in facts["running"]
    assert facts["questions"] == ""
    assert "former acceptance contract" in facts["missions"]
    assert "former acceptance contract" not in facts["latest_review"]
    assert manager.prompts == []

    # Missing current feedback is not permission to relabel an old runtime
    # failure as a review. A real unanswered live question still appears.
    review_path.unlink()
    stub.memory.backlog.update(
        live.id, park_reason="operator_pause", pending_question="Which licensed dataset may I use?",
    )
    updated = stub._letter_facts(since=time.time() - 60, now=time.time())
    assert updated["latest_review"] == ""
    assert "Which licensed dataset" in updated["questions"]
    assert "old receipt" not in updated["questions"]
