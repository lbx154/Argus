
import pytest

from argus.core.session import SessionMeta, read_session_meta, write_session_meta
from argus.manager.domain_author import RouteContext
from argus.manager.front_door import _maybe_name_session


def session(tmp_path):
    sid = "s-agent-title"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "projects" / sid).mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(workspace), workdir=str(workspace)))
    return {"session_id": sid, "global_root": tmp_path}


@pytest.mark.parametrize("title", ["", "NONE", "KEEP", "17"])
def test_missing_or_placeholder_titles_do_not_freeze_raw_input(tmp_path, title):
    state = session(tmp_path)
    assert _maybe_name_session(state, "17\nLong raw user instruction", suggested_name=title) == ""
    assert read_session_meta(tmp_path, state["session_id"]).display_name == ""
    _maybe_name_session(state, "next turn", suggested_name="订单去重")
    assert read_session_meta(tmp_path, state["session_id"]).display_name == "订单去重"


@pytest.mark.parametrize("fast", [False, True])
@pytest.mark.parametrize(("title", "named"), [("Refund calculation", "Refund calculation"),
                                              ("none", ""), ("KEEP", ""), ("42", "")])
def test_a_route_carries_an_agent_title_without_an_extra_call(fast, title, named):
    """The Manager's session_title names the session and the task; a
    placeholder in its place is no title for either."""
    context = RouteContext(known_verticals=("software",), fast=fast)
    decision = context.decide({"to": "team", "summary": "A small repository change", "assumptions": [],
                               "choice": "existing", "vertical": "software", "workflow_mode": "direct",
                               "session_title": title, "execution_task": "Fix the refund formula"})
    assert (decision.vertical, decision.session_title) == ("software", named)
