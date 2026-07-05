from argus_skill.apps.cli._follow import _follow_layer_from_event


def test_follow_layer_detects_all_four_roles() -> None:
    assert _follow_layer_from_event({"type": "life.manager.intent.completed"}, "engineer") == "manager"
    assert _follow_layer_from_event({"type": "life.planner.verdict"}, "engineer") == "planner"
    assert _follow_layer_from_event({"type": "round.start"}, "planner") == "engineer"
    assert _follow_layer_from_event({"type": "round.review.started"}, "engineer") == "reviewer"


def test_follow_layer_prefers_explicit_agent_layer() -> None:
    assert _follow_layer_from_event({"agent_layer": "manager", "type": "round.start"}, "engineer") == "manager"
