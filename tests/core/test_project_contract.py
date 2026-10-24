"""A Manager may clarify what it meant; it may not quietly move the goalposts.

The Manager may clarify explicitly manager-owned intent on its own. Changing
operator-owned requirements needs the operator to agree, independently of
whether verification uses measurements or qualitative judgment.

The failure this prevents is specific and is the reason a contract exists at
all: a Manager that cannot meet a number relaxes the number, and the project
then reports success against a goal nobody agreed to. Every test below is
written from that angle rather than from the happy path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.project_contract import (
    CLAUSE_PRECISE,
    ContractError,
    contract_briefing,
    issue_confirmation,
    load_contract,
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
        clauses=[make_clause(*_SPEEDUP), make_clause(*_READABLE, authority="manager")],
    )


# -- what the Manager may do alone -------------------------------------------


def test_recording_an_assumption_needs_nobody() -> None:
    updated, _ = revise_contract(
        current=_contract(),
        assumptions=["sequence length 4096 (rather than every length)"],
        by="manager",
    )

    assert updated.assumptions == ("sequence length 4096 (rather than every length)",)


# -- what it may not --------------------------------------------------------


# -- how the operator says yes, and how narrowly ----------------------------


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
                make_clause(*_READABLE, authority="manager"),
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
        clauses=[make_clause(*_RELAXED), make_clause(*_READABLE, authority="manager")],
        by="manager",
        confirmation=confirmation,
    )

    with pytest.raises(ContractError) as excinfo:
        revise_contract(
            current=updated,
            clauses=[make_clause(*_READABLE, authority="manager")],
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
            clauses=[make_clause(*_RELAXED), make_clause(*_READABLE, authority="manager")],
            by="manager",
            confirmation=confirmation,
            now=2000.0,
        )

    assert "expired" in str(excinfo.value)


# -- persistence -------------------------------------------------------------


def test_an_unknown_clause_kind_is_refused_at_construction() -> None:
    with pytest.raises(ContractError):
        make_clause("vibes", "it should feel good")


# -- the wiring: a contract has to actually get written ----------------------


def test_contract_briefing_does_not_repeat_a_superseded_objective() -> None:
    """A stale contract must not tell roles that old no-code work still binds."""
    contract = new_contract(
        objective="audit the source tree but do not change code",
        clauses=[make_clause(CLAUSE_PRECISE, "do not edit production files")],
    )

    block = contract_briefing(
        contract,
        authoritative_objective="start fixing the best Argus optimization",
    )

    assert "start fixing the best Argus optimization" in block
    assert "superseded objective" in block
    assert "audit the source tree" not in block
    assert "do not edit production files" not in block


# -- the contract has to reach the role that must honour it ------------------


def test_assumptions_are_shown_as_readings_chosen_without_asking(
    tmp_path: Path,
) -> None:
    """Every role sees which readings were the Manager's, not the operator's."""
    from argus.core.project_contract import contract_briefing

    contract = new_contract(
        objective="make it faster",
        assumptions=["1.5x over PyTorch (rather than any speedup)"],
    )

    briefing = contract_briefing(contract)

    assert "1.5x over PyTorch (rather than any speedup)" in briefing
    assert "the Manager chose without asking" in briefing


def test_manager_records_only_operator_stated_constraints(tmp_path: Path) -> None:
    """A constraint nobody asked for becomes a goal nobody agreed to."""
    from types import SimpleNamespace

    from argus.manager.front_door import PreparedManagerHandoff

    division = SimpleNamespace(
        execution_task="make the attention kernel faster",
        vertical="kernelbench",
        research_target_level="",
        target_venue="",
        precise_constraints=("at least 1.5x over PyTorch on B200",),
        assumptions=({"chose": "sequence length 4096", "instead_of": "every length"},),
    )
    PreparedManagerHandoff(
        mem=SimpleNamespace(root=tmp_path),
        body="make the attention kernel faster, at least 1.5x",
        manager=SimpleNamespace(commit_vertical_decision=lambda *a, **k: division),
        decision=division,
        intent_id="i",
        root_task_id=None,
    ).commit()

    contract = load_contract(tmp_path)
    assert contract is not None
    assert [c.text for c in contract.precise()] == [
        "at least 1.5x over PyTorch on B200"
    ]
    assert contract.assumptions == ("sequence length 4096 (rather than every length)",)


def test_contract_recording_failure_is_visible(tmp_path: Path, monkeypatch) -> None:
    """Dispatch may continue, but stale contract authority must leave evidence."""
    from types import SimpleNamespace

    from argus.manager.front_door import PreparedManagerHandoff

    def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("argus.core.project_contract.save_contract", fail_save)
    decision = SimpleNamespace(
        execution_task="fix stale contract recording",
        vertical="software",
        research_target_level="",
        target_venue="",
    )

    PreparedManagerHandoff(
        mem=SimpleNamespace(root=tmp_path),
        body="fix stale contract recording",
        manager=SimpleNamespace(commit_vertical_decision=lambda *a, **k: decision),
        decision=decision,
        intent_id="i",
        root_task_id=None,
    ).commit()

    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "life.manager.goal_contract.failed" in events
    assert "disk full" in events


def test_the_clause_appears_in_the_real_planner_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    """End to end through the actual prompt builder, not just the block helper.

    Sabotaging the call in `roles/prompts/planner.py` turns this red; asserting
    on `contract_briefing` alone would not.
    """
    from argus.core.project_contract import (
        CLAUSE_PRECISE,
        state_dir_for_cwd,
    )
    from argus.roles.prompts.planner import build_continuous_prompt

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    workdir = tmp_path / "wd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    save_contract(
        state_dir_for_cwd(workdir),
        contract=new_contract(
            objective="make the attention kernel faster",
            clauses=[make_clause(CLAUSE_PRECISE, "at least 1.5x over PyTorch on B200")],
        ),
    )

    prompt = build_continuous_prompt(
        continuous_objective="make the attention kernel faster",
        journal_tail="",
        planning_cycle=0,
    )

    assert "at least 1.5x over PyTorch on B200" in prompt


@pytest.mark.parametrize("role", ["engineer", "reviewer"])
def test_every_role_that_could_violate_the_contract_can_see_it(
    tmp_path: Path, monkeypatch, role: str
) -> None:
    """Planner alone is not enough.

    The Engineer can satisfy a mission task while missing the requirement the
    task exists to serve, and the Reviewer's verdict is what closes work. A
    constraint only the Planner sees is a constraint the closing role never
    checks against.
    """
    from argus.core.project_contract import (
        CLAUSE_PRECISE,
        state_dir_for_cwd,
    )

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    workdir = tmp_path / "wd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    save_contract(
        state_dir_for_cwd(workdir),
        contract=new_contract(
            objective="make it faster",
            clauses=[make_clause(CLAUSE_PRECISE, "at least 1.5x over PyTorch on B200")],
        ),
    )

    if role == "engineer":
        from argus.roles.prompts.engineer import build_mission_prompt

        text = build_mission_prompt(
            task="optimise the inner loop",
            skill_text="",
            next_action=None,
        )
    else:
        from types import SimpleNamespace

        from argus.roles.prompts.reviewer import render_reviewer_prompt

        owner = SimpleNamespace(
            skill_store=None,
            memory_maintenance_enabled=False,
            mission=None,
            _last_prompt_block_stats=None,
        )
        static, delta = render_reviewer_prompt(
            owner,
            preselected_skill_block="",
            objective="optimise the inner loop",
            operator_messages=[],
            planner_review_instruction="",
            round_index=0,
            session_id=None,
            main_summary="did a thing",
            main_error=None,
            working_dir=workdir,
        )
        text = static + delta

    assert "at least 1.5x over PyTorch on B200" in text
