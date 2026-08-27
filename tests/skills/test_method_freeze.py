from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals._base import load_vertical_contract
from argus_skill.verticals.research.method_freeze import (
    CONFIRMATION_RESULT_PATH,
    FREEZE_PATH,
    declare_method_freeze,
    record_confirmation_result,
)


def test_helpers_write_well_formed_freeze_and_confirmation(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("paper\n", encoding="utf-8")

    freeze = declare_method_freeze(
        tmp_path,
        method_identity="method-v12",
        method_description="Fixed objective and estimator.",
        confirmation_command="python research/confirm.py --split heldout-v1",
        data_split_identity="heldout-v1 sha256:abc",
        frozen_at="2026-08-27T00:00:00+00:00",
    )
    confirmation = record_confirmation_result(
        tmp_path,
        result={"headline_accuracy": 0.73},
        completed_at="2026-08-27T01:00:00+00:00",
    )

    assert json.loads((tmp_path / FREEZE_PATH).read_text()) == freeze
    assert json.loads((tmp_path / CONFIRMATION_RESULT_PATH).read_text()) == confirmation
    assert len(freeze["manuscript_sha256_at_freeze"]) == 64
    assert confirmation["confirmation_run"]["data_split_identity"].startswith("heldout-v1")


def test_research_contract_review_prompt_renders_method_freeze_facts(
    tmp_path: Path,
) -> None:
    contract = load_vertical_contract("research", project_root=tmp_path)

    def review_fragment() -> str:
        return contract.prompt_fragment(
            role="reviewer",
            operation="evaluate",
            stage="review",
            scope="",
            project_root=tmp_path,
        )

    assert "Declared method freeze" not in review_fragment()
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("frozen manuscript\n", encoding="utf-8")
    declare_method_freeze(
        tmp_path,
        method_identity="method-final",
        method_description="Frozen method.",
        confirmation_command="python confirm.py",
        data_split_identity="never-seen-confirmation-split",
    )
    frozen_digest = json.loads(
        (tmp_path / FREEZE_PATH).read_text()
    )["manuscript_sha256_at_freeze"]
    frozen_prompt = review_fragment()

    assert "the manuscript has changed since the freeze" not in frozen_prompt
    assert frozen_digest not in frozen_prompt

    manuscript.write_text("changed manuscript\n", encoding="utf-8")

    prompt = review_fragment()

    assert "method-final" in prompt
    assert "never-seen-confirmation-split" in prompt
    assert "Headline numbers may change only" in prompt
    assert "Further exploration variants belong to the next paper" in prompt
    assert "compare every headline number" in prompt
    assert "against the then-current manuscript" in prompt
    assert "the manuscript has changed since the freeze" in prompt
    assert frozen_digest not in prompt


def test_research_prompts_disclose_changed_trials_and_review_the_full_record(
    tmp_path: Path,
) -> None:
    contract = load_vertical_contract("research", project_root=tmp_path)
    engineer = contract.prompt_fragment(
        role="engineer",
        operation="mission",
        stage="research",
        scope="",
        project_root=tmp_path,
    )
    reviewer = contract.prompt_fragment(
        role="reviewer",
        operation="evaluate",
        stage="review",
        scope="",
        project_root=tmp_path,
    )

    assert all(
        phrase in prompt
        for phrase, prompt in (
            ("after inspecting results", engineer),
            ("report's main text", engineer),
            ("discarded or archived failed rounds", reviewer),
            ("final showcase is not a qualified review", reviewer),
        )
    )
