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
        ('view: {"path": "/data/sessions/abc/state.json"}', "查阅 Argus 内部文件"),
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


def test_repeated_start_and_complete_of_one_item_render_once() -> None:
    dedupe = ProgressDeduper()
    start = {"kind": "tool_use", "item_id": "it-1", "text": 'view: {"path": "a.py"}'}
    done = {**start, "status": "completed"}
    label, _ = describe_progress_step(start)
    assert dedupe.is_repeat(start, label) is False
    assert dedupe.is_repeat(done, describe_progress_step(done)[0]) is True
    # A changed label for the same item is an in-place update, not a repeat.
    assert dedupe.is_repeat(done, "查阅 a.py ✗") is False
    # Events without an id are never collapsed.
    assert dedupe.is_repeat({"kind": "tool_use"}, label) is False
    assert dedupe.is_repeat({"kind": "tool_use"}, label) is False


def test_tally_summarises_a_long_turn_in_plain_counts() -> None:
    now = [0.0]
    tally = ProgressTally(clock=lambda: now[0])
    for index in range(3):
        text = f'web_search: {{"query": "q{index}"}}'
        event = {"kind": "tool_use", "item_id": f"s{index}", "text": text}
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
