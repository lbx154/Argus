"""Unit tests for the Phase G observability surfaces:

* ``life.event_log.JsonlEventSink`` — tee + roll
* ``life.notify.dispatch_journal_entry`` — webhook + cmd channels
* ``apps._life_repl._inbox_drainer_for`` — inbox bus drains and
  advances offset
* ``apps._init_identity.run_init_identity`` — never overwrites; writes
  next file on conflict
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

from argus_skill.life import notify
from argus_skill.life.event_log import EVENT_FILE, ROLL_FILE, JsonlEventSink

# ---- JsonlEventSink ----------------------------------------------------


class _CaptureSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        return

    def close(self) -> None:
        return


def test_jsonl_event_sink_writes_and_forwards(tmp_path: Path) -> None:
    cap = _CaptureSink()
    sink = JsonlEventSink(cap, life_dir=tmp_path)
    sink.handle_event({"type": "round.start", "round": 1})
    sink.handle_event({"type": "round.review.completed", "input_tokens": 10})
    assert len(cap.events) == 2
    rows = (tmp_path / EVENT_FILE).read_text().splitlines()
    assert len(rows) == 2
    parsed = [json.loads(r) for r in rows]
    assert parsed[0]["type"] == "round.start"
    assert "ts" in parsed[0]


def test_jsonl_event_sink_survives_downstream_raise(tmp_path: Path) -> None:
    class _Bad:
        def handle_event(self, event: dict) -> None:
            raise RuntimeError("boom")

        def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
            return

        def close(self) -> None:
            return

    sink = JsonlEventSink(_Bad(), life_dir=tmp_path)
    sink.handle_event({"type": "x"})
    rows = (tmp_path / EVENT_FILE).read_text().splitlines()
    assert len(rows) == 1


def test_jsonl_event_sink_rolls_when_size_exceeded(tmp_path: Path) -> None:
    sink = JsonlEventSink(None, life_dir=tmp_path, roll_bytes=1024 * 1024)
    payload = {"type": "spam", "blob": "x" * 2000}
    # Write enough to exceed 1 MiB.
    for _ in range(800):
        sink.handle_event(payload)
    primary = tmp_path / EVENT_FILE
    rolled = tmp_path / ROLL_FILE
    assert rolled.exists()
    assert primary.exists()


def test_jsonl_event_sink_normalises_unjsonable(tmp_path: Path) -> None:
    sink = JsonlEventSink(None, life_dir=tmp_path)
    obj = object()
    sink.handle_event({"type": "weird", "blob": obj})
    line = (tmp_path / EVENT_FILE).read_text().strip()
    parsed = json.loads(line)
    assert parsed["type"] == "weird"
    assert isinstance(parsed["blob"], str)


def test_jsonl_event_sink_drops_idle_poll_chatter(tmp_path: Path) -> None:
    cap = _CaptureSink()
    sink = JsonlEventSink(cap, life_dir=tmp_path)
    # Idle-poll chatter still reaches downstream but is NOT persisted.
    sink.handle_event({"type": "life.status", "text": "backlog empty; exiting"})
    sink.handle_event({"type": "life.status", "text": "stop requested while idle"})
    sink.handle_event({"type": "life.status", "text": "real status"})
    # Downstream got all three.
    assert len(cap.events) == 3
    # Disk only kept the meaningful one.
    rows = (tmp_path / EVENT_FILE).read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["text"] == "real status"


# ---- notify ------------------------------------------------------------


def test_dispatch_journal_entry_skips_irrelevant_kinds() -> None:
    called = {"webhook": False, "cmd": False}
    with mock.patch.object(notify, "_post_webhook", lambda p: called.__setitem__("webhook", True)):
        with mock.patch.object(notify, "_run_cmd", lambda p: called.__setitem__("cmd", True)):
            notify.dispatch_journal_entry({"kind": "user_note", "title": "x"})
    assert called == {"webhook": False, "cmd": False}


def test_dispatch_journal_entry_dispatches_for_terminal_kinds() -> None:
    called = {"webhook": 0, "cmd": 0}

    def _w(p):
        called["webhook"] += 1
        assert p["kind"] == "mission_failed"
        assert p["title"] == "boom"

    def _c(p):
        called["cmd"] += 1

    with mock.patch.object(notify, "_post_webhook", _w):
        with mock.patch.object(notify, "_run_cmd", _c):
            notify.dispatch_journal_entry({
                "kind": "mission_failed",
                "title": "boom",
                "summary": "stack trace",
                "ts": time.time(),
                "cost_usd": 0.5,
                "tags": ["life"],
            })
    assert called == {"webhook": 1, "cmd": 1}


def test_dispatch_journal_entry_accepts_dataclass_entry() -> None:
    from argus_skill.life.memory import JournalEntry

    e = JournalEntry.new(
        kind="auth_failure",
        title="codex auth lost",
        summary="run codex login",
        tags=["life"],
        cost_usd=0.0,
    )
    received = {}
    with mock.patch.object(
        notify, "_post_webhook", lambda p: received.update(p)
    ):
        with mock.patch.object(notify, "_run_cmd", lambda p: None):
            notify.dispatch_journal_entry(e)
    assert received["kind"] == "auth_failure"
    assert received["title"] == "codex auth lost"


def test_webhook_uses_env_url_and_posts_json(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_open(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        return _Resp()

    monkeypatch.setenv("ARGUS_SKILL_NOTIFY_WEBHOOK", "https://example.test/hook")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    with mock.patch("urllib.request.urlopen", _fake_open):
        notify.dispatch_journal_entry({"kind": "mission_failed", "title": "t"})
    assert captured["url"] == "https://example.test/hook"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["kind"] == "mission_failed"


def test_telegram_command_parser_hides_shell_noise() -> None:
    assert (
        notify._parse_command(
            "/bin/bash -lc \"find /home -maxdepth 3 -type f "
            "-name 'validate_results.py' 2>/dev/null | sed -n '1,120p'\""
        )
        == "🔎 查找 validate_results.py"
    )
    assert "git status --short" in notify._parse_command(
        "/bin/bash -lc 'git -C /home/argustest/argus-skill status --short'"
    )
    assert notify._parse_command(
        "sed -n '1,10p' a.py && sed -n '1,20p' b.py"
    ) == "📖 读取了 2 个文件"
    assert notify._parse_command("python -m pytest tests/foo.py") == (
        "🧪 pytest tests/foo.py"
    )


def test_telegram_progress_summary_includes_command_result() -> None:
    lines = notify._summarize_progress([
        {
            "kind": "agent_message",
            "text": "I found the target repo and I am running the focused checks.",
        },
        {
            "kind": "command_execution",
            "text": "/bin/bash -lc 'pytest -q tests/foo.py'",
            "status": "completed",
            "exit_code": 0,
            "output_excerpt": "1 passed in 0.10s",
        },
    ])
    assert lines[0].startswith("💭 I found the target repo")
    assert lines[1] == "✅ 🧪 pytest -q tests/foo.py — 1 passed in 0.10s"


def test_telegram_stream_reporter_keeps_final_summary(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", "chat")

    reporter = notify.TelegramStreamReporter()
    sent: list[str] = []
    edited: list[tuple[int, str]] = []
    deleted: list[int] = []

    reporter._send_message = lambda text: sent.append(text) or 42
    reporter._edit_message = lambda msg_id, text: edited.append((msg_id, text)) or True
    reporter._delete_message = lambda msg_id: deleted.append(msg_id)

    reporter.start_mission(title="Readable Telegram trace", layer="engineer")
    reporter.on_event({
        "type": "engineer.progress",
        "kind": "agent_message",
        "text": "first partial",
        "replace": True,
        "message_id": "m1",
    })
    reporter.on_event({
        "type": "engineer.progress",
        "kind": "agent_message",
        "text": "final complete",
        "replace": True,
        "message_id": "m1",
    })

    reporter._flush()
    assert sent
    assert "final complete" in sent[-1]
    assert "first partial" not in sent[-1]

    reporter.end_mission(status="done")
    assert not deleted
    assert edited
    assert "已完成" in edited[-1][1]
    assert "完整日志：argus-skill --follow" in edited[-1][1]


# ---- inbox drainer -----------------------------------------------------


def test_inbox_drainer_returns_messages_in_order_and_advances_offset(
    tmp_path: Path,
) -> None:
    from argus_skill.apps._life_repl import _inbox_drainer_for

    drain = _inbox_drainer_for(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        json.dumps({"text": "hello"}) + "\n"
        + json.dumps({"text": "world"}) + "\n",
        encoding="utf-8",
    )
    assert drain() == "hello"
    assert drain() == "world"
    assert drain() is None  # exhausted

    # Advancing the offset keeps the next call honest.
    inbox.open("a", encoding="utf-8").write(json.dumps({"text": "again"}) + "\n")
    assert drain() == "again"


def test_inbox_drainer_swallows_corrupt_lines(tmp_path: Path) -> None:
    from argus_skill.apps._life_repl import _inbox_drainer_for

    drain = _inbox_drainer_for(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        "this is not json\n"
        + json.dumps({"text": "ok"}) + "\n",
        encoding="utf-8",
    )
    # First line corrupt → returns None for that call (offset advanced).
    assert drain() is None
    # Second call sees the next line.
    assert drain() == "ok"
    assert drain() is None


def test_inbox_drainer_returns_none_when_inbox_missing(tmp_path: Path) -> None:
    from argus_skill.apps._life_repl import _inbox_drainer_for

    drain = _inbox_drainer_for(tmp_path)
    assert drain() is None


# ---- init-identity wizard -----------------------------------------------


def test_init_identity_seeds_when_missing(tmp_path: Path, monkeypatch) -> None:
    from argus_skill.apps._init_identity import run_init_identity

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    rc = run_init_identity(tmp_path)
    assert rc == 0
    text = (tmp_path / "identity.md").read_text()
    assert "argus-skill — operator identity card" in text
    assert "Red lines" in text


def test_init_identity_never_overwrites_existing(
    tmp_path: Path, monkeypatch
) -> None:
    from argus_skill.apps._init_identity import run_init_identity

    target = tmp_path / "identity.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# my hand-written card\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    rc = run_init_identity(tmp_path)
    assert rc == 0
    # Original preserved.
    assert target.read_text() == "# my hand-written card\n"
    # New template written next to it.
    assert (tmp_path / "identity.next.md").exists()


# ---- supervisor inbox plumbing ------------------------------------------


def test_supervisor_drains_user_inbox_and_splices_into_prelude(
    tmp_path: Path,
) -> None:
    """End-to-end: a mission running with a user_inbox callable should
    splice its messages into prelude_context."""
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.life.supervisor import (
        LifeBudget,
        LifeSupervisor,
        LifeSupervisorConfig,
    )

    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="t", objective="o", iterate=False, max_cost_usd=0.5,
    ))

    captured: dict[str, str] = {}

    class _FakeRunner:
        def execute(self, *, objective, sink, **kw):
            captured["prelude_context"] = str(kw.get("prelude_context", ""))
            class _O:
                success = True
                status = "done"
                rounds = 1
                stop_reason = ""
                final_message = "ok"
                completion_summary_markdown = "## done\n"
                matched_skill_name = ""
                skill_distilled = False
                had_follow_up = False
            return _O()

    nudges = iter(["please add tests", "use pytest -q"])
    def _drain():
        try:
            return next(nudges)
        except StopIteration:
            return None

    sup = LifeSupervisor(
        memory=mem,
        runner=_FakeRunner(),
        sink=_CaptureSink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(per_mission_cap_usd=10.0, daily_cap_usd=10.0, max_missions=1),
            user_inbox=_drain,
        ),
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4-mini",
    )
    sup.run()
    pc = captured["prelude_context"]
    assert "Operator messages" in pc
    assert "please add tests" in pc
    assert "use pytest -q" in pc
