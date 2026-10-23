"""Live-step formatting for the cockpit's progress trail."""

from __future__ import annotations

import pytest

from argus.core.progress_step import (
    ProgressDeduper,
    ProgressTally,
    describe_progress_step,
    strip_shell_wrapper,
)
from argus.skills.builtins import builtin_skill_source_path


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
        # A patch with no braces in it still names its file, not its "***" header.
        ("apply_patch: *** Begin Patch\n*** Update File: /work/proj/pkg/core.py\n@@\n def f():", "修改 core.py"),
        ('view: {"path": "/work/proj"}', "查阅项目目录"),
        ('argus_actions-hand_off: {"to": "done"}', "交出工作"),
        ('mystery_tool: {"x": 1}', "调用 mystery_tool"),
        # Trial s-6f0e6213 read "调用 task": a helper agent the role starts.
        ('task: {"description": "独立审阅小时格式化接口", "prompt": "..."}', "交给子助手：独立审阅小时格式化接口"),
        ('task: {"prompt": "..."}', "交给一个子助手"),
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
    first = {**_MISSION, "status": "running", "text": 'view: {"path": "/elsewhere/state/projects/a/meta.json"}'}
    second = {**_MISSION, "status": "running", "text": 'view: {"path": "/elsewhere/state/projects/b/meta.json"}'}
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
        ('glob: {"pattern": "**/*"}', "查找所有文件"),
        ('glob: {"pattern": "**/*.py"}', "查找 .py 文件"),
        ('glob: {"pattern": "src/**/*.ts"}', "查找 src 里的 .ts 文件"),
        ('glob: {"pattern": "src/**/*"}', "查找 src 里的所有文件"),
        ('glob: {"pattern": "METHOD.md"}', "查找 METHOD.md"),
        # Alternatives in braces name the files among them, the first few
        # (trial s-7d356ee9 read "查找文件" four times); a wildcard is no name.
        ('glob: {"pattern": "{pyproject.toml,setup.cfg,setup.py,pytest.ini}"}',
         "查找 pyproject.toml、setup.cfg、setup.py 等文件"),
        ('glob: {"pattern": "{SKILL.md,*/SKILL.md,reviewer/**/*.md}"}', "查找 SKILL.md 等文件"),
        ('glob: {"pattern": "{SKILL.md,reviewer/**/SKILL.md}"}', "查找 SKILL.md"),
        ('glob: {"pattern": ".venv/bin/{python,pip}"}', "查找 python、pip"),
        ('glob: {"pattern": "**/*{review,python,date,审阅}*"}', "查找文件"),
        ('glob: {"pattern": "{*,root/**,reviewer/**}*{review,truncate,boundary}*"}', "查找文件"),
        ('glob: {"pattern": "**/*.{py,ts}"}', "查找文件"),
        # Trial s-36ca9747 read "查找 .*"; any other wildcard is files too.
        ('glob: {"pattern": ".*"}', "查找文件"),
        ('glob: {"pattern": "tests/test_*.py"}', "查找 tests 里的名字含 test_、.py 的文件"),
        # Trial s-f0e1b2f0 read "查找文件" for these: the words a pattern keeps name it.
        ('glob: {"pattern": "**/*SKILL*"}', "查找名字含 SKILL 的文件"),
        ('glob: {"pattern": "**/*manager*"}', "查找名字含 manager 的文件"),
        ('rg: {"pattern": "route", "paths": "/work/proj/notes.md"}', "搜索：route"),
        ('grep: {"query": "see /work/proj/deep/notes.md"}', "搜索：see notes.md"),
    ],
)
def test_search_patterns_read_without_slashes(text: str, expected: str) -> None:
    label, _ = describe_progress_step({"kind": "tool_use", "text": text}, _WS)
    assert label == expected
    assert "/" not in label


def test_workspace_from_the_caller_marks_internal_files() -> None:
    event = {"kind": "tool_use", "text": 'view: {"path": "/runtime/argus/verticals/research/README.md"}'}
    assert describe_progress_step(event, _WS)[0] == "查阅 Argus 内部文件"
    inside = {"kind": "tool_use", "text": 'view: {"path": "/work/proj/RESEARCH_NOTES.md"}'}
    assert describe_progress_step(inside, _WS)[0] == "查阅 RESEARCH_NOTES.md"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # A repository the operator asked about lives outside the session
        # workspace; reading it is project work, not Argus plumbing.
        ("/data/someone/subject-repo/README.md", "查阅 README.md"),
        ("/srv/state/projects/s-1/state.json", "查阅 Argus 内部文件"),
    ],
)
def test_only_argus_plumbing_is_called_internal(path: str, expected: str) -> None:
    event = {"kind": "tool_use", "text": f'read: {{"path": "{path}"}}'}
    assert describe_progress_step(event, _WS)[0] == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # A recalled knowledge page is material the role reads, not plumbing.
        ("/repo/.autors/learn/wiki/pages/deepinfer-inference-architecture.md",
         "查阅知识页 deepinfer-inference-architecture"),
        # A shared wiki under the Argus home is a knowledge base too.
        ("/srv/home/wiki/_shared_verticals/software/pages/lessons/20261020-cli-stdin-validation.md",
         "查阅知识页 20261020-cli-stdin-validation"),
        ("/srv/home/wiki/_global/pages/house-style.md", "查阅知识页 house-style"),
        ("/srv/home/wiki/_shared_verticals/software", "查阅知识库"),
        ("/srv/state/workspaces/s-1/.autors/s-1/wiki/INDEX.md", "查阅知识页 INDEX"),
        ("/repo/.autors/learn/wiki", "查阅知识库"),
        # The project's own wiki folder is the work.
        ("/repo/docs/wiki/pages/setup.md", "查阅 setup.md"),
        # A folder inside a wiki is the wiki, not one of its pages.
        ("/repo/.autors/learn/wiki/pages", "查阅知识库"),
        # Trial s-6f0e6213: skills read as "查阅文件", with no name.
        ("/srv/state/skills/_shared_verticals/software/reviewer/software-change-review.md",
         "查阅技能 software-change-review"),
        ("/srv/state/projects/s-1/skills", "查阅技能库"),
        ("/srv/argus/verticals/software/skills/engineer", "查阅技能库"),
        ("/srv/argus/builtin_skills/reviewer/argus-reviewer-role.md", "查阅技能 argus-reviewer-role"),
        # A role's notes for the next holder, whatever file holds them.
        ("/srv/state/projects/s-1/handoffs/0f40/CHECKPOINT.md", "查阅交接记录"),
        # .autors holds the project's wiki and nothing else (trial s-757b9dac
        # read "查阅文件" twice for these folders).
        ("/repo/.autors", "查阅知识库"),
        ("/repo/.autors/learn", "查阅知识库"),
        # The backend spills a long tool output to a temp file and reads it back.
        ("/tmp/1792644657527-copilot-tool-output-87d59816852246238961139c2f724073.txt", "查阅工具输出"),
    ],
)
def test_knowledge_pages_and_spilled_outputs_read_as_what_they_are(path: str, expected: str) -> None:
    event = {"kind": "tool_use", "text": f'view: {{"path": "{path}"}}'}
    assert describe_progress_step(event, _WS)[0] == expected


