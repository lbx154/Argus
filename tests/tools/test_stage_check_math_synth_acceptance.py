from __future__ import annotations

import json
import sys
from pathlib import Path

from argus_skill.tools import stage_check


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload))


def _seed_stale_speedrun_state(root: Path) -> None:
    _write_json(
        root / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "setup",
            "stage": "optimize",
            "vertical": "speedrun",
        },
    )


def _seed_math_synth_acceptance(root: Path) -> None:
    _write(
        root / "research" / "MANAGER_SETUP_ACCEPTANCE.md",
        """
# Manager Setup Acceptance - Math Synth

The math_synth setup gate is accepted from project-local evidence.

Acceptance result: explicit math_synth checker exits 0.

```text
✅ 2 shell pass, 0 shell fail, 0 structural-gate pass, 0 structural-gate fail, 0 fail-closed state finding(s), 0 advisory finding(s) (reviewer rules)
```

The stale default route must not be satisfied with speedrun baseline or
reference artifacts.
""",
    )


def _seed_math_synth_optimize_files(root: Path) -> None:
    _write(root / "MISSION.md", "math_synth\n")
    _write(root / "run_eval.py", "# frozen\n")
    _write(root / "mission" / "SETUP.md", "setup\n")
    _write(root / "research" / "GROUND_TRUTH.md", "score = mean(pass@4-pass@1)\n")
    _write(root / "attempts" / "default_surcharge_trap_v46" / "baseline.py", "# attempt\n")


def test_unqualified_bounded_accepts_certified_math_synth_setup_packet(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_stale_speedrun_state(tmp_path)
    _seed_math_synth_acceptance(tmp_path)
    _seed_math_synth_optimize_files(tmp_path)
    before = (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["stage-check", "--project-root", str(tmp_path), "--bounded"],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "📋 Stage: optimize  (vertical: math_synth)" in out
    assert "✅ At least one attempt scaffolded" in out
    assert "Baseline scripts present" not in out
    assert "Reference scores present" not in out
    assert (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8") == before


def test_parent_root_unqualified_bounded_consumes_child_math_synth_acceptance(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    parent = tmp_path
    child = parent / "projects" / "07197071cf43"
    _seed_stale_speedrun_state(parent)
    _seed_stale_speedrun_state(child)
    _seed_math_synth_acceptance(child)
    _seed_math_synth_optimize_files(child)
    parent_before = (parent / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    child_before = (child / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["stage-check", "--project-root", str(parent), "--bounded"],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "📋 Stage: optimize  (vertical: math_synth)" in out
    assert "✅ At least one attempt scaffolded" in out
    assert "Baseline scripts present" not in out
    assert "Reference scores present" not in out
    assert (parent / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8") == parent_before
    assert (child / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8") == child_before


def test_explicit_math_synth_stage_check_remains_unchanged(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_stale_speedrun_state(tmp_path)
    _seed_math_synth_acceptance(tmp_path)
    _seed_math_synth_optimize_files(tmp_path)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--vertical",
            "math_synth",
            "--stage",
            "optimize",
            "--bounded",
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "📋 Stage: optimize  (vertical: math_synth)" in out
    assert "✅ At least one attempt scaffolded" in out


def test_stale_speedrun_setup_without_acceptance_still_fails_normally(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_stale_speedrun_state(tmp_path)
    _seed_math_synth_optimize_files(tmp_path)

    monkeypatch.setattr(
        sys,
        "argv",
        ["stage-check", "--project-root", str(tmp_path), "--bounded"],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "📋 Stage: setup  (vertical: speedrun)" in out
    assert "❌ Baseline scripts present" in out
    assert "❌ Reference scores present" in out
