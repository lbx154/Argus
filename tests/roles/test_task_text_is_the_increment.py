"""A task's text is its increment; only the operator's words and the Protocol are frozen.

s-efe9deac's Planner filled the nine-heading implementation brief for every
Experiment task (one reached 6,730 characters), and re-issued briefs had to keep
their acceptance verbatim. Task 02a2583442f7's brief closed its budget with
"若无法在此合同完成，保留原件和缺口，不自动重跑或放宽": when vLLM found no memory for
its KV cache, the Engineer would not change the memory setting and the operator
was asked to authorize the fix (11:57, answered 14:10; asked again at 14:28).
The prompts are rendered from the state test_hard_gate_homes uses, with that
task's real brief, trimmed to three of its sections.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_hard_gate_homes import _engineer, _planner, _project, _reviewer

import argus

BRIEF = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "brief_02a2583442f7.json")
    .read_text(encoding="utf-8")
)
METHOD_CARD = Path(argus.__file__).parent / "verticals" / "research" / "skills" / "engineer" / "method-card.md"
TEMPLATE = ("implementation brief", "Environment prerequisites", "verbatim")
LOCK_RULE = ("An operator-locked direction waives only an objection that the idea is not original; "
             "evidence against its effect is listed under Known prior art in METHOD.md and tested, "
             "never set aside.")


def test_the_planner_writes_the_increment_without_a_template(tmp_path) -> None:
    state, workspace, mission = _project(tmp_path)
    prompt = _planner(state, workspace, mission)

    assert [word for word in TEMPLATE if word in prompt] == []
    assert " ".join(prompt.split()).count(
        "A task's text carries only what is new for it and the operator's own acceptance words"
    ) == 1


def test_the_engineer_reads_the_real_brief_as_written_and_owns_its_settings(tmp_path) -> None:
    state, workspace, mission = _project(tmp_path)
    prompt = _engineer(state, workspace, {**mission, "objective": BRIEF["objective"]}, None)
    rules = prompt.replace(BRIEF["objective"], "")

    assert "## Current mission task\n" + BRIEF["objective"] in prompt
    assert [word for word in TEMPLATE if word in rules] == []
    assert prompt.count("keeps the METHOD.md Protocol's data, scale and acceptance: repair the "
                        "cause, never lower the bar. These and the operator's own acceptance "
                        "words are the only numbers frozen, never relaxed; a tolerance or setting "
                        "an agent chose for engineering is reported, or changed with the reason "
                        "beside it.") == 1


def test_the_method_card_asks_whether_the_experiment_can_answer_instead_of_a_brief() -> None:
    card = METHOD_CARD.read_text(encoding="utf-8")
    template = card[card.index("```markdown"):]

    assert [word for word in TEMPLATE if word in card] == []
    assert "## The implementation brief" not in card
    for item in ("Ceiling at both ends", "Minimum detectable effect",
                 "Strongest same-information baseline", "Known counter-evidence"):
        assert template.count(item) == 1, item
    assert template.count("## Known prior art") == 1
    assert "waives only" not in card


def test_the_lock_rule_reads_once_beside_the_originality_rule_it_limits(tmp_path) -> None:
    # A filled card replaces its placeholder text, so the rule lives where
    # the Reviewer and the Planner judge related work, not in the template.
    state, workspace, mission = _project(tmp_path)

    assert [_reviewer(state, workspace, mission).count(LOCK_RULE),
            _planner(state, workspace, mission).count(LOCK_RULE)] == [1, 1]
