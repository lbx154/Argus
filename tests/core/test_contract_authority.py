"""Checkability must not grant permission to change operator requirements."""
import json

import pytest

from argus.core.project_contract import (
    ContractError,
    contract_briefing,
    load_contract,
    make_clause,
    new_contract,
    revise_contract,
    save_contract,
)


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


def test_invalid_modification_authority_is_rejected():
    with pytest.raises(ContractError, match='authority'):
        make_clause('semantic', 'Keep source data private', authority='anyone')
