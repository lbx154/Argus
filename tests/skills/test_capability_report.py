"""The per-project capability report: every axis measured from a synthetic project.

The fixtures mirror the field names a real project leaves behind (``type``/``ts``
events, flat ``usage.jsonl`` rows, ``stage:<name>`` backlog tags) and also the
older ``event``/``timestamp`` spellings, so the reader stays tolerant.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus.verticals.research import capability_report as mod

T0 = 1_789_551_000.0
HOUR = 3600.0
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
    return subprocess.run(
        [sys.executable, "-m", "argus.verticals.research.capability_report", *args],
        capture_output=True, text=True, check=False, cwd=REPO_ROOT, env=env, timeout=120,
    )


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _events() -> list[dict]:
    e: list[dict] = []
    # Mission A in idea, two rounds, one quick 'continue' review and one 'done'.
    e.append({"type": "life.mission.started", "item_id": "A", "ts": T0})
    e.append({"type": "round.start", "item_id": "A", "round_index": 1, "ts": T0 + 10})
    e.append({"type": "round.review.started", "item_id": "A", "round_index": 1, "ts": T0 + 600})
    e.append({"type": "round.review.completed", "item_id": "A", "round_index": 1, "status": "continue", "ts": T0 + 620})
    e.append({"type": "round.start", "item_id": "A", "round_index": 2, "ts": T0 + 700})
    e.append({"type": "round.review.started", "item_id": "A", "round_index": 2, "ts": T0 + 1500})
    e.append({"type": "round.review.completed", "item_id": "A", "round_index": 2, "status": "done", "ts": T0 + 1620})
    e.append({"type": "life.mission.completed", "item_id": "A", "status": "done", "ts": T0 + 1630})
    # A review skipped by a provider pause must not count as a verdict.
    e.append({"type": "round.review.completed", "item_id": "A", "round_index": 3, "status": "blocked", "review_skipped": True, "ts": T0 + 1640})
    # Manager advances idea -> experiment after one hour.
    e.append({"type": "life.manager.stage_decision", "action": "advance", "current_stage": "idea", "target_stage": "experiment", "ts": T0 + HOUR})
    # Mission B in experiment, spelled the old way (event/timestamp), one round.
    e.append({"event": "life.mission.started", "item_id": "B", "timestamp": T0 + HOUR + 60})
    e.append({"event": "round.start", "item_id": "B", "round_index": 1, "timestamp": T0 + HOUR + 70})
    e.append({"event": "round.review.started", "item_id": "B", "round_index": 1, "timestamp": T0 + HOUR + 900})
    e.append({"event": "round.review.completed", "item_id": "B", "round_index": 1, "status": "done", "timestamp": T0 + HOUR + 1200})
    e.append({"event": "life.mission.completed", "item_id": "B", "status": "done", "timestamp": T0 + HOUR + 1210})
    # The experiment -> paper transition was never logged; the paper mission's
    # backlog start dates the boundary.  The paper -> review advance is logged.
    e.append({"type": "life.mission.started", "item_id": "C", "ts": T0 + 2 * HOUR})
    e.append({"type": "round.start", "item_id": "C", "round_index": 1, "ts": T0 + 2 * HOUR + 5})
    e.append({"type": "life.mission.completed", "item_id": "C", "status": "done", "ts": T0 + 2 * HOUR + 1800})
    e.append({"type": "life.manager.stage_decision", "action": "advance", "current_stage": "paper", "target_stage": "review", "ts": T0 + 3 * HOUR})
    e.append({"type": "life.manager.stage_decision", "action": "complete", "current_stage": "review", "target_stage": "review", "ts": T0 + 3.5 * HOUR})
    e.append({"type": "engineer.progress", "text": "noise", "ts": T0 + 3.5 * HOUR + 1})
    e.append({"type": "garbage-without-timestamp"})
    return e


def _backlog() -> list[dict]:
    return [
        {"id": "A", "status": "done", "tags": ["planner", "stage:idea"], "started_ts": T0, "finished_ts": T0 + 1630},
        {"id": "B", "status": "done", "tags": ["planner", "stage:experiment"], "started_ts": T0 + HOUR + 60, "finished_ts": T0 + HOUR + 1210},
        {"id": "C", "status": "done", "tags": ["stage:paper"], "started_ts": T0 + 2 * HOUR, "finished_ts": T0 + 2 * HOUR + 1800},
    ]


def _usage() -> list[dict]:
    return [
        {"run_label": "engineer-round", "input_tokens": 1000, "output_tokens": 100, "cached_input_tokens": 400, "cost_usd": 0.5},
        {"run_label": "reviewer-verdict", "input_tokens": 500, "output_tokens": 50, "cached_input_tokens": 0, "cost_usd": 0.25},
        {"role": "planner", "usage": {"input_tokens": 200, "output_tokens": 20, "cached_input_tokens": 100}, "pricing": {"cost_usd": 0.05}},
        {"run_label": "frontdoor", "input_tokens": 10, "output_tokens": 1, "cost_usd": 0.001},
        "not a json object",
    ]


def _build_state(state: Path) -> None:
    _jsonl(state / "events.jsonl", _events())
    rows = _usage()
    (state / "usage.jsonl").write_text(
        "".join((json.dumps(r) if isinstance(r, dict) else r) + "\n" for r in rows), encoding="utf-8"
    )
    _jsonl(state / "backlog.jsonl", _backlog()[:1])
    _jsonl(state / "backlog.archive.jsonl", _backlog()[1:])
    (state / "session.json").write_text(json.dumps({"id": "s-test0001"}), encoding="utf-8")
    (state / "mission-view.json").write_text(json.dumps({"stage": {"id": "review", "label": "Review"}}), encoding="utf-8")
    skills = state / "skills" / "engineer"
    skills.mkdir(parents=True)
    (skills / "gpu-procedure.md").write_text(
        "---\nname: gpu-procedure\ndescription: How to pin a CUDA build.\n---\nSteps.\n", encoding="utf-8"
    )
    (skills / "surveyed-a6000.md").write_text(
        "---\nname: surveyed-a6000\ndescription: Surveyed 2026-09-10: kernel benchmarks on A6000; re-verify after 90 days.\n---\nRecord.\n",
        encoding="utf-8",
    )
    (skills / "quoted.md").write_text(
        '---\nname: quoted\ndescription: "Surveyed 2026-09-11: data loaders on CPU"\n---\nRecord.\n', encoding="utf-8"
    )


def _fake_pdf(path: Path, pages: int) -> None:
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        + b"2 0 obj << /Type /Pages /Kids [] /Count " + str(pages).encode() + b" >> endobj\n%%EOF\n"
    )


def _build_workspace(ws: Path) -> None:
    (ws / "METHOD.md").write_text(
        "# Regularized pivoted feature maps\n\n"
        "## Claim\nRidge-shifted pivots keep the map well conditioned.\n\n"
        "## Components\n\n"
        "| Component | The idea prescribes | Notes |\n|---|---|---|\n"
        '| ridge pivot | "add lambda to every pivot before the square root" | |\n'
        '| schur update | "rank-one Schur complement per pivot" | simplified to dense |\n'
        "| baseline | plain pivoted Cholesky | |\n\n"
        "## Protocol\nDatasets: California Housing and MNIST; seeds 0,1,2.\n\n"
        "## What would falsify the claim\nA conditioning number that grows with k.\n",
        encoding="utf-8",
    )
    argus_dir = ws / ".argus"
    (argus_dir / "round-checks").mkdir(parents=True)
    (argus_dir / "spec_components.json").write_text(json.dumps({"items": {
        "tests/spec/test_claim_shape.py::test_pivot_shift": {"component": "ridge pivot", "kind": "invariant"},
        "tests/spec/test_knockouts.py::test_schur_matches_dense": {"component": "schur update", "kind": "knockout"},
    }}), encoding="utf-8")
    (argus_dir / "round-checks" / "round-2.json").write_text("{}", encoding="utf-8")
    (argus_dir / "round-checks" / "latest.json").write_text(json.dumps({
        "round_index": 2, "exit_code": 1, "counts": {"passed": 1, "failed": 1},
        "outcomes": {
            "tests/spec/test_claim_shape.py::test_pivot_shift": "passed",
            "tests/spec/test_knockouts.py::test_schur_matches_dense": "failed",
        },
        "components": {
            "ridge pivot": {"status": "proven", "tests": [{"id": "tests/spec/test_claim_shape.py::test_pivot_shift", "kind": "invariant", "outcome": "passed"}]},
            "schur update": {"status": "contradicted", "tests": [{"id": "tests/spec/test_knockouts.py::test_schur_matches_dense", "kind": "knockout", "outcome": "failed"}]},
        },
    }), encoding="utf-8")
    spec = ws / "tests" / "spec"
    spec.mkdir(parents=True)
    (spec / "test_claim_shape.py").write_text("def test_pivot_shift():\n    assert True\n", encoding="utf-8")
    (spec / "test_knockouts.py").write_text("def test_schur_matches_dense():\n    assert True\n", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "maps.py").write_text("import numpy as np\n\ndef run(seed=42):\n    return np.zeros(1)\n", encoding="utf-8")
    (ws / "configs").mkdir()
    (ws / "configs" / "train.yaml").write_text("seeds: [0, 1, 2]\nlambda: 0.1  # why: matches the claim's ridge\n", encoding="utf-8")
    (ws / "scripts").mkdir()
    (ws / "scripts" / "run_all.sh").write_text("python -m src.maps --seeds 2 3 4\n", encoding="utf-8")
    (ws / "experiments").mkdir()
    (ws / "experiments" / "results.json").write_text(json.dumps({
        "datasets": {"california_housing": {"rmse": 0.5}, "yearpredictionmsd": {"rmse": 9.0}},
        "device": "cpu",
    }), encoding="utf-8")
    clone = ws / "third_party" / "rpcholesky" / ".git"
    clone.mkdir(parents=True)
    (clone / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (clone / "refs" / "heads").mkdir(parents=True)
    (clone / "refs" / "heads" / "main").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    (ws / "third_party" / "plain-copy").mkdir()
    paper = ws / "paper"
    (paper / "figures").mkdir(parents=True)
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\usepackage{graphicx}\\begin{document}\n"
        "\\includegraphics{figures/missing.pdf}\n\\end{document}\n", encoding="utf-8"
    )
    _fake_pdf(paper / "main.pdf", 9)
    wiki = ws / ".autors" / "s-test0001" / "wiki"
    (wiki / "pages").mkdir(parents=True)
    (wiki / "INDEX.md").write_text("# index\n", encoding="utf-8")
    (wiki / "pages" / "kernels.md").write_text("# kernels\n", encoding="utf-8")
    (wiki / "pages" / "pivots.md").write_text("# pivots\n", encoding="utf-8")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    state = tmp_path / "state"
    ws = tmp_path / "ws"
    state.mkdir()
    ws.mkdir()
    _build_state(state)
    _build_workspace(ws)
    return state, ws


def test_report_covers_every_axis(tmp_path: Path) -> None:
    state, ws = _project(tmp_path)
    report = mod.build_report(state, ws)

    assert report["project_id"] == "s-test0001"
    assert report["current_stage"] == "review"
    assert report["total_hours"] == 3.5

    stages = report["stages"]
    assert list(stages) == ["idea", "experiment", "paper", "review"]
    assert stages["idea"]["hours"] == 1.0
    assert stages["experiment"]["hours"] == 1.0  # closed by the paper mission's start
    assert stages["paper"]["hours"] == 1.0
    assert stages["review"]["hours"] == 0.5
    assert stages["review"]["left_at"] is not None and not stages["review"]["open"]

    missions = report["missions"]
    assert missions["count"] == 3
    assert missions["per_stage"] == {"experiment": 1, "idea": 1, "paper": 1}
    assert missions["mean_rounds"] == 1.33
    assert missions["completed_status"] == {"done": 3}

    reviewer = report["reviewer"]
    assert reviewer["verdicts"] == {"continue": 1, "done": 2}
    assert reviewer["skipped"] == 1
    assert reviewer["median_duration_s"] == 120.0
    assert reviewer["under_60s"] == 1

    tokens = report["tokens"]
    assert tokens["source"] == "usage.jsonl"
    assert (tokens["input"], tokens["output"], tokens["cached"]) == (1710, 171, 500)
    assert tokens["usd"] == 0.801
    assert tokens["per_role"]["engineer"]["usd"] == 0.5
    assert tokens["per_role"]["planner"]["input"] == 200
    assert tokens["per_role"]["other"]["calls"] == 1

    card = report["method_card"]
    assert card["exists"] and card["components"] == 3
    assert card["status_counts"] == {"contradicted": 1, "proven": 1, "untested": 1}
    assert card["anchors"] == 2
    assert card["spec_tests_bound"] == 2
    assert card["hyperparameters_with_why"] >= 1
    assert card["has_protocol"] and card["has_falsifiers"]

    spec = report["spec_checks"]
    assert spec["spec_dir_exists"] and spec["spec_files"] == 2
    assert spec["last_round_index"] == 2 and spec["last_exit_code"] == 1
    assert spec["counts"] == {"passed": 1, "failed": 1}
    assert spec["contradicted_components"] == ["schur update"]

    reference = report["reference"]
    assert reference["count"] == 2
    by_name = {c["name"]: c for c in reference["clones"]}
    assert by_name["rpcholesky"]["git"] and by_name["rpcholesky"]["revision"].startswith("0123456789ab")
    assert not by_name["plain-copy"]["git"]

    seeds = report["protocol"]["seeds"]
    assert seeds["values"] == [0, 1, 2, 3, 4, 42]
    assert seeds["distinct"] == 6
    assert seeds["list_declarations"] == 1 and seeds["cli_flags"] == 1

    datasets = report["protocol"]["datasets"]
    assert "california_housing" in datasets["names"]
    assert "yearpredictionmsd" in datasets["names"]
    assert "mnist" in datasets["names"]
    assert "method_card" in datasets["sources"]["mnist"]

    assert report["figures"]["issues"] >= 1

    knowledge = report["knowledge"]
    assert knowledge["project_skills"] == 3
    assert knowledge["decision_records"] == 2
    assert knowledge["wiki_pages"] == 2

    paper = report["paper"]
    assert paper["main_pdf_exists"] and paper["pages"] == 9

    json.dumps(report)  # JSON-ready throughout


def test_markdown_table_and_compare_mode(tmp_path: Path) -> None:
    state, ws = _project(tmp_path)
    report = mod.build_report(state, ws)
    text = mod.render_markdown(report)
    assert text.startswith("## Argus capability report: s-test0001")
    assert "| Metric | Value |" in text
    assert "| stage idea hours | 1 |" in text
    assert "| review verdicts | continue=1, done=2 |" in text
    assert "| reviews under 60 s | 1 |" in text
    assert "| cost usd | 0.801 |" in text
    assert "| contradicted components | schur update |" in text
    assert "| reference revisions | plain-copy@?, rpcholesky@0123456789ab |" in text
    assert "| paper pages | 9 |" in text

    baseline = json.loads(json.dumps(report))
    baseline["project_id"] = "s-old"
    baseline["total_hours"] = 5.0
    baseline["reviewer"]["under_60s"] = 4
    baseline["paper"]["main_pdf_exists"] = False
    compare = mod.render_markdown(report, baseline)
    assert "| Metric | s-old | s-test0001 | Delta |" in compare
    assert "| total hours | 5 | 3.5 | -1.50 |" in compare
    assert "| reviews under 60 s | 4 | 1 | -3 |" in compare
    assert "| paper main.pdf | no | yes | changed |" in compare
    assert "| missions | 3 | 3 | 0 |" in compare


def test_empty_project_is_fail_soft(tmp_path: Path) -> None:
    state = tmp_path / "missing-state"
    ws = tmp_path / "missing-ws"
    report = mod.build_report(state, ws)
    assert report["stages"] == {}
    assert report["total_hours"] is None
    assert report["missions"]["count"] == 0
    assert report["reviewer"]["median_duration_s"] is None
    assert report["tokens"]["source"] == "none" and report["tokens"]["usd"] == 0.0
    assert report["method_card"]["exists"] is False
    assert report["spec_checks"]["spec_dir_exists"] is False
    assert report["reference"]["count"] == 0
    assert report["protocol"]["seeds"]["distinct"] == 0
    assert report["protocol"]["datasets"]["count"] == 0
    assert report["figures"]["issues"] == 0
    assert report["knowledge"] == {"project_skills": 0, "decision_records": 0, "wikis": 0, "wiki_pages": 0}
    assert report["paper"]["main_pdf_exists"] is False and report["paper"]["pages"] is None
    assert "| Metric | Value |" in mod.render_markdown(report)


def test_usage_falls_back_to_recorded_events(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    _jsonl(state / "events.jsonl", [
        {"type": "usage.recorded", "run_label": "planner-plan", "ts": T0,
         "usage": {"input_tokens": 30, "output_tokens": 3, "cached_input_tokens": 10},
         "pricing": {"cost_usd": 0.02}},
    ])
    tokens = mod.token_summary(state, mod.load_events(state))
    assert tokens["source"] == "events.usage.recorded"
    assert (tokens["input"], tokens["output"], tokens["cached"], tokens["usd"]) == (30, 3, 10, 0.02)
    assert list(tokens["per_role"]) == ["planner"]


def test_stage_timeline_from_missions_alone(tmp_path: Path) -> None:
    backlog = {
        "A": {"id": "A", "tags": ["stage:idea"], "started_ts": T0, "finished_ts": T0 + HOUR},
        "B": {"id": "B", "stage": "experiment", "started_ts": T0 + HOUR, "finished_ts": T0 + 3 * HOUR},
    }
    events = [{"type": "life.mission.started", "item_id": "A", "ts": T0}, {"type": "life.mission.completed", "item_id": "B", "ts": T0 + 3 * HOUR}]
    timeline = mod.stage_timeline(events, backlog)
    assert timeline["stages"]["idea"]["hours"] == 1.0
    assert timeline["stages"]["experiment"]["hours"] == 2.0
    assert timeline["total_hours"] == 3.0


def test_cli_json_markdown_and_baseline(tmp_path: Path) -> None:
    state, ws = _project(tmp_path)
    out = tmp_path / "report.json"
    cmd = ["--state-dir", str(state), "--workspace", str(ws)]
    proc = _run_cli([*cmd, "--json", "--out", str(out)])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["project_id"] == "s-test0001"
    assert json.loads(out.read_text(encoding="utf-8"))["paper"]["pages"] == 9

    proc = _run_cli([*cmd, "--markdown"])
    assert proc.returncode == 0, proc.stderr
    assert "| Metric | Value |" in proc.stdout

    proc = _run_cli([*cmd, "--baseline", str(out)])
    assert proc.returncode == 0, proc.stderr
    assert "| Delta |" in proc.stdout
    assert "| missions | 3 | 3 | 0 |" in proc.stdout

    proc = _run_cli([*cmd, "--markdown", "--baseline", str(tmp_path / "nope.json")])
    assert proc.returncode == 0
    assert "baseline unreadable" in proc.stderr
