"""The cockpit sees spend per task and the operator's per-task budget."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.core.usage import UsageLedger, UsageRecord
from argus.webapi import project_state
from argus.webapi.mission_items import set_budget_config


def _project(root: Path, sid: str = "s-budget01") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "backlog.jsonl").write_text(json.dumps({
        "id": "abc123", "title": "do X", "objective": "do X fully",
        "status": "running", "priority": 100, "ts": time.time(),
    }) + "\n", encoding="utf-8")
    now = time.time()
    UsageLedger(life, migrate_legacy=False).append_many([
        UsageRecord(
            call_id=f"c{index}", project_id=sid, mission_id=f"abc123:attempt:{index}",
            provider="copilot", model="m", run_label="engineer", started_at=now - 1,
            completed_at=now, status="succeeded", input_tokens=10, cached_input_tokens=0,
            output_tokens=5, reasoning_output_tokens=0, premium_requests=1.0,
            pricing_status="priced", pricing_tier="t", cost_usd=0.04, cost_basis="provider",
        )
        for index in (1, 2, 3)
    ])
    return life


def test_snapshot_reports_spend_per_task_and_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "40")
    _project(tmp_path)
    snap = project_state.build_snapshot("s-budget01", global_root=tmp_path)
    assert snap is not None
    usage = snap["mission_usage"]["abc123"]
    assert usage["calls"] == 3
    assert usage["premium_requests"] == pytest.approx(3)
    assert snap["account_budget"]["mission_budget"] == {"requests": 40.0, "usd": 0.0}
    # The quota probe is off in tests: no account, and nothing is hidden or blocked.
    assert snap["account_budget"]["account"] is None


def test_budget_form_saves_the_per_task_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for name in ("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "ARGUS_SKILL_MISSION_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)
    life = _project(tmp_path)
    result = set_budget_config(
        {
            "global_daily_cap": "120",
            "codex_daily_requests": "400",
            "copilot_daily_requests": "800",
            "copilot_daily_premium": "300",
            "mission_budget_premium": "37.5",
            "mission_budget_usd": "2",
        },
        project_state_dir=life,
        global_root=tmp_path,
    )
    assert result["values"]["ARGUS_SKILL_MISSION_BUDGET_REQUESTS"] == "37.5"
    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert persisted["ARGUS_SKILL_MISSION_BUDGET_USD"] == "2"
    with pytest.raises(ValueError):
        set_budget_config(
            {
                "global_daily_cap": "120",
                "codex_daily_requests": "400",
                "copilot_daily_requests": "800",
                "copilot_daily_premium": "300",
                "mission_budget_usd": "-1",
            },
            project_state_dir=life,
            global_root=tmp_path,
        )
