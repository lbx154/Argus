"""Live-step formatting for the cockpit's progress trail."""

from __future__ import annotations

import json
import os
import shlex

import pytest

from argus.adapters.stream_progress import make_stream_progress_callback
from argus.core.progress_step import (
    ProgressDeduper,
    ProgressTally,
    describe_progress_step,
)
from argus.skills.builtins import builtin_skill_source_path


def test_command_step_says_what_the_command_does() -> None:
    label, detail = describe_progress_step({
        "kind": "command_execution",
        "text": "/bin/bash -lc 'rg --line-number cockpit frontend/tui/src'",
        # The bucketed summary must NOT win: it hides what actually ran.
        "action_summary": "inspecting project state",
    })
    assert label == "搜索：cockpit"
    assert detail == "rg --line-number cockpit frontend/tui/src", "the command, unwrapped, stays in the detail"


_WS = "/work/proj"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('view: {"path": "/work/proj/src/main.py"}', "查阅 main.py"),
        ('view: {"path": "/data/state/copilot-home/abc/state.json"}', "查阅 Argus 内部文件"),
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
        # Argus' rules and a project's state are Argus finding its way: no step.
        ("/srv/state/skills/_shared_verticals/software/reviewer/software-change-review.md", ""),
        ("/srv/state/projects/s-1/skills", ""),
        ("/srv/argus/verticals/software/skills/engineer", ""),
        ("/srv/argus/builtin_skills/reviewer/argus-reviewer-role.md", ""),
        ("/srv/state/projects/s-1/handoffs/0f40/CHECKPOINT.md", ""),
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


def test_argus_own_tool_is_no_operator_step_even_when_it_fails() -> None:
    # A Manager turn's failed status read (trial s-ea560f48): the operator saw
    # it as a red "运行 Argus 内部工具" step with its command line.
    event = {
        "type": "engineer.progress", "kind": "command_execution", "status": "failed",
        "text": "/usr/bin/bash -lc 'python -m argus.tools.manager_live_view --workspace "
                "/srv/state/workspaces/s-1 --state-dir /srv/state/projects/s-1 status'",
    }
    label, _ = describe_progress_step(event, _WS)
    assert label == ""
    assert ProgressDeduper().phase_kind(event, label) == "", "never streamed as a step"


def test_backend_session_paths_in_a_command_are_cut_to_their_last_name() -> None:
    files = "/srv/state/copilot-home/session-state/09822296-0a3e-4707-8382-d75ac8da42f6/files"
    label, detail = describe_progress_step({
        "kind": "command_execution",
        "text": f"mkdir -p {files} && python -m pytest -q 2>&1 | tee {files}/pytest.log",
    }, _WS)
    assert label == "运行测试"
    assert detail == "mkdir -p …/files && python -m pytest -q 2>&1 | tee …/pytest.log"
    _, kept = describe_progress_step({"kind": "command_execution", "text": "git -C /work/repo status"}, _WS)
    assert kept == "git -C /work/repo status", "a path of the work stays as it is"


def test_paths_in_the_project_read_from_its_root() -> None:
    # Trial s-7d356ee9: "rm -r -- /fastdata/…/work/datefmt/build" spelled out the project's path.
    _, detail = describe_progress_step({
        "kind": "command_execution",
        "text": "rm -r -- /work/proj/build /work/proj2/build && ls /work/proj",
    }, _WS)
    assert detail == "rm -r -- build /work/proj2/build && ls ."
    # Trial s-757b9dac: a result line named the package by its whole path;
    # a path outside the project keeps the rules it had.
    result, _ = describe_progress_step({
        "kind": "tool_result",
        "text": "Public package: /work/proj/timefmt/__init__.py | /usr/lib/python3/site-packages/x.py",
    }, _WS)
    assert result == "↳ Public package: timefmt/__init__.py | /usr/lib/python3/site-packages/x.py"


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)


_TRIAL_WS = "/srv/state/workspaces/s-99ca9ad6"
_TRIAL_STATE = "/srv/state/projects/s-99ca9ad6"
_INSTALL = os.path.dirname(os.path.dirname(str(builtin_skill_source_path())))


# Trial s-99ca9ad6: the Manager's turn showed "$ /usr/bin/bash -lc 'cat …/SKILL.md'"
# and its reads of the backlog and events; each command here is one it ran.
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (f"cat {_INSTALL}/argus/builtin_skills/manager/replying/SKILL.md", ""),
        (f"ls -la {_TRIAL_STATE}", ""),
        (f"tail -n 8 {_TRIAL_STATE}/backlog.jsonl", ""),
        (f"tail -n 22 {_TRIAL_STATE}/events.jsonl", ""),
        (f"rg --files {_TRIAL_STATE}/skills /srv/state/skills {_INSTALL}/argus/builtin_skills 2>/dev/null | rg -i skill", ""),
        (f"sed -n '1,200p' {_INSTALL}/argus/life/delivery.py", ""),
        (f"ls -la {_TRIAL_WS}", "查看项目目录"),
        (f"rg --files {_TRIAL_WS} | head -65", "查看项目目录"),
        ("nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.free --format=csv", "查看 GPU 状态"),
        ("ps -eo pid,ppid,user,etime,comm --sort=-pcpu | head -45", "查看机器状态"),
        ("python - <<'PY'\nimport urllib.request\nPY", "运行一段 Python 脚本"),
    ],
)
def test_trial_commands_read_plainly_and_argus_rules_and_state_are_no_steps(command: str, expected: str) -> None:
    sink = _Sink()
    callback = make_stream_progress_callback(sink)
    callback("simple-1.stdout", json.dumps({"type": "item.completed", "item": {
        "id": "item_1", "type": "command_execution", "status": "completed",
        "command": f"/usr/bin/bash -lc {shlex.quote(command)}",
    }}))
    [event] = [e for e in sink.events if e["type"] == "engineer.progress"]
    label, _ = describe_progress_step(event, _TRIAL_WS)
    assert label == expected
    assert not any(raw in label for raw in ("bash", "$", "/"))


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
    assert "REDACTED" in detail


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


def test_a_manager_turn_streams_each_plain_step_as_it_happens() -> None:
    # Trial s-99ca9ad6: a 544-second Manager turn. Each step reaches the live
    # stream as the runner reports it; Argus' own reads never do.
    from argus.apps._self_reply import _PhaseSink

    phases: list[str] = []
    sink = _PhaseSink(_Sink(), lambda label, **_kw: phases.append(label), _TRIAL_WS)
    callback = make_stream_progress_callback(sink)
    commands = [
        f"cat {_INSTALL}/argus/builtin_skills/manager/team-handoff/SKILL.md",
        "nvidia-smi --query-gpu=index,memory.free --format=csv",
        f"tail -n 8 {_TRIAL_STATE}/backlog.jsonl",
        "python - <<'PY'\nimport urllib.request\nPY",
    ]
    seen = []
    for index, command in enumerate(commands):
        callback("simple-1.stdout", json.dumps({"type": "item.completed", "item": {
            "id": f"item_{index}", "type": "command_execution", "status": "completed",
            "command": f"/usr/bin/bash -lc {shlex.quote(command)}",
        }}))
        seen.append(list(phases))
    assert seen == [[], ["查看 GPU 状态"], ["查看 GPU 状态"], ["查看 GPU 状态", "运行一段 Python 脚本"]]
