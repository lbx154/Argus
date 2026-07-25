"""Live-step formatting for the cockpit's progress trail."""

from __future__ import annotations

from argus_skill.core.progress_step import (
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


def test_tool_step_names_the_tool_and_its_argument() -> None:
    label, _ = describe_progress_step({
        "kind": "tool_use",
        "text": 'view: {"path": "/tmp/x/main.py"}',
    })
    assert label.startswith("⚙ view")
    assert "/tmp/x/main.py" in label


def test_file_change_lists_the_touched_files_and_caps_the_list() -> None:
    label, _ = describe_progress_step({
        "kind": "file_change",
        "changes": ["src/app.py", "src/api.py", "src/db.py", "src/ui.py"],
    })
    assert label == "✎ src/app.py, src/api.py, src/db.py +1"


def test_credentials_never_reach_the_status_line() -> None:
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345678901"
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
