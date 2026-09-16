"""Host-run spec checks (research vertical): the host runs the project's own spec
suite after an Engineer round and shows what happened as evidence, never as a gate."""
from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from argus.engineer.round_evidence import (
    RoundEvidence,
    RoundEvidenceRequest,
    registered_round_evidence_providers,
)
from argus.verticals.research.spec_checks import (
    NOT_A_GATE_SENTENCE,
    SPEC_CHECK_TIMEOUT_KNOB,
    SPEC_CHECKS_KNOB,
    STATE_LAST_TEST_IDS,
    component_status,
    configured_spec_check_timeout_s,
    join_components,
    load_spec_components,
    parse_summary,
    render_for_engineer,
    render_for_reviewer,
    round_evidence,
    run_spec_checks,
)

_ALPHA = textwrap.dedent(
    """
    import pytest

    def test_ok():
        assert True

    def test_bad():
        assert 1 == 2, "boom"

    @pytest.mark.skip(reason="not today")
    def test_skip():
        pass
    """
)
_BETA = "def test_beta_ok():\n    assert 2 + 2 == 4\n"


def _make_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "project"
    spec = workdir / "tests" / "spec"
    spec.mkdir(parents=True)
    (spec / "test_alpha.py").write_text(_ALPHA, encoding="utf-8")
    (spec / "test_beta.py").write_text(_BETA, encoding="utf-8")
    return workdir


def _run(workdir: Path, life_dir: Path, *, round_index: int = 1, previous=frozenset(), **kw):
    return run_spec_checks(
        workdir,
        life_dir=life_dir,
        round_index=round_index,
        previous_test_ids=previous,
        **kw,
    )


def test_reports_counts_failures_skips_and_writes_log(tmp_path: Path) -> None:
    workdir = _make_workdir(tmp_path)
    life_dir = tmp_path / "life"

    report = _run(workdir, life_dir)

    assert report is not None
    assert report.interpreter == sys.executable  # no project .venv -> fallback
    assert report.exit_code == 1 and not report.timed_out
    assert report.counts["PASSED"] == 2
    assert report.counts["FAILED"] == 1
    assert report.counts["SKIPPED"] == 1
    assert report.failed_ids == ("tests/spec/test_alpha.py::test_bad",)
    assert len(report.skipped) == 1
    skipped_id, skipped_reason = report.skipped[0]
    assert skipped_id.startswith("tests/spec/test_alpha.py:")
    assert skipped_reason == "not today"
    assert "tests/spec/test_beta.py::test_beta_ok" in report.test_ids
    assert report.check_dirs == ("tests/spec",)
    assert report.collection_complete

    log_path = Path(report.log_path)
    assert log_path == life_dir / "round-checks" / "round-1.txt"
    log_text = log_path.read_text(encoding="utf-8")
    assert "test_bad" in log_text and "AssertionError: boom" in log_text
    assert (life_dir / "round-checks" / "pytest.ini").read_text() == "[pytest]\n"

    text = render_for_reviewer(report)
    assert text.startswith("## Host-run project checks (round 1)")
    assert "Interpreter: " + sys.executable in text
    assert "Exit code: 1" in text
    assert "tests/spec/test_alpha.py::test_bad" in text
    assert "not today" in text
    assert report.log_path in text
    assert NOT_A_GATE_SENTENCE in text
    assert "not a gate" in NOT_A_GATE_SENTENCE

    short = render_for_engineer(report)
    assert short.startswith("## Host-run project checks from your previous round")
    assert "tests/spec/test_alpha.py::test_bad" in short
    assert len(short) < len(text)


def test_second_round_names_tests_that_are_no_longer_collected(tmp_path: Path) -> None:
    workdir = _make_workdir(tmp_path)
    life_dir = tmp_path / "life"
    first = _run(workdir, life_dir)
    assert first is not None

    (workdir / "tests" / "spec" / "test_beta.py").unlink()
    second = _run(workdir, life_dir, round_index=2, previous=first.test_ids)

    assert second is not None
    assert second.removed_test_ids == ("tests/spec/test_beta.py::test_beta_ok",)
    assert Path(second.log_path).name == "round-2.txt"
    text = render_for_reviewer(second)
    assert "collected last round but not collected now" in text
    assert "tests/spec/test_beta.py::test_beta_ok" in text
    assert "Collected last round but not now" in render_for_engineer(second)


