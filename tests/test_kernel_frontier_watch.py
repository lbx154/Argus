from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from argus_skill.verticals.kernel_engineering.frontier_watch import (
    STAGES,
    canonicalize,
    ledger_path,
    main,
    snapshot_path,
    template,
    validate_record,
)
from argus_skill.verticals.kernel_engineering.stages import CHECKLIST_ITEMS, STAGE_CHECKS


def _record(*, stage: str = "optimize", searched_at: datetime | None = None) -> dict:
    searched = searched_at or datetime.now(UTC)
    return {
        "schema_version": 1,
        "stage": stage,
        "network_status": "online",
        "searched_at": searched.isoformat(),
        "frontier_as_of": searched.date().isoformat(),
        "trigger": "stage_entry",
        "checked_surfaces": [
            "target_repository",
            "official_toolchains",
            "research_frontier",
            "adjacent_implementations",
        ],
        "queries": [
            {
                "query": "repo latest pull requests",
                "channel": "github",
                "purpose": "avoid duplicate upstream work",
            },
            {
                "query": "TileLang Blackwell latest release",
                "channel": "official_docs",
                "purpose": "check current toolchain support",
            },
            {
                "query": "linear attention B200 kernel latest paper",
                "channel": "arxiv",
                "purpose": "find stronger mechanisms and baselines",
            },
        ],
        "sources": [
            {
                "url": "https://github.com/example/project/pulls",
                "title": "Target repository pull requests",
                "source_type": "official_repo",
                "relevance": "No overlapping TileLang backend PR was open.",
            },
            {
                "url": "https://docs.nvidia.com/cuda/blackwell-tuning-guide/",
                "title": "Blackwell Tuning Guide",
                "source_type": "official_docs",
                "relevance": "Confirms architecture-specific optimization constraints.",
            },
            {
                "url": "https://arxiv.org/abs/2601.00001",
                "title": "Recent kernel paper",
                "source_type": "preprint",
                "relevance": "Provides a candidate scheduling mechanism to compare.",
            },
        ],
        "material_updates": [],
        "no_material_update": True,
        "decision_impact": "No material update; retain the measured TileLang plan.",
    }


def test_valid_no_update_frontier_record_passes() -> None:
    assert validate_record(_record(), expected_stage="optimize") == []


def test_old_frontier_record_does_not_expire_but_offline_fails() -> None:
    old = _record(searched_at=datetime.now(UTC) - timedelta(days=30))
    assert validate_record(old, expected_stage="optimize") == []

    offline = _record()
    offline["network_status"] = "offline"
    errors = validate_record(offline, expected_stage="optimize")
    assert any("online" in error for error in errors)


def test_template_cannot_be_recorded_without_replacing_placeholders() -> None:
    record = template("scope")
    errors = validate_record(record, expected_stage="scope")
    assert any("placeholder" in error for error in errors)


def test_record_and_check_cli_write_snapshot_and_append_ledger(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "frontier.json"
    source.write_text(json.dumps(_record(stage="baseline")), encoding="utf-8")

    assert (
        main(
            [
                "record",
                "--project-root",
                str(tmp_path),
                "--stage",
                "baseline",
                "--input",
                str(source),
            ]
        )
        == 0
    )
    assert snapshot_path(tmp_path, "baseline").is_file()
    assert ledger_path(tmp_path).is_file()
    assert len(ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()) == 1

    assert main(["check", "--project-root", str(tmp_path), "--stage", "baseline"]) == 0
    assert "evidence recorded as of" in capsys.readouterr().out


def test_check_rejects_snapshot_not_bound_to_latest_ledger(
    tmp_path: Path,
    capsys,
) -> None:
    stage = "scope"
    snapshot = canonicalize(_record(stage=stage), stage=stage)
    target = snapshot_path(tmp_path, stage)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(snapshot), encoding="utf-8")

    assert main(["check", "--project-root", str(tmp_path), "--stage", stage]) == 2
    assert "FRONTIER_WATCH.jsonl" in capsys.readouterr().err


def test_record_cli_accepts_stdin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_record(stage="scope"))))
    assert (
        main(
            [
                "record",
                "--project-root",
                str(tmp_path),
                "--stage",
                "scope",
                "--input",
                "-",
            ]
        )
        == 0
    )
    assert snapshot_path(tmp_path, "scope").is_file()


def test_frontier_gate_is_event_driven_not_required_at_every_stage() -> None:
    for stage in ("scope", "report"):
        commands = "\n".join(command for _label, command in STAGE_CHECKS[stage])
        assert "frontier_watch check" in commands
        ids = {item.id for item in CHECKLIST_ITEMS[stage]}
        assert f"{stage}.frontier_current" in ids
    for stage in set(STAGES) - {"scope", "report"}:
        commands = "\n".join(command for _label, command in STAGE_CHECKS[stage])
        assert "frontier_watch check" not in commands
        ids = {item.id for item in CHECKLIST_ITEMS[stage]}
        assert f"{stage}.frontier_current" not in ids
