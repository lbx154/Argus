"""Compiling Lean without holding the mathematician still.

``verify`` blocks its caller for the whole compile. Measured on this host with a
stub compiler, a six-second compile plus a six-second axiom audit costs the
caller twelve seconds of doing nothing; with Mathlib it is minutes of a
reasoning agent sitting idle. ``submit``/``reclaim`` is the same step with the
waiting removed.

Everything here is adversarial about one thing: an asynchronous run has a much
wider window in which the source can move under it than a synchronous one, and
the guarantee ``verify`` just closed — a run whose inputs did not hold still
publishes nothing at all — has to survive that widening rather than be quietly
weakened by it. So the tests below check both halves of how that is done: the
compile reads a snapshot taken at submit, and publication still checks the live
files.

The other distinction under test is the one the whole vertical rests on. A
background process that died wrote no verdict, and "the run was lost" must never
reach a reader as "the proof was rejected".

The compiler is stubbed with a small executable rather than timed against a real
one: a test that depends on beating a real Lean is a test that passes on a slow
host and says nothing on a fast one. Where a test needs a compile to still be
running, it gates the stub on a file rather than on a sleep, so the state under
test is chosen rather than raced for.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from argus_skill.verticals.math import lean_async
from argus_skill.verticals.math.lean_async import (
    LeanRunLost,
    LeanRunUnknown,
    outstanding_runs,
    reclaim_lean_run,
    submit_lean_run,
)
from argus_skill.verticals.math.lean_evidence import (
    CompiledArtifactChangedError,
    discover_lean_sources,
    main,
    validate_lean_evidence,
    verify_lean_source,
)
from argus_skill.verticals.math.objective_mode import set_objective
from argus_skill.verticals.math.stages import stage_completion_issues

pytestmark = pytest.mark.integration

CORE_THEOREM = (
    "theorem argus_add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
)
FIDELITY = (
    "# Statement fidelity\n\n"
    "`argus_add_comm` formalizes: for all natural numbers a and b, a + b = b + a.\n"
    "Objects: natural numbers. Quantifiers: universal over a and b.\n"
    "Hypotheses: none. Conclusion: commutativity of addition. Added assumptions: none.\n"
)
SWAPPED_THEOREM = (
    "theorem argus_add_comm (n : Nat) : n = n + 1 := Nat.add_comm n 1\n"
)
SWAPPED_FIDELITY = (
    "# Statement fidelity\n\n"
    "`argus_add_comm` formalizes: every natural number equals its own successor.\n"
    "Objects: natural numbers. Quantifiers: universal over n.\n"
    "Hypotheses: none. Conclusion: n = n + 1. Added assumptions: none.\n"
)

#: Generous, because it only bounds how long a stub compiler takes to exit; a
#: failure here is a hung worker, not a slow one.
_SETTLE_SECONDS = 60.0


# -- fixtures ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A host with no Mathlib and a run directory of its own.

    The run directory is read from the environment by both this process and the
    worker it spawns, and the worker inherits this environment, so setting it
    here is what keeps one test's handles out of another's ``status``.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_LEAN_RUNS", str(tmp_path / "lean-runs"))


def _project(tmp_path: Path) -> Path:
    set_objective(tmp_path, mode="targeted", goal="G")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["verification_profile"] = "develop"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PROOF_GRAPH.json").write_text(
        json.dumps({
            "goal": "G",
            "routes": [{"name": "route", "status": "current", "evidence": ""}],
            "nodes": {
                "G": {
                    "statement": "G",
                    "status": "proved",
                    "is_goal": True,
                    "depends_on": [],
                    "reviewer_confirmed": True,
                }
            },
        }),
        encoding="utf-8",
    )
    return tmp_path


def _lean_dir(root: Path) -> Path:
    path = root / "research" / "lean"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _formalized(root: Path, text: str = CORE_THEOREM) -> tuple[Path, Path]:
    source = _lean_dir(root) / "Main.lean"
    source.write_text(text, encoding="utf-8")
    fidelity = _lean_dir(root) / "statement_fidelity.md"
    fidelity.write_text(FIDELITY, encoding="utf-8")
    return source, fidelity


def _stub_lean(tmp_path: Path, name: str, body: str) -> str:
    """An executable standing in for ``lean``, answering ``--version`` first.

    Written with this interpreter's own path rather than ``env python3``, so the
    stub runs wherever the suite does.
    """
    script = tmp_path / (f"{name}.py" if os.name == "nt" else name)
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, time, pathlib\n"
        "if '--version' in sys.argv:\n"
        "    print('Lean (version 4.0.0, stub)')\n"
        "    raise SystemExit(0)\n"
        + body,
        encoding="utf-8",
    )
    script.chmod(0o755)
    if os.name != "nt":
        return str(script)
    wrapper = tmp_path / f"{name}.cmd"
    wrapper.write_text(
        f'@"{sys.executable}" "{script}" %*\r\n',
        encoding="utf-8",
    )
    return str(wrapper)


def _instant_lean(tmp_path: Path) -> str:
    return _stub_lean(tmp_path, "instant-lean", "raise SystemExit(0)\n")


def _failing_lean(tmp_path: Path) -> str:
    return _stub_lean(
        tmp_path,
        "failing-lean",
        "print('Main.lean:1:44: error: type mismatch')\nraise SystemExit(1)\n",
    )


def _gated_lean(tmp_path: Path, gate: Path) -> str:
    """A compile that does not finish until the test says so."""
    return _stub_lean(
        tmp_path,
        "gated-lean",
        f"gate = pathlib.Path({str(gate)!r})\n"
        "deadline = time.time() + 120\n"
        "while not gate.exists() and time.time() < deadline:\n"
        "    time.sleep(0.02)\n"
        "raise SystemExit(0)\n",
    )


def _run_dir(handle: str) -> Path:
    return lean_async._runs_root() / handle


def _wait_until(condition, what: str = "the background run", *, doomed=None) -> None:
    """Poll for ``condition``; ``doomed`` returns why waiting can no longer help.

    Once ``doomed`` gives a reason, ``condition`` is probed once more before
    failing: the worker publishes its outcome and only then exits, so "dead"
    observed between those two steps has to lose to the record it just wrote.
    """
    deadline = time.monotonic() + _SETTLE_SECONDS
    while time.monotonic() < deadline:
        if condition():
            return
        if doomed is not None:
            reason = doomed()
            if reason and not condition():
                raise AssertionError(f"{what} can no longer happen: {reason}")
        time.sleep(0.02)
    raise AssertionError(f"{what} never reached the state under test")


def _lock_is_held(handle: str) -> bool:
    """Whether the worker has reached its lock and written its pid into it.

    Killing before that point would race the child's own startup, and this test
    is about a worker that died mid-compile, not one that never began.
    """
    lock = _run_dir(handle) / "worker.lock"
    return lock.is_file() and lock.stat().st_size > 0


def _worker_died(handle: str) -> str | None:
    """A reason once the worker is gone without an answer, or ``None``.

    Liveness is the library's own probe, so it means what ``reclaim`` means by
    it: the worker's lock is held, or — before the worker has reached its lock —
    the pid recorded at submit is still running. A worker still starting up is
    therefore alive through its pid, never mistaken for dead.
    """
    run_dir = _run_dir(handle)
    if lean_async._worker_alive(run_dir, lean_async._read_run(run_dir)):
        return None
    try:
        log = (run_dir / "worker.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""
    return (
        "the worker exited without publishing an outcome; worker.log ends "
        f"with:\n{log[-2000:] or '<empty>'}"
    )


def _settle(handle: str) -> None:
    """Wait until the background run has published a terminal record.

    A worker that dies before publishing — the incident shape is an interpreter
    whose imports no longer resolve — fails this wait at once with its log,
    rather than burning the whole deadline into a bare timeout.
    """
    outcome = _run_dir(handle) / "outcome.json"
    _wait_until(
        lambda: outcome.is_file(),
        f"the background compile for {handle}",
        doomed=lambda: _worker_died(handle),
    )


def _artifacts(root: Path) -> set[str]:
    """Every name the artifact directory carries, lock file excluded.

    The lock is infrastructure both paths create on the way in; the question
    these tests ask is which *records* were published.
    """
    return {
        path.name
        for path in _lean_dir(root).iterdir()
        if path.name != ".lean-artifacts.lock"
    }


def _codes(root: Path) -> set[str]:
    return {issue.code for issue in validate_lean_evidence(root).issues}


# -- the cost this exists to remove -----------------------------------------

def test_submit_returns_while_the_compiler_is_still_running(
    tmp_path: Path,
) -> None:
    """The whole point: the Engineer gets the prompt back, not the answer."""
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    gate = tmp_path / "gate"
    lean_bin = _gated_lean(tmp_path, gate)

    started = time.monotonic()
    submitted = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=lean_bin,
        timeout_seconds=120.0,
    )
    elapsed = time.monotonic() - started

    # The compiler has not been allowed to exit, so any blocking call would
    # still be inside it.
    assert not gate.exists()
    assert elapsed < 10.0, elapsed
    handle = submitted["handle"]
    assert submitted["state"] == "running"
    assert [run["state"] for run in outstanding_runs()] == ["running"]

    gate.write_text("go", encoding="utf-8")
    _settle(handle)


def test_a_submitted_run_leaves_nothing_for_the_gate_to_find(
    tmp_path: Path,
) -> None:
    """The staged copy is not a second formalization the project has to answer for.

    A run directory inside the tree would be discovered as another ``.lean``
    file with no recorded result, so an outstanding compile would block
    completion with a message about a path the Engineer never wrote — and the
    only way to stop that would be to teach discovery to skip a directory name,
    which is how ``build/`` once became a hiding place.
    """
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    gate = tmp_path / "gate"
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_gated_lean(tmp_path, gate),
        timeout_seconds=120.0,
    )["handle"]

    assert discover_lean_sources(root) == (source,)
    assert "lean_result_missing" in _codes(root)  # the real source, not the copy
    assert _codes(root) == {"lean_result_missing"}

    gate.write_text("go", encoding="utf-8")
    _settle(handle)


# -- reclaim before there is an answer --------------------------------------

def test_reclaim_before_the_compile_finishes_writes_nothing(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    gate = tmp_path / "gate"
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_gated_lean(tmp_path, gate),
        timeout_seconds=120.0,
    )["handle"]

    answer = reclaim_lean_run(handle)

    assert answer["state"] == "running"
    assert "still running" in answer["message"]
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}
    # And the handle is still good: reclaiming early must not consume it.
    assert reclaim_lean_run(handle)["state"] == "running"

    gate.write_text("go", encoding="utf-8")
    _settle(handle)
    assert reclaim_lean_run(handle)["status"] == "success"


def test_the_cli_gives_an_unfinished_run_an_exit_code_of_its_own(
    tmp_path: Path,
    capsys,
) -> None:
    """0 would read as a pass and 1 as a failing proof. It is neither."""
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    gate = tmp_path / "gate"
    lean_bin = _gated_lean(tmp_path, gate)
    assert main([
        "submit", str(source), "--statement-fidelity", str(fidelity),
        "--project-root", str(root), "--lean-bin", lean_bin, "--timeout", "120",
    ]) == 0
    handle = json.loads(capsys.readouterr().out)["handle"]

    assert main(["reclaim", handle]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "running"
    assert "status" not in payload
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}

    gate.write_text("go", encoding="utf-8")
    _settle(handle)
    assert main(["reclaim", handle]) == 0


# -- reclaim writes what verify writes ---------------------------------------

def test_reclaim_records_exactly_what_the_synchronous_path_records(
    tmp_path: Path,
) -> None:
    """Same artifacts, same verdict, same digests — and the gate accepts both."""
    lean_bin = _instant_lean(tmp_path)

    synchronous = _project(tmp_path / "sync")
    sync_source, sync_fidelity = _formalized(synchronous)
    expected = verify_lean_source(
        sync_source, statement_fidelity=sync_fidelity, lean_bin=lean_bin
    )

    asynchronous = _project(tmp_path / "async")
    async_source, async_fidelity = _formalized(asynchronous)
    handle = submit_lean_run(
        async_source,
        statement_fidelity=async_fidelity,
        project_root=asynchronous,
        lean_bin=lean_bin,
    )["handle"]
    _settle(handle)
    actual = reclaim_lean_run(handle)

    assert _artifacts(asynchronous) == _artifacts(synchronous)
    for field in (
        "schema_version",
        "status",
        "tool",
        "exit_code",
        "stdout",
        "stderr",
        "proof_holes",
        "audit_exit_code",
        "source_sha256",
        "statement_fidelity_sha256",
        "lake_workspace",
        "environment_failure",
    ):
        assert actual[field] == expected[field], field
    # Both name the file the project carries, not the copy the compiler read.
    assert actual["source"] == str(async_source)
    assert Path(expected["source"]) == sync_source
    assert actual["compiled_copy"] != actual["source"]

    recorded = json.loads(
        (_lean_dir(asynchronous) / "lean_check.json").read_text(encoding="utf-8")
    )
    assert recorded["source_sha256"] == hashlib.sha256(
        async_source.read_bytes()
    ).hexdigest()
    assert recorded["statement_fidelity_sha256"] == hashlib.sha256(
        async_fidelity.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    assert validate_lean_evidence(asynchronous).issues == ()
    assert stage_completion_issues("solve", asynchronous) == ()
    # The run directory is gone; a reclaimed handle is spent.
    assert not _run_dir(handle).exists()
    assert outstanding_runs() == []


def test_a_compile_that_failed_is_reported_as_a_failed_compile(
    tmp_path: Path,
) -> None:
    """The async path must still be able to say the proof is broken."""
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_failing_lean(tmp_path),
    )["handle"]
    _settle(handle)

    published = reclaim_lean_run(handle)

    assert published["status"] == "type_error"
    assert published["environment_failure"] == ""
    assert "lean_compile_failed" in _codes(root)
    assert stage_completion_issues("solve", root) != ()


# -- the widened window ------------------------------------------------------

def test_a_source_edited_between_submit_and_reclaim_publishes_nothing(
    tmp_path: Path,
) -> None:
    """The guard `verify` closed, over the window `submit` opened.

    The snapshot means the compiler's answer is honestly about the submitted
    text — but that text is no longer what the project carries, so publishing
    the answer against the live file would hand new text an old verdict, with
    every later check finding the pair consistent.
    """
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_instant_lean(tmp_path),
    )["handle"]
    _settle(handle)

    source.write_text(SWAPPED_THEOREM, encoding="utf-8")

    with pytest.raises(CompiledArtifactChangedError) as raised:
        reclaim_lean_run(handle)

    message = str(raised.value)
    assert "the Lean source" in message
    assert str(source) in message
    assert "submit it again" in message
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}
    assert "lean_result_missing" in _codes(root)
    # The dead run does not linger waiting to be reclaimed a second time.
    assert not _run_dir(handle).exists()


def test_a_fidelity_note_rewritten_between_submit_and_reclaim_publishes_nothing(
    tmp_path: Path,
) -> None:
    """The unchecked half of the argument has the same binding, so the same rule."""
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_instant_lean(tmp_path),
    )["handle"]
    _settle(handle)

    fidelity.write_text(SWAPPED_FIDELITY, encoding="utf-8")

    with pytest.raises(CompiledArtifactChangedError) as raised:
        reclaim_lean_run(handle)

    message = str(raised.value)
    assert "statement_fidelity.md" in message
    assert "the Lean source" not in message
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}


def test_the_compiler_reads_the_snapshot_not_the_file_being_edited(
    tmp_path: Path,
) -> None:
    """Why staging is required rather than merely tidy.

    A compiler launched against the live file reads it when it gets there, not
    when it was launched, so an in-place background compile would answer about
    whatever the Engineer last saved. Here the source is rewritten while the
    compile is gated open, and the compile still reads the submitted bytes.
    """
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    submitted_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    gate = tmp_path / "gate"
    lean_bin = _stub_lean(
        tmp_path,
        "echoing-lean",
        "import hashlib\n"
        f"gate = pathlib.Path({str(gate)!r})\n"
        "deadline = time.time() + 120\n"
        "while not gate.exists() and time.time() < deadline:\n"
        "    time.sleep(0.02)\n"
        "target = [a for a in sys.argv[1:] if a.endswith('.lean')][-1]\n"
        "print('READ:' + hashlib.sha256(open(target, 'rb').read()).hexdigest())\n"
        "raise SystemExit(0)\n",
    )
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=lean_bin,
        timeout_seconds=120.0,
    )["handle"]

    source.write_text(SWAPPED_THEOREM, encoding="utf-8")
    edited_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    gate.write_text("go", encoding="utf-8")
    _settle(handle)

    outcome = json.loads(
        (_run_dir(handle) / "outcome.json").read_text(encoding="utf-8")
    )
    assert outcome["ok"] is True
    assert f"READ:{submitted_digest}" in outcome["result"]["stdout"]
    assert edited_digest not in outcome["result"]["stdout"]

    # An honest answer about text the project no longer carries is still not
    # publishable, which is the other half of the pair.
    with pytest.raises(CompiledArtifactChangedError):
        reclaim_lean_run(handle)


# -- a run that never produced an answer -------------------------------------

def test_a_lost_run_is_reported_as_lost_and_never_as_a_verdict(
    tmp_path: Path,
) -> None:
    """A killed worker said nothing about the mathematics. It must not seem to."""
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    gate = tmp_path / "gate"
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_gated_lean(tmp_path, gate),
        timeout_seconds=120.0,
    )["handle"]
    record = json.loads((_run_dir(handle) / "run.json").read_text(encoding="utf-8"))

    _wait_until(
        lambda: _lock_is_held(handle),
        "the worker taking its lock",
        doomed=lambda: _worker_died(handle),
    )
    os.kill(int(record["pid"]), getattr(signal, "SIGKILL", signal.SIGTERM))
    _wait_until(lambda: [run["state"] for run in outstanding_runs()] == ["lost"])

    with pytest.raises(LeanRunLost) as raised:
        reclaim_lean_run(handle)

    message = str(raised.value)
    assert "without recording an answer" in message
    assert "environment failure, not a statement about the proof" in message
    assert "submit the run again" in message.lower()
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}
    # Not a compiler verdict anywhere: the project still has no result at all.
    assert "lean_result_missing" in _codes(root)
    assert "lean_compile_failed" not in _codes(root)
    assert not _run_dir(handle).exists()

    gate.write_text("go", encoding="utf-8")


def test_the_cli_words_a_lost_run_as_a_refusal(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_instant_lean(tmp_path),
    )["handle"]
    _settle(handle)
    # A worker that died mid-write leaves an outcome that is not a result.
    (_run_dir(handle) / "outcome.json").write_text("{tru", encoding="utf-8")

    assert main(["reclaim", handle]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "status" not in payload
    assert "environment failure" in payload["error"]
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}


def test_a_worker_that_dies_on_import_fails_the_wait_at_once_with_its_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker whose interpreter cannot resolve an import dies before its lock.

    The 2026-09-05 incident had exactly this shape: a launcher rewritten to an
    interpreter whose user-site could no longer resolve ``portalocker``, so the
    child fell over during import and published nothing. Waiting a wait like
    ``_settle`` out to its deadline would cost a minute per call and report a
    bare timeout; the dead worker has to fail it immediately, root cause quoted.
    """
    sabotage = tmp_path / "sabotage"
    sabotage.mkdir()
    # `_launch_worker` prepends the package root to PYTHONPATH but keeps what
    # was already there, and site-packages come after both — so this shadows
    # portalocker for the worker alone; this process imported the real one long
    # ago, and env changes do not reach its sys.path.
    (sabotage / "portalocker.py").write_text(
        "raise ImportError('portalocker deliberately unresolvable')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(sabotage))

    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_instant_lean(tmp_path),
    )["handle"]

    started = time.monotonic()
    with pytest.raises(AssertionError) as raised:
        _settle(handle)
    elapsed = time.monotonic() - started

    assert elapsed < _SETTLE_SECONDS / 2, elapsed
    assert "portalocker deliberately unresolvable" in str(raised.value)
    assert [run["state"] for run in outstanding_runs()] == ["lost"]


