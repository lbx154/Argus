"""A Manager may clarify what it meant; it may not quietly move the goalposts.

Operator decision (North-Star §9.3): the Manager clarifies semantic intent on
its own, but changing a precise constraint — a target number, a baseline, a
budget, the objective itself — needs the operator to agree.

The failure this prevents is specific and is the reason a contract exists at
all: a Manager that cannot meet a number relaxes the number, and the project
then reports success against a goal nobody agreed to. Every test below is
written from that angle rather than from the happy path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.core.project_contract import (
    CLAUSE_PRECISE,
    CLAUSE_SEMANTIC,
    ContractError,
    issue_confirmation,
    load_contract,
    load_history,
    make_clause,
    new_contract,
    revise_contract,
    save_contract,
)

_SPEEDUP = ("precise", "beat the PyTorch baseline by at least 1.5x on B200")
_RELAXED = ("precise", "beat the PyTorch baseline by at least 1.1x on B200")
_READABLE = ("semantic", "the write-up should be readable by a systems engineer")


def _contract():
    return new_contract(
        objective="make the attention kernel faster",
        clauses=[make_clause(*_SPEEDUP), make_clause(*_READABLE)],
    )


# -- what the Manager may do alone -------------------------------------------


def test_semantic_clarification_needs_nobody(tmp_path: Path) -> None:
    current = _contract()

    updated, revision = revise_contract(
        current=current,
        clauses=[
            make_clause(*_SPEEDUP),
            make_clause(CLAUSE_SEMANTIC, "the write-up should name its baseline"),
        ],
        by="manager",
    )

    assert updated.revision == 2
    assert revision.added == () and revision.removed == ()
    assert len(updated.semantic()) == 1


def test_recording_an_ambiguity_needs_nobody() -> None:
    updated, _ = revise_contract(
        current=_contract(),
        ambiguities=["operator did not say which sequence length matters"],
        by="manager",
    )

    assert updated.ambiguities == (
        "operator did not say which sequence length matters",
    )


# -- what it may not --------------------------------------------------------


def test_relaxing_a_precise_target_is_refused() -> None:
    """The whole point: 1.5x must not silently become 1.1x."""
    with pytest.raises(ContractError) as excinfo:
        revise_contract(
            current=_contract(),
            clauses=[make_clause(*_RELAXED), make_clause(*_READABLE)],
            by="manager",
        )

    assert "operator confirmation" in str(excinfo.value)


def test_adding_a_precise_constraint_is_refused_too() -> None:
    """Tightening is also a change to what done means, so it is also confirmed."""
    with pytest.raises(ContractError):
        revise_contract(
            current=_contract(),
            clauses=[
                make_clause(*_SPEEDUP),
                make_clause(*_READABLE),
                make_clause(CLAUSE_PRECISE, "must fit in 40GB"),
            ],
            by="manager",
        )


def test_rewriting_the_objective_is_refused() -> None:
    with pytest.raises(ContractError):
        revise_contract(
            current=_contract(),
            objective="make the attention kernel simpler",
            by="manager",
        )


# -- how the operator says yes, and how narrowly ----------------------------


def test_a_confirmation_covering_the_change_lets_it_through() -> None:
    current = _contract()
    changed = (make_clause(*_SPEEDUP).id, make_clause(*_RELAXED).id)
    confirmation = issue_confirmation(contract=current, covers=changed)

    updated, revision = revise_contract(
        current=current,
        clauses=[make_clause(*_RELAXED), make_clause(*_READABLE)],
        by="manager",
        confirmation=confirmation,
    )

    assert updated.precise()[0].text == _RELAXED[1]
    assert revision.confirmation_id == confirmation.confirmation_id


def test_a_confirmation_does_not_cover_a_change_it_never_named() -> None:
    """Confirming one relaxation must not authorise a second, unrelated one."""
    current = _contract()
    confirmation = issue_confirmation(
        contract=current,
        covers=[make_clause(*_SPEEDUP).id, make_clause(*_RELAXED).id],
    )

    with pytest.raises(ContractError) as excinfo:
        revise_contract(
            current=current,
            clauses=[
                make_clause(*_RELAXED),
                make_clause(*_READABLE),
                make_clause(CLAUSE_PRECISE, "and skip the correctness check"),
            ],
            by="manager",
            confirmation=confirmation,
        )

    assert "does not cover" in str(excinfo.value)


def test_a_confirmation_cannot_be_replayed_after_the_contract_moves() -> None:
    current = _contract()
    changed = (make_clause(*_SPEEDUP).id, make_clause(*_RELAXED).id)
    confirmation = issue_confirmation(contract=current, covers=changed)
    updated, _ = revise_contract(
        current=current,
        clauses=[make_clause(*_RELAXED), make_clause(*_READABLE)],
        by="manager",
        confirmation=confirmation,
    )

    with pytest.raises(ContractError) as excinfo:
        revise_contract(
            current=updated,
            clauses=[make_clause(*_READABLE)],
            by="manager",
            confirmation=confirmation,
        )

    assert "issued against revision" in str(excinfo.value)


def test_an_expired_confirmation_is_refused() -> None:
    current = _contract()
    changed = (make_clause(*_SPEEDUP).id, make_clause(*_RELAXED).id)
    confirmation = issue_confirmation(
        contract=current, covers=changed, ttl_seconds=60.0, now=1000.0
    )

    with pytest.raises(ContractError) as excinfo:
        revise_contract(
            current=current,
            clauses=[make_clause(*_RELAXED), make_clause(*_READABLE)],
            by="manager",
            confirmation=confirmation,
            now=2000.0,
        )

    assert "expired" in str(excinfo.value)


def test_a_clause_id_follows_its_text_not_its_position() -> None:
    """Otherwise reordering the list would redirect a confirmation elsewhere."""
    first = make_clause(*_SPEEDUP)
    same_text_later = make_clause(*_SPEEDUP)

    assert first.id == same_text_later.id
    assert first.id != make_clause(*_RELAXED).id


# -- persistence -------------------------------------------------------------


def test_no_contract_reads_back_as_none_not_as_an_empty_one(tmp_path: Path) -> None:
    """Projects that predate contracts are exempt, so the difference matters."""
    assert load_contract(tmp_path) is None


def test_a_saved_contract_round_trips(tmp_path: Path) -> None:
    save_contract(tmp_path, contract=_contract())

    loaded = load_contract(tmp_path)

    assert loaded is not None
    assert loaded.objective == "make the attention kernel faster"
    assert len(loaded.precise()) == 1
    assert len(loaded.semantic()) == 1


def test_the_revision_history_is_append_only(tmp_path: Path) -> None:
    current = _contract()
    save_contract(tmp_path, contract=current)
    updated, revision = revise_contract(
        current=current,
        ambiguities=["which sequence length?"],
        by="manager",
    )
    save_contract(tmp_path, contract=updated, revision=revision)
    updated2, revision2 = revise_contract(
        current=updated,
        ambiguities=["and which dtype?"],
        by="manager",
    )
    save_contract(tmp_path, contract=updated2, revision=revision2)

    history = load_history(tmp_path)

    assert [row["revision"] for row in history] == [2, 3]


def test_a_preserved_precise_clause_is_recorded_as_preserved() -> None:
    """The audit must show what survived, not only what moved."""
    _, revision = revise_contract(
        current=_contract(),
        clauses=[
            make_clause(*_SPEEDUP),
            make_clause(CLAUSE_SEMANTIC, "name the baseline"),
        ],
        by="manager",
    )

    assert revision.preserved == (make_clause(*_SPEEDUP).id,)


def test_a_corrupt_contract_file_reads_as_no_contract(tmp_path: Path) -> None:
    (tmp_path / "goal_contract.json").write_text("{not json", encoding="utf-8")

    assert load_contract(tmp_path) is None


def test_an_unknown_clause_kind_is_refused_at_construction() -> None:
    with pytest.raises(ContractError):
        make_clause("vibes", "it should feel good")


# -- the wiring: a contract has to actually get written ----------------------


def test_manager_commit_records_the_contract(tmp_path: Path) -> None:
    """Otherwise this whole module is a type nobody constructs."""
    from types import SimpleNamespace

    from argus_skill.manager.front_door import PreparedManagerHandoff

    division = SimpleNamespace(
        execution_task="make the attention kernel faster",
        vertical="kernelbench",
        research_target_level="",
        target_venue="",
    )
    handoff = PreparedManagerHandoff(
        mem=SimpleNamespace(root=tmp_path),
        body="make the attention kernel faster",
        manager=SimpleNamespace(
            commit_vertical_decision=lambda *a, **k: division
        ),
        decision=division,
        intent_id="intent-1",
        root_task_id=None,
    )

    handoff.commit()

    contract = load_contract(tmp_path)
    assert contract is not None
    assert contract.objective == "make the attention kernel faster"


def test_a_second_commit_does_not_overwrite_a_confirmed_contract(
    tmp_path: Path,
) -> None:
    """Re-triage must not reset a constraint the operator confirmed."""
    from types import SimpleNamespace

    from argus_skill.manager.front_door import PreparedManagerHandoff

    current = _contract()
    changed = (make_clause(*_SPEEDUP).id, make_clause(*_RELAXED).id)
    updated, revision = revise_contract(
        current=current,
        clauses=[make_clause(*_RELAXED), make_clause(*_READABLE)],
        by="manager",
        confirmation=issue_confirmation(contract=current, covers=changed),
    )
    save_contract(tmp_path, contract=updated, revision=revision)

    division = SimpleNamespace(
        execution_task="something else entirely",
        vertical="kernelbench",
        research_target_level="",
        target_venue="",
    )
    PreparedManagerHandoff(
        mem=SimpleNamespace(root=tmp_path),
        body="something else entirely",
        manager=SimpleNamespace(commit_vertical_decision=lambda *a, **k: division),
        decision=division,
        intent_id="intent-2",
        root_task_id=None,
    ).commit()

    reloaded = load_contract(tmp_path)
    assert reloaded is not None
    assert reloaded.revision == 2
    assert reloaded.precise()[0].text == _RELAXED[1]
