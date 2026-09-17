"""The hourly consolidation pass: index rebuild, receipt, validation and the one model call."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import argus.life.consolidation as consolidation
from argus.core.file_lock import exclusive_file_lock
from argus.life.consolidation import (
    Receipt,
    build_prompt,
    consolidate_knowledge,
    lesson_pages,
    lessons_digest,
    list_pages,
    read_receipt,
    rebuild_index,
    render_index,
    validate_principles,
    write_receipt,
)
from argus.wiki.journal import read_knowledge_events

VERTICAL = "research"
NOW = 1_789_650_000.0  # 2026-09-17 UTC
TODAY = "2026-09-17"
HOUR = 3600.0


def _page(title: str, description: str, body: str, *, kind: str | None = None) -> str:
    extra = f"kind: {kind}\n" if kind else ""
    return f"---\ntitle: {title}\ndescription: {description}\n{extra}---\n\n{body}\n"


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    root = home / "wiki" / "_shared_verticals" / VERTICAL
    (root / "pages").mkdir(parents=True)
    return home, root


def _lesson(root: Path, name: str, title: str, body: str = "What happened and what to do.") -> Path:
    path = root / "pages" / "lessons" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_page(title, f"{title} in one line", body, kind="lesson"), encoding="utf-8")
    return path


def _three_lessons(root: Path) -> list[Path]:
    return [
        _lesson(root, "20260915-pin-versions", "Pin versions"),
        _lesson(root, "20260916-smoke-first", "Smoke test first"),
        _lesson(root, "20260917-read-logs", "Read the logs"),
    ]


def _principles_doc(
    links: list[list[str]],
    *,
    title: str = f"Principles for {VERTICAL}",
    history: list[str] | None = None,
    kind: str | None = "principles",
) -> str:
    lines = ["---", f"title: {title}", "description: What repeated lessons taught us."]
    if kind:
        lines.append(f"kind: {kind}")
    lines += ["---", "", f"# {title}", ""]
    for index, hrefs in enumerate(links, start=1):
        evidence = ", ".join(f"[L{n}]({href})" for n, href in enumerate(hrefs, start=1))
        lines.append(f"{index}. Do the thing number {index} before starting — evidence: {evidence}")
    if history is not None:
        lines += ["", "## History", ""] + [f"- {line}" for line in history]
    return "\n".join(lines) + "\n"


def _stub_model(monkeypatch: pytest.MonkeyPatch, reply):
    """Stand in for the model call; records prompts and returns ``reply``."""
    calls: list[dict] = []

    def fake(runner, *, prompt, vertical_root):
        calls.append({"runner": runner, "prompt": prompt, "root": vertical_root})
        if isinstance(reply, Exception):
            raise reply
        return reply(vertical_root) if callable(reply) else reply

    monkeypatch.setattr(consolidation, "_run_principles_model", fake)
    return calls


def _run(home: Path, *, now: float = NOW, emit=None, interval: float = HOUR, life_dir: Path | None = None):
    return consolidate_knowledge(
        runner=object(),
        global_root=home,
        life_dir=life_dir or (home / "projects" / "s-abc123"),
        vertical=VERTICAL,
        emit=emit,
        now=now,
        interval_s=interval,
    )


# --------------------------------------------------------------------------- index


def test_index_groups_pages_by_kind_and_keeps_the_heading(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    (root / "INDEX.md").write_text("# Research knowledge\n\n- [Old](pages/old.md) — stale line\n", encoding="utf-8")
    _lesson(root, "20260915-a", "Older lesson")
    _lesson(root, "20260917-b", "Newer lesson")
    facts = root / "pages" / "facts" / "gpu.md"
    facts.parent.mkdir()
    facts.write_text(_page("GPU hosts", "Which hosts have GPUs", "Two."), encoding="utf-8")  # kind by folder
    (root / "pages" / "notes").mkdir()
    (root / "pages" / "notes" / "survey.md").write_text(
        _page("Framework survey", "What is current", "Body.", kind="survey"), encoding="utf-8"
    )
    (root / "pages" / "howto.md").write_text(_page("How to run", "Steps", "Body."), encoding="utf-8")

    changed = rebuild_index(root, list_pages(root), vertical=VERTICAL)
    text = (root / "INDEX.md").read_text(encoding="utf-8")

    assert changed is True
    assert text.startswith("# Research knowledge\n")
    assert "stale line" not in text
    sections = [line for line in text.splitlines() if line.startswith("## ")]
    assert sections == ["## Lessons", "## Facts", "## Surveys", "## Pages"]
    assert text.index("Newer lesson") < text.index("Older lesson"), "lessons are newest first"
    assert "- [GPU hosts](pages/facts/gpu.md) — Which hosts have GPUs" in text
    assert "- [Framework survey](pages/notes/survey.md) — What is current" in text
    assert "- [How to run](pages/howto.md) — Steps" in text


def test_index_rebuild_is_idempotent_and_uses_a_default_heading(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _lesson(root, "20260917-a", "A lesson")

    assert rebuild_index(root, list_pages(root), vertical=VERTICAL) is True
    first = (root / "INDEX.md").read_text(encoding="utf-8")
    assert rebuild_index(root, list_pages(root), vertical=VERTICAL) is False
    assert (root / "INDEX.md").read_text(encoding="utf-8") == first
    assert first.startswith("# Research knowledge\n")


def test_index_lists_principles_before_the_sections(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _lesson(root, "20260917-a", "A lesson")
    (root / "principles.md").write_text(
        _page("Principles for research", "The rules so far", "1. Do it.", kind="principles"), encoding="utf-8"
    )
    text = render_index(root, list_pages(root), heading="# Research knowledge")
    assert text.index("[Principles for research](principles.md) — The rules so far") < text.index("## Lessons")


def test_unreadable_pages_are_skipped_not_raised(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _lesson(root, "20260917-a", "Good")
    (root / "pages" / "broken.md").write_text("no front matter here\n", encoding="utf-8")
    (root / "pages" / ".hidden.md").write_text(_page("Hidden", "x", "y"), encoding="utf-8")
    assert [page.title for page in list_pages(root)] == ["Good"]


# --------------------------------------------------------------------------- digest and receipt


def test_lessons_digest_changes_only_with_lesson_files(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _three_lessons(root)
    before = lessons_digest(lesson_pages(list_pages(root)))
    (root / "pages" / "facts").mkdir()
    (root / "pages" / "facts" / "f.md").write_text(_page("Fact", "d", "b"), encoding="utf-8")
    assert lessons_digest(lesson_pages(list_pages(root))) == before
    _lesson(root, "20260918-new", "New lesson")
    assert lessons_digest(lesson_pages(list_pages(root))) != before


def test_receipt_round_trips_and_tolerates_corruption(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    receipt = Receipt(vertical=VERTICAL, last_ts=NOW, lessons_digest="abc", lesson_count=3, principle_count=2, outcome="compiled")
    path = write_receipt(root, receipt, now=NOW)
    assert path == root / ".consolidated.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["last_ts"] == NOW and data["lessons_digest"] == "abc" and data["updated_at"].startswith(TODAY)
    loaded = read_receipt(root)
    assert (loaded.last_ts, loaded.lessons_digest, loaded.lesson_count, loaded.principle_count) == (NOW, "abc", 3, 2)
    path.write_text("{not json", encoding="utf-8")
    assert read_receipt(root) == Receipt()


# --------------------------------------------------------------------------- skip logic


def test_skips_with_fewer_than_three_lessons_and_then_waits_for_the_interval(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _lesson(root, "20260916-a", "One")
    _lesson(root, "20260917-b", "Two")
    calls = _stub_model(monkeypatch, RuntimeError("must not be called"))

    first = _run(home)
    assert first["outcome"] == "skipped_few_lessons" and first["lessons"] == 2
    assert read_receipt(root).last_ts == NOW

    _lesson(root, "20260917-c", "Three")
    second = _run(home, now=NOW + 60)
    assert second["outcome"] == "skipped_interval"
    assert read_receipt(root).last_ts == NOW, "an interval skip does not touch the receipt"
    assert calls == []


def test_no_shared_root_or_vertical_skips_without_writing(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    _stub_model(monkeypatch, RuntimeError("must not be called"))
    assert _run(home)["outcome"] == "skipped_no_root"
    assert not home.exists()
    empty = consolidate_knowledge(runner=object(), global_root=home, life_dir=home, vertical="", emit=None, now=NOW)
    assert empty["outcome"] == "skipped_no_vertical"
    bad = consolidate_knowledge(runner=object(), global_root=home, life_dir=home, vertical="../x", emit=None, now=NOW)
    assert bad["outcome"] == "skipped_bad_vertical"


def test_unchanged_lessons_skip_the_model_until_a_lesson_changes(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    doc = _principles_doc([
        ["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"],
    ])
    calls = _stub_model(monkeypatch, doc)

    assert _run(home)["outcome"] == "compiled"
    assert len(calls) == 1
    assert _run(home, now=NOW + HOUR + 1)["outcome"] == "skipped_unchanged"
    assert len(calls) == 1
    receipt = read_receipt(root)
    assert receipt.outcome == "skipped_unchanged" and receipt.lessons_digest, "the compiled digest is kept"

    _lesson(root, "20260918-new", "New lesson")
    assert _run(home, now=NOW + 2 * HOUR + 2)["outcome"] == "compiled"
    assert len(calls) == 2
    assert _run(home, now=NOW + 2 * HOUR + 3)["outcome"] == "skipped_interval"


def test_interval_comes_from_the_knob_when_not_given(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    _stub_model(monkeypatch, RuntimeError("must not be called"))
    write_receipt(root, Receipt(last_ts=NOW - 100, lessons_digest="x", outcome="compiled"), now=NOW - 100)
    monkeypatch.setenv("ARGUS_SKILL_CONSOLIDATE_INTERVAL_S", "120")
    result = consolidate_knowledge(runner=object(), global_root=home, life_dir=home, vertical=VERTICAL, emit=None, now=NOW)
    assert result["outcome"] == "skipped_interval"
    monkeypatch.setenv("ARGUS_SKILL_CONSOLIDATE_INTERVAL_S", "60")
    result = consolidate_knowledge(runner=object(), global_root=home, life_dir=home, vertical=VERTICAL, emit=None, now=NOW)
    assert result["outcome"] == "failed", "past the interval the pass proceeds to the model"


def test_a_concurrent_pass_holding_the_lock_makes_this_one_skip(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    _stub_model(monkeypatch, RuntimeError("must not be called"))
    with (root / ".consolidate.lock").open("a+", encoding="utf-8") as handle:
        with exclusive_file_lock(handle, timeout_seconds=0.0):
            assert _run(home)["outcome"] == "skipped_busy"
    assert not (root / ".consolidated.json").exists()


# --------------------------------------------------------------------------- validation


def test_validation_rules(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _three_lessons(root)
    a, b, c = (
        "pages/lessons/20260915-pin-versions.md",
        "pages/lessons/20260916-smoke-first.md",
        "pages/lessons/20260917-read-logs.md",
    )
    good = validate_principles(_principles_doc([[a, b], [b, c]], history=["2026-09-10: 1 principles from 3 lessons"]), root=root)
    assert good.ok and len(good.principles) == 2
    assert good.principles[0].links == (a, b)
    assert good.history == ["2026-09-10: 1 principles from 3 lessons"]

    assert "front matter" in validate_principles("no front matter\n1. x\n", root=root).reason
    assert "no numbered" in validate_principles(_principles_doc([]), root=root).reason
    too_many = validate_principles(_principles_doc([[a, b]] * 13), root=root)
    assert not too_many.ok and "at most 12" in too_many.reason
    missing = validate_principles(_principles_doc([[a, "pages/lessons/never-written.md"]]), root=root)
    assert not missing.ok and "do not exist" in missing.reason
    outside = validate_principles(_principles_doc([[a, "../../../etc/passwd"]]), root=root)
    assert not outside.ok and "do not exist" in outside.reason
    one_link = validate_principles(_principles_doc([[a]]), root=root)
    assert not one_link.ok and "at least 2" in one_link.reason


def test_wrapped_evidence_lines_belong_to_their_principle(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _three_lessons(root)
    text = (
        "---\ntitle: Principles for research\ndescription: d\nkind: principles\n---\n\n"
        "1. Pin versions first —\n   evidence: [A](pages/lessons/20260915-pin-versions.md),\n"
        "   [B](pages/lessons/20260916-smoke-first.md)\n\n"
        "Some closing prose that is not a principle.\n"
    )
    check = validate_principles(text, root=root)
    assert check.ok and len(check.principles) == 1 and len(check.principles[0].links) == 2


# --------------------------------------------------------------------------- the pass


def test_stubbed_model_produces_principles_event_and_journal(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    doc = _principles_doc([
        ["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"],
        ["pages/lessons/20260916-smoke-first.md", "pages/lessons/20260917-read-logs.md"],
    ])
    calls = _stub_model(monkeypatch, doc)
    events: list[dict] = []

    result = _run(home, emit=events.append, life_dir=home / "projects" / "s-fb4716b7")

    assert result["outcome"] == "compiled" and result["principles"] == 2 and result["changed"] is True
    assert calls[0]["root"] == root
    text = (root / "principles.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: Principles for research\n")
    assert "kind: principles\n" in text
    assert "1. Do the thing number 1 before starting — evidence: [L1](pages/lessons/20260915-pin-versions.md)" in text
    assert text.rstrip().endswith(f"## History\n\n- {TODAY}: 2 principles from 3 lessons")

    assert events == [{
        "type": "knowledge.learned",
        "kind": "learned",
        "scope": "vertical",
        "vertical": VERTICAL,
        "path": "principles.md",
        "title": "Principles for research",
        "source_project": "s-fb4716b7",
        "mission_id": "",
        "page_kind": "principles",
        "text": "Principles for research: 2 principles from 3 lessons",
    }]
    journal = read_knowledge_events(home)
    assert len(journal) == 1
    record = journal[0]
    assert (record["kind"], record["scope"], record["vertical"], record["path"]) == ("learned", "vertical", VERTICAL, "principles.md")
    assert record["role"] == "consolidation" and record["page_kind"] == "principles"
    assert record["source_project"] == "s-fb4716b7" and record["note"] == "2 principles from 3 lessons"

    receipt = read_receipt(root)
    assert receipt.outcome == "compiled" and receipt.principle_count == 2 and receipt.lesson_count == 3
    assert receipt.lessons_digest == lessons_digest(lesson_pages(list_pages(root)))
    assert receipt.compiled_at.startswith(TODAY)
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "[Principles for research](principles.md)" in index and "## Lessons" in index


def test_a_bad_reply_restores_the_previous_principles_and_records_the_failure(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    previous = _principles_doc(
        [["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"]],
        history=["2026-09-10: 1 principles from 3 lessons"],
    )
    (root / "principles.md").write_text(previous, encoding="utf-8")

    def reply_and_clobber(vertical_root: Path) -> str:
        # The model may write the file itself; a bad write must not survive.
        bad = _principles_doc([["pages/lessons/20260915-pin-versions.md", "pages/lessons/gone.md"]])
        (vertical_root / "principles.md").write_text(bad, encoding="utf-8")
        return bad

    _stub_model(monkeypatch, reply_and_clobber)
    events: list[dict] = []

    result = _run(home, emit=events.append)

    assert result["outcome"] == "failed" and "do not exist" in result["failure"]
    assert (root / "principles.md").read_text(encoding="utf-8") == previous
    assert events == [] and read_knowledge_events(home) == []
    receipt = read_receipt(root)
    assert receipt.outcome == "failed" and "do not exist" in receipt.failure
    assert receipt.lessons_digest == "", "a failed pass leaves the digest so the next interval retries"
    assert receipt.last_ts == NOW
    assert (root / "INDEX.md").exists(), "the index is still rebuilt"


def test_a_failed_model_call_is_recorded_and_retried_after_the_interval(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    calls = _stub_model(monkeypatch, RuntimeError("provider unavailable"))

    result = _run(home)
    assert result["outcome"] == "failed" and result["failure"] == "RuntimeError: provider unavailable"
    assert not (root / "principles.md").exists()
    assert _run(home, now=NOW + 10)["outcome"] == "skipped_interval"
    assert _run(home, now=NOW + HOUR + 1)["outcome"] == "failed"
    assert len(calls) == 2


def test_a_reply_without_a_document_fails_cleanly(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    _stub_model(monkeypatch, "I could not decide on any principles.")
    result = _run(home)
    assert result["outcome"] == "failed" and "no principles.md document" in result["failure"]


def test_the_model_may_write_the_file_directly_and_fences_are_tolerated(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    doc = _principles_doc([["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260917-read-logs.md"]])

    def write_then_reply(vertical_root: Path) -> str:
        (vertical_root / "principles.md").write_text(doc, encoding="utf-8")
        return "Wrote principles.md."

    _stub_model(monkeypatch, write_then_reply)
    assert _run(home)["outcome"] == "compiled"
    assert "kind: principles" in (root / "principles.md").read_text(encoding="utf-8")

    _lesson(root, "20260918-more", "More")
    _stub_model(monkeypatch, "```markdown\n" + doc + "```\n")
    assert _run(home, now=NOW + HOUR + 1)["outcome"] == "compiled"


def test_history_survives_a_reply_that_dropped_it_and_the_kind_is_added(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    (root / "principles.md").write_text(
        _principles_doc(
            [["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"]],
            history=["2026-09-10: 1 principles from 3 lessons", "2026-09-12: 1 principles from 3 lessons"],
        ),
        encoding="utf-8",
    )
    _stub_model(monkeypatch, _principles_doc(
        [["pages/lessons/20260916-smoke-first.md", "pages/lessons/20260917-read-logs.md"]], kind=None,
    ))

    assert _run(home)["outcome"] == "compiled"
    text = (root / "principles.md").read_text(encoding="utf-8")
    assert "kind: principles\n" in text
    history = text.split("## History", 1)[1].strip().splitlines()
    assert history == [
        "- 2026-09-10: 1 principles from 3 lessons",
        "- 2026-09-12: 1 principles from 3 lessons",
        f"- {TODAY}: 1 principles from 3 lessons",
    ]


def test_prompt_lists_newest_lessons_first_with_bounded_bodies(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _lesson(root, "20260915-old", "Old lesson", body="old " * 10)
    _lesson(root, "20260917-new", "New lesson", body="x" * 2000)
    lessons = lesson_pages(list_pages(root))
    prompt = build_prompt(vertical=VERTICAL, lessons=lessons, current_principles="", today=TODAY)
    assert prompt.index("New lesson") < prompt.index("Old lesson")
    assert "x" * 600 in prompt and "x" * 601 not in prompt
    assert "(none yet)" in prompt and f"{TODAY}: <n> principles from 2 lessons" in prompt
    for page in lessons:
        assert f"]({page.relative})" in prompt


def test_prompt_shows_at_most_forty_lessons(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    for index in range(45):
        _lesson(root, f"202609{(index % 28) + 1:02d}-l{index:02d}", f"Lesson {index:02d}")
    prompt = build_prompt(vertical=VERTICAL, lessons=lesson_pages(list_pages(root)), current_principles="x", today=TODAY)
    assert prompt.count("\n### [") == 40 and "(40 of 45)" in prompt


def test_the_model_call_uses_the_cheap_route_unless_a_model_is_named(monkeypatch, tmp_path: Path) -> None:
    seen: list[dict] = []

    def fake_exec(backend, *, prompt, run_label, options=None, **_kw):
        seen.append({"backend": backend, "prompt": prompt, "run_label": run_label, "options": options})
        return SimpleNamespace(exit_code=0, fatal_error=None, last_agent_message="reply text")

    monkeypatch.setattr(consolidation, "gateway_run_exec", fake_exec)
    monkeypatch.setattr(consolidation, "resolve_manager_classify_model", lambda **_kw: "cheap-route-model")
    backend = SimpleNamespace(backend="fake")
    runner = SimpleNamespace(_backend=backend)

    monkeypatch.delenv("ARGUS_SKILL_REFLECTION_MODEL", raising=False)
    assert consolidation._run_principles_model(runner, prompt="p", vertical_root=tmp_path) == "reply text"
    options = seen[-1]["options"]
    assert seen[-1]["backend"] is backend and seen[-1]["run_label"] == "knowledge-consolidation"
    assert options.model == "cheap-route-model" and options.reasoning_effort == "low"
    assert options.sandbox_mode == "workspace-write" and options.working_dir == str(tmp_path)

    monkeypatch.setenv("ARGUS_SKILL_REFLECTION_MODEL", "named-model")
    consolidation._run_principles_model(runner, prompt="p", vertical_root=tmp_path)
    assert seen[-1]["options"].model == "named-model"

    monkeypatch.setattr(
        consolidation, "gateway_run_exec",
        lambda *a, **k: SimpleNamespace(exit_code=1, fatal_error="boom", last_agent_message=""),
    )
    with pytest.raises(RuntimeError, match="boom"):
        consolidation._run_principles_model(runner, prompt="p", vertical_root=tmp_path)
    with pytest.raises(RuntimeError, match="no model backend"):
        consolidation._run_principles_model(object(), prompt="p", vertical_root=tmp_path)