# -- handles -----------------------------------------------------------------

@pytest.mark.parametrize(
    "handle",
    ["", "  ", "../../etc", "20260814T101500", "not-a-handle", "20260814T101500-zz"],
)
def test_a_handle_that_this_module_did_not_mint_is_refused(handle: str) -> None:
    """A handle becomes a path, so it is matched rather than merely sanitized."""
    with pytest.raises(LeanRunUnknown):
        reclaim_lean_run(handle)


def test_a_handle_whose_run_directory_is_gone_says_so(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_instant_lean(tmp_path),
    )["handle"]
    _settle(handle)
    reclaim_lean_run(handle)

    with pytest.raises(LeanRunUnknown, match="submit again"):
        reclaim_lean_run(handle)


# -- status ------------------------------------------------------------------

def test_status_derives_what_it_reports_and_changes_nothing(
    tmp_path: Path,
    capsys,
) -> None:
    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    handle = submit_lean_run(
        source,
        statement_fidelity=fidelity,
        project_root=root,
        lean_bin=_instant_lean(tmp_path),
    )["handle"]
    _settle(handle)

    assert main(["status"]) == 0
    reported = json.loads(capsys.readouterr().out)["runs"]
    assert [run["handle"] for run in reported] == [handle]
    assert reported[0]["state"] == "finished"
    assert reported[0]["source"] == str(source)

    # Reading it neither publishes nor consumes anything.
    assert _artifacts(root) == {"Main.lean", "statement_fidelity.md"}
    assert main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["state"] == "finished"
    assert reclaim_lean_run(handle)["status"] == "success"


