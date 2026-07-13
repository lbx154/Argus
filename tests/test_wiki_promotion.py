"""Tests for argus_skill.wiki.promotion — mechanical RunCard-driven promotion."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.promotion import mechanical_promote


def _write_page(wiki: Path, kind: str, slug: str, status: str = "scratch") -> Path:
    p = wiki / "pages" / kind / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": slug,
        "type": kind.rstrip("s"),  # techniques→technique, etc.
        "status": status,
        "title": slug,
        "tags": [],
        "sources": [],
        "related_runs": [],
        "related_projects": [],
        "revisit_after": None,
        "created_at": date.today().isoformat(),
        "last_reviewed_at": date.today().isoformat(),
        "reviewer_note": "",
    }
    front = yaml.safe_dump(fm, sort_keys=False).strip()
    p.write_text(f"---\n{front}\n---\n\nbody for {slug}\n", encoding="utf-8")
    return p


def _write_run(
    wiki: Path,
    run_id: str,
    *,
    outcome: str,
    mentions: list[str],
    mission_id: str | None = None,
    closed_at: str = "",
) -> Path:
    p = wiki / "sources" / "runs" / f"{run_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": run_id,
        "mission_id": mission_id or run_id,
        "outcome": outcome,
        "closed_at": closed_at,
        "ingested_at": date.today().isoformat(),
        "ingested_by": "test",
        "checksum": "",
    }
    front = yaml.safe_dump(fm, sort_keys=False).strip()
    body_lines = [f"Worked through {m} in this mission." for m in mentions]
    p.write_text(f"---\n{front}\n---\n\n" + "\n".join(body_lines) + "\n",
                 encoding="utf-8")
    return p


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    return init_wiki(project="demo", base=tmp_path)


def _status(p: Path) -> str:
    text = p.read_text(encoding="utf-8")
    front = text.split("---\n", 2)[1]
    return yaml.safe_load(front)["status"]


def test_promote_scratch_to_candidate_after_two_refs(wiki: Path):
    page = _write_page(wiki, "techniques", "force-eager-vllm")
    _write_run(wiki, "mission-1", outcome="success", mentions=["force-eager-vllm"])
    _write_run(wiki, "mission-2", outcome="success", mentions=["force-eager-vllm"])
    s = mechanical_promote(wiki)
    assert s["promoted"] == 1
    assert _status(page) == "candidate"


def test_single_ref_does_not_promote(wiki: Path):
    page = _write_page(wiki, "techniques", "force-eager-vllm")
    _write_run(wiki, "mission-1", outcome="success", mentions=["force-eager-vllm"])
    s = mechanical_promote(wiki)
    assert s["promoted"] == 0
    assert _status(page) == "scratch"


def test_multiple_roundcards_from_one_mission_count_as_one_reference(
    wiki: Path,
):
    page = _write_page(wiki, "techniques", "force-eager-vllm")
    _write_run(
        wiki,
        "mission-1-r001",
        mission_id="mission-1",
        closed_at="2026-07-13T01:00:00Z",
        outcome="partial",
        mentions=["force-eager-vllm"],
    )
    _write_run(
        wiki,
        "mission-1-r002",
        mission_id="mission-1",
        closed_at="2026-07-13T02:00:00Z",
        outcome="success",
        mentions=["force-eager-vllm"],
    )

    first = mechanical_promote(wiki)
    assert first["promoted"] == 0
    assert _status(page) == "scratch"

    _write_run(
        wiki,
        "mission-2-r001",
        mission_id="mission-2",
        closed_at="2026-07-14T01:00:00Z",
        outcome="success",
        mentions=["force-eager-vllm"],
    )
    second = mechanical_promote(wiki)
    assert second["promoted"] == 1
    assert _status(page) == "candidate"


def test_promote_candidate_to_stable_with_successes(wiki: Path):
    page = _write_page(wiki, "techniques", "diag-x", status="candidate")
    _write_run(wiki, "m1", outcome="success", mentions=["diag-x"])
    _write_run(wiki, "m2", outcome="success", mentions=["diag-x"])
    _write_run(wiki, "m3", outcome="success", mentions=["diag-x"])
    s = mechanical_promote(wiki)
    assert s["promoted"] == 1
    assert _status(page) == "stable"


def test_candidate_not_promoted_without_enough_successes(wiki: Path):
    page = _write_page(wiki, "techniques", "shaky", status="candidate")
    _write_run(wiki, "m1", outcome="unknown", mentions=["shaky"])
    _write_run(wiki, "m2", outcome="unknown", mentions=["shaky"])
    _write_run(wiki, "m3", outcome="unknown", mentions=["shaky"])
    s = mechanical_promote(wiki)
    # 3 refs but 0 successes — must not promote to stable
    assert s["promoted"] == 0
    assert _status(page) == "candidate"


def test_demote_candidate_on_two_failures(wiki: Path):
    page = _write_page(wiki, "techniques", "bad-skill", status="candidate")
    _write_run(wiki, "m1", outcome="failure", mentions=["bad-skill"])
    _write_run(wiki, "m2", outcome="failure", mentions=["bad-skill"])
    s = mechanical_promote(wiki)
    assert s["demoted"] == 1
    assert _status(page) == "scratch"


def test_demote_stable_on_two_failures(wiki: Path):
    page = _write_page(wiki, "techniques", "regressing-skill", status="stable")
    _write_run(wiki, "m1", outcome="failure", mentions=["regressing-skill"])
    _write_run(wiki, "m2", outcome="failure", mentions=["regressing-skill"])
    s = mechanical_promote(wiki)
    assert s["demoted"] == 1
    assert _status(page) == "candidate"


def test_idempotent(wiki: Path):
    _write_page(wiki, "techniques", "x")
    _write_run(wiki, "m1", outcome="success", mentions=["x"])
    _write_run(wiki, "m2", outcome="success", mentions=["x"])
    s1 = mechanical_promote(wiki)
    s2 = mechanical_promote(wiki)
    assert s1["promoted"] == 1
    # Second pass — already candidate, nothing to do.
    assert s2["promoted"] == 0
    assert s2["demoted"] == 0


def test_no_pages_no_error(wiki: Path):
    s = mechanical_promote(wiki)
    assert s["promoted"] == 0
    assert s["errors"] == 0
