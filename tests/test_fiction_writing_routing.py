"""fiction_writing routing: the Manager can select it, it is presented to the
Manager as DISTINCT from a research 'literature review', and it round-trips
through persist/resolve. The Manager's LLM judgment is mocked (fake runner);
its live discrimination is a P3 eval — here we prove the plumbing + the menu
disambiguation deterministically."""
from __future__ import annotations

import json

from argus_skill.manager import Manager
from argus_skill.manager.domain_author import build_vertical_decision_prompt
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    persist_vertical,
    resolve_vertical,
)


class _FakeResult:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"


class _FakeRunner:
    def __init__(self, decision: dict) -> None:
        self._decision = decision
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "run_label": run_label})
        return _FakeResult(json.dumps(self._decision))


_FICTION = {
    "choice": "existing", "vertical": "fiction_writing",
    "rationale": "write an original short story",
    "execution_task": "Write the requested urban-suspense short story.",
}
_RESEARCH = {
    "choice": "existing", "vertical": "research",
    "rationale": "a paper with a literature review",
    "execution_task": "Write the diffusion-model literature review paper.",
}


def test_fiction_request_routes_to_fiction_writing(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_FICTION))
    div = mgr.divide("写一个三千字的都市悬疑短篇小说")
    assert div.vertical == "fiction_writing"
    assert div.kind == "custom"  # not research/optimize — same class as learning
    assert not (tmp_path / "research" / "DOMAINS").exists()  # no data domain authored


def test_literature_review_does_not_capture_fiction(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_RESEARCH))
    div = mgr.divide("写一篇关于扩散模型的文献综述")
    assert div.vertical == "research"
    assert div.vertical != "fiction_writing"


def test_decision_menu_disambiguates_fiction_from_literature_review():
    prompt = build_vertical_decision_prompt(
        "write a short story", verticals_with_purpose=VERTICAL_PURPOSES
    )
    assert "fiction_writing" in prompt
    # The menu the model reads must explicitly separate fiction from a survey.
    assert "literature review" in prompt.lower()
    assert "narrative prose" in prompt.lower()


def test_persist_resolve_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    persist_vertical(tmp_path, "fiction_writing")
    assert resolve_vertical(tmp_path) == "fiction_writing"
