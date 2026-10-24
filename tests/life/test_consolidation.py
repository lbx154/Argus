"""The consolidation pass: index rebuild, receipt, validation and the one model call."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import argus.life.consolidation as consolidation
from argus.core.file_lock import exclusive_file_lock
from argus.core.handoff import HandOffActions
from argus.life.consolidation import (
    consolidate_knowledge,
    lesson_facts,
    lesson_pages,
    list_pages,
    read_receipt,
    rebuild_index,
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


def _form(links: list[list[str]], *, retire: tuple[dict, ...] = ()) -> dict:
    """A ``consolidate`` form: one principle per list of lesson paths."""
    return {
        "description": "What repeated lessons taught us.",
        "principles": [{"rule": f"Do the thing number {index} before starting", "evidence": hrefs}
                       for index, hrefs in enumerate(links, start=1)],
        "retire": list(retire),
    }


def _stub_model(monkeypatch: pytest.MonkeyPatch, reply):
    """Stand in for the model call: records prompts and files ``reply`` (a form, or ``None`` for no call)."""
    calls: list[dict] = []

    def fake(runner, *, prompt, vertical_root, actions):
        calls.append({"runner": runner, "prompt": prompt, "root": vertical_root})
        if isinstance(reply, Exception):
            raise reply
        if reply is not None:
            try:
                actions.dispatch("hand_off", reply)
            except ValueError as refusal:
                calls[-1]["refused"] = str(refusal)
        return actions.handoff.value if actions.handoff else None

    monkeypatch.setattr(consolidation, "_run_principles_model", fake)
    return calls


def _run(home: Path, *, now: float = NOW, emit=None, life_dir: Path | None = None):
    return consolidate_knowledge(
        runner=object(),
        global_root=home,
        life_dir=life_dir or (home / "projects" / "s-abc123"),
        vertical=VERTICAL,
        emit=emit,
        now=now,
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


def test_unreadable_pages_are_skipped_not_raised(tmp_path: Path) -> None:
    _home_, root = _home(tmp_path)
    _lesson(root, "20260917-a", "Good")
    (root / "pages" / "broken.md").write_text("no front matter here\n", encoding="utf-8")
    (root / "pages" / ".hidden.md").write_text(_page("Hidden", "x", "y"), encoding="utf-8")
    assert [page.title for page in list_pages(root)] == ["Good"]


# --------------------------------------------------------------------------- lesson facts and receipt


# --------------------------------------------------------------------------- skip logic


def test_skips_with_fewer_than_three_lessons_and_then_waits_for_a_page_change(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _lesson(root, "20260916-a", "One")
    _lesson(root, "20260917-b", "Two")
    calls = _stub_model(monkeypatch, RuntimeError("must not be called"))

    first = _run(home)
    assert first["outcome"] == "skipped_few_lessons" and first["lessons"] == 2
    assert read_receipt(root).last_ts == NOW

    second = _run(home, now=NOW + 60)
    assert second["outcome"] == "skipped_unchanged"
    assert read_receipt(root).last_ts == NOW, "nothing new does not touch the receipt"
    assert calls == []

    _lesson(root, "20260917-c", "Three")
    _stub_model(monkeypatch, RuntimeError("provider unavailable"))
    assert _run(home, now=NOW + 120)["outcome"] == "failed", "a new page is new input"


def test_no_shared_root_or_vertical_skips_without_writing(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    _stub_model(monkeypatch, RuntimeError("must not be called"))
    assert _run(home)["outcome"] == "skipped_no_root"
    assert not home.exists()
    empty = consolidate_knowledge(runner=object(), global_root=home, life_dir=home, vertical="", emit=None, now=NOW)
    assert empty["outcome"] == "skipped_no_vertical"
    bad = consolidate_knowledge(runner=object(), global_root=home, life_dir=home, vertical="../x", emit=None, now=NOW)
    assert bad["outcome"] == "skipped_bad_vertical"


def test_a_concurrent_pass_holding_the_lock_makes_this_one_skip(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    _stub_model(monkeypatch, RuntimeError("must not be called"))
    with (root / ".consolidate.lock").open("a+", encoding="utf-8") as handle:
        with exclusive_file_lock(handle, timeout_seconds=0.0):
            assert _run(home)["outcome"] == "skipped_busy"
    assert not (root / ".consolidated.json").exists()


# --------------------------------------------------------------------------- validation


def test_the_form_cites_and_retires_only_listed_lessons(tmp_path: Path, monkeypatch) -> None:
    a, b = "pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"
    for case, (form, refusal) in enumerate((
        (_form([[a, "pages/lessons/never-written.md"]]), "not a listed lesson"),
        (_form([[a, a]]), "each lesson once"),
        (_form([[a, b]], retire=({"page": a, "why": "fixed since"},)), "retired lesson supports nothing"),
        (_form([], retire=({"page": "../../../etc/passwd", "why": "gone"},)), "not a listed lesson"),
    )):
        home, root = _home(tmp_path / str(case))
        _three_lessons(root)
        calls = _stub_model(monkeypatch, form)
        assert _run(home)["outcome"] == "failed"
        assert refusal in calls[0]["refused"]


def test_stubbed_model_produces_principles_event_and_journal(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    calls = _stub_model(monkeypatch, _form([
        ["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"],
        ["pages/lessons/20260916-smoke-first.md", "pages/lessons/20260917-read-logs.md"],
    ]))
    events: list[dict] = []

    result = _run(home, emit=events.append, life_dir=home / "projects" / "s-fb4716b7")

    assert result["outcome"] == "compiled" and result["principles"] == 2 and result["changed"] is True
    assert calls[0]["root"] == root
    text = (root / "principles.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: Principles for research\n")
    assert "kind: principles\n" in text
    assert ("1. Do the thing number 1 before starting — evidence: [Pin versions](pages/lessons/20260915-pin-versions.md), "
            "[Smoke test first](pages/lessons/20260916-smoke-first.md).") in text
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
    assert receipt.lessons_compiled == lesson_facts(lesson_pages(list_pages(root)))
    assert receipt.compiled_at.startswith(TODAY)
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "[Principles for research](principles.md)" in index and "## Lessons" in index


def test_a_lesson_the_model_retires_leaves_the_principles_the_count_and_the_index(
    tmp_path: Path, monkeypatch,
) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    stale = _lesson(root, "20260910-set-pythonpath", "Set PYTHONPATH under safe-path mode")
    calls = _stub_model(monkeypatch, _form([
        ["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"],
        ["pages/lessons/20260916-smoke-first.md", "pages/lessons/20260917-read-logs.md"],
    ], retire=({"page": "pages/lessons/20260910-set-pythonpath.md",
                "why": "the host no longer sets PYTHONSAFEPATH"},)))

    result = _run(home)

    assert result["outcome"] == "compiled" and result["lessons"] == 3
    assert not stale.exists()
    text = (root / "principles.md").read_text(encoding="utf-8")
    assert "set-pythonpath" not in text and text.rstrip().endswith(f"- {TODAY}: 2 principles from 3 lessons")
    assert "set-pythonpath" not in (root / "INDEX.md").read_text(encoding="utf-8")
    [record] = read_knowledge_events(home, kinds=["retired"])
    assert (record["role"], record["scope"], record["vertical"], record["path"]) == (
        "consolidation", "vertical", VERTICAL, "pages/lessons/20260910-set-pythonpath.md")
    assert record["note"] == "the host no longer sets PYTHONSAFEPATH"
    assert read_receipt(root).lessons_compiled == lesson_facts(lesson_pages(list_pages(root)))
    # The retirement changed the pages; the next pass finds nothing new to compile.
    assert _run(home, now=NOW + HOUR)["outcome"] == "skipped_unchanged" and len(calls) == 1


def test_a_refused_form_keeps_the_previous_principles_and_records_the_failure(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    previous = _principles_doc(
        [["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"]],
        history=["2026-09-10: 1 principles from 3 lessons"],
    )
    (root / "principles.md").write_text(previous, encoding="utf-8")
    _stub_model(monkeypatch, _form([["pages/lessons/20260915-pin-versions.md", "pages/lessons/gone.md"]]))
    events: list[dict] = []

    result = _run(home, emit=events.append)

    assert result["outcome"] == "failed" and "no consolidate form" in result["failure"]
    assert (root / "principles.md").read_text(encoding="utf-8") == previous
    assert events == [] and read_knowledge_events(home) == []
    receipt = read_receipt(root)
    assert receipt.outcome == "failed" and "no consolidate form" in receipt.failure
    assert receipt.lessons_compiled == [], "a failed pass does not count the lessons as compiled"
    assert receipt.last_ts == NOW
    assert (root / "INDEX.md").exists(), "the index is still rebuilt"


def test_lessons_that_share_no_principle_are_an_answer_not_a_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    calls = _stub_model(monkeypatch, _form([]))

    result = _run(home)
    assert result["outcome"] == "no_principles"
    assert not (root / "principles.md").exists()
    assert _run(home, now=NOW + 5 * HOUR)["outcome"] == "skipped_unchanged"
    assert len(calls) == 1


def test_history_is_kept_and_the_kind_is_added(tmp_path: Path, monkeypatch) -> None:
    home, root = _home(tmp_path)
    _three_lessons(root)
    (root / "principles.md").write_text(
        _principles_doc(
            [["pages/lessons/20260915-pin-versions.md", "pages/lessons/20260916-smoke-first.md"]],
            history=["2026-09-10: 1 principles from 3 lessons", "2026-09-12: 1 principles from 3 lessons"],
        ),
        encoding="utf-8",
    )
    _stub_model(monkeypatch, _form([["pages/lessons/20260916-smoke-first.md", "pages/lessons/20260917-read-logs.md"]]))

    assert _run(home)["outcome"] == "compiled"
    text = (root / "principles.md").read_text(encoding="utf-8")
    assert "kind: principles\n" in text
    history = text.split("## History", 1)[1].strip().splitlines()
    assert history == [
        "- 2026-09-10: 1 principles from 3 lessons",
        "- 2026-09-12: 1 principles from 3 lessons",
        f"- {TODAY}: 1 principles from 3 lessons",
    ]


def test_the_model_call_uses_the_light_model(monkeypatch, tmp_path: Path) -> None:
    seen: list[dict] = []

    def fake_exec(backend, *, prompt, run_label, options=None, **_kw):
        seen.append({"backend": backend, "prompt": prompt, "run_label": run_label, "options": options})
        return SimpleNamespace(exit_code=0, fatal_error=None)

    monkeypatch.setattr(consolidation, "gateway_run_exec", fake_exec)
    monkeypatch.setattr(consolidation, "resolve_light_model", lambda **_kw: "light-model")
    backend = SimpleNamespace(backend="memory")
    runner = SimpleNamespace(_backend=backend)

    monkeypatch.delenv("ARGUS_SKILL_LIGHT_REASONING_EFFORT", raising=False)
    def actions() -> HandOffActions:
        return HandOffActions("consolidate", operator_available=False)

    assert consolidation._run_principles_model(runner, prompt="p", vertical_root=tmp_path, actions=actions()) is None
    options = seen[-1]["options"]
    assert seen[-1]["backend"] is backend and seen[-1]["run_label"] == "knowledge-consolidation"
    assert options.model == "light-model" and options.reasoning_effort == "low"
    assert options.sandbox_mode == "read-only" and options.working_dir == str(tmp_path)

    monkeypatch.setattr(
        consolidation, "gateway_run_exec",
        lambda *a, **k: SimpleNamespace(exit_code=1, fatal_error="boom", last_agent_message=""),
    )
    with pytest.raises(RuntimeError, match="boom"):
        consolidation._run_principles_model(runner, prompt="p", vertical_root=tmp_path, actions=actions())
    with pytest.raises(RuntimeError, match="no model backend"):
        consolidation._run_principles_model(object(), prompt="p", vertical_root=tmp_path, actions=actions())