@pytest.mark.parametrize(
    "pattern",
    ["^(name|description):.*(engineer|Python)", "class EngineArgs|chunked_prefill", r"\\b_schedule_prefill\\b"],
)
def test_a_regular_expression_is_not_shown_raw(pattern: str) -> None:
    text = f'rg: {{"pattern": "{pattern}", "paths": ["/srv/state/skills"]}}'
    assert describe_progress_step({"kind": "tool_use", "text": text}, _WS)[0] == "搜索文件内容"


def test_argus_own_tool_is_named_not_spelled_out() -> None:
    command = (
        "python -m argus.tools.manager_live_view --workspace /srv/state/workspaces/s-1 "
        "--state-dir /srv/state/projects/s-1 status"
    )
    label, detail = describe_progress_step({"kind": "command_execution", "text": command}, _WS)
    assert label == "运行 Argus 内部工具"
    assert detail == command, "the command line stays in the folded detail"


def test_backend_session_paths_in_a_command_are_cut_to_their_last_name() -> None:
    files = "/srv/state/copilot-home/session-state/09822296-0a3e-4707-8382-d75ac8da42f6/files"
    label, _ = describe_progress_step({
        "kind": "command_execution",
        "text": f"mkdir -p {files} && python -m pytest -q 2>&1 | tee {files}/pytest.log",
    }, _WS)
    assert label == "$ mkdir -p …/files && python -m pytest -q 2>&1 | tee …/pytest.log"
    kept, _ = describe_progress_step({"kind": "command_execution", "text": "git -C /work/repo status"}, _WS)
    assert kept == "$ git -C /work/repo status", "a path of the work stays as it is"


def test_paths_in_the_project_read_from_its_root() -> None:
    # Trial s-7d356ee9: "rm -r -- /fastdata/…/work/datefmt/build" spelled out the project's path.
    label, _ = describe_progress_step({
        "kind": "command_execution",
        "text": "rm -r -- /work/proj/build /work/proj2/build && ls /work/proj",
    }, _WS)
    assert label == "$ rm -r -- build /work/proj2/build && ls ."
    # Trial s-757b9dac: a result line named the package by its whole path;
    # a path outside the project keeps the rules it had.
    result, _ = describe_progress_step({
        "kind": "tool_result",
        "text": "Public package: /work/proj/timefmt/__init__.py | /usr/lib/python3/site-packages/x.py",
    }, _WS)
    assert result == "↳ Public package: timefmt/__init__.py | /usr/lib/python3/site-packages/x.py"


def test_paths_into_the_installed_argus_package_are_cut_in_a_command() -> None:
    builtin = str(builtin_skill_source_path())
    label, _ = describe_progress_step({
        "kind": "command_execution",
        "text": f"rg -n '^name:' /srv/state/skills {builtin} -g SKILL.md",
    }, _WS)
    assert label == "$ rg -n '^name:' …/skills …/builtin_skills -g SKILL.md"
    event = {"kind": "tool_use", "text": f'view: {{"path": "{builtin}/engineer/minimal-coding-agent.md"}}'}
    assert describe_progress_step(event, _WS)[0] == "查阅技能 minimal-coding-agent"


def test_a_tool_result_cuts_plumbing_paths_as_a_command_does() -> None:
    # Trial s-36ca9747: a result line spelled out the whole path into the built-in skills.
    builtin = str(builtin_skill_source_path())
    label, _ = describe_progress_step({
        "kind": "tool_result",
        "text": f"{builtin}/engineer/minimal-coding-agent.md:3:description: x | /work/proj/a.py",
    }, _WS)
    assert label == "↳ …/minimal-coding-agent.md:3:description: x | a.py"


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


def test_credentials_never_reach_the_status_line(redact_secrets_on) -> None:
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


def test_one_line_patch_names_only_the_file() -> None:
    # A saved apply_patch detail has the hunk on the same line as the path.
    text = (
        "apply_patch: *** Begin Patch *** Update File: /data/state/config.json "
        '@@ { + "SOME_SETTING": "x" } *** End Patch'
    )
    assert describe_progress_step({"kind": "tool_use", "text": text})[0] == "修改 config.json"
