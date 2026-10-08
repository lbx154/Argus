"""Live-step formatting for the cockpit's progress trail."""

from __future__ import annotations

import pytest

from argus.core.progress_step import (
    ProgressDeduper,
    ProgressTally,
    describe_progress_step,
    strip_shell_wrapper,
)


def test_command_step_shows_the_real_command_not_a_euphemism() -> None:
    label, detail = describe_progress_step({
        "kind": "command_execution",
        "text": "/bin/bash -lc 'rg --line-number cockpit frontend/tui/src'",
        # The bucketed summary must NOT win: it hides what actually ran.
        "action_summary": "inspecting project state",
    })
    assert label == "$ rg --line-number cockpit frontend/tui/src"
    assert detail == "", "a single-line command already says everything"


def test_failed_command_is_marked() -> None:
    label, _ = describe_progress_step({
        "kind": "command_execution",
        "text": "pytest tests/test_x.py",
        "status": "failed",
    })
    assert label.startswith("✗ $ ")
    assert "pytest tests/test_x.py" in label


def test_multiline_command_keeps_the_rest_as_detail() -> None:
    label, detail = describe_progress_step({
        "kind": "command_execution",
        "text": "cd /repo\npytest -q tests/a.py",
    })
    assert label == "$ cd /repo"
    assert detail == "cd /repo pytest -q tests/a.py"


_WS = "/work/proj"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('view: {"path": "/work/proj/src/main.py"}', "查阅 main.py"),
        ('view: {"path": "/data/state/projects/abc/state.json"}', "查阅 Argus 内部文件"),
        ('view: {"path": "src/main.py"}', "查阅 main.py"),
        ('grep: {"pattern": "def route", "path": "/work/proj"}', "搜索：def route"),
        ('web_search: {"query": "rl reward shaping"}', "搜索：rl reward shaping"),
        ('web_fetch: {"url": "https://arxiv.org/abs/1234"}', "读取 arxiv.org"),
        ('edit: {"file_path": "/work/proj/a/b.py", "old_str": "{x}"}', "修改 b.py"),
        (
            "apply_patch: *** Begin Patch\n*** Update File: /work/proj/pkg/mod.py\n@@\n-{a}\n+{b}",
            "修改 mod.py",
        ),
        ('mystery_tool: {"x": 1}', "调用 mystery_tool"),
    ],
)
def test_tool_step_reads_as_verb_and_object(text: str, expected: str) -> None:
    label, detail = describe_progress_step({"kind": "tool_use", "text": text, "workspace": _WS})
    assert label == expected
    assert "{" not in label
    assert "/" not in label, "no path, absolute or relative, in the status line"
    # The raw arguments remain inspectable in the collapsed detail.
    assert detail.startswith(text.split(":", 1)[0])


def test_completion_echo_of_one_call_renders_once() -> None:
    dedupe = ProgressDeduper()
    start = {"kind": "tool_use", "call_id": "c-1", "status": "running", "text": 'view: {"path": "a.py"}'}
    done = {**start, "status": "completed"}
    assert dedupe.classify(start, describe_progress_step(start)[0]) == "new"
    assert dedupe.is_repeat(done, describe_progress_step(done)[0]) is True


# Shape of real runner rows: every call of a mission shares one item_id and
# none carries a call id; a failure is the same text reported again.
_MISSION = {"kind": "tool_use", "item_id": "7c17ec4c586c", "actor": "reviewer"}


def test_mission_level_item_id_does_not_merge_distinct_calls() -> None:
    dedupe = ProgressDeduper()
    first = {**_MISSION, "status": "running", "text": 'view: {"path": "/elsewhere/state/skills/a/skill.md"}'}
    second = {**_MISSION, "status": "running", "text": 'view: {"path": "/elsewhere/state/skills/b/notes.md"}'}
    label = describe_progress_step(first, _WS)[0]
    assert label == describe_progress_step(second, _WS)[0] == "查阅 Argus 内部文件"
    assert dedupe.classify(first, label) == "new"
    assert dedupe.classify(second, label) == "new", "a second call with the same label still happened"
    again = dict(first)
    assert dedupe.classify(again, label) == "new", "re-reading the same file is a new step"


def test_failure_of_a_known_call_is_shown_as_an_update() -> None:
    dedupe = ProgressDeduper()
    tally = ProgressTally()
    running = {**_MISSION, "status": "running", "text": 'view: {"path": "/work/proj/x.md"}'}
    failed = {**running, "status": "failed"}
    label = describe_progress_step(running, _WS)[0]
    assert dedupe.classify(running, label) == "new"
    assert dedupe.classify(failed, describe_progress_step(failed, _WS)[0]) == "update"
    tally.observe(running)
    tally.observe(failed)
    assert tally.reads == 1, "an outcome is not a second read"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('glob: {"pattern": "**/*"}', "查找 所有文件"),
        ('glob: {"pattern": "**/*.py"}', "查找 .py 文件"),
        ('glob: {"pattern": "src/**/*.ts"}', "查找 src 里的 .ts 文件"),
        ('glob: {"pattern": "METHOD.md"}', "查找 METHOD.md"),
        ('rg: {"pattern": "route", "paths": "/work/proj/notes.md"}', "搜索：route"),
        ('grep: {"query": "see /work/proj/deep/notes.md"}', "搜索：see notes.md"),
    ],
)
def test_search_patterns_read_without_slashes(text: str, expected: str) -> None:
    label, _ = describe_progress_step({"kind": "tool_use", "text": text}, _WS)
    assert label == expected
    assert "/" not in label


