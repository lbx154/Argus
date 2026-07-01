"""Regression: stage_check must resolve project-local DATA-domain verticals.

A Manager can author a bespoke vertical as a project-local data domain
(``research/DOMAINS/<name>.json``) instead of a packaged ``argus_skill.verticals``
module. The runtime resolves those via the canonical ``load_vertical`` resolver
(supervisor/_core.py, loop.py, _runtime.py). ``stage_check`` was the LAST consumer
still using raw ``importlib.import_module`` and therefore crashed with
"unknown vertical" on every data-domain vertical the runtime resolved fine — so
the bounded acceptance gate could never run for a bespoke domain. This pins the
fix: stage_check now uses ``load_vertical`` too.

(This inconsistency was surfaced by a live self-hosted argus run whose engineer
correctly diagnosed and patched it — a genuine self-repair, landed here properly.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from argus_skill.tools import stage_check
from argus_skill.verticals._base import load_vertical


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_vertical_resolves_project_local_data_domain(tmp_path: Path) -> None:
    # The resolver returns a duck-typed shim exposing the same tables a packaged
    # stages module does — which is exactly what stage_check reads off it.
    _write_json(
        tmp_path / "research" / "DOMAINS" / "python_tdd.json",
        {"name": "python_tdd", "stages": ["scope"]},
    )
    dom = load_vertical("python_tdd", project_root=tmp_path)
    assert list(dom.STAGE_ORDER) == ["scope"]
    assert dom.STAGE_CHECKS            # dict[stage -> checks]
    assert isinstance(dom.REVIEWER_CHECKLISTS, dict)


def test_stage_check_loads_data_domain_vertical_not_unknown(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_json(
        tmp_path / "research" / "DOMAINS" / "python_tdd.json",
        {"name": "python_tdd", "stages": ["scope"]},
    )
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"vertical": "python_tdd", "current_stage": "scope"},
    )
    monkeypatch.setattr(
        sys, "argv",
        ["stage-check", "--project-root", str(tmp_path), "--bounded"],
    )
    status = stage_check.main()
    captured = capsys.readouterr()

    # The vertical RESOLVED: the stage banner (printed only after a successful
    # load) names it, and neither the old "unknown vertical" nor a load failure
    # appears. Return code is not asserted — structural gates may still flag the
    # bare domain; the point is that loading no longer crashes the gate.
    assert isinstance(status, int)
    assert "(vertical: python_tdd)" in captured.out
    assert "unknown vertical" not in captured.err
    assert "failed to load" not in captured.err
