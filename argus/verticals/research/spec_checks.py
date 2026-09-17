"""Host-run spec checks after each Engineer round (research vertical).

After the Engineer's turn the host runs the project's own ``tests/spec`` (and
``tests/parity`` when present) suite with pytest, in the project's own
interpreter, and records what happened. The result is *evidence*: it is
rendered into the Reviewer's raw-evidence slot and summarised for the next
Engineer round through the vertical-blind registry in
``argus.engineer.round_evidence``. Nothing here blocks a stage, admits or
rejects a task, or overrides a role's judgment; roles read it and decide.

Why the host and not the Engineer: an Engineer account of "all tests pass" is
narrative. A read-only Reviewer cannot cheaply rerun the suite either. Running
the suite here costs zero model tokens and yields facts the Reviewer can weigh
against the account -- how many tests exist, which failed or were skipped and
why, and which tests that were collected last round are no longer collected.

The run is isolated from the project's own pytest configuration on purpose:
the ini file is a host-written empty ``[pytest]`` section, ``addopts`` is
cleared, and ``--confcutdir`` excludes a project-root ``conftest.py``. A
project cannot therefore silently deselect its own spec tests with
``-k``/``--deselect`` in ``pyproject.toml``, ``pytest.ini``, ``PYTEST_ADDOPTS``
or a root conftest. Conftests under ``tests/`` still load, so fixtures work.

Besides the text log, every run is written as JSON under
``<workdir>/.argus/round-checks/`` (``round-<n>.json`` and a ``latest.json``
copy) so that the web UI and the method-card derivation can read the same
facts without re-running anything. When the project's ``tests/spec/conftest.py``
records component markers in ``<workdir>/.argus/spec_components.json`` during
collection, the run is joined with them: each METHOD.md component gets the
outcomes of the tests that carry its marker and a summary status.

Everything is fail-soft: any exception logs and returns ``None``. The module
registers :func:`round_evidence` as a provider when it is imported, which the
research vertical does on package import; the round loop itself never names
this module.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...engineer.round_evidence import (
    RoundEvidence,
    RoundEvidenceRequest,
    register_round_evidence_provider,
)

log = logging.getLogger(__name__)

SPEC_CHECKS_KNOB = "ARGUS_RESEARCH_SPEC_CHECKS"
SPEC_CHECK_TIMEOUT_KNOB = "ARGUS_RESEARCH_SPEC_CHECK_TIMEOUT_SECONDS"
DEFAULT_CHECK_TIMEOUT_S = 600.0
MIN_CHECK_TIMEOUT_S = 30.0
MAX_CHECK_TIMEOUT_S = 3600.0

#: Project-relative directories the host runs. ``tests/spec`` must exist for
#: the checks to run at all; the others are added when present.
REQUIRED_CHECK_DIR = "tests/spec"
OPTIONAL_CHECK_DIRS = ("tests/parity",)

CHECKS_SUBDIR = "round-checks"
#: Where the JSON twin of each run lives, relative to the project workdir.
ROUND_CHECKS_JSON_DIR = Path(".argus") / CHECKS_SUBDIR
LATEST_CHECKS_JSON = "latest.json"
#: Written by the project's ``tests/spec/conftest.py`` during collection:
#: ``{"generated_at": float, "items": {nodeid: {"component": str, "kind": str}}}``.
SPEC_COMPONENTS_FILE = Path(".argus") / "spec_components.json"

COMPONENT_KINDS: tuple[str, ...] = ("knockout", "differential", "invariant", "claim", "parity")
#: Kind inferred from the test file name when a marker leaves ``kind`` out.
_KIND_BY_FILE = {
    "test_knockouts.py": "knockout",
    "test_differential.py": "differential",
    "test_claim_shape.py": "claim",
    "test_parity_reference.py": "parity",
}
_DEFAULT_KIND = "invariant"
#: Kinds whose passing test counts as positive evidence that the component
#: does what the card says (a passing invariant alone does not).
_PROBING_KINDS = frozenset({"knockout", "differential"})
COMPONENT_STATUSES: tuple[str, ...] = ("proven", "contradicted", "partial")
NOT_A_GATE_SENTENCE = (
    "The host ran these after the Engineer turn; weigh them as evidence, "
    "they are not a gate."
)

OUTCOMES: tuple[str, ...] = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")
_OUTCOME_WORDS = {"passed", "failed", "error", "skipped", "xfail", "xpass"}

MAX_FAILED_IDS = 40
MAX_SKIPPED = 20
MAX_REMOVED_IDS = 40
MAX_OUTPUT_TAIL_CHARS = 4000
_REVIEWER_TAIL_CHARS = 1500
_ENGINEER_FAILED_IDS = 10
_ENGINEER_REMOVED_IDS = 10

# ``-rA`` short-summary lines. Skips are grouped by location, so their "id" is
# ``path:lineno`` rather than a node id; every other outcome carries a node id.
_SUMMARY_LINE = re.compile(
    r"^(?P<outcome>PASSED|FAILED|ERROR|XFAIL|XPASS)\s+(?P<nodeid>\S+)(?:\s+-\s+(?P<reason>.*))?$"
)
_SKIPPED_LINE = re.compile(
    r"^SKIPPED(?:\s+\[(?P<count>\d+)\])?\s+(?P<location>\S+?):(?:\s+(?P<reason>.*))?$"
)
_SUMMARY_HEADER = re.compile(r"^=+ short test summary info =+$")
_SECTION_HEADER = re.compile(r"^=+ .* =+$")

# pytest exit codes whose test-id set is NOT a complete picture of the suite.
_INCOMPLETE_EXIT_CODES = frozenset({2, 3, 4})


@dataclass(frozen=True)
class SpecCheckReport:
    """What the host observed from one run of the project's own checks."""

    round_index: int
    workdir: str
    interpreter: str
    command: list[str]
    exit_code: int | None
    timed_out: bool
    timeout_s: float
    duration_s: float
    counts: dict[str, int]
    failed_ids: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]
    removed_test_ids: tuple[str, ...]
    output_tail: str
    test_ids: frozenset[str]
    log_path: str
    #: False when the run timed out or pytest stopped before collecting the
    #: whole suite; the id set then cannot be compared with the previous round.
    collection_complete: bool = True
    check_dirs: tuple[str, ...] = field(default_factory=tuple)
    #: Every summary row: node id -> outcome. Skips are keyed by their
    #: ``path:lineno`` location because ``-rA`` groups them that way.
    outcomes: dict[str, str] = field(default_factory=dict)
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    #: METHOD.md component -> ``{"tests": [{id, kind, outcome}], "status"}``,
    #: the join of this run with ``.argus/spec_components.json``; ``{}`` when
    #: the project records no component markers.
    components: dict[str, dict] = field(default_factory=dict)
    #: ``<workdir>/.argus/round-checks/round-<n>.json`` ("" if it could not be written).
    json_path: str = ""
    ran_at: float = 0.0