def test_includes_parity_suite_when_present(tmp_path: Path) -> None:
    workdir = _make_workdir(tmp_path)
    parity = workdir / "tests" / "parity"
    parity.mkdir()
    (parity / "test_parity.py").write_text("def test_par():\n    pass\n", encoding="utf-8")

    report = _run(workdir, tmp_path / "life")

    assert report is not None
    assert report.check_dirs == ("tests/spec", "tests/parity")
    assert "tests/parity" in report.command
    assert "tests/parity/test_parity.py::test_par" in report.test_ids
    assert report.counts["PASSED"] == 3


def _process_gone(pid: int, wait_s: float = 5.0) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        status = Path(f"/proc/{pid}/status")
        if status.exists() and "State:\tZ" in status.read_text(errors="replace"):
            return True  # killed, awaiting reap by its new parent
        time.sleep(0.1)
    return False


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    spec = workdir / "tests" / "spec"
    spec.mkdir(parents=True)
    pid_file = tmp_path / "pids.json"
    (spec / "test_slow.py").write_text(
        textwrap.dedent(
            f"""
            import json, os, subprocess, sys, time

            def test_slow():
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                json.dump({{"pytest": os.getpid(), "child": child.pid}}, open({str(pid_file)!r}, "w"))
                time.sleep(30)
            """
        ),
        encoding="utf-8",
    )

    started = time.monotonic()
    report = _run(workdir, tmp_path / "life", timeout_s=2)
    elapsed = time.monotonic() - started

    assert report is not None
    assert report.timed_out is True
    assert report.exit_code is None
    assert elapsed < 20
    assert not report.collection_complete
    assert report.removed_test_ids == ()
    pids = json.loads(pid_file.read_text())
    assert _process_gone(pids["pytest"])
    assert _process_gone(pids["child"])
    text = render_for_reviewer(report)
    assert "TIMED OUT" in text and "process group" in text
    assert NOT_A_GATE_SENTENCE in text


def test_knob_off_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = _make_workdir(tmp_path)
    assert SPEC_CHECKS_KNOB == "ARGUS_RESEARCH_SPEC_CHECKS"
    for value in ("off", "0"):
        monkeypatch.setenv(SPEC_CHECKS_KNOB, value)
        assert _run(workdir, tmp_path / "life") is None
    assert not (tmp_path / "life").exists()


