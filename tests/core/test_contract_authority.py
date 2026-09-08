"""Checkability must not grant permission to change operator requirements."""
import json

import pytest

from argus_skill.core.project_contract import (
    Clause,
    ContractError,
    confirmation_changes,
    contract_briefing,
    issue_confirmation,
    load_contract,
    make_clause,
    new_contract,
    revise_contract,
    save_contract,
)


def test_semantic_operator_boundary_cannot_be_removed():
    boundary = make_clause('semantic', 'Use only data sources approved by the operator')
    current = new_contract(objective='Analyse the dataset', clauses=[boundary])
    with pytest.raises(ContractError, match='operator confirmation'):
        revise_contract(current=current, clauses=[], by='manager')


def test_manager_owned_numeric_working_parameter_can_change():
    current = new_contract(objective='Measure latency', clauses=[
        make_clause('precise', 'Use batches of 8 during exploration', authority='manager'),
    ])
    updated, _ = revise_contract(current=current, clauses=[
        make_clause('precise', 'Use batches of 16 during exploration', authority='manager'),
    ], by='manager')
    assert updated.precise()[0].text == 'Use batches of 16 during exploration'


def test_authority_cannot_be_downgraded_by_relabeling_a_clause():
    text = 'Keep unpublished source material private'
    boundary = make_clause('semantic', text, authority='operator')
    current = new_contract(objective='Review the manuscript', clauses=[boundary])
    proposed = make_clause('semantic', text, authority='manager')
    with pytest.raises(ContractError, match='operator confirmation'):
        revise_contract(current=current, clauses=[proposed], by='manager')
    confirmed, revision = revise_contract(
        current=current, clauses=[proposed], by='manager',
        confirmation=issue_confirmation(contract=current, covers=[boundary.id]),
    )
    assert confirmed.clauses[0].authority == 'manager'
    assert revision.removed == (boundary.id,)


def test_legacy_permissions_are_preserved_without_rewriting_the_file(tmp_path):
    path = tmp_path / 'goal_contract.json'
    payload = {'contract': {'objective': 'Review a manuscript', 'clauses': [
        {'kind': 'semantic', 'text': 'Keep the report readable'},
        {'kind': 'precise', 'text': 'Stay within the agreed budget'},
    ]}, 'history': []}
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    current = load_contract(tmp_path)
    assert current.semantic()[0].authority == 'manager'
    assert current.precise()[0].authority == 'operator'
    with pytest.raises(ContractError, match='operator confirmation'):
        revise_contract(current=current, clauses=[], by='manager')
    assert path.read_bytes() == before


def test_unknown_explicit_authority_does_not_grant_manager_edit_permission(tmp_path):
    path = tmp_path / 'goal_contract.json'
    path.write_text(json.dumps({'contract': {'objective': 'Review', 'clauses': [
        {'kind': 'semantic', 'text': 'Keep source material private', 'authority': 'unknown'},
    ]}}))
    current = load_contract(tmp_path)
    with pytest.raises(ContractError, match='operator confirmation'):
        revise_contract(current=current, clauses=[], by='manager')


def test_authority_round_trip_and_prompt_distinguish_binding_intent_from_working_parameters(tmp_path):
    current = new_contract(objective='Review a manuscript', clauses=[
        make_clause('semantic', 'Do not disclose unpublished text', authority='operator'),
        make_clause('precise', 'Review in batches of 8', authority='manager'),
    ])
    save_contract(tmp_path, contract=current)
    loaded = load_contract(tmp_path)
    assert loaded == current
    briefing = contract_briefing(loaded)
    assert 'Operator-owned requirements' in briefing
    assert 'Manager-owned working requirements' in briefing
    assert 'Do not disclose unpublished text' in briefing


def test_exclusion_is_not_an_unprotected_way_to_remove_operator_boundaries():
    current = new_contract(objective='Audit the system', exclusions=['Do not publish private data'])
    with pytest.raises(ContractError, match='operator confirmation'):
        revise_contract(current=current, exclusions=[], by='manager')


def test_invalid_modification_authority_is_rejected():
    with pytest.raises(ContractError, match='authority'):
        make_clause('semantic', 'Keep source data private', authority='anyone')


def test_confirmation_enumeration_uses_the_same_normalization_as_revision():
    current = new_contract(objective='Review the data')
    proposed = [Clause('SEMANTIC', ' Use approved sources ', authority='OPERATOR')]
    changes = confirmation_changes(current, clauses=proposed)
    assert changes == (make_clause('semantic', 'Use approved sources').id,)
    updated, _ = revise_contract(
        current=current, clauses=proposed, by='manager',
        confirmation=issue_confirmation(contract=current, covers=changes),
    )
    assert updated.clauses[0].authority == 'operator'
