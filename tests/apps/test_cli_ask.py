"""``argus-skill --ask "<question>"`` — headless inline answer, nothing queued.

The CLI answer reuses the Manager quick-reply path (``build_quick_reply_prompt``
fed through ``run_exec`` via the front-door runner), the same fast path the
chat/web ``/ask`` is built on. It must work headless (non-TTY, no
``--continuous``, no daemon) and must never turn the question into a backlog
item. The backend is scripted (a canned ``dsh`` binary, or its absence) so
the whole path is deterministic.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.apps.cli import main


def _queued_rows(life_root: Path) -> list[str]:
    """Every non-empty backlog line written under the life root."""
    rows: list[str] = []
    for path in Path(life_root).rglob("backlog.jsonl"):
        rows.extend(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return rows


def test_ask_prints_quick_reply_reply_and_queues_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scripted backend: the front-door runner and run_exec are canned."""
    from argus_skill.core import run_gateway
    from argus_skill.core.models import RunnerResult
    from argus_skill.manager import front_door
    from argus_skill.roles.prompts.manager import build_quick_reply_prompt

    captured: dict[str, object] = {}

    class _FakeRunner:
        pass

    def fake_ensure(chat_state, mem):
        captured["session_id"] = chat_state.get("session_id")
        captured["global_root"] = chat_state.get("global_root")
        captured["mem_type"] = type(mem).__name__
        return _FakeRunner()

    def fake_run_exec(backend, *, prompt, run_label, options):
        captured["run_label"] = run_label
        captured["prompt"] = prompt
        captured["options_skip_git_repo_check"] = options.skip_git_repo_check
        return RunnerResult(exit_code=0, agent_messages=["42, obviously."])

    monkeypatch.setattr(front_door, "_ensure_manager_runner", fake_ensure)
    monkeypatch.setattr(run_gateway, "run_exec", fake_run_exec)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    rc = main(["--ask", "what is 2+2?", "--life-dir", str(tmp_path / "life")])
    out = capsys.readouterr().out

    assert rc == 0
    assert "42, obviously." in out
    assert captured["run_label"] == "manager-ask"
    prompt = str(captured["prompt"])
    assert prompt.startswith(build_quick_reply_prompt(objective="what is 2+2?"))
    assert prompt.rfind("## OperatorContext") > prompt.index("what is 2+2?")
    assert captured["options_skip_git_repo_check"] is True
    assert _queued_rows(tmp_path / "life") == []


def test_ask_runs_the_real_quick_reply_path_against_a_scripted_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end from the CLI entry: a fake ``dsh`` binary on PATH answers.

    No internals are monkeypatched: the front-door Manager runner is built
    for real and spawns the scripted ``dsh`` CLI, whose stdout becomes the
    reply. This proves the wiring — parser → action dispatch → quick-reply
    runner → reply → stdout — without ``--continuous``, from a non-TTY
    process, with nothing queued.
    """
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dsh = bin_dir / ("dsh.cmd" if os.name == "nt" else "dsh")
    script = (
        "@echo off\r\n"
        "echo canned answer from scripted dsh (asked: what is 2+2?)\r\n"
        "exit /b 0\r\n"
        if os.name == "nt"
        else (
            "#!/bin/sh\n"
            'task=""\n'
            'for arg in "$@"; do task="$arg"; done\n'
            'printf "canned answer from scripted dsh (asked: %s)\\n" "$task"\n'
            "exit 0\n"
        )
    )
    dsh.write_text(script, encoding="utf-8")
    dsh.chmod(0o755)

    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    )
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dsh")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_AGENT_IO_LOG", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_REVIEWER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MANAGER_BACKEND", raising=False)
    monkeypatch.chdir(work)

    rc = main(["--ask", "what is 2+2?", "--life-dir", str(tmp_path / "life")])
    out = capsys.readouterr().out

    assert rc == 0
    assert "canned answer from scripted dsh" in out
    assert "what is 2+2?" in out
    assert _queued_rows(tmp_path / "life") == []


def test_ask_reports_backend_failure_when_runner_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real path with the runner binary absent: a distinct backend failure.

    No internals are monkeypatched: the front-door Manager runner is built
    for real and its spawn fails because ``dsh`` is not on PATH (the PATH
    here is an empty directory, so the outcome is deterministic). The CLI
    must gate on the runner's ``exit_code``/``fatal_error`` and report the
    backend failure, never the misleading "empty reply" text — and the
    spawn layer must name the missing binary without a raw traceback.
    """
    work = tmp_path / "work"
    work.mkdir()
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()

    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dsh")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_AGENT_IO_LOG", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_REVIEWER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MANAGER_BACKEND", raising=False)
    monkeypatch.chdir(work)

    rc = main(["--ask", "what is 2+2?", "--life-dir", str(tmp_path / "life")])
    err = capsys.readouterr().err

    assert rc == 1
    assert "runner binary not found" in err
    assert "dsh" in err
    assert "empty reply" not in err
    assert "codex" not in err
    assert "Traceback" not in err
    assert _queued_rows(tmp_path / "life") == []


def test_ask_requires_a_non_empty_question(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--ask", "", "--life-dir", str(tmp_path / "life")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--ask requires a non-empty question" in err