def test_workspace_from_the_caller_marks_internal_files() -> None:
    event = {"kind": "tool_use", "text": 'view: {"path": "/runtime/argus/verticals/research/skills/playbook.md"}'}
    assert describe_progress_step(event, _WS)[0] == "查阅 Argus 内部文件"
    inside = {"kind": "tool_use", "text": 'view: {"path": "/work/proj/RESEARCH_NOTES.md"}'}
    assert describe_progress_step(inside, _WS)[0] == "查阅 RESEARCH_NOTES.md"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # A repository the operator asked about lives outside the session
        # workspace; reading it is project work, not Argus plumbing.
        ("/data/someone/subject-repo/README.md", "查阅 README.md"),
        ("/srv/state/projects/s-1/handoffs/0f40/CHECKPOINT.md", "查阅 Argus 内部文件"),
        ("/srv/state/skills/_shared_verticals/research/paper-playbook.md", "查阅 Argus 内部文件"),
        ("/work/proj/.argus/teams/t1/shards/w1.jsonl", "查阅 Argus 内部文件"),
        ("/work/proj/.argus/teams/t1/artifacts/routes/route-01.md", "查阅 route-01.md"),
    ],
)
def test_only_argus_plumbing_is_called_internal(path: str, expected: str) -> None:
    event = {"kind": "tool_use", "text": f'read: {{"path": "{path}"}}'}
    assert describe_progress_step(event, _WS)[0] == expected


def test_tally_summarises_a_long_turn_in_plain_counts() -> None:
    now = [0.0]
    tally = ProgressTally(clock=lambda: now[0])
    for index in range(3):
        text = f'web_search: {{"query": "q{index}"}}'
        event = {"kind": "tool_use", "call_id": f"s{index}", "status": "running", "text": text}
        tally.observe(event)
        tally.observe({**event, "status": "completed"})
    for index in range(5):
        event = {"kind": "tool_use", "item_id": f"r{index}", "text": f'web_fetch: {{"url": "https://e{index}.org"}}'}
        tally.observe(event)
    now[0] = 185.0
    assert tally.summary() == "已搜索 3 次 · 读了 5 页 · 用时 3 分钟"


def test_file_change_lists_the_touched_files_and_caps_the_list() -> None:
    label, _ = describe_progress_step({
        "kind": "file_change",
        "changes": ["src/app.py", "src/api.py", "src/db.py", "src/ui.py"],
    })
    assert label == "✎ src/app.py, src/api.py, src/db.py +1"


def test_credentials_never_reach_the_status_line() -> None:
    secret = "ghp_" + "A" * 36
    label, detail = describe_progress_step({
        "kind": "command_execution",
        "text": f"curl -H 'Authorization: token {secret}' https://api.github.com",
    })
    assert secret not in label
    assert secret not in detail
    assert "REDACTED" in label


def test_malformed_events_degrade_instead_of_raising() -> None:
    assert describe_progress_step(None) == ("working", "")
    assert describe_progress_step({}) == ("working", "")
    assert describe_progress_step({"kind": "command_execution"}) == (
        "running a command",
        "",
    )


def test_unknown_kind_falls_back_to_the_reported_text() -> None:
    label, _ = describe_progress_step({"kind": "mystery", "text": "doing a thing"})
    assert label == "doing a thing"


def test_strip_shell_wrapper_unwraps_quoted_bash_c() -> None:
    assert strip_shell_wrapper("/bin/bash -lc 'ls -la'") == "ls -la"
    assert strip_shell_wrapper('bash -c "echo hi"') == "echo hi"
    assert strip_shell_wrapper("ls -la") == "ls -la"


@pytest.mark.parametrize(
    ("kind", "text", "tool"),
    [
        ("tool_use", 'read: {"cells": null, "includeOutputs": false, "path": "/w/handoffs/CHECKPOINT.md"}', "read"),
        ("command_execution", "cd /w && pytest", "bash"),
    ],
)
def test_failed_call_without_call_id_is_one_failed_step(kind: str, text: str, tool: str) -> None:
    # The runner reports a failure as a "running" row then a "failed" row with
    # the same text and no call id. The operator must see one failed step,
    # not a "completed" step followed by a failed copy of it.
    from argus.webapi.manager_dispatch import record_turn_step

    base = {"type": "engineer.progress", "kind": kind, "text": text, "actor": "engineer-r1",
            "agent_layer": "engineer", "tool_name": tool, "item_id": "0f404b224068"}
    rows = [{**base, "status": "running"},
            {**base, "status": "failed", "output_excerpt": "includeOutputs requires a notebook cells selection."}]
    dedupe = ProgressDeduper()
    steps: list[dict] = []
    for ts, row in enumerate(rows, start=1):
        label, detail = describe_progress_step(row, "/w")
        phase_kind = dedupe.phase_kind(row, label)
        if not phase_kind:
            continue
        record_turn_step(steps, {"kind": phase_kind, "label": label, "detail": detail, "tool": tool,
                                 "status": row["status"], "output": row.get("output_excerpt", "")}, now=ts)
    assert [step["status"] for step in steps] == ["failed"]
    assert steps[0]["ended_ts"] == 2
