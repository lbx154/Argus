"""Compiling Lean without holding the mathematician still.

``verify`` in :mod:`.lean_evidence` blocks its caller for the whole compile. On
a source that imports Mathlib that is minutes, and the caller is a reasoning
agent, so the cost is minutes of a mathematician doing nothing. This module adds
the other shape of the same step: ``submit`` starts the compile and returns a
handle, ``reclaim`` turns that handle into exactly the records ``verify`` would
have written.

Nothing here weakens the rule ``verify`` enforces, and one part of it has to be
*stronger*, because the window is wider. Two separate mechanisms, both required:

**The compile reads a snapshot.** ``submit`` copies the canonical source and the
statement fidelity document into a per-run staging directory and compiles the
copy. This is not a convenience. A compiler launched against a live file reads
that file when it gets to it, not when it was launched — measured, not
assumed — so an in-place background compile answers about whatever text the
Engineer last saved, while the digest stamped at submit names the text that was
there before. That pair is a forgery of precisely the kind
``_refuse_if_moved_under_the_compiler`` exists to prevent, and stretching the
window from "one compile" to "however long the agent was away" makes it the
normal case rather than a race. Compiling a copy makes the answer be about a
fixed text by construction.

**Publication still checks the live files.** A fixed text is not the same as a
current one. ``reclaim`` re-reads the project's own source and fidelity document
and refuses if either no longer matches what was compiled — nothing published,
no result, no log, no certificate, no ledger evidence, and the remedy is to
submit again. So the snapshot buys the freedom to keep editing while a compile
runs; it does not buy the right to publish an answer about text the project no
longer carries.

The third distinction this module has to keep is the one the rest of the
vertical keeps everywhere: an environment failure is worded differently from a
broken proof. A background process that died without writing an answer produced
*no verdict at all*, so ``reclaim`` says the run was lost and writes nothing. It
must never be able to reach a reader as ``lean_compile_failed``.

Process death is allowed to cost one recompile. There is no daemon, no timer,
and no reconciliation: a handle is a directory on disk, and every question about
it is answered by looking.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ...core.daemon_lock import is_pid_running
from ...core.file_lock import exclusive_file_lock
from .lean_evidence import (
    _STATEMENT_FIDELITY_NAME,
    CompiledArtifactChangedError,
    _digest_fidelity,
    _digest_file,
    _sha256,
    classify_environment_failure,
)

__all__ = [
    "LeanRunLost",
    "LeanRunUnknown",
    "outstanding_runs",
    "reclaim_lean_run",
    "submit_lean_run",
]

#: Where run directories live. Deliberately *outside* the project: a staged
#: ``Main.lean`` inside it would be found by ``discover_lean_sources`` and
#: judged as a second, resultless formalization, and the only way to stop that
#: would be to teach discovery to skip a directory name — which is how
#: "put the proof in build/" became "have no proof" the last time this vertical
#: tried it. Outside the tree there is nothing to skip and nothing to hide in.
_RUNS_DIR_ENV = "ARGUS_SKILL_LEAN_RUNS"

_RUN_RECORD = "run.json"
_OUTCOME = "outcome.json"
_WORKER_LOCK = "worker.lock"
_WORKER_LOG = "worker.log"

_SCHEMA_VERSION = 1

#: Machine-minted, so a handle can be quoted into a later command without
#: quoting anything else with it. Validated on the way back in, because a
#: handle is turned into a path.
_HANDLE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")


class LeanRunUnknown(ValueError):
    """No run by that handle, so there is nothing to answer with."""


class LeanRunLost(ValueError):
    """The background compile ended without producing an answer.

    Deliberately not a compiler verdict and deliberately not a status. The
    process is gone and wrote nothing, so what is known is that *no* compile
    result exists — which is a fact about this host, not about the mathematics.
    Reporting it as ``lean_compile_failed`` would let a killed process read as a
    broken proof, and the whole vertical is built on that distinction surviving.
    """


# -- run directories ---------------------------------------------------------

def _runs_root() -> Path:
    configured = os.environ.get(_RUNS_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".local" / "state" / "argus-skill" / "lean-runs").resolve()


def _new_handle() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:8]}"


def _run_dir(handle: str) -> Path:
    """The directory a handle names, or a refusal.

    A handle arrives as a command-line string and is used to build a path, so
    it is matched against the exact shape this module mints rather than merely
    checked for ``..``.
    """
    token = str(handle or "").strip()
    if not _HANDLE.match(token):
        raise LeanRunUnknown(
            f"{token!r} is not a Lean run handle; a handle is what `submit` "
            "printed, of the form 20260814T101500-0f3a9c21"
        )
    return _runs_root() / token


def _read_run(run_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((run_dir / _RUN_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LeanRunUnknown(
            f"no Lean run is recorded at {run_dir} ({exc}); a handle does not "
            "survive the run directory being removed, so submit again"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise LeanRunUnknown(
            f"the run record at {run_dir} is not readable as a Lean run; "
            "submit again"
        )
    return payload


def _read_outcome(run_dir: Path) -> dict[str, Any] | None:
    """What the worker published, or ``None`` when it has published nothing.

    An unparseable outcome is treated as absent rather than as an error: the
    file is written atomically, so an unreadable one means the worker died
    mid-write, and "the run produced no answer" is exactly what that is.
    """
    try:
        payload = json.loads((run_dir / _OUTCOME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _discard(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)


# -- is the worker still there -----------------------------------------------

def _worker_alive(run_dir: Path, record: dict[str, Any]) -> bool:
    """Whether a worker is still running, without a daemon watching for one.

    The worker holds an exclusive lock on ``worker.lock`` for its whole life and
    writes its pid into it once held, so a non-blocking probe of that lock is an
    exact answer that survives pid reuse. The pid is only consulted for the
    milliseconds between ``Popen`` returning and the child reaching the lock,
    where the lock is free but the run has certainly not been lost.
    """
    lock_path = run_dir / _WORKER_LOCK
    try:
        started = lock_path.stat().st_size > 0
    except OSError:
        started = False
    if not started:
        pid = int(record.get("pid") or 0)
        return pid > 0 and is_pid_running(pid)
    try:
        with lock_path.open("a+b") as handle:
            with exclusive_file_lock(
                handle, timeout_seconds=0.0, lock_name="Lean run lock"
            ):
                return False
    except TimeoutError:
        return True
    except OSError:
        return False


def _state(run_dir: Path, record: dict[str, Any]) -> str:
    if (run_dir / _OUTCOME).is_file():
        return "finished"
    return "running" if _worker_alive(run_dir, record) else "lost"


# -- drift -------------------------------------------------------------------

def _moved(label: str, path: Path, before: str, after: str | None) -> str | None:
    if after == before:
        return None
    if after is None:
        return f"{label} ({path}) could no longer be read"
    return f"{label} ({path}) changed while the compile was outstanding"


def _refuse_if_moved_since_submit(record: dict[str, Any]) -> None:
    """Refuse to publish an answer about text the project no longer carries.

    The backstop the snapshot does not replace. The compile is honest about the
    bytes it read either way — that is what staging bought — but a certificate
    is a statement about the file the project has now, and re-verifying is the
    only thing that makes those two the same file again.
    """
    source = Path(str(record.get("canonical_source") or ""))
    fidelity = Path(str(record.get("canonical_fidelity") or ""))
    reasons = [
        reason
        for reason in (
            _moved(
                "the Lean source",
                source,
                str(record.get("source_sha256") or ""),
                _digest_file(source),
            ),
            _moved(
                f"the {_STATEMENT_FIDELITY_NAME}",
                fidelity,
                str(record.get("statement_fidelity_sha256") or ""),
                _digest_fidelity(fidelity),
            ),
        )
        if reason
    ]
    if not reasons:
        return
    raise CompiledArtifactChangedError(
        "; ".join(reasons)
        + ". The compile is about the text this project carried when the run "
        "was submitted, so nothing was recorded — no result, no compile log, "
        "no claim evidence. The staged run has been discarded; submit it again "
        "against the file you have now."
    )


def _staged_drift(record: dict[str, Any]) -> str | None:
    """Whether the snapshot itself moved, which would make the copy pointless."""
    staged_source = Path(str(record.get("staged_source") or ""))
    staged_fidelity = Path(str(record.get("staged_fidelity") or ""))
    for label, path, before, after in (
        (
            "the staged Lean source",
            staged_source,
            str(record.get("source_sha256") or ""),
            _digest_file(staged_source),
        ),
        (
            f"the staged {_STATEMENT_FIDELITY_NAME}",
            staged_fidelity,
            str(record.get("statement_fidelity_sha256") or ""),
            _digest_fidelity(staged_fidelity),
        ),
    ):
        if after != before:
            return (
                f"{label} ({path}) was modified while the compiler was reading "
                "it, so the run has no fixed text to be an answer about"
            )
    return None


# -- submit ------------------------------------------------------------------

def submit_lean_run(
    source: Path | str,
    *,
    statement_fidelity: Path | str,
    artifact_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    claim: str = "",
    timeout_seconds: float = 30.0,
    lean_bin: str | None = None,
    lake_bin: str | None = None,
    use_lake: bool | None = None,
) -> dict[str, Any]:
    """Start the compile ``verify`` would run and return a handle for it.

    Everything ``verify_lean_source`` settles before calling the compiler is
    settled here, in the same order and under the same artifact lock: the
    canonical artifacts are materialized, the Lake decision is made from the
    host, and both digests are taken from the bytes that are about to be
    compiled. What differs is where the compiler reads: a copy in a run
    directory, so the text it sees cannot move.

    The run directory sits outside the project. That has a cost — the copy's
    Lake workspace is resolved from its own parents, and a project carrying its
    own ``lakefile.toml`` above the source would compile in a different search
    path than ``verify`` would. Rather than let that silently become a
    ``missing_dependency`` verdict aimed at someone who already has the library,
    a run whose copy would resolve differently is refused here and sent to
    ``verify``.
    """
    from ...tools.lean_check import (  # noqa: PLC0415 — optional, heavy import
        _artifact_directory_lock,
        _atomic_artifact_write,
        _resolve_lake_workspace,
        prepare_canonical_lean_artifacts,
    )

    source_path = Path(str(source)).expanduser().resolve()
    root = (
        Path(str(artifact_dir)).expanduser().resolve()
        if artifact_dir is not None
        else source_path.parent
    )
    project = Path(str(project_root if project_root is not None else ".")).expanduser().resolve()
    handle = _new_handle()
    run_dir = _runs_root() / handle

    with _artifact_directory_lock(root):
        canonical_source, canonical_fidelity = prepare_canonical_lean_artifacts(
            source_path,
            root,
            statement_fidelity,
        )
        workspace = _resolve_lake_workspace(canonical_source)
        through_lake = workspace is not None if use_lake is None else use_lake
        compiled_bytes = canonical_source.read_bytes()
        compiled_fidelity = canonical_fidelity.read_text(encoding="utf-8")
        source_digest = _sha256(compiled_bytes)
        fidelity_digest = _sha256(compiled_fidelity.encode("utf-8"))

        run_dir.parent.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir()
        # Same names the compiler would have seen in place. `run_lean_check`
        # only ever passes the path it is given to the executable, so the name
        # is not load-bearing for it — but a compile log naming `Main.lean` and
        # a certificate naming `Main.lean` should be talking about one file.
        staged_source = run_dir / canonical_source.name
        staged_fidelity = run_dir / canonical_fidelity.name
        try:
            _atomic_artifact_write(staged_source, compiled_bytes)
            _atomic_artifact_write(
                staged_fidelity, compiled_fidelity.encode("utf-8")
            )
            staged_workspace = _resolve_lake_workspace(staged_source)
            if staged_workspace != workspace:
                raise ValueError(
                    "this project resolves its own Lake workspace "
                    f"({workspace}), and a staged copy outside the project "
                    f"resolves {staged_workspace or 'none'} instead, so the "
                    "background compile would search a different path than "
                    "`verify` does. Use `verify` for this project"
                )
            record: dict[str, Any] = {
                "schema_version": _SCHEMA_VERSION,
                "handle": handle,
                "submitted_at": time.time(),
                "artifact_dir": str(root),
                "project_root": str(project),
                "claim": str(claim or ""),
                "canonical_source": str(canonical_source),
                "canonical_fidelity": str(canonical_fidelity),
                "staged_source": str(staged_source),
                "staged_fidelity": str(staged_fidelity),
                "source_sha256": source_digest,
                "statement_fidelity_sha256": fidelity_digest,
                "use_lake": bool(through_lake),
                "lake_workspace": str(workspace) if through_lake and workspace else "",
                "timeout_seconds": float(timeout_seconds),
                "lean_bin": lean_bin,
                "lake_bin": lake_bin,
                "pid": 0,
            }
            _write_run(run_dir, record)
            record["pid"] = _launch_worker(run_dir)
            _write_run(run_dir, record)
        except BaseException:
            # A submit that did not start a compile must not leave a handle
            # behind that a later `reclaim` would have to call lost.
            _discard(run_dir)
            raise

    return {
        "handle": handle,
        "state": "running",
        "source": str(canonical_source),
        "statement_fidelity": str(canonical_fidelity),
        "claim": record["claim"],
        "run_dir": str(run_dir),
        "reclaim_with": (
            "python -m argus_skill.verticals.math.lean_evidence reclaim " + handle
        ),
    }


def _write_run(run_dir: Path, record: dict[str, Any]) -> None:
    from ...tools.lean_check import _atomic_artifact_write  # noqa: PLC0415

    _atomic_artifact_write(
        run_dir / _RUN_RECORD,
        (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _launch_worker(run_dir: Path) -> int:
    """Start the compile in its own session so it outlives this process.

    ``argus_skill.tools.subagent._registry._launch_durable_command`` does the
    same job and is better tested, and it is not reused here for three reasons
    that are all about it being the *subagent* launcher rather than a generic
    one: its exit sidecar goes to ``Path(".argus_subagents")``, a path relative
    to the caller's working directory, so a ``reclaim`` run from a different
    directory would look for it somewhere else; it runs its command through
    ``bash -lc`` on a single string, which would put every path here through
    shell quoting for no gain; and the records that make it observable live in
    the directory ``scan_external_work`` reads, where a Lean compile would
    surface in the Engineer's external-work advisory as a subagent needing
    attention. A compile is not delegated work and has no supervisor to answer
    for it. The one idea worth borrowing — a terminal record written by the
    child itself, so the answer survives losing its owner — is what
    ``outcome.json`` is.
    """
    package_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{existing}" if existing else str(package_root)
    )
    with (run_dir / _WORKER_LOG).open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "argus_skill.verticals.math.lean_async",
                str(run_dir),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(run_dir),
            start_new_session=os.name != "nt",
            env=env,
        )
    return int(process.pid)


# -- the worker --------------------------------------------------------------

def _worker(run_dir: Path) -> int:
    """Compile the snapshot and publish one terminal record, or none.

    Anything this does not catch leaves no ``outcome.json``, and ``reclaim``
    calls that lost. That is the right default: a worker that crashed produced
    no compiler verdict, and inventing one would be the exact confusion this
    module exists to avoid.
    """
    from ...tools.lean_check import (  # noqa: PLC0415 — optional, heavy import
        _atomic_artifact_write,
        run_lean_check,
    )

    record = _read_run(run_dir)
    lock_path = run_dir / _WORKER_LOCK
    with lock_path.open("a+b") as handle:
        with exclusive_file_lock(handle, lock_name="Lean run lock"):
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n".encode("utf-8"))
            handle.flush()
            try:
                result = run_lean_check(
                    Path(str(record["staged_source"])),
                    timeout_seconds=float(record["timeout_seconds"]),
                    lean_bin=record.get("lean_bin"),
                    lake_bin=record.get("lake_bin"),
                    use_lake=bool(record["use_lake"]),
                )
            except (OSError, ValueError) as exc:
                outcome: dict[str, Any] = {
                    "ok": False,
                    "kind": "lost",
                    "error": f"the Lean run could not be carried out: {exc}",
                }
            else:
                drift = _staged_drift(record)
                outcome = (
                    {"ok": False, "kind": "changed", "error": drift}
                    if drift
                    else {"ok": True, "result": result}
                )
            _atomic_artifact_write(
                run_dir / _OUTCOME,
                (json.dumps(outcome, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
            )
    return 0


# -- reclaim -----------------------------------------------------------------

def reclaim_lean_run(handle: str) -> dict[str, Any]:
    """Turn a finished run into the records ``verify`` would have written.

    Publishes nothing at all unless it publishes everything: the refusal checks
    come before the first field is assigned and before either artifact write,
    the same ordering ``verify_lean_source`` uses, because a half-written
    certificate is worse than none.

    A run that is still compiling is reported as such and left alone. A run
    whose worker is gone without an answer raises :class:`LeanRunLost`.
    """
    from ...tools.lean_check import (  # noqa: PLC0415 — optional, heavy import
        COMPILE_LOG,
        LEAN_CHECK_RESULT,
        _artifact_directory_lock,
        _atomic_artifact_write,
        render_compile_log,
    )

    run_dir = _run_dir(handle)
    record = _read_run(run_dir)
    outcome = _read_outcome(run_dir)

    if outcome is None:
        if _worker_alive(run_dir, record):
            return {
                "handle": handle,
                "state": "running",
                "source": record.get("canonical_source", ""),
                "claim": record.get("claim", ""),
                "message": (
                    "the compile is still running, so nothing was written; "
                    "reclaim the same handle again when you next surface"
                ),
            }
        _discard(run_dir)
        raise LeanRunLost(
            f"the background compile for {handle} ended without recording an "
            "answer, so this run has no compiler verdict at all — it is an "
            "environment failure, not a statement about the proof. Nothing was "
            f"written. The transcript, if any, was at {run_dir / _WORKER_LOG}; "
            "submit the run again."
        )

    if not outcome.get("ok"):
        detail = str(outcome.get("error") or "the run recorded no reason")
        _discard(run_dir)
        if outcome.get("kind") == "changed":
            raise CompiledArtifactChangedError(
                f"{detail}. Nothing was recorded — no result, no compile log, "
                "no claim evidence. Submit the run again."
            )
        raise LeanRunLost(
            f"the background compile for {handle} produced no compiler verdict: "
            f"{detail}. This is an environment failure, not a statement about "
            "the proof, and nothing was written. Submit the run again."
        )

    result = outcome.get("result")
    if not isinstance(result, dict):
        _discard(run_dir)
        raise LeanRunLost(
            f"the background compile for {handle} recorded an answer that is "
            "not a compiler result, so there is no verdict to publish and "
            "nothing was written. Submit the run again."
        )

    root = Path(str(record["artifact_dir"]))
    canonical_source = Path(str(record["canonical_source"]))
    canonical_fidelity = Path(str(record["canonical_fidelity"]))

    with _artifact_directory_lock(root):
        # Both guards, in this order, before anything is assigned or written:
        # the snapshot has to be the text that was compiled, and the project
        # has to still carry that text.
        staged = _staged_drift(record)
        if staged is not None:
            _discard(run_dir)
            raise CompiledArtifactChangedError(
                f"{staged}. Nothing was recorded — no result, no compile log, "
                "no claim evidence. Submit the run again."
            )
        try:
            _refuse_if_moved_since_submit(record)
        except CompiledArtifactChangedError:
            # The run can never become publishable: the text it answers about is
            # not the text the project has, and no later reclaim of this handle
            # could change that. Leaving it on disk would be a staging directory
            # that accumulates with nothing that would ever clear it.
            _discard(run_dir)
            raise

        published = dict(result)
        # The compiler read the copy; the copy is byte-identical to the file
        # named here, which is what the two digest checks above just
        # established. So the result is about `canonical_source`, and saying so
        # is what lets the certificate, the gate, and the claim ledger cite an
        # artifact a reader of the repository can open. `command` and `cwd` are
        # left as the compiler reported them, because a transcript that names a
        # directory the compile did not run in is not a transcript, and
        # `compiled_copy` says where it did run.
        published["source"] = str(canonical_source)
        published["compiled_copy"] = str(record["staged_source"])
        published["source_sha256"] = str(record["source_sha256"])
        published["statement_fidelity"] = str(canonical_fidelity)
        published["statement_fidelity_sha256"] = str(
            record["statement_fidelity_sha256"]
        )
        published["lake_workspace"] = str(record.get("lake_workspace") or "")
        published["environment_failure"] = classify_environment_failure(published)
        _atomic_artifact_write(
            root / LEAN_CHECK_RESULT,
            (json.dumps(published, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
        _atomic_artifact_write(
            root / COMPILE_LOG,
            render_compile_log(published).encode("utf-8"),
        )

    payload: dict[str, Any] = dict(published)
    payload["handle"] = handle
    payload["state"] = "reclaimed"
    claim = str(record.get("claim") or "")
    if claim:
        from .math_state import (  # noqa: PLC0415 — avoids an import cycle
            record_lean_evidence,
        )

        recording = record_lean_evidence(
            Path(str(record["project_root"])),
            claim_id=claim,
            source=canonical_source,
            expect_result=published,
        )
        payload["kernel"] = recording.as_dict()
    _discard(run_dir)
    return payload


# -- status ------------------------------------------------------------------

def outstanding_runs() -> list[dict[str, Any]]:
    """Every run directory on this host and what it currently is.

    Derived by looking, never stored. There is no list of outstanding runs to
    fall out of date, and reading this changes nothing — including the runs it
    reports as lost, which stay until someone reclaims them and is told so.
    """
    root = _runs_root()
    try:
        candidates = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        return []
    runs: list[dict[str, Any]] = []
    for path in candidates:
        if not _HANDLE.match(path.name):
            continue
        try:
            record = _read_run(path)
        except LeanRunUnknown:
            continue
        runs.append(
            {
                "handle": path.name,
                "state": _state(path, record),
                "source": record.get("canonical_source", ""),
                "claim": record.get("claim", ""),
                "submitted_at": record.get("submitted_at", 0.0),
                "age_seconds": round(
                    max(0.0, time.time() - float(record.get("submitted_at") or 0.0)), 1
                ),
                "run_dir": str(path),
            }
        )
    return runs


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(_worker(Path(sys.argv[1])))