def test_without_spec_directory_returns_none(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    (workdir / "tests").mkdir(parents=True)
    (workdir / "tests" / "test_root.py").write_text("def test_x():\n    pass\n")

    assert _run(workdir, tmp_path / "life") is None
    assert not (tmp_path / "life").exists()


def test_project_pytest_config_cannot_deselect_spec_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = _make_workdir(tmp_path)
    (workdir / "pytest.ini").write_text("[pytest]\naddopts = -k nothing_matches_this\n")
    (workdir / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-k nothing_matches_this"\n'
    )
    (workdir / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n    items.clear()\n"
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing_matches_this")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "does-not-exist"))

    report = _run(workdir, tmp_path / "life")

    assert report is not None
    assert report.counts["PASSED"] == 2
    assert report.counts["FAILED"] == 1
    assert report.exit_code == 1
    assert "-o" in report.command and "addopts=" in report.command
    assert str(workdir / "tests") == report.command[report.command.index("--confcutdir") + 1]


def test_conftest_under_tests_still_provides_fixtures(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    spec = workdir / "tests" / "spec"
    spec.mkdir(parents=True)
    (workdir / "tests" / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef answer():\n    return 42\n"
    )
    (spec / "test_fixture.py").write_text("def test_answer(answer):\n    assert answer == 42\n")

    report = _run(workdir, tmp_path / "life")

    assert report is not None
    assert report.exit_code == 0
    assert report.counts["PASSED"] == 1


def test_timeout_knob_is_clamped_and_tolerant() -> None:
    assert SPEC_CHECK_TIMEOUT_KNOB == "ARGUS_RESEARCH_SPEC_CHECK_TIMEOUT_SECONDS"
    assert configured_spec_check_timeout_s({}) == 600.0
    assert configured_spec_check_timeout_s({SPEC_CHECK_TIMEOUT_KNOB: "5"}) == 30.0
    assert configured_spec_check_timeout_s({SPEC_CHECK_TIMEOUT_KNOB: "99999"}) == 3600.0
    assert configured_spec_check_timeout_s({SPEC_CHECK_TIMEOUT_KNOB: "abc"}) == 600.0
    assert configured_spec_check_timeout_s({SPEC_CHECK_TIMEOUT_KNOB: "120"}) == 120.0


def test_parse_summary_reads_only_the_short_summary_section() -> None:
    output = textwrap.dedent(
        """
        FAILED not/in/summary.py::test_noise
        =========================== short test summary info ============================
        PASSED tests/spec/test_a.py::test_ok
        SKIPPED [2] tests/spec/test_a.py:4: not today
        XFAIL tests/spec/test_a.py::test_xf - known
        XPASS tests/spec/test_a.py::test_xp
        ERROR tests/spec/test_e.py::test_err - RuntimeError: nope
        FAILED tests/spec/test_a.py::test_bad - AssertionError: boom
        1 failed, 1 passed, 2 skipped, 1 xfailed in 0.02s
        """
    )
    counts, rows = parse_summary(output)
    assert counts == {
        "PASSED": 1, "FAILED": 1, "ERROR": 1, "SKIPPED": 2, "XFAIL": 1, "XPASS": 1,
    }
    assert ("SKIPPED", "tests/spec/test_a.py:4", "not today") in rows
    assert ("FAILED", "tests/spec/test_a.py::test_bad", "AssertionError: boom") in rows
    assert all(test_id != "not/in/summary.py::test_noise" for _o, test_id, _r in rows)


def test_host_failure_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = _make_workdir(tmp_path)
    import argus.verticals.research.spec_checks as module

    def boom(*_a, **_k):
        raise OSError("no fork for you")

    monkeypatch.setattr(module.subprocess, "Popen", boom)
    assert _run(workdir, tmp_path / "life") is None


# --- component markers -> per-component status -------------------------------

# A minimal stand-in for the tests/spec/conftest.py template: it registers the
# ``component`` marker and records, during collection, which test carries which
# component (kind inferred from the file name when the marker leaves it out).
_COMPONENT_CONFTEST = textwrap.dedent(
    """
    import json, time
    from pathlib import Path

    _KIND_BY_FILE = {
        "test_knockouts.py": "knockout",
        "test_differential.py": "differential",
        "test_claim_shape.py": "claim",
        "test_parity_reference.py": "parity",
    }

    def pytest_configure(config):
        config.addinivalue_line(
            "markers", "component(name, kind=None): METHOD.md component this test probes"
        )

    def pytest_collection_modifyitems(config, items):
        rows = {}
        for item in items:
            marker = item.get_closest_marker("component")
            if marker is None or not marker.args:
                continue
            kind = marker.kwargs.get("kind") or _KIND_BY_FILE.get(
                Path(str(item.fspath)).name, "invariant"
            )
            rows[item.nodeid] = {"component": marker.args[0], "kind": kind}
        try:
            out = Path(str(config.rootpath)) / ".argus" / "spec_components.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"generated_at": time.time(), "items": rows}))
        except OSError:
            pass
    """
)

_COMPONENT_KNOCKOUTS = textwrap.dedent(
    """
    import pytest

    @pytest.mark.component("attention", kind="knockout")
    def test_attention_knockout_moves_output():
        assert True

    @pytest.mark.component("gating")
    def test_gating_knockout_moves_output():
        assert False, "removing the gate changed nothing"

    def test_unmarked_helper():
        assert True
    """
)

_COMPONENT_DIFFERENTIAL = textwrap.dedent(
    """
    import pytest

    @pytest.mark.component("attention", kind="differential")
    def test_attention_matches_oracle():
        assert True

    @pytest.mark.component("attention", kind="invariant")
    def test_attention_rows_sum_to_one():
        assert True

    @pytest.mark.component("gating", kind="invariant")
    def test_gate_in_unit_interval():
        assert True
    """
)


def _make_component_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "project"
    spec = workdir / "tests" / "spec"
    spec.mkdir(parents=True)
    (spec / "conftest.py").write_text(_COMPONENT_CONFTEST, encoding="utf-8")
    (spec / "test_knockouts.py").write_text(_COMPONENT_KNOCKOUTS, encoding="utf-8")
    (spec / "test_differential.py").write_text(_COMPONENT_DIFFERENTIAL, encoding="utf-8")
    return workdir


def test_components_are_joined_from_spec_markers_and_written_as_json(tmp_path: Path) -> None:
    workdir = _make_component_workdir(tmp_path)
    life_dir = tmp_path / "life"

    report = _run(workdir, life_dir, round_index=3)

    assert report is not None
    # The project's conftest recorded the markers during collection.
    recorded = json.loads((workdir / ".argus" / "spec_components.json").read_text())
    assert set(recorded) == {"generated_at", "items"}
    assert recorded["items"]["tests/spec/test_knockouts.py::test_gating_knockout_moves_output"] == {
        "component": "gating", "kind": "knockout",  # kind inferred from the file name
    }

    assert set(report.components) == {"attention", "gating"}
    attention = report.components["attention"]
    assert attention["status"] == "proven"
    assert sorted(t["kind"] for t in attention["tests"]) == ["differential", "invariant", "knockout"]
    assert all(t["outcome"] == "PASSED" for t in attention["tests"])
    gating = report.components["gating"]
    assert gating["status"] == "contradicted"
    assert {t["id"]: t["outcome"] for t in gating["tests"]} == {
        "tests/spec/test_knockouts.py::test_gating_knockout_moves_output": "FAILED",
        "tests/spec/test_differential.py::test_gate_in_unit_interval": "PASSED",
    }
    # Unmarked tests are still in the outcomes but belong to no component.
    assert report.outcomes["tests/spec/test_knockouts.py::test_unmarked_helper"] == "PASSED"
    assert all(
        "test_unmarked_helper" not in t["id"]
        for entry in report.components.values() for t in entry["tests"]
    )

    # JSON twin: round file + latest.json, text log untouched where it was.
    round_path = workdir / ".argus" / "round-checks" / "round-3.json"
    latest_path = workdir / ".argus" / "round-checks" / "latest.json"
    assert report.json_path == str(round_path)
    assert Path(report.log_path) == life_dir / "round-checks" / "round-3.txt"
    payload = json.loads(round_path.read_text(encoding="utf-8"))
    assert payload == json.loads(latest_path.read_text(encoding="utf-8"))
    assert {
        "round_index", "ran_at", "exit_code", "timed_out", "duration_s", "counts",
        "outcomes", "skipped_reasons", "removed_test_ids", "components",
    } <= set(payload)
    assert payload["round_index"] == 3
    assert payload["exit_code"] == 1 and payload["timed_out"] is False
    assert isinstance(payload["ran_at"], float) and payload["ran_at"] > 0
    assert isinstance(payload["duration_s"], float)
    assert payload["counts"]["PASSED"] == 5 and payload["counts"]["FAILED"] == 1
    assert payload["outcomes"] == report.outcomes
    assert payload["skipped_reasons"] == {}
    assert payload["removed_test_ids"] == []
    assert payload["components"] == report.components
    for entry in payload["components"].values():
        assert set(entry) == {"tests", "status"}
        assert all(set(t) == {"id", "kind", "outcome"} for t in entry["tests"])
    assert not list((workdir / ".argus" / "round-checks").glob("*.tmp"))

    text = render_for_reviewer(report)
    section = text.split("### Components (from tests/spec markers)", 1)[1]
    assert "- attention: proven, 3 tests (differential, invariant, knockout)" in section
    assert "- gating: contradicted, 2 tests (invariant, knockout); failing: tests/spec/test_knockouts.py::test_gating_knockout_moves_output" in section
    assert NOT_A_GATE_SENTENCE in text
    assert text.count("not a gate") == 1

    short = render_for_engineer(report)
    assert "Contradicted component 'gating': tests/spec/test_knockouts.py::test_gating_knockout_moves_output" in short
    assert "attention" not in short


def test_without_component_markers_components_are_empty_but_json_is_still_written(
    tmp_path: Path,
) -> None:
    workdir = _make_workdir(tmp_path)

    report = _run(workdir, tmp_path / "life")

    assert report is not None
    assert not (workdir / ".argus" / "spec_components.json").exists()
    assert report.components == {}
    round_path = workdir / ".argus" / "round-checks" / "round-1.json"
    assert report.json_path == str(round_path)
    payload = json.loads(round_path.read_text())
    assert payload["components"] == {}
    assert payload["outcomes"]["tests/spec/test_alpha.py::test_bad"] == "FAILED"
    skipped_key = next(iter(payload["skipped_reasons"]))
    assert skipped_key.startswith("tests/spec/test_alpha.py:")
    assert payload["skipped_reasons"][skipped_key] == "not today"
    assert json.loads((workdir / ".argus" / "round-checks" / "latest.json").read_text()) == payload
    assert "### Components" not in render_for_reviewer(report)
    assert "Contradicted component" not in render_for_engineer(report)


def test_latest_json_follows_the_most_recent_round(tmp_path: Path) -> None:
    workdir = _make_workdir(tmp_path)
    life_dir = tmp_path / "life"
    first = _run(workdir, life_dir, round_index=1)
    second = _run(workdir, life_dir, round_index=2, previous=first.test_ids)
    assert first is not None and second is not None
    checks = workdir / ".argus" / "round-checks"
    assert sorted(p.name for p in checks.glob("round-*.json")) == ["round-1.json", "round-2.json"]
    latest = json.loads((checks / "latest.json").read_text())
    assert latest["round_index"] == 2
    assert latest == json.loads((checks / "round-2.json").read_text())


def test_malformed_spec_components_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".argus").mkdir()
    target = tmp_path / ".argus" / "spec_components.json"
    for bad in ("{not json", "[]", '{"items": "nope"}', '{"items": {"a::t": {"kind": "knockout"}}}'):
        target.write_text(bad)
        assert load_spec_components(tmp_path) == {}
    assert load_spec_components(tmp_path / "missing") == {}
    target.write_text(json.dumps({"items": {
        "tests/spec/test_claim_shape.py::test_a": {"component": "a"},
        "tests/spec/test_other.py::test_b": {"component": "b", "kind": "weird"},
        "tests/spec/test_other.py::test_c": {"component": "  ", "kind": "knockout"},
    }}))
    assert load_spec_components(tmp_path) == {
        "tests/spec/test_claim_shape.py::test_a": {"component": "a", "kind": "claim"},
        "tests/spec/test_other.py::test_b": {"component": "b", "kind": "invariant"},
    }


def test_component_status_rules() -> None:
    passed_knockout = {"id": "k", "kind": "knockout", "outcome": "PASSED"}
    passed_invariant = {"id": "i", "kind": "invariant", "outcome": "PASSED"}
    failed_invariant = {"id": "f", "kind": "invariant", "outcome": "FAILED"}
    errored = {"id": "e", "kind": "differential", "outcome": "ERROR"}
    skipped = {"id": "s", "kind": "knockout", "outcome": "SKIPPED"}
    assert component_status([passed_knockout, passed_invariant]) == "proven"
    assert component_status([passed_invariant]) == "partial"  # nothing probed the component
    assert component_status([passed_knockout, skipped]) == "partial"
    assert component_status([passed_knockout, failed_invariant]) == "contradicted"
    assert component_status([errored]) == "contradicted"
    assert component_status([]) == "partial"


def test_join_reports_skipped_marked_tests_by_file_location() -> None:
    outcomes = {"tests/spec/test_knockouts.py::test_a": "PASSED"}
    spec = {
        "tests/spec/test_knockouts.py::test_a": {"component": "x", "kind": "knockout"},
        "tests/spec/test_knockouts.py::test_b": {"component": "x", "kind": "knockout"},
        "tests/spec/test_differential.py::test_c": {"component": "y", "kind": "differential"},
    }
    joined = join_components(
        outcomes, spec, skipped_files=frozenset({"tests/spec/test_knockouts.py"}),
    )
    assert [t["outcome"] for t in joined["x"]["tests"]] == ["PASSED", "SKIPPED"]
    assert joined["x"]["status"] == "partial"
    # Marked but never reported (e.g. the run died first): kept as an empty, partial entry.
    assert joined["y"] == {"tests": [], "status": "partial"}


# --- the round-evidence provider -----------------------------------------------


def test_provider_is_registered_on_import_and_packages_a_run(tmp_path: Path) -> None:
    assert round_evidence in registered_round_evidence_providers()
    workdir = _make_workdir(tmp_path)
    life_dir = tmp_path / "life"

    first = round_evidence(RoundEvidenceRequest(workdir=workdir, life_dir=life_dir, round_index=1))

    assert isinstance(first, RoundEvidence)
    assert first.reviewer_text.startswith("## Host-run project checks (round 1)")
    assert NOT_A_GATE_SENTENCE in first.reviewer_text
    assert first.engineer_note.startswith("## Host-run project checks from your previous round")
    assert "tests/spec/test_beta.py::test_beta_ok" in first.state[STATE_LAST_TEST_IDS]
    assert first.state[STATE_LAST_TEST_IDS] == sorted(first.state[STATE_LAST_TEST_IDS])
    json.dumps(first.state)  # opaque but serialisable

    # The state round-trips: the next round names the test that disappeared.
    (workdir / "tests" / "spec" / "test_beta.py").unlink()
    second = round_evidence(RoundEvidenceRequest(
        workdir=workdir, life_dir=life_dir, round_index=2, previous_state=first.state,
    ))
    assert second is not None
    assert "collected last round but not collected now" in second.reviewer_text
    assert "tests/spec/test_beta.py::test_beta_ok" in second.reviewer_text
    assert "tests/spec/test_beta.py::test_beta_ok" not in second.state[STATE_LAST_TEST_IDS]


def test_provider_returns_none_without_spec_suite_or_with_knob_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    assert round_evidence(RoundEvidenceRequest(workdir=bare, life_dir=tmp_path / "life", round_index=1)) is None
    workdir = _make_workdir(tmp_path)
    monkeypatch.setenv(SPEC_CHECKS_KNOB, "off")
    assert round_evidence(RoundEvidenceRequest(workdir=workdir, life_dir=tmp_path / "life", round_index=1)) is None


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_incomplete_run_keeps_the_previous_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "project"
    spec = workdir / "tests" / "spec"
    spec.mkdir(parents=True)
    (spec / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8",
    )
    monkeypatch.setenv(SPEC_CHECK_TIMEOUT_KNOB, "30")  # clamped floor; the run is killed there
    import argus.verticals.research.spec_checks as module

    monkeypatch.setattr(module, "configured_spec_check_timeout_s", lambda env=None: 2.0)
    previous = {STATE_LAST_TEST_IDS: ["tests/spec/test_old.py::test_gone"]}

    produced = round_evidence(RoundEvidenceRequest(
        workdir=workdir, life_dir=tmp_path / "life", round_index=2, previous_state=previous,
    ))

    assert produced is not None
    assert "TIMED OUT" in produced.reviewer_text
    assert produced.state == previous
