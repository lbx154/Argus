from __future__ import annotations

import pytest

from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi.routes.daemon import _resume_parks_after_start


@pytest.mark.parametrize("rc,enabled,resumed", [(0, True, True), (1, True, False), (0, False, False)])
def test_explicit_resume_ends_every_park_the_planner_or_a_question_does_not_hold(
    tmp_path, rc, enabled, resumed,
):
    memory = LifeMemory.open(tmp_path)
    for reason in ["provider_fence", "operator_pause", "infra", "research_open", "operator_question"]:
        item = memory.backlog.add(BacklogItem.new(title=reason, objective="synthetic"))
        memory.backlog.update(
            item.id, park_reason=reason,
            pending_question="Which?" if reason == "operator_question" else "",
        )
    result = {"rc": rc}
    assert _resume_parks_after_start(tmp_path, result, enabled=enabled) is result
    rows = {item.title: item.status for item in memory.backlog.all()}
    for reason in ["provider_fence", "operator_pause", "infra"]:
        assert rows[reason] == ("pending" if resumed else "parked")
    # The Planner retires its own parks; only the answer ends a question.
    assert rows["research_open"] == rows["operator_question"] == "parked"