def spec_checks_enabled(env: dict[str, str] | None = None) -> bool:
    """``ARGUS_RESEARCH_SPEC_CHECKS`` is on unless set to off/0/false/no."""
    from ...core.knobs import resolve_knob

    value = resolve_knob(SPEC_CHECKS_KNOB, "on", env=env).value
    return value.strip().lower() not in {"0", "off", "false", "no"}


def configured_spec_check_timeout_s(env: dict[str, str] | None = None) -> float:
    """``ARGUS_RESEARCH_SPEC_CHECK_TIMEOUT_SECONDS`` clamped to 30..3600."""
    from ...core.knobs import resolve_knob

    raw = resolve_knob(
        SPEC_CHECK_TIMEOUT_KNOB, str(int(DEFAULT_CHECK_TIMEOUT_S)), env=env,
    ).value
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_CHECK_TIMEOUT_S
    return min(MAX_CHECK_TIMEOUT_S, max(MIN_CHECK_TIMEOUT_S, value))


def spec_check_dirs(workdir: Path) -> list[str]:
    """Project-relative check directories, or ``[]`` when there is no spec suite."""
    if not (workdir / REQUIRED_CHECK_DIR).is_dir():
        return []
    dirs = [REQUIRED_CHECK_DIR]
    dirs.extend(rel for rel in OPTIONAL_CHECK_DIRS if (workdir / rel).is_dir())
    return dirs


