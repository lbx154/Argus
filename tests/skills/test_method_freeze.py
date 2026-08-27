from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.method_freeze import (
    CONFIRMATION_RESULT_PATH,
    FREEZE_PATH,
    declare_method_freeze,
    record_confirmation_result,
    research_review_prompt_block,
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


def test_review_prompt_is_silent_without_freeze_and_renders_facts_when_present(
    tmp_path: Path,
) -> None:
    assert research_review_prompt_block(tmp_path) == ""
    declare_method_freeze(
        tmp_path,
        method_identity="method-final",
        method_description="Frozen method.",
        confirmation_command="python confirm.py",
        data_split_identity="never-seen-confirmation-split",
    )

    prompt = research_review_prompt_block(tmp_path)

    assert "method-final" in prompt
    assert "never-seen-confirmation-split" in prompt
    assert "Headline numbers may change only" in prompt
    assert "Further exploration variants belong to the next paper" in prompt
    assert "compare every headline number" in prompt
