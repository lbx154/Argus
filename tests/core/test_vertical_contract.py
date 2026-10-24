from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.vertical_contract import (
    VerticalContractError,
    vertical_contract,
)
from argus.skills.stage_machine import ChecklistItem


def _item(item_id: str) -> ChecklistItem:
    return ChecklistItem(item_id, f"Verify {item_id}", f"evidence for {item_id}")


@pytest.mark.parametrize("value", [None, "false", "true", 0, 1, [], {}])
def test_stage_rollback_policy_rejects_non_boolean_values(value: object) -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("build",),
        CHECKLIST_ITEMS={"build": (_item("build.output"),)},
        completion_gate="none",
        ALLOW_STAGE_ROLLBACK=value,
    )

    with pytest.raises(VerticalContractError, match="ALLOW_STAGE_ROLLBACK must be a boolean"):
        vertical_contract("external_lab", provider)


def test_vertical_validator_can_defer_checks_by_verification_profile(
    tmp_path: Path,
) -> None:
    from argus.core.pipeline_state import write_pipeline_state

    seen: list[str | None] = []

    def issues(stage, root, *, verification_profile=None):
        _ = (stage, root)
        seen.append(verification_profile)
        return [] if verification_profile == "explore" else ["certify now"]

    contract = vertical_contract(
        "feedback_loop",
        SimpleNamespace(
            CHECKLIST_STAGE_ORDER=("propose", "final"),
            CHECKLIST_ITEMS={
                "propose": (_item("propose.output"),),
                "final": (_item("final.output"),),
            },
            VERIFICATION_STAGE_PROFILES={
                "propose": "explore",
                "final": "certify",
            },
            completion_gate="none",
            stage_completion_issues=issues,
        ),
    )

    assert contract.completion_issues("propose", tmp_path) == ()
    assert contract.completion_issues("final", tmp_path) == ("certify now",)
    # A profile saved by an older version is ignored: the stage decides.
    state_root = tmp_path / "state"
    write_pipeline_state(state_root, {"verification_profile": "certify"})
    assert contract.completion_issues(
        "propose",
        tmp_path,
        state_root=state_root,
    ) == ()
    assert seen == ["explore", "certify", "explore"]


def test_non_callable_completion_validator_fails_visibly() -> None:
    with pytest.raises(VerticalContractError, match="non-callable"):
        vertical_contract(
            "broken",
            SimpleNamespace(
                CHECKLIST_STAGE_ORDER=("verify",),
                CHECKLIST_ITEMS={"verify": (_item("verify.output"),)},
                completion_gate="none",
                stage_completion_issues=[],
            ),
        )


def test_non_callable_role_prompt_context_fails_visibly() -> None:
    with pytest.raises(VerticalContractError, match="non-callable role prompt context"):
        vertical_contract(
            "broken",
            SimpleNamespace(
                CHECKLIST_STAGE_ORDER=("verify",),
                CHECKLIST_ITEMS={"verify": (_item("verify.output"),)},
                completion_gate="none",
                render_role_prompt_context=[],
            ),
        )


def test_empty_required_checklist_fails_but_runtime_authored_is_explicit() -> None:
    with pytest.raises(VerticalContractError, match="empty required"):
        vertical_contract(
            "empty",
            SimpleNamespace(
                CHECKLIST_STAGE_ORDER=("work",),
                CHECKLIST_ITEMS={"work": ()},
                completion_gate="none",
            ),
        )

    contract = vertical_contract(
        "authored_later",
        SimpleNamespace(
            CHECKLIST_STAGE_ORDER=("work",),
            CHECKLIST_OPTIONAL_STAGES=("work",),
            CHECKLIST_ITEMS={},
            completion_gate="none",
        ),
    )
    assert contract.checklist_optional_stages == frozenset({"work"})


def test_incomplete_vertical_fails_visibly() -> None:
    with pytest.raises(VerticalContractError, match="no checklist"):
        vertical_contract(
            "broken",
            SimpleNamespace(
                CHECKLIST_STAGE_ORDER=("work",),
                completion_gate="none",
            ),
        )


def _live_search_provider(**extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("scope", "solve"),
        CHECKLIST_ITEMS={
            "scope": (_item("scope.output"),),
            "solve": (_item("solve.output"),),
        },
        completion_gate="none",
        **extra,
    )


def test_live_search_stage_typos_fail_visibly() -> None:
    with pytest.raises(VerticalContractError, match="live search for unknown stages"):
        vertical_contract(
            "typo",
            _live_search_provider(ENGINEER_LIVE_SEARCH_STAGES=("research",)),
        )

    # A bare string would otherwise be iterated character by character.
    with pytest.raises(VerticalContractError, match="not a collection of stages"):
        vertical_contract(
            "stringly",
            _live_search_provider(ENGINEER_LIVE_SEARCH_STAGES="solve"),
        )


def test_blank_live_search_stage_is_an_error_not_a_silent_drop() -> None:
    """A dropped blank would forge the "never search" declaration.

    ``frozenset()`` is a meaningful answer ("this vertical never searches"), so
    quietly discarding a whitespace typo would turn it into that answer with no
    diagnostic at all.
    """
    for blank in ("", "   ", "\t"):
        with pytest.raises(VerticalContractError, match="blank live search stage"):
            vertical_contract(
                "blank",
                _live_search_provider(
                    ENGINEER_LIVE_SEARCH_STAGES=("scope", blank),
                ),
            )


def test_non_string_live_search_stage_is_rejected_not_coerced() -> None:
    """``str()`` coercion would let anything with a __str__ through."""

    class _LooksLikeAStage:
        def __str__(self) -> str:
            return "solve"

    with pytest.raises(VerticalContractError, match="is not a string"):
        vertical_contract(
            "coerced",
            _live_search_provider(
                ENGINEER_LIVE_SEARCH_STAGES=(_LooksLikeAStage(),),
            ),
        )

    with pytest.raises(VerticalContractError, match="is not a string"):
        vertical_contract(
            "numeric",
            _live_search_provider(ENGINEER_LIVE_SEARCH_STAGES=("scope", 1)),
        )


def test_core_has_no_vertical_package_imports() -> None:
    """Core defines the vertical contract and must never resolve one.

    ``rglob``, not ``glob``: ``core/`` grew subpackages after this test was
    written, and a non-recursive scan silently stopped covering them. A
    subpackage is exactly where the import would appear, since that is where
    the code long enough to want a shortcut lives.
    """
    core = Path(__file__).parents[2] / "argus" / "core"
    offenders: list[str] = []
    for path in core.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "verticals" in str(node.module or ""):
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if ".verticals" in alias.name:
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