def project_interpreter(workdir: Path) -> str:
    """The project's own venv interpreter when it has one, else this process's."""
    candidate = workdir / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _check_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def build_check_command(
    workdir: Path, *, interpreter: str, ini_path: Path, check_dirs: list[str],
) -> list[str]:
    return [
        interpreter,
        "-m",
        "pytest",
        *check_dirs,
        "-q",
        "-rA",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "--rootdir",
        str(workdir),
        "--confcutdir",
        str(workdir / "tests"),
        "-c",
        str(ini_path),
    ]


def parse_summary(output: str) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    """Return ``(counts, rows)`` from the ``-rA`` short test summary section.

    ``rows`` are ``(outcome, test_id, reason)`` in output order. Counts come
    from the summary lines themselves (skips count their ``[N]`` multiplier),
    which keeps a project's custom terminal reporter from misleading us.
    """
    counts = {outcome: 0 for outcome in OUTCOMES}
    rows: list[tuple[str, str, str]] = []
    in_summary = False
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if _SUMMARY_HEADER.match(line):
            in_summary = True
            continue
        if not in_summary:
            continue
        if _SECTION_HEADER.match(line):
            break
        skipped = _SKIPPED_LINE.match(line)
        if skipped:
            count = int(skipped.group("count") or 1)
            counts["SKIPPED"] += count
            rows.append((
                "SKIPPED",
                skipped.group("location"),
                (skipped.group("reason") or "").strip(),
            ))
            continue
        match = _SUMMARY_LINE.match(line)
        if match:
            outcome = match.group("outcome")
            counts[outcome] += 1
            rows.append((outcome, match.group("nodeid"), (match.group("reason") or "").strip()))
    return counts, rows


def infer_component_kind(test_id: str) -> str:
    """Kind from the spec file name when the marker does not say."""
    file_part = test_id.split("::", 1)[0]
    return _KIND_BY_FILE.get(Path(file_part).name, _DEFAULT_KIND)


def load_spec_components(workdir: Path) -> dict[str, dict[str, str]]:
    """``{nodeid: {"component": name, "kind": kind}}`` from the conftest's file.

    Missing, unreadable or malformed files give ``{}``; rows without a
    component name are dropped; a missing or unknown ``kind`` is inferred from
    the file name.
    """
    path = Path(workdir) / SPEC_COMPONENTS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for test_id, row in items.items():
        if not isinstance(test_id, str) or not isinstance(row, dict):
            continue
        component = str(row.get("component") or "").strip()
        if not component:
            continue
        kind = str(row.get("kind") or "").strip().lower()
        if kind not in COMPONENT_KINDS:
            kind = infer_component_kind(test_id)
        result[test_id] = {"component": component, "kind": kind}
    return result


def component_status(tests: list[dict]) -> str:
    """``contradicted`` on any failure/error; ``proven`` when every test passed and
    at least one knockout or differential is among them; else ``partial``."""
    outcomes = [str(test.get("outcome") or "") for test in tests]
    if any(outcome in {"FAILED", "ERROR"} for outcome in outcomes):
        return "contradicted"
    if (
        tests
        and all(outcome == "PASSED" for outcome in outcomes)
        and any(test.get("kind") in _PROBING_KINDS for test in tests)
    ):
        return "proven"
    return "partial"


def join_components(
    outcomes: dict[str, str],
    spec_components: dict[str, dict[str, str]],
    *,
    skipped_files: frozenset[str] = frozenset(),
) -> dict[str, dict]:
    """Group this run's outcomes by METHOD.md component.

    A marked test that has no summary row of its own but whose file recorded
    a skip is reported as ``SKIPPED`` (``-rA`` folds skips by location). Marked
    tests with no outcome at all (e.g. after a timeout) are left out, so a
    component may end up with an empty test list and ``partial`` status.
    """
    grouped: dict[str, list[dict]] = {}
    for test_id, row in spec_components.items():
        outcome = outcomes.get(test_id)
        if outcome is None and test_id.split("::", 1)[0] in skipped_files:
            outcome = "SKIPPED"
        tests = grouped.setdefault(row["component"], [])
        if outcome is None:
            continue
        tests.append({"id": test_id, "kind": row["kind"], "outcome": outcome})
    result: dict[str, dict] = {}
    for component in sorted(grouped):
        tests = sorted(grouped[component], key=lambda test: test["id"])
        result[component] = {"tests": tests, "status": component_status(tests)}
    return result


