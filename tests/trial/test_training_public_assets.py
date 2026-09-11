"""Shipped library paths are distinct from arbitrary tenant/global files."""
import json

import pytest
from test_training_runtime import training as training

from argus_skill.trial.training_capture import (
    HOSTED_PROFILE,
    _content_diagnostic,
    _hosted_sensitive,
)
from argus_skill.trial.training_public_assets import (
    ROOT,
    check_public_skill_event,
    public_skill_body,
    public_skill_literal,
)

ASSET = ROOT + "/_shared_verticals/software/engineer/software-change-implementation.md"


@pytest.mark.parametrize("scope,relative", [
    ("software", "engineer/software-change-implementation.md"),
    ("software", "reviewer/software-change-review.md"),
    ("math", "engineer/math-research-execution.md"),
    ("research", "engineer"),
])
def test_builtin_registry_only_recognizes_published_paths(scope, relative):
    path = ROOT + "/_shared_verticals/" + scope + "/" + relative
    assert public_skill_literal(path)
    assert not _hosted_sensitive(path, sid="s-a1b2c3d4", mission_id="a1b2c3d4e5f6")
    assert not public_skill_literal(path + "/../private")
    assert not public_skill_literal(ROOT + "/_shared_verticals/private-custom/engineer/private.md")
    assert not public_skill_literal(ROOT + "/private-not-distributed.md")


@pytest.mark.parametrize("arguments", [{"path": ASSET}, {"path": ASSET, "offset": 2, "limit": 3}])
def test_actual_shipped_body_and_pi_line_selection_are_checked(arguments):
    body = public_skill_body(ASSET)
    assert body and "operator" in body.lower()
    lines = body.split("\n")
    if arguments.get("offset"):
        expected = "\n".join(lines[1:4]) + f"\n\n[{len(lines)-4} more lines in file. Use offset=5 to continue.]"
    else:
        expected = body
    payload = {"toolName": "read", "input": arguments, "content": [{"type": "text", "text": expected}],
               "isError": False, "output_complete": True}
    check_public_skill_event("tool_call", payload)
    check_public_skill_event("tool_result", payload)
    with pytest.raises(ValueError, match="unverified_public_skill_content"):
        check_public_skill_event("tool_result", {**payload, "content": [{"type": "text", "text": expected + " changed privately"}]})
    with pytest.raises(ValueError, match="unverified_public_skill_content"):
        check_public_skill_event("tool_call", {"toolName": "bash", "input": {"command": "cat " + ASSET}})


def test_modified_global_copy_is_quarantined_before_persistence_with_safe_diagnosis(training):
    sid, mission = "s-a1b2c3d4", "a1b2c3d4e5f6"
    project = training.analytics.tenants["tenant-one"]["data_dir"] / "home/.argus-skill/projects" / sid
    project.mkdir()
    (project / "session.json").write_text(json.dumps({"id": sid}))
    (project / "events.jsonl").touch()
    training.journal.poll("tenant-one")
    episode = training.capture.begin("tenant-one", sid, "synthetic-assets", observer_verified=True,
                                     allowed_tools=["read"], runtime_profile=HOSTED_PROFILE,
                                     runtime_metadata={"mission_id": mission})["episode_id"]
    payload = {"toolName": "read", "toolCallId": "read-real", "input": {"path": ASSET}}
    assert training.capture.event("tenant-one", sid, episode, "tool_call", payload)["state"] == "capturing"
    result = training.capture.event("tenant-one", sid, episode, "tool_result", {
        **payload, "content": [{"type": "text", "text": "PRIVATE_MODIFIED_SKILL_VALUE"}],
        "isError": False, "output_complete": True,
    })
    assert result["state"] == "quarantined" and result["reason"] == "unverified_public_skill_content"
    assert result["diagnostic"] == {"kind": "tool_result", "field": "payload.content", "detector": "public_skill_digest"}
    with training.analytics._db() as db:
        row = db.execute("SELECT record,diagnostic FROM training_tool_episodes WHERE id=?", (episode,)).fetchone()
    assert row["record"] == "[]" and "PRIVATE_MODIFIED_SKILL_VALUE" not in row["diagnostic"]


def test_diagnostic_fields_never_reveal_arbitrary_keys_or_matching_values():
    result = _content_diagnostic("tool_call", {"input": {"command": "cat /home/alice/private"}}, sid=None, mission_id=None)
    assert result == {"kind": "tool_call", "field": "payload.input.command", "detector": "private_path"}
    result = _content_diagnostic("context", {"PRIVATE_KEY_VALUE": "contact alice@example.org"}, sid=None, mission_id=None)
    assert result["field"] == "payload.*"
    assert "alice" not in json.dumps(result) and "PRIVATE_KEY_VALUE" not in json.dumps(result)
