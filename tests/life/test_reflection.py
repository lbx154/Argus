"""Reflection after a mission and after a researched answer.

The model is a stub that writes files into the directories the host offers;
the tests check what the host does around that call: when it declines to
run, which directories it opens, and how a new page becomes an INDEX line, a
journal record, a project event and a receipt.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.models import RunnerResult
from argus.life import reflection
from argus.life.reflection import (
    PROMPT_CHAR_LIMIT,
    reflect_after_answer,
    reflect_after_mission,
)
from argus.wiki.context import (
    PRINCIPLES_HEADER,
    render_principles_block,
    render_project_principles,
)
from argus.wiki.journal import read_knowledge_events

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
SURVEY_NAME = "torch-compile-status.md"
SURVEY = (
    "---\n"
    "title: Where torch.compile stands\n"
    "description: What the current release notes say about compile coverage.\n"
    "kind: survey\n"
    "audience: global\n"
    f"source: chat/{SID}\n"
    "created: 2026-09-17\n"
    "confidence: medium\n"
    "reverify_after: 2026-12-16\n"
    "---\n\n"
    "## Question\nWhat is the state of compile?\n\n"
    "## What we concluded\nCoverage widened in the last two releases.\n\n"
    "## Sources\n- https://example.org/notes (2026-09-17)\n- https://example.org/issues (2026-09-17)\n\n"
    "## Re-verify after\n2026-12-16 — the next release may change coverage.\n"
)
RESEARCH_REPLY = (
    "Two sources agree: https://example.org/notes and https://example.org/issues both "
    "describe the same widening of coverage across the last two releases."
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
        self.skills = self.life / "skills" / "engineer"
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
            host_round_log="round 1: installed torch again (40 min)",
            run_reality="One round; the venv already carried torch.",
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
    monkeypatch.setenv("ARGUS_SKILL_REFLECTION", "1")
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "1")
    monkeypatch.setenv("ARGUS_SKILL_REFLECTION_MODEL", "reflection-model-under-test")
    return fixture


def _learned(events: list[dict[str, Any]], **match: Any) -> list[dict[str, Any]]:
    found = [event for event in events if event.get("type") == "knowledge.learned"]
    return [event for event in found if all(event.get(key) == value for key, value in match.items())]


# --------------------------------------------------------------------------- skips


def test_reflection_skips_when_switched_off(roots: _Roots, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REFLECTION", "0")
    backend = _Backend([(roots.vertical_root / "pages" / "lessons" / LESSON_NAME, LESSON)])

    result = roots.reflect(backend)

    assert "ARGUS_SKILL_REFLECTION" in result["skipped"]
    assert backend.calls == []
    assert roots.events == []


def test_reflection_skips_without_a_backend(roots: _Roots) -> None:
    result = roots.reflect(None, runner=SimpleNamespace())

    assert result["skipped"] == "no model backend"


def test_short_and_roundless_missions_still_get_the_model_s_judgment(roots: _Roots) -> None:
    backend = _Backend()

    assert roots.reflect(backend, elapsed_s=12.0)["skipped"] == ""
    assert roots.reflect(backend, mission_id="m-rounds", rounds=0)["skipped"] == ""
    assert len(backend.calls) == 2


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

    # The call: the workspace is the working directory, the vertical root and
    # the project Skill layer are opened for writing, nothing else is loaded.
    call = backend.calls[0]
    options = call["options"]
    assert call["run_label"] == "reflection"
    assert options.working_dir == str(roots.workspace)
    assert str(roots.vertical_root) in options.add_dirs
    assert str(roots.life / "skills") in options.add_dirs
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
        "round 1: installed torch again (40 min)",
        "One round; the venv already carried torch.",
        "- An older lesson",
        str(lesson_path.parent),
        "Judge novelty against the saved libraries",
        "at most 300 words per page",
        "WROTE: nothing",
    ):
        assert expected in prompt, expected

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


# --------------------------------------------------------------------------- facts + skills


def test_fact_page_and_procedure_are_recorded_and_the_fact_is_shared(roots: _Roots) -> None:
    roots.with_project_wiki()
    fact_path = roots.project_wiki / "pages" / "env" / "torch.md"
    skill_path = roots.skills / "check-venv.md"
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
    assert skill_events[0]["path"] == "skills/engineer/check-venv.md"

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


def test_fact_pages_are_not_shared_after_a_review_that_did_not_pass(roots: _Roots) -> None:
    roots.with_project_wiki()
    fact_path = roots.project_wiki / "pages" / "env" / "torch.md"
    backend = _Backend([(fact_path, FACT)])

    result = roots.reflect(backend, review_status="continue")

    assert result["created"] == [str(fact_path)]
    assert result["promoted"] == []
    assert not (roots.vertical_root / "pages" / "env" / "torch.md").exists()


def test_without_a_project_wiki_the_prompt_asks_for_no_fact_pages(roots: _Roots) -> None:
    backend = _Backend()

    roots.reflect(backend)

    assert "This project has no Wiki yet, so write no fact pages." in backend.calls[0]["prompt"]


def test_pages_that_do_not_parse_are_ignored_and_a_failed_call_is_recorded(roots: _Roots) -> None:
    broken = roots.vertical_root / "pages" / "lessons" / "20260917-broken.md"
    backend = _Backend([(broken, "no front matter here\n")], exit_code=1)

    result = roots.reflect(backend)

    assert result["skipped"] == ""
    assert result["created"] == []
    assert result["ignored"] == [str(broken)]
    assert result["failure"] == "exit code 1"
    assert _learned(roots.events) == []
    assert not (roots.life / ".argus" / "REFLECTED.json").exists()
    from argus.life.answer_learning import learning_status
    assert learning_status(roots.home, SID)["jobs"][0]["status"] == "failed"


def test_a_raising_backend_never_reaches_the_caller(roots: _Roots) -> None:
    class _Exploding:
        def run_exec(self, **_kwargs):
            raise RuntimeError("provider went away")

    result = roots.reflect(_Exploding())

    assert result["skipped"] == ""
    assert result["failure"] == "RuntimeError: provider went away"


# --------------------------------------------------------------------------- answers


def test_an_empty_reply_is_the_only_answer_not_handed_to_the_model(roots: _Roots) -> None:
    backend = _Backend()
    assert _answer(roots, backend, reply="   ")["skipped"] == "nothing was said"
    assert backend.calls == []
    # An ordinary sentence still goes to the model, which decides whether to keep anything.
    result = _answer(roots, backend, operator_text="ok", reply="Sure, done.")
    assert result["skipped"] == "" and len(backend.calls) == 1
    assert "WROTE: nothing" in str(backend.calls[0])


def _answer(roots: _Roots, backend: Any, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        runner_backend=backend,
        global_root=roots.home,
        life_dir=roots.life,
        project_id=SID,
        vertical="",
        operator_text="现在 torch.compile 覆盖到哪一步了?",
        reply=RESEARCH_REPLY,
        emit=roots.events.append,
    )
    kwargs.update(overrides)
    return reflect_after_answer(**kwargs)


def test_survey_page_is_created_indexed_and_recorded(roots: _Roots) -> None:
    global_root = roots.home / "wiki" / "_global"
    survey_path = global_root / "pages" / "surveys" / SURVEY_NAME
    backend = _Backend([(survey_path, SURVEY)])

    result = _answer(roots, backend)

    assert result["skipped"] == ""
    assert result["created"] == [str(survey_path)]
    call = backend.calls[0]
    assert call["run_label"] == "answer-learning"
    assert call["options"].working_dir == str(global_root)
    assert call["options"].add_dirs == [str(global_root), str(roots.home / "operator"), str(roots.life / "skills" / "self")]
    prompt = call["prompt"]
    assert "现在 torch.compile 覆盖到哪一步了?" in prompt
    assert "- https://example.org/notes" in prompt
    assert "- https://example.org/issues" in prompt
    assert "kind: survey" in prompt
    assert "## Re-verify after" in prompt
    today = datetime.now(timezone.utc).date()
    assert f"created: {today.isoformat()}" in prompt
    assert len(prompt) <= PROMPT_CHAR_LIMIT

    index = (global_root / "INDEX.md").read_text(encoding="utf-8")
    assert "## Surveys" in index
    assert f"- [Where torch.compile stands](pages/surveys/{SURVEY_NAME}) — " in index

    events = _learned(roots.events, page_kind="survey")
    assert len(events) == 1
    assert events[0]["scope"] == "global"
    assert events[0]["path"] == f"pages/surveys/{SURVEY_NAME}"
    assert events[0]["source_project"] == SID
    record = read_knowledge_events(roots.home)[0]
    assert record["role"] == "answer-learning"
    assert record["page_kind"] == "survey"


def test_a_rewritten_survey_keeps_its_earlier_text_and_gains_a_dated_update(roots: _Roots) -> None:
    global_root = roots.home / "wiki" / "_global"
    survey_path = global_root / "pages" / "surveys" / SURVEY_NAME
    _answer(roots, _Backend([(survey_path, SURVEY)]))
    roots.events.clear()
    rewritten = SURVEY.replace(
        "Coverage widened in the last two releases.",
        "Coverage is now nearly complete.",
    ).replace("confidence: medium", "confidence: high")

    result = _answer(roots, _Backend([(survey_path, rewritten)]))

    assert result["updated"] == [str(survey_path)]
    assert result["repaired"] == [str(survey_path)]
    text = survey_path.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).date().isoformat()
    assert text.startswith(SURVEY.rstrip("\n"))
    assert f"\n## Update {today}\n" in text
    assert "Coverage is now nearly complete." in text
    assert text.count("Coverage widened in the last two releases.") == 1
    # The existing slug was listed in the prompt so the model could extend it.
    events = _learned(roots.events, page_kind="survey")
    assert len(events) == 1
    assert read_knowledge_events(roots.home)[0]["note"].startswith("updated: ")
    index = (global_root / "INDEX.md").read_text(encoding="utf-8")
    assert index.count(f"pages/surveys/{SURVEY_NAME}") == 1


def test_an_appended_update_is_left_as_written(roots: _Roots) -> None:
    global_root = roots.home / "wiki" / "_global"
    survey_path = global_root / "pages" / "surveys" / SURVEY_NAME
    _answer(roots, _Backend([(survey_path, SURVEY)]))
    appended = SURVEY + "\n## Update 2026-09-17\n\nNothing changed.\n"

    result = _answer(roots, _Backend([(survey_path, appended)]))

    assert result["repaired"] == []
    assert survey_path.read_text(encoding="utf-8") == appended


def test_answer_learning_lets_the_model_decline_and_writes_nothing(roots: _Roots) -> None:
    backend = _Backend()  # writes no file: the model judged there was nothing to keep

    result = _answer(roots, backend, reply="Sure, done.", operator_text="ok")

    assert result["skipped"] == "" and result["created"] == [] and result["updated"] == []
    assert len(backend.calls) == 1
    assert not list((roots.home / "wiki" / "_global" / "pages" / "surveys").glob("*.md"))


def test_answer_learning_respects_its_switch(roots: _Roots, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "off")
    backend = _Backend()

    result = _answer(roots, backend)

    assert "ARGUS_SKILL_ANSWER_LEARNING" in result["skipped"]
    assert backend.calls == []


def test_answer_can_learn_a_method_and_explicit_preference_with_knowledge(roots: _Roots) -> None:
    survey = roots.home / "wiki" / "_global" / "pages" / "surveys" / SURVEY_NAME
    skill = roots.life / "skills" / "self" / "check-environment.md"
    profile = roots.home / "operator" / "profile.md"
    preference = "---\ntitle: Working preferences\ndescription: Explicit user preference.\nkind: profile\naudience: private\n---\nExplain the mechanism before the formulas.\n"
    backend = _Backend([(survey, SURVEY), (skill, SKILL), (profile, preference)])
    result = _answer(roots, backend, operator_text="Explain the mechanism first in future.",
                     evidence="read existing environment; successful import")
    assert set(result["created"]) == {str(survey), str(skill), str(profile)}
    assert _learned(roots.events, page_kind="skill", scope="project")
    assert _learned(roots.events, page_kind="profile", scope="private")
    # Rewriting identical files must not masquerade as new learning.
    roots.events.clear()
    repeated = _answer(roots, backend)
    assert repeated["updated"] == []
    assert _learned(roots.events) == []


def test_long_answers_keep_the_writing_boundaries_and_preference_rules(roots: _Roots) -> None:
    backend = _Backend()
    _answer(roots, backend, operator_text="request " * 2000, reply="answer " * 6000, evidence="trace " * 6000)
    prompt = backend.calls[0]["prompt"]
    assert len(prompt) <= PROMPT_CHAR_LIMIT
    assert "Only explicit user statements support a lasting preference" in prompt
    assert "You may write only inside" in prompt
    assert prompt.endswith("`WROTE: nothing`.")


def test_a_vertical_answer_lands_in_the_vertical_wiki(roots: _Roots) -> None:
    survey_path = roots.vertical_root / "pages" / "surveys" / SURVEY_NAME
    backend = _Backend([(survey_path, SURVEY)])

    result = _answer(roots, backend, vertical=VERTICAL)

    assert result["created"] == [str(survey_path)]
    assert _learned(roots.events, scope="vertical", vertical=VERTICAL, page_kind="survey")


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


def test_render_principles_block_is_absent_without_the_file(tmp_path: Path) -> None:
    assert render_principles_block(tmp_path) == ""


def test_render_principles_block_strips_front_matter_and_history(tmp_path: Path) -> None:
    (tmp_path / "principles.md").write_text(PRINCIPLES, encoding="utf-8")

    block = render_principles_block(tmp_path)

    assert block.startswith(PRINCIPLES_HEADER + "\n1. Check the venv")
    assert "title: Research principles" not in block
    assert "## History" not in block
    assert "compiled from 4 lessons" not in block
    assert "2. Measure once before optimizing" in block


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


def test_project_principles_follow_the_decided_vertical(roots: _Roots) -> None:
    roots.vertical_root.mkdir(parents=True)
    (roots.vertical_root / "principles.md").write_text(PRINCIPLES, encoding="utf-8")

    assert render_project_principles(roots.workspace) == ""
    _decide_vertical(roots.workspace, VERTICAL)
    assert render_project_principles(roots.workspace).startswith(PRINCIPLES_HEADER)


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
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=1),
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
    assert captured["run_reality"] == "One round; the venv already carried torch."
    assert captured["host_round_log"] == ""
    assert captured["stop_reason"].startswith("status=done;")
    assert captured["rounds"] == 1
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
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=1),
            project_worktree=tmp_path / "campaign",
        ),
    )
    memory.backlog.add(BacklogItem.new(title="t", objective="o"))

    result = supervisor.tick()

    assert result is not None
    assert result["status"] == "done"


def test_reflection_can_run_concurrently_with_other_projects(roots: _Roots) -> None:
    """Two projects reflecting at once append to one journal without losing a line."""
    other_life = roots.home / "projects" / "s-other"
    other_life.mkdir(parents=True)
    other_root = roots.home / "wiki" / "_shared_verticals" / "quant"
    backends = [
        _Backend([(roots.vertical_root / "pages" / "lessons" / LESSON_NAME, LESSON)]),
        _Backend([(other_root / "pages" / "lessons" / LESSON_NAME, LESSON)]),
    ]
    threads = [
        threading.Thread(target=roots.reflect, args=(backends[0],)),
        threading.Thread(
            target=roots.reflect, args=(backends[1],),
            kwargs={
                "life_dir": other_life, "project_id": "s-other", "mission_id": "m-2",
                "vertical": "quant",
            },
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    projects = sorted(record["source_project"] for record in read_knowledge_events(roots.home))
    assert projects == ["s-other", SID]


def test_both_prompts_keep_the_operator_s_own_affairs_out_of_shared_pages(tmp_path: Path) -> None:
    from argus.life.reflection import build_answer_prompt, build_reflection_prompt

    answer = build_answer_prompt(
        project_id="s-1", vertical="", operator_text="my company is split 65/30/5, should I sign?",
        reply="x" * 700, root=tmp_path, existing=[],
    )
    mission = build_reflection_prompt(
        project_id="s-1", vertical="research", mission_id="m", title="t", objective="o", acceptance="a",
        review_status="done", review_reason="", stop_reason="", host_round_log="", run_reality="",
        vertical_root=tmp_path, project_wiki=None, skills_dir=tmp_path / "skills", existing_lessons=[],
    )
    for prompt in (answer, mission):
        assert "keep the operator's own affairs out" in prompt
        assert "read by other projects and other people" in prompt