# -- the claim ledger --------------------------------------------------------

def _claimed(root: Path, source: Path, capsys) -> None:
    from argus_skill.verticals.math.math_state import main as state_main

    state_main([
        "context", "--project-root", str(root),
        "--id", "ctx", "--statement", "Natural numbers.",
    ])
    state_main([
        "claim", "--project-root", str(root),
        "--id", "C1", "--context", "ctx",
        "--statement", "addition of naturals is commutative",
        "--formal-file", str(source),
    ])
    capsys.readouterr()


def test_reclaim_writes_the_certificate_and_the_ledger_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    """``--claim`` is given once, at submit, and honoured once, at reclaim."""
    from argus_skill.proof_ledger import load_state

    lean_bin = _instant_lean(tmp_path)

    synchronous = _project(tmp_path / "sync")
    sync_source, sync_fidelity = _formalized(synchronous)
    _claimed(synchronous, sync_source, capsys)
    assert main([
        "verify", str(sync_source), "--statement-fidelity", str(sync_fidelity),
        "--claim", "C1", "--project-root", str(synchronous), "--lean-bin", lean_bin,
    ]) == 0
    capsys.readouterr()

    asynchronous = _project(tmp_path / "async")
    async_source, async_fidelity = _formalized(asynchronous)
    _claimed(asynchronous, async_source, capsys)
    assert main([
        "submit", str(async_source), "--statement-fidelity", str(async_fidelity),
        "--claim", "C1", "--project-root", str(asynchronous), "--lean-bin", lean_bin,
    ]) == 0
    handle = json.loads(capsys.readouterr().out)["handle"]
    _settle(handle)
    assert main(["reclaim", handle]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["kernel"]["recorded"] is not None
    assert _artifacts(asynchronous) == _artifacts(synchronous)
    assert (asynchronous / "research" / "lean" / "certificates").is_dir()
    evidence = load_state(asynchronous).evidence
    assert len(evidence) == len(load_state(synchronous).evidence) == 1
    assert validate_lean_evidence(asynchronous).issues == ()


def test_a_reclaim_refused_for_drift_records_nothing_in_the_claim_ledger(
    tmp_path: Path,
    capsys,
) -> None:
    """The refusal has to reach the ledger, not only the artifact directory."""
    from argus_skill.proof_ledger import load_state

    root = _project(tmp_path / "p")
    source, fidelity = _formalized(root)
    _claimed(root, source, capsys)
    assert main([
        "submit", str(source), "--statement-fidelity", str(fidelity),
        "--claim", "C1", "--project-root", str(root),
        "--lean-bin", _instant_lean(tmp_path),
    ]) == 0
    handle = json.loads(capsys.readouterr().out)["handle"]
    _settle(handle)

    source.write_text(SWAPPED_THEOREM, encoding="utf-8")

    assert main(["reclaim", handle]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "changed while the compile was outstanding" in payload["error"]
    assert not (_lean_dir(root) / "lean_check.json").exists()
    assert not (_lean_dir(root) / "compile.log").exists()
    assert not (_lean_dir(root) / "certificates").exists()
    assert not load_state(root).evidence
