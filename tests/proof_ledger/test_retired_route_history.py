"""Retiring a plan preserves its exact historical goal after claim revision."""
from dataclasses import replace

import pytest

from argus.proof_ledger import ClaimVersion, ContextVersion, MathState, ProofRoute, load_state
from argus.verticals.math.math_state import main


@pytest.fixture
def revised_route():
    context = ContextVersion(context_id="ctx", version=1, statement="Integers with usual order.")
    old = ClaimVersion(
        claim_id="C1", version=1, context=context.ref(), natural_statement="2 > 0."
    )
    new = replace(old, version=2, natural_statement="2 > 1 > 0.")
    lemma = replace(old, claim_id="L1", natural_statement="2 > 1.")
    route = ProofRoute(route_id="R1", goal=old.ref(), obligations=(lemma.ref(),))
    state = MathState(contexts=[context], claims=[old, new, lemma], routes=[route])
    return state, route, new, lemma


def test_retired_goal_can_reference_real_history_without_rewriting_it(revised_route):
    state, route, new, lemma = revised_route
    state.routes[0] = replace(route, retired_because="Replaced by a stronger claim.")
    state.routes.append(ProofRoute(route_id="R2", goal=new.ref(), obligations=(lemma.ref(),)))
    before = state.as_dict()
    assert not state.validate()
    assert state.as_dict() == before


@pytest.mark.parametrize("reason", ["", "   "])
def test_active_route_still_requires_current_goal(revised_route, reason):
    state, route, _, _ = revised_route
    state.routes[0] = replace(route, retired_because=reason)
    assert "route_goal_stale" in {issue.code for issue in state.validate()}


@pytest.mark.parametrize(
    "changes",
    [{"subject_id": "missing"}, {"content_hash": "0" * 64}],
)
def test_retirement_does_not_accept_an_unrecorded_exact_reference(revised_route, changes):
    state, route, _, _ = revised_route
    state.routes[0] = replace(
        route, goal=replace(route.goal, **changes), retired_because="Retired."
    )
    assert "route_goal_stale" in {issue.code for issue in state.validate()}


def test_active_self_dependency_is_still_rejected(revised_route):
    state, route, new, _ = revised_route
    state.routes[0] = replace(route, goal=new.ref(), obligations=(new.ref(),))
    assert "route_circular" in {issue.code for issue in state.validate()}


def test_cli_retire_revise_replace_keeps_history_valid(tmp_path, capsys):
    def run(*args):
        code = main([args[0], "--project-root", str(tmp_path), *args[1:]])
        output = capsys.readouterr()
        return code, output

    def succeeds(*args):
        code, output = run(*args)
        assert code == 0, output

    succeeds("context", "--id", "ctx", "--statement", "Integers with usual order.")
    succeeds("claim", "--id", "C1", "--context", "ctx", "--statement", "2 > 0.")
    succeeds("claim", "--id", "L1", "--context", "ctx", "--statement", "2 > 1.")
    succeeds("route", "--id", "R1", "--goal", "C1", "--obligation", "L1")
    succeeds("check")
    succeeds("retire-route", "--id", "R1", "--because", "Replaced by a stronger claim.")
    before = load_state(tmp_path).as_dict()
    succeeds("revise-claim", "--id", "C1", "--statement", "2 > 1 > 0.")
    succeeds("route", "--id", "R2", "--goal", "C1", "--obligation", "L1")
    succeeds("check")
    after = load_state(tmp_path).as_dict()
    assert after["routes"][0] == before["routes"][0]
    assert all(claim in after["claims"] for claim in before["claims"])
    code, _ = run("route", "--id", "R1", "--goal", "C1", "--obligation", "L1")
    assert code != 0
    assert load_state(tmp_path).as_dict() == after
