from argus_skill.life.supervisor._mission_execution import (
    bounded_dag_node_max_rounds,
)


def test_bounded_dag_node_allows_one_reviewer_repair_round(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_BOUNDED_DAG_NODE_MAX_ROUNDS", raising=False)

    assert bounded_dag_node_max_rounds() == 3


def test_bounded_dag_round_budget_is_short_and_never_single_round(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_BOUNDED_DAG_NODE_MAX_ROUNDS", "1")
    assert bounded_dag_node_max_rounds() == 2

    monkeypatch.setenv("ARGUS_SKILL_BOUNDED_DAG_NODE_MAX_ROUNDS", "99")
    assert bounded_dag_node_max_rounds() == 8

    monkeypatch.setenv("ARGUS_SKILL_BOUNDED_DAG_NODE_MAX_ROUNDS", "invalid")
    assert bounded_dag_node_max_rounds() == 3
