from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.vertical_contract import (
    VerticalContractError,
    vertical_contract,
)
from argus_skill.skills.stage_machine import ChecklistItem


def _item(item_id: str) -> ChecklistItem:
    return ChecklistItem(item_id, f"Verify {item_id}", f"evidence for {item_id}")


def test_minimal_non_research_vertical_implements_only_documented_contract() -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("build", "verify"),
        CHECKLIST_ITEMS={"build": (_item("build.output"),), "verify": (_item("verify.output"),)},
        completion_gate="none",
    )

    contract = vertical_contract("minimal_delivery", provider)

    assert contract.name == "minimal_delivery"
    assert contract.stage_order == ("build", "verify")
    assert contract.completion_gate == "none"
    assert contract.mission_kind == "custom"
    assert contract.ground_before_handoff is False
    assert contract.banner("engineer") == ""
    assert contract.evidence_schema is None
    assert contract.assurance_level == "reviewer"


def test_provider_declares_routing_metadata_without_manager_name_tables() -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("work",),
        CHECKLIST_ITEMS={"work": (_item("work.output"),)},
        completion_gate="none",
        MISSION_KIND="software",
        GROUND_BEFORE_HANDOFF=True,
    )

    contract = vertical_contract("maintainer", provider)

    assert contract.mission_kind == "software"
    assert contract.ground_before_handoff is True


def test_provider_completion_validator_is_typed_and_normalized(tmp_path: Path) -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("verify",),
        CHECKLIST_ITEMS={"verify": (_item("verify.output"),)},
        completion_gate="none",
        stage_completion_issues=lambda stage, root: [f"{stage}:{root.name}", ""],
    )

    contract = vertical_contract("validated", provider)

    assert contract.completion_issues("verify", tmp_path) == (
        f"verify:{tmp_path.name}",
    )
    assert contract.assurance_level == "hybrid"


def test_vertical_validator_can_defer_checks_by_verification_profile(
    tmp_path: Path,
) -> None:
    from argus_skill.core.pipeline_state import write_pipeline_state

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
    state_root = tmp_path / "state"
    write_pipeline_state(state_root, {"verification_profile": "certify"})
    assert contract.completion_issues(
        "propose",
        tmp_path,
        state_root=state_root,
    ) == ("certify now",)
    assert seen == ["explore", "certify", "certify"]


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
    assert contract.assurance_level == "runtime-authored"


def test_primary_stage_deliverables_are_exposed_by_contract() -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("scope", "work"),
        CHECKLIST_ITEMS={
            "scope": (_item("scope.output"),),
            "work": (_item("work.output"),),
        },
        STAGE_PRIMARY_DELIVERABLES={
            "scope": ("research/SCOPE.md", "research/setup.md"),
        },
        completion_gate="none",
    )

    contract = vertical_contract("scoped", provider)

    assert contract.primary_deliverables("scope") == (
        "research/SCOPE.md",
        "research/setup.md",
    )
    assert contract.primary_deliverables("work") == ()


def test_stage_checks_are_validated_and_mark_contract_hybrid() -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("work",),
        CHECKLIST_ITEMS={"work": (_item("work.output"),)},
        STAGE_CHECKS={"work": [("Artifact exists", "test -s RESULT.md")]},
        completion_gate="none",
    )

    contract = vertical_contract("checked", provider)

    assert contract.assurance_level == "hybrid"
    assert contract.stage_checks == {
        "work": (("Artifact exists", "test -s RESULT.md"),)
    }

    provider.STAGE_CHECKS = {"ghost": [("No", "false")]}
    with pytest.raises(VerticalContractError, match="unknown stages"):
        vertical_contract("checked", provider)


def test_incomplete_vertical_fails_visibly() -> None:
    with pytest.raises(VerticalContractError, match="no checklist"):
        vertical_contract(
            "broken",
            SimpleNamespace(
                CHECKLIST_STAGE_ORDER=("work",),
                completion_gate="none",
            ),
        )


_CORE_LIVE_SEARCH_DEFAULT = frozenset({"research"})


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


def test_undeclared_live_search_stages_cover_the_vertical() -> None:
    """A vertical that says nothing searches throughout its own stage order."""
    contract = vertical_contract("quiet", _live_search_provider())

    assert contract.engineer_live_search_stages is None
    assert contract.live_search_stages(_CORE_LIVE_SEARCH_DEFAULT) == frozenset(
        contract.stage_order
    )


def test_vertical_declares_its_own_live_search_stages() -> None:
    contract = vertical_contract(
        "declared",
        _live_search_provider(ENGINEER_LIVE_SEARCH_STAGES=("Scope", " solve ")),
    )

    assert contract.engineer_live_search_stages == frozenset({"scope", "solve"})
    assert contract.live_search_stages(_CORE_LIVE_SEARCH_DEFAULT) == frozenset(
        {"scope", "solve"}
    )


def test_declared_empty_live_search_is_distinct_from_no_declaration() -> None:
    """An explicit empty set means "never search", not "use the default"."""
    contract = vertical_contract(
        "offline",
        _live_search_provider(ENGINEER_LIVE_SEARCH_STAGES=frozenset()),
    )

    assert contract.engineer_live_search_stages == frozenset()
    assert contract.live_search_stages(_CORE_LIVE_SEARCH_DEFAULT) == frozenset()


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
    core = Path(__file__).parents[2] / "argus_skill" / "core"
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