def check_report_payload(report: SpecCheckReport) -> dict:
    """The JSON twin of a report (what ``round-<n>.json`` holds)."""
    return {
        "round_index": report.round_index,
        "ran_at": report.ran_at,
        "exit_code": report.exit_code,
        "timed_out": report.timed_out,
        "duration_s": round(report.duration_s, 3),
        "counts": dict(report.counts),
        "outcomes": dict(report.outcomes),
        "skipped_reasons": dict(report.skipped_reasons),
        "removed_test_ids": list(report.removed_test_ids),
        "components": report.components,
        "collection_complete": report.collection_complete,
        "check_dirs": list(report.check_dirs),
        "log_path": report.log_path,
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_check_json(workdir: Path, report: SpecCheckReport) -> str:
    """Write ``round-<n>.json`` and its ``latest.json`` copy; return the round path or ``""``."""
    checks_dir = Path(workdir) / ROUND_CHECKS_JSON_DIR
    round_path = checks_dir / f"round-{report.round_index}.json"
    payload = check_report_payload(report)
    try:
        _write_json_atomic(round_path, payload)
        _write_json_atomic(checks_dir / LATEST_CHECKS_JSON, payload)
    except OSError:
        log.warning("could not write round-check JSON under %s", checks_dir, exc_info=True)
        return ""
    return str(round_path)


def _kill_process_group(process: subprocess.Popen) -> None:
    if os.name == "nt":
        process.kill()
        return
    import signal

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()


def run_spec_checks(
    workdir: Path,
    *,
    life_dir: Path,
    round_index: int,
    previous_test_ids: frozenset[str],
    timeout_s: float | None = None,
) -> SpecCheckReport | None:
    """Run the project's own spec suite and report what happened, or ``None``.

    ``None`` means "nothing to show": the knob is off, the project has no
    ``tests/spec`` directory, or the host itself failed (logged). An explicit
    ``timeout_s`` overrides the knob and is not clamped.
    """
    try:
        return _run_spec_checks(
            Path(workdir),
            life_dir=Path(life_dir),
            round_index=int(round_index),
            previous_test_ids=frozenset(previous_test_ids or ()),
            timeout_s=timeout_s,
        )
    except Exception:  # noqa: BLE001 - evidence gathering must never break the round
        log.exception("host-run spec checks failed for round %s", round_index)
        return None


def _run_spec_checks(
    workdir: Path,
    *,
    life_dir: Path,
    round_index: int,
    previous_test_ids: frozenset[str],
    timeout_s: float | None,
) -> SpecCheckReport | None:
    if not spec_checks_enabled():
        return None
    check_dirs = spec_check_dirs(workdir)
    if not check_dirs:
        return None
    checks_dir = life_dir / CHECKS_SUBDIR
    checks_dir.mkdir(parents=True, exist_ok=True)
    ini_path = checks_dir / "pytest.ini"
    ini_path.write_text("[pytest]\n", encoding="utf-8")
    interpreter = project_interpreter(workdir)
    command = build_check_command(
        workdir, interpreter=interpreter, ini_path=ini_path, check_dirs=check_dirs,
    )
    effective_timeout = (
        float(timeout_s) if timeout_s is not None else configured_spec_check_timeout_s()
    )

    started = time.monotonic()
    ran_at = time.time()
    timed_out = False
    process = subprocess.Popen(
        command,
        cwd=str(workdir),
        env=_check_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    try:
        output, _ = process.communicate(timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    except BaseException:
        _kill_process_group(process)
        process.communicate()
        raise
    duration_s = time.monotonic() - started
    output = output or ""
    exit_code: int | None = None if timed_out else process.returncode

    counts, rows = parse_summary(output)
    test_ids = frozenset(test_id for _outcome, test_id, _reason in rows)
    failed_ids = tuple(
        test_id for outcome, test_id, _reason in rows if outcome in {"FAILED", "ERROR"}
    )[:MAX_FAILED_IDS]
    skipped = tuple(
        (test_id, reason) for outcome, test_id, reason in rows if outcome == "SKIPPED"
    )[:MAX_SKIPPED]
    collection_complete = not timed_out and exit_code not in _INCOMPLETE_EXIT_CODES
    removed = (
        tuple(sorted(previous_test_ids - test_ids))[:MAX_REMOVED_IDS]
        if collection_complete
        else ()
    )

    log_path = checks_dir / f"round-{round_index}.txt"
    header = (
        f"# host-run project checks, round {round_index}\n"
        f"# cwd: {workdir}\n"
        f"# command: {' '.join(command)}\n"
        f"# exit_code: {exit_code} timed_out: {timed_out} "
        f"duration_s: {duration_s:.1f} timeout_s: {effective_timeout:.0f}\n\n"
    )
    log_path.write_text(header + output, encoding="utf-8")

    outcomes = {test_id: outcome for outcome, test_id, _reason in rows}
    skipped_reasons = {test_id: reason for outcome, test_id, reason in rows if outcome == "SKIPPED"}
    skipped_files = frozenset(location.rsplit(":", 1)[0] for location in skipped_reasons)
    components = join_components(
        outcomes, load_spec_components(workdir), skipped_files=skipped_files,
    )

    report = SpecCheckReport(
        round_index=round_index,
        workdir=str(workdir),
        interpreter=interpreter,
        command=command,
        exit_code=exit_code,
        timed_out=timed_out,
        timeout_s=effective_timeout,
        duration_s=duration_s,
        counts=counts,
        failed_ids=failed_ids,
        skipped=skipped,
        removed_test_ids=removed,
        output_tail=output[-MAX_OUTPUT_TAIL_CHARS:],
        test_ids=test_ids,
        log_path=str(log_path),
        collection_complete=collection_complete,
        check_dirs=tuple(check_dirs),
        outcomes=outcomes,
        skipped_reasons=skipped_reasons,
        components=components,
        ran_at=ran_at,
    )
    json_path = write_check_json(workdir, report)
    return replace(report, json_path=json_path)


def _counts_line(report: SpecCheckReport) -> str:
    parts = [
        f"{report.counts.get(outcome, 0)} {outcome.lower()}" for outcome in OUTCOMES
    ]
    return ", ".join(parts)


def _status_line(report: SpecCheckReport) -> str:
    if report.timed_out:
        return (
            f"Result: TIMED OUT after {report.timeout_s:.0f}s; the host killed the "
            "whole process group. No test outcome below is complete."
        )
    return f"Exit code: {report.exit_code} (duration {report.duration_s:.1f}s)"


def _exit_code_hint(report: SpecCheckReport) -> str:
    if report.timed_out:
        return ""
    if report.exit_code == 5:
        return "pytest collected no tests in these directories."
    if report.exit_code in _INCOMPLETE_EXIT_CODES:
        return (
            "pytest stopped before the whole suite ran (collection error, internal "
            "error or usage error); see the output tail."
        )
    return ""


def _failing_ids(entry: dict) -> list[str]:
    return [
        str(test.get("id"))
        for test in entry.get("tests", ())
        if test.get("outcome") in {"FAILED", "ERROR"}
    ]


def _component_lines(report: SpecCheckReport) -> list[str]:
    lines = []
    for component, entry in report.components.items():
        tests = entry.get("tests", [])
        kinds = sorted({str(test.get("kind")) for test in tests})
        line = (
            f"- {component}: {entry.get('status', 'partial')}, {len(tests)} "
            f"test{'s' if len(tests) != 1 else ''}"
        )
        if kinds:
            line += f" ({', '.join(kinds)})"
        failing = _failing_ids(entry)
        if failing:
            line += "; failing: " + ", ".join(failing)
        lines.append(line)
    return lines


def render_for_reviewer(report: SpecCheckReport) -> str:
    """Raw-evidence block for the Reviewer: facts, with the not-a-gate sentence."""
    lines = [
        f"## Host-run project checks (round {report.round_index})",
        f"Command (cwd {report.workdir}): {' '.join(report.command)}",
        f"Interpreter: {report.interpreter}",
        _status_line(report),
        f"Counts: {_counts_line(report)}",
    ]
    hint = _exit_code_hint(report)
    if hint:
        lines.append(hint)
    if report.failed_ids:
        lines.append("Failures / errors:")
        lines.extend(f"- {test_id}" for test_id in report.failed_ids)
    if report.skipped:
        lines.append("Skips (with reasons):")
        lines.extend(
            f"- {test_id} - {reason or '(no reason given)'}"
            for test_id, reason in report.skipped
        )
    if report.removed_test_ids:
        lines.append(
            "Removed tests (collected last round but not collected now): "
            + ", ".join(report.removed_test_ids)
        )
    if report.components:
        lines.append("### Components (from tests/spec markers)")
        lines.extend(_component_lines(report))
    lines.append(f"Full output: {report.log_path}")
    if (report.timed_out or report.exit_code != 0) and report.output_tail.strip():
        tail = report.output_tail[-_REVIEWER_TAIL_CHARS:].rstrip()
        lines.append("Output tail:")
        lines.append("```")
        lines.append(tail)
        lines.append("```")
    lines.append(NOT_A_GATE_SENTENCE)
    return "\n".join(lines)


def render_for_engineer(report: SpecCheckReport) -> str:
    """Short note for the next Engineer round about the previous round's checks."""
    lines = [
        "## Host-run project checks from your previous round",
        f"{_status_line(report)}; counts: {_counts_line(report)}.",
    ]
    hint = _exit_code_hint(report)
    if hint:
        lines.append(hint)
    if report.failed_ids:
        shown = report.failed_ids[:_ENGINEER_FAILED_IDS]
        more = len(report.failed_ids) - len(shown)
        lines.append(
            "Failing: " + ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")
        )
    if report.removed_test_ids:
        shown = report.removed_test_ids[:_ENGINEER_REMOVED_IDS]
        more = len(report.removed_test_ids) - len(shown)
        lines.append(
            "Collected last round but not now: "
            + ", ".join(shown)
            + (f" (+{more} more)" if more > 0 else "")
        )
    for component, entry in report.components.items():
        if entry.get("status") != "contradicted":
            continue
        failing = _failing_ids(entry)
        lines.append(
            f"Contradicted component '{component}': "
            + (", ".join(failing) if failing else "a marked test failed")
        )
    lines.append(
        f"Full output: {report.log_path}. The Reviewer sees the same facts; "
        "address or explain them rather than editing the tests to fit."
    )
    return "\n".join(lines)


# --- the round-evidence provider ---------------------------------------------

#: Key under which the provider keeps the ids collected last round.
STATE_LAST_TEST_IDS = "last_test_ids"


def round_evidence(request: RoundEvidenceRequest) -> RoundEvidence | None:
    """Run the spec checks for one round and package them as round evidence.

    ``None`` when there is nothing to show (knob off, no ``tests/spec``, host
    failure). The state carries the sorted test ids of a complete run so the
    next round can name tests that disappeared; an incomplete run (timeout,
    collection error) keeps the previous ids.
    """
    previous_raw = request.previous_state.get(STATE_LAST_TEST_IDS, ())
    previous_ids = frozenset(
        str(test_id) for test_id in previous_raw if isinstance(test_id, str)
    ) if isinstance(previous_raw, (list, tuple, set, frozenset)) else frozenset()
    report = run_spec_checks(
        request.workdir,
        life_dir=request.life_dir,
        round_index=request.round_index,
        previous_test_ids=previous_ids,
    )
    if report is None:
        return None
    state = (
        {STATE_LAST_TEST_IDS: sorted(report.test_ids)}
        if report.collection_complete
        else dict(request.previous_state)
    )
    return RoundEvidence(
        reviewer_text=render_for_reviewer(report),
        engineer_note=render_for_engineer(report),
        state=state,
    )


register_round_evidence_provider(round_evidence)


__all__ = [
    "COMPONENT_KINDS",
    "COMPONENT_STATUSES",
    "DEFAULT_CHECK_TIMEOUT_S",
    "LATEST_CHECKS_JSON",
    "NOT_A_GATE_SENTENCE",
    "ROUND_CHECKS_JSON_DIR",
    "SPEC_CHECKS_KNOB",
    "SPEC_CHECK_TIMEOUT_KNOB",
    "SPEC_COMPONENTS_FILE",
    "STATE_LAST_TEST_IDS",
    "SpecCheckReport",
    "build_check_command",
    "check_report_payload",
    "component_status",
    "configured_spec_check_timeout_s",
    "infer_component_kind",
    "join_components",
    "load_spec_components",
    "parse_summary",
    "project_interpreter",
    "render_for_engineer",
    "render_for_reviewer",
    "round_evidence",
    "run_spec_checks",
    "spec_check_dirs",
    "spec_checks_enabled",
    "write_check_json",
]
