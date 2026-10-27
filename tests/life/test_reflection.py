"""Reflection after a mission and after a researched answer.

The model is a stub that writes files into the directories the host offers;
the tests check what the host does around that call: when it declines to
run, which directories it opens, and how a new page becomes an INDEX line, a
journal record, a project event and a receipt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.handoff import CALL_HAND_OFF
from argus.core.models import RunnerResult
from argus.life import reflection
from argus.life.reflection import (
    PROMPT_CHAR_LIMIT,
    reflect_after_mission,
)
from argus.wiki.context import (
    PRINCIPLES_HEADER,
    render_principles_block,
)
from argus.wiki.journal import read_knowledge_events
from tests.hand_off import submit

SID = "s-proj"
MISSION = "m-1"
VERTICAL = "research"

LESSON_NAME = "20260917-torch-in-venv.md"
LESSON = (
    "---\n"
    "title: Look for torch in the workspace venv first\n"
    "description: The venv already carried torch; a fresh install cost forty minutes.\n"
    "kind: lesson\n"
    "audience: vertical\n"
    f"source: {SID}/{MISSION}\n"
    "created: 2026-09-17\n"
    "confidence: medium\n"
    "---\n\n"
    "## What happened\nThe first round installed torch again.\n\n"
    "## Why\nNobody checked `.venv` before installing.\n\n"
    "## Next time\nRun `python -c 'import torch'` inside the venv first.\n\n"
    "## Evidence\n- rounds/1/log.txt\n"
)
FACT = (
    "---\n"
    "title: Where torch lives on this machine\n"
    "description: torch 2.x is in the workspace venv; CUDA 12 wheels.\n"
    "kind: fact\n"
    "audience: vertical\n"
    f"source: {SID}/{MISSION}\n"
    "created: 2026-09-17\n"
    "confidence: high\n"
    "---\n\n"
    "The workspace `.venv` carries torch; do not reinstall it.\n"
)
SKILL = (
    "---\n"
    "name: check the venv before installing\n"
    "description: Import the package inside the venv before any pip install.\n"
    "---\n\n"
    "## When\nBefore installing a heavy package.\n\n## Steps\n1. Try the import.\n"
)


@dataclass
class _Backend:
    """A stand-in model that writes the given files and reports success."""

    files: list[tuple[Path, str]] = field(default_factory=list)
    exit_code: int = 0
    fatal_error: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_exec(self, *, prompt: str, options: Any, run_label: str, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "options": options, "run_label": run_label})
        for path, text in self.files:
            if run_label == "answer-learning":
                work = Path(options.working_dir)
                raw = path.as_posix()
                if "/pages/surveys/" in raw:
                    path = work / "knowledge" / "pages" / "surveys" / raw.split("/pages/surveys/", 1)[1]
                elif "/skills/self/" in raw:
                    path = work / "skills" / raw.split("/skills/self/", 1)[1]
                elif "/operator/" in raw:
                    path = work / "operator" / raw.split("/operator/", 1)[1]
                else:
                    raise AssertionError(f"unrecognized answer-learning destination {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return RunnerResult(
            exit_code=self.exit_code, fatal_error=self.fatal_error, agent_messages=["WROTE"],
        )


class _Roots:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.workspace = tmp_path / "workspace"
        self.life = self.home / "projects" / SID
        self.vertical_root = self.home / "wiki" / "_shared_verticals" / VERTICAL
        self.project_wiki = self.workspace / ".autors" / SID / "wiki"
        self.skills = self.workspace / ".agents" / "skills"
        self.workspace.mkdir(parents=True)
        self.life.mkdir(parents=True)
        self.events: list[dict[str, Any]] = []

    def with_project_wiki(self) -> "_Roots":
        (self.project_wiki / "pages").mkdir(parents=True)
        (self.project_wiki / "INDEX.md").write_text("# Project\n", encoding="utf-8")
        return self

    def reflect(self, backend: Any, **overrides: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(
            runner=SimpleNamespace(_backend=backend),
            workspace=self.workspace,
            life_dir=self.life,
            global_root=self.home,
            vertical=VERTICAL,
            project_id=SID,
            mission_id=MISSION,
            title="Fine-tune the baseline",
            objective="Reproduce the baseline number on the small split.",
            acceptance="The number is within 0.5 of the paper.",
            review_status="done",
            review_reason="The number matched; the run log is attached.",
            stop_reason="status=done; stop_kind=none; reason=none",
            emit=self.events.append,
            elapsed_s=900.0,
            rounds=2,
        )
        kwargs.update(overrides)
        return reflect_after_mission(**kwargs)


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Roots:
    fixture = _Roots(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(fixture.home))
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "1")
    monkeypatch.setenv("ARGUS_SKILL_LIGHT_MODEL", "reflection-model-under-test")
    return fixture


def _learned(events: list[dict[str, Any]], **match: Any) -> list[dict[str, Any]]:
    found = [event for event in events if event.get("type") == "knowledge.learned"]
    return [event for event in found if all(event.get(key) == value for key, value in match.items())]


# --------------------------------------------------------------------------- skips


def test_reflection_skips_when_switched_off(roots: _Roots, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")
    backend = _Backend([(roots.vertical_root / "pages" / "lessons" / LESSON_NAME, LESSON)])

    result = roots.reflect(backend)

    assert result["skipped"] == "learning is off"
    assert backend.calls == []
    assert roots.events == []


# --------------------------------------------------------------------------- lessons


def test_lesson_page_is_indexed_journaled_announced_and_receipted(roots: _Roots) -> None:
    older = roots.vertical_root / "pages" / "lessons" / "20260901-older.md"
    older.parent.mkdir(parents=True)
    older.write_text(
        "---\ntitle: An older lesson\ndescription: Kept from before.\nkind: lesson\n---\n\nBody.\n",
        encoding="utf-8",
    )
    lesson_path = roots.vertical_root / "pages" / "lessons" / LESSON_NAME
    backend = _Backend([(lesson_path, LESSON)])

    result = roots.reflect(backend)

    assert result["skipped"] == ""
    assert result["created"] == [str(lesson_path)]
    assert result["updated"] == []
    assert result["failure"] == ""

    # The call: the workspace (with its Agent Skills) is the working directory,
    # the vertical root is opened for writing, nothing else is loaded.
    call = backend.calls[0]
    options = call["options"]
    assert call["run_label"] == "reflection"
    assert options.working_dir == str(roots.workspace)
    assert options.add_dirs == [str(roots.vertical_root), str(roots.home / "operator")]
    assert f"{roots.skills}/<slug>/SKILL.md" in call["prompt"]
    assert options.sandbox_mode == "workspace-write"
    assert options.skip_git_repo_check is True
    assert options.skill_paths == []
    assert options.reasoning_effort == "low"
    assert options.model == "reflection-model-under-test"

    # The prompt carries the facts and the titles already known, bounded.
    prompt = call["prompt"]
    assert len(prompt) <= PROMPT_CHAR_LIMIT
    for expected in (
        "Reproduce the baseline number on the small split.",
        "The number is within 0.5 of the paper.",
        "The number matched; the run log is attached.",
        "- An older lesson",
        str(lesson_path.parent),
        "Judge novelty against the saved libraries",
        "at most 300 words per page",
    ):
        assert expected in prompt, expected
    assert prompt.endswith(CALL_HAND_OFF)

    # INDEX.md of the vertical lists the lesson once, under its own section.
    index = (roots.vertical_root / "INDEX.md").read_text(encoding="utf-8")
    assert index.startswith("# Research knowledge\n")
    assert "## Lessons" in index
    assert (
        f"- [Look for torch in the workspace venv first](pages/lessons/{LESSON_NAME}) — "
        "The venv already carried torch; a fresh install cost forty minutes.\n"
    ) in index

    # One project event and one journal line describe the same page.
    events = _learned(roots.events, kind="learned")
    assert len(events) == 1
    event = events[0]
    assert event["scope"] == "vertical"
    assert event["vertical"] == VERTICAL
    assert event["path"] == f"pages/lessons/{LESSON_NAME}"
    assert event["title"] == "Look for torch in the workspace venv first"
    assert event["source_project"] == SID
    assert event["mission_id"] == MISSION
    assert event["page_kind"] == "lesson"

    journal = read_knowledge_events(roots.home, kinds=["learned"])
    assert len(journal) == 1
    record = journal[0]
    assert record["scope"] == "vertical"
    assert record["vertical"] == VERTICAL
    assert record["path"] == f"pages/lessons/{LESSON_NAME}"
    assert record["source_project"] == SID
    assert record["mission_id"] == MISSION
    assert record["role"] == "reflection"
    assert record["page_kind"] == "lesson"

    receipt = json.loads((roots.life / ".argus" / "REFLECTED.json").read_text(encoding="utf-8"))
    assert receipt["missions"][MISSION]["created"] == [str(lesson_path)]


def test_receipt_prevents_a_second_reflection_on_the_same_mission(roots: _Roots) -> None:
    lesson_path = roots.vertical_root / "pages" / "lessons" / LESSON_NAME
    backend = _Backend([(lesson_path, LESSON)])

    first = roots.reflect(backend)
    second = roots.reflect(backend)

    assert first["created"] == [str(lesson_path)]
    assert second["skipped"] == "already reflected on this mission"
    assert len(backend.calls) == 1
    assert len(_learned(roots.events)) == 1


def test_a_lesson_without_a_vertical_goes_to_the_global_wiki(roots: _Roots) -> None:
    global_root = roots.home / "wiki" / "_global"
    lesson_path = global_root / "pages" / "lessons" / LESSON_NAME
    backend = _Backend([(lesson_path, LESSON)])

    result = roots.reflect(backend, vertical="")

    assert result["created"] == [str(lesson_path)]
    assert str(global_root) in backend.calls[0]["options"].add_dirs
    assert (global_root / "INDEX.md").read_text(encoding="utf-8").startswith("# Global knowledge\n")
    assert _learned(roots.events, scope="global", path=f"pages/lessons/{LESSON_NAME}")


def test_a_saved_page_the_mission_showed_outdated_is_retired_with_who_and_why(roots: _Roots) -> None:
    lessons = roots.vertical_root / "pages" / "lessons"
    lessons.mkdir(parents=True)
    stale = lessons / "20260901-safe-path.md"
    stale.write_text(
        "---\ntitle: Set PYTHONPATH under PYTHONSAFEPATH\n"
        "description: Safe-path mode hides the source tree from CLI subprocesses.\n"
        "kind: lesson\nsource: s-old/m-0\ncreated: 2026-09-01\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (roots.vertical_root / "INDEX.md").write_text(
        "# Research knowledge\n\n## Lessons\n\n- [Set PYTHONPATH under PYTHONSAFEPATH]"
        "(pages/lessons/20260901-safe-path.md) — Safe-path mode hides the source tree.\n",
        encoding="utf-8",
    )
    outside = roots.workspace / "notes.md"
    outside.write_text("# Not a library page\n", encoding="utf-8")
    backend = _ReplyBackend(replies=[{"retire": [
        {"page": str(stale), "why": "PYTHONSAFEPATH is no longer set; the CLI tests pass without PYTHONPATH."},
        {"page": str(outside), "why": "not a page"},
    ]}])

    result = roots.reflect(
        backend, review_reason="All tests pass without PYTHONPATH; the recalled lesson "
        f"{stale} no longer holds because PYTHONSAFEPATH is unset.",
    )

    prompt = backend.calls[0]["prompt"]
    # Listed by file name under a heading that names their directory, so a full path can be written.
    assert f"## Lessons this vertical already has, in `{lessons}`" in prompt
    assert "- Set PYTHONPATH under PYTHONSAFEPATH (`20260901-safe-path.md`)" in prompt
    assert "name it in retire of your hand_off" in prompt
    assert "names it in its description" in prompt
    assert result["retired"] == [str(stale)]
    assert not stale.exists() and outside.exists()
    assert "safe-path" not in (roots.vertical_root / "INDEX.md").read_text(encoding="utf-8")
    [kept] = (roots.vertical_root / ".history" / "lessons").iterdir()
    assert "retired by reflection: PYTHONSAFEPATH is no longer set" in kept.read_text(encoding="utf-8")
    [record] = read_knowledge_events(roots.home, kinds=["retired"])
    assert (record["scope"], record["vertical"], record["path"]) == (
        "vertical", VERTICAL, "pages/lessons/20260901-safe-path.md")
    assert (record["role"], record["mission_id"], record["source_project"]) == ("reflection", MISSION, "s-old/m-0")
    assert record["note"].startswith("PYTHONSAFEPATH is no longer set")
    # Retiring is not learning: no page is announced as learned.
    assert _learned(roots.events) == []


# --------------------------------------------------------------------------- facts + skills


def test_fact_page_and_procedure_are_recorded_and_the_fact_is_shared(roots: _Roots) -> None:
    roots.with_project_wiki()
    fact_path = roots.project_wiki / "pages" / "env" / "torch.md"
    skill_path = roots.skills / "check-venv" / "SKILL.md"
    backend = _Backend([(fact_path, FACT), (skill_path, SKILL)])

    result = roots.reflect(backend)

    assert sorted(result["created"]) == sorted([str(fact_path), str(skill_path)])
    assert "This project has no Wiki yet" not in backend.calls[0]["prompt"]
    assert f"{roots.project_wiki}/pages/<topic>/<slug>.md" in backend.calls[0]["prompt"]

    fact_events = _learned(roots.events, kind="learned", page_kind="fact")
    assert len(fact_events) == 1
    assert fact_events[0]["scope"] == "project"
    assert fact_events[0]["path"] == "pages/env/torch.md"
    assert fact_events[0]["title"] == "Where torch lives on this machine"

    skill_events = _learned(roots.events, kind="learned", page_kind="skill")
    assert len(skill_events) == 1
    assert skill_events[0]["scope"] == "project"
    assert skill_events[0]["path"] == ".agents/skills/check-venv/SKILL.md"

    # audience: vertical after an accepted review — copied into the shared tier.
    shared_copy = roots.vertical_root / "pages" / "env" / "torch.md"
    assert shared_copy.read_text(encoding="utf-8") == FACT
    assert result["promoted"] == ["pages/env/torch.md"]
    promoted = _learned(roots.events, kind="promoted")
    assert len(promoted) == 1
    assert promoted[0]["scope"] == "vertical"
    assert promoted[0]["path"] == "pages/env/torch.md"
    journal_kinds = sorted(record["kind"] for record in read_knowledge_events(roots.home))
    assert journal_kinds == ["learned", "learned", "promoted"]


# --------------------------------------------------------------------------- principles


PRINCIPLES = (
    "---\n"
    "title: Research principles\n"
    "description: Working rules distilled from repeated lessons.\n"
    "kind: principles\n"
    "---\n\n"
    "1. Check the venv before installing anything heavy — evidence: "
    "[a](pages/lessons/20260901-a.md), [b](pages/lessons/20260902-b.md)\n"
    "2. Measure once before optimizing — evidence: "
    "[c](pages/lessons/20260903-c.md), [d](pages/lessons/20260904-d.md)\n\n"
    "## History\n"
    "- 2026-09-17: compiled from 4 lessons\n"
)


def test_render_principles_block_is_bounded(tmp_path: Path) -> None:
    long_body = "\n".join(f"{index}. Rule number {index} " + "x" * 200 for index in range(1, 30))
    (tmp_path / "principles.md").write_text(
        "---\ntitle: P\ndescription: d\nkind: principles\n---\n\n" + long_body + "\n", encoding="utf-8",
    )

    block = render_principles_block(tmp_path)

    assert len(block) <= 1500
    assert block.startswith(PRINCIPLES_HEADER)
    assert block.endswith("…")


def _decide_vertical(workspace: Path, vertical: str) -> None:
    state = workspace / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"vertical": vertical}), encoding="utf-8")


def test_role_prompts_carry_the_principles_only_when_the_vertical_has_them(roots: _Roots) -> None:
    from argus.roles.prompts.engineer import build_mission_prompt
    from argus.roles.prompts.planner import build_continuous_prompt
    from argus.roles.prompts.reviewer import render_reviewer_prompt

    _decide_vertical(roots.workspace, VERTICAL)

    def engineer() -> str:
        return build_mission_prompt(
            task="Implement the fix.",
            skill_text="",
            next_action="",
            original_request="Make it hold.",
            project_root=roots.workspace,
        )

    def planner() -> str:
        return build_continuous_prompt(
            continuous_objective="Reach the target.",
            journal_tail="",
            planning_cycle=0,
            project_root=roots.workspace,
            state_root=roots.workspace,
        )

    def reviewer() -> str:
        owner = SimpleNamespace(skill_store=None, mission=None, _last_prompt_block_stats={})
        static, delta = render_reviewer_prompt(
            owner,
            objective="Implement the fix.",
            operator_messages=[],
            planner_review_instruction="Verify the behavior.",
            round_index=1,
            session_id="s-1",
            main_summary="round 1 account",
            main_error=None,
            working_dir=roots.workspace,
            vertical_state_root=roots.workspace,
            vertical=VERTICAL,
            preselected_skill_block="",
        )
        return static + delta

    for render in (engineer, planner, reviewer):
        assert PRINCIPLES_HEADER not in render(), render.__name__

    roots.vertical_root.mkdir(parents=True)
    (roots.vertical_root / "principles.md").write_text(PRINCIPLES, encoding="utf-8")

    for render in (engineer, planner, reviewer):
        text = render()
        assert PRINCIPLES_HEADER in text, render.__name__
        assert "1. Check the venv before installing anything heavy" in text, render.__name__
        assert "compiled from 4 lessons" not in text, render.__name__


# --------------------------------------------------------------------------- the settlement hook


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    final_review_status: str = "done"
    final_review_source: str = "reviewer"
    final_review_reason: str = "The number matched."
    summary: str = "One round; the venv already carried torch."


class _Runner:
    def __init__(self) -> None:
        self.usage_contexts: list[Any] = []

    def _set_usage_context(self, mission_id):
        self.usage_contexts.append(mission_id)

    def execute(self, **kwargs):
        return _Outcome()


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event):
        self.events.append(event)


def test_settlement_hands_the_mission_facts_to_reflection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "argus.manager.skill_tidy.propagate_after_mission",
        lambda *_args, **_kwargs: {"to_shared": 0, "errors": 0},
    )
    captured: dict[str, Any] = {}

    def _reflect(**kwargs):
        captured.update(kwargs)
        return {"skipped": "", "created": [], "updated": []}

    monkeypatch.setattr(reflection, "reflect_after_mission", _reflect)
    memory = LifeMemory.open(tmp_path / "life")
    _decide_vertical(tmp_path / "campaign", VERTICAL)
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            project_worktree=tmp_path / "campaign",
        ),
    )
    item = BacklogItem.new(title="Fine-tune the baseline", objective="Reproduce the baseline number.")
    memory.backlog.add(item)

    assert supervisor.tick() is not None

    assert captured["mission_id"] == item.id
    assert captured["title"] == "Fine-tune the baseline"
    assert captured["objective"] == "Reproduce the baseline number."
    assert captured["workspace"] == tmp_path / "campaign"
    assert captured["life_dir"] == memory.root
    assert captured["project_id"] == "life"
    assert captured["global_root"] == supervisor._budget_global_root()
    assert captured["vertical"] == VERTICAL
    assert captured["review_status"] == "done"
    assert captured["review_reason"] == "The number matched."
    assert captured["stop_reason"].startswith("status=done;")
    assert captured["rounds"] == 1
    assert captured["mission_accepted"] is True
    # The final review gave no learning judgment, so reflection is not skipped.
    assert captured["learning"] == ""
    assert "reviewed_fact" in captured
    assert captured["elapsed_s"] >= 0.0
    assert captured["emit"] == supervisor._emit
    assert captured["runner"] is supervisor.runner


def test_a_failing_reflection_never_changes_the_mission_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "argus.manager.skill_tidy.propagate_after_mission",
        lambda *_args, **_kwargs: {"to_shared": 0, "errors": 0},
    )

    def _explode(**_kwargs):
        raise RuntimeError("reflection broke")

    monkeypatch.setattr(reflection, "reflect_after_mission", _explode)
    memory = LifeMemory.open(tmp_path / "life")
    (tmp_path / "campaign").mkdir()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            project_worktree=tmp_path / "campaign",
        ),
    )
    memory.backlog.add(BacklogItem.new(title="t", objective="o"))

    result = supervisor.tick()

    assert result is not None
    assert result["status"] == "done"


def test_the_prompt_keeps_the_operator_s_own_affairs_out_of_shared_pages(tmp_path: Path) -> None:
    from argus.life.reflection import build_reflection_prompt

    prompt = build_reflection_prompt(
        project_id="s-1", vertical="research", mission_id="m", title="t", objective="o", acceptance="a",
        review_status="done", review_reason="", stop_reason="",
        vertical_root=tmp_path, project_wiki=None, skills_dir=tmp_path / "skills", existing_lessons=[],
    )
    assert "keep the operator's own affairs out" in prompt
    assert "read by other projects and other people" in prompt


# --------------------------------------------------------------------------- adaptive reflection and reviewed facts


@dataclass
class _ReplyBackend(_Backend):
    """A stand-in model that files the given ``reflect`` forms in turn; the last one that passes counts."""

    replies: list[dict] = field(default_factory=lambda: [{"retire": []}])

    def run_exec(self, *, prompt: str, options: Any, run_label: str, resume_thread_id=None):
        result = super().run_exec(prompt=prompt, options=options, run_label=run_label)
        if not self.exit_code:
            for form in self.replies:
                submit(options, ("hand_off", form))
        return result


def _fact(fact: str, *refs: str) -> dict:
    return {"retire": [], "reviewed_fact": {"keep": True, "fact": fact, "evidence_refs": list(refs)}}


def _fact_candidate(**extra: Any) -> dict[str, Any]:
    from argus.manager.reviewed_facts import reviewed_fact_candidate

    candidate = reviewed_fact_candidate(
        source_campaign="campaign-07",
        reviewer_reason="The held-out effect is confirmed.",
        research_result={"result_class": "verified_new_result", "evidence": ["result.json"]},
        evidence_refs=extra.pop("refs", ["result.json", "log.txt"]),
    )
    return {**candidate, **extra}


def _labels(backend: _Backend) -> list[str]:
    return [call["run_label"] for call in backend.calls]


def test_an_accepted_mission_the_reviewer_found_routine_is_not_reflected_on(roots: _Roots) -> None:
    backend = _ReplyBackend()

    result = roots.reflect(backend, learning="nothing_new", mission_accepted=True, rounds=1)

    assert "nothing new" in result["skipped"]
    assert backend.calls == []


def test_a_routine_mission_with_a_fact_to_judge_still_reflects(roots: _Roots) -> None:
    backend = _ReplyBackend(replies=[_fact("A kept fact.", "log.txt")])

    result = roots.reflect(
        backend, learning="nothing_new", mission_accepted=True, rounds=1, reviewed_fact=_fact_candidate(),
    )

    assert result["reviewed_fact_recorded"] is True
    assert _labels(backend) == ["reflection"]


def test_a_fact_only_in_a_draft_message_is_not_recorded(roots: _Roots) -> None:
    backend = _ReplyBackend(replies=[
        _fact("A draft claim.", "result.json"), {"retire": [], "reviewed_fact": {"keep": False}},
    ])

    result = roots.reflect(backend, reviewed_fact=_fact_candidate())

    assert result["reviewed_fact_recorded"] is False
    assert not (roots.home / "reviewed-facts.md").exists()
    # An explicit "none" is an answer: no separate judgment runs.
    assert _labels(backend) == ["reflection"]


def test_unwritable_knowledge_directories_leave_the_fact_unrecorded(
    roots: _Roots, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _ReplyBackend()
    real_mkdir = Path.mkdir

    def refuse(self: Path, *args: Any, **kwargs: Any) -> None:
        if "lessons" in self.parts:
            raise PermissionError("read-only")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse)

    result = roots.reflect(backend, reviewed_fact=_fact_candidate())

    assert result["failure"] == "knowledge directories are not writable"
    assert backend.calls == []
    assert not (roots.home / "reviewed-facts.md").exists()
    from argus.life.reflection_status import learning_status
    # Unable to keep anything is a failure, not "nothing to learn".
    assert learning_status(roots.home, SID)["jobs"][0]["status"] == "failed"


def test_a_fact_is_not_judged_twice_when_reflection_raises_after_recording_it(
    roots: _Roots, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _ReplyBackend(replies=[_fact("A kept fact.", "log.txt")])

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("receipt store broke")

    monkeypatch.setattr(reflection, "_write_receipt", explode)

    result = roots.reflect(backend, reviewed_fact=_fact_candidate())

    assert "raised" in result["skipped"]
    from argus.life.reflection_status import learning_status
    assert learning_status(roots.home, SID)["jobs"][0]["status"] == "failed"
    assert _labels(backend) == ["reflection"]
    digest = (roots.home / "reviewed-facts.md").read_text(encoding="utf-8")
    assert digest.count("A kept fact.") == 1


def test_a_failed_reflection_leaves_the_fact_unrecorded(roots: _Roots) -> None:
    backend = _ReplyBackend(exit_code=1, fatal_error="provider down")

    result = roots.reflect(backend, reviewed_fact=_fact_candidate())

    assert result["reviewed_fact_recorded"] is False
    assert _labels(backend) == ["reflection"]
    assert not (roots.home / "reviewed-facts.md").exists()


def test_the_fact_section_and_its_refs_are_never_cut(roots: _Roots, monkeypatch: pytest.MonkeyPatch) -> None:
    refs = [f"evidence/run-{index:03d}.json" for index in range(120)]
    backend = _ReplyBackend()
    monkeypatch.setattr(reflection, "PROMPT_CHAR_LIMIT", 6_000)

    roots.reflect(
        backend, reviewed_fact=_fact_candidate(refs=refs),
        objective="x" * 20_000, acceptance="y" * 20_000, review_reason="z" * 20_000,
    )

    prompt = backend.calls[0]["prompt"]
    assert "x" * 1_999 not in prompt, "the bounded part was cut"
    assert "Allowed evidence refs:" in prompt
    assert all(f"- {ref}" in prompt for ref in refs)
    assert "verified_new_result" in prompt
    # The call to hand_off comes after the fact section: the last thing the model reads.
    assert prompt.endswith(f"- {refs[-1]}\n\n{CALL_HAND_OFF}")


def test_a_fact_resting_on_a_ref_not_allowed_is_refused_and_not_recorded(roots: _Roots) -> None:
    backend = _ReplyBackend(replies=[_fact("A claim.", "made-up.json")])

    result = roots.reflect(backend, reviewed_fact=_fact_candidate())

    assert result["reviewed_fact_recorded"] is False
    assert _labels(backend) == ["reflection"]
    assert not (roots.home / "reviewed-facts.md").exists()


def test_a_recorded_text_hand_off_retires_nothing_and_records_no_fact(roots: _Roots) -> None:
    """Replay of s-efe9deac reflection 801cf5ff (trial-1023): codex wrote "hand_off:"
    with a retire request as text and never called the tool; the text changes nothing."""
    record = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac"
         / "reflection_text_handoff_801cf5ff.json").read_text(encoding="utf-8")
    )
    target = roots.vertical_root / "pages" / "lessons" / record["retire_target"]["name"]
    target.parent.mkdir(parents=True)
    target.write_text(record["retire_target"]["text"], encoding="utf-8")
    from argus.manager.reviewed_facts import reviewed_fact_candidate

    class _TextOnly(_Backend):
        def run_exec(self, **kwargs: Any) -> RunnerResult:
            result = super().run_exec(**kwargs)
            result.agent_messages = [record["final_message"]]
            return result

    backend = _TextOnly()
    result = roots.reflect(
        backend, mission_id=record["mission_id"], reviewed_fact=reviewed_fact_candidate(
            source_campaign="s-efe9deac", reviewer_reason=record["reviewer_reason"],
            research_result=record["research_result"], evidence_refs=record["evidence_refs"],
        ),
    )

    assert "retire:" in record["final_message"] and target.name in record["final_message"]
    assert result["retired"] == [] and target.exists()
    assert result["reviewed_fact_recorded"] is False
    assert not (roots.home / "reviewed-facts.md").exists()
    assert not list(roots.home.glob("reviewed-fact-source-*.json"))
    assert _labels(backend) == ["reflection"]
