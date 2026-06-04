"""Project-local experiment IO contract — standalone, no argus_skill dependency.

This helper implements the run-directory artifact contract the L2 reviewer
checks, so your experiment worker never has to re-implement manifest/status/
progress/STOP/row bookkeeping. It does NO model loading and runs NO inference:
you call your approved framework (TRL, LLaMA-Factory, vLLM, the API route in
``llm.py``, ...) and feed each result here to be recorded consistently.

A run directory ends up with::

    experiments/runs/<run_id>/
        manifest.json        # written before the first expensive call
        status.json          # atomically updated: state + per-method counts
        progress.jsonl       # one line before and after every trial
        results.jsonl        # raw scored rows (method, task_id, score, ...)
        stdout.log           # your worker's own logs (optional)
        STOP                 # operator-created cancellation flag (you check it)

Minimal worker loop::

    import experiment_io, gpu_env
    gpu_env.configure_caches()
    tasks = list(experiment_io.read_tasks("benchmarks/full/tasks.jsonl"))
    with experiment_io.RunWriter(
        "experiments/runs/main", method="proposed",
        manifest={"benchmark": "gaia", "model": "qwen2.5-7b", "n_tasks": len(tasks)},
    ) as run:
        for task in tasks:
            run.raise_if_stopped()
            prediction = call_your_framework(task)        # <- you provide this
            run.record(task_id=task["id"], prediction=prediction,
                       score=score(prediction, task.get("gold")))
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Iterator, Literal

# Field names the reviewer's full-scale evidence check accepts for each row.
METHOD_FIELDS = ("method", "condition", "variant", "baseline", "condition_name")
TASK_ID_FIELDS = ("task_id", "episode_id", "sample_id", "example_id", "id")
SCORE_FIELDS = ("success", "score", "verdict", "prediction", "answer", "output")

STOP_FILENAME = "STOP"
CANCELLED_SENTINEL = "run_cancelled"
CANCELLED_EXIT_CODE = 130


def read_tasks(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    """Yield task objects from a JSONL benchmark file."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                yield obj


def atomic_write_json(path: str | os.PathLike[str], obj: Any) -> None:
    """Write ``obj`` as JSON to ``path`` atomically (write temp + os.replace)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def count_rows_by_method(results_path: str | os.PathLike[str]) -> dict[str, int]:
    """Count distinct task ids per method in a results JSONL file.

    Mirrors how the reviewer counts full-scale evidence, so you can self-audit a
    run before claiming it complete.
    """
    seen: dict[str, set[str]] = {}
    path = Path(results_path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            method = _first_field(row, METHOD_FIELDS) or "unknown"
            task_id = _first_field(row, TASK_ID_FIELDS)
            if task_id is None:
                continue
            seen.setdefault(str(method), set()).add(str(task_id))
    return {method: len(ids) for method, ids in seen.items()}


def _first_field(row: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        if field in row and str(row[field]).strip() != "":
            return row[field]
    return None


class RunCancelled(RuntimeError):
    """Raised internally when a STOP file is observed; converted to exit 130."""


class RunWriter:
    """Writes the reviewer-compatible run-directory artifact contract.

    One ``RunWriter`` records a single method/condition. Use several (or one per
    sub-agent job) to cover a full method x benchmark matrix.
    """

    def __init__(
        self,
        run_dir: str | os.PathLike[str],
        *,
        method: str,
        manifest: dict[str, Any] | None = None,
        results_filename: str = "results.jsonl",
        stop_check_interval_seconds: float = 30.0,
        echo: bool = True,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.method = method
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.results_path = self.run_dir / results_filename
        self.progress_path = self.run_dir / "progress.jsonl"
        self.status_path = self.run_dir / "status.json"
        self.manifest_path = self.run_dir / "manifest.json"
        self.stop_path = self.run_dir / STOP_FILENAME
        self._echo = echo
        self._stop_interval = stop_check_interval_seconds
        self._last_stop_check = 0.0
        self._completed = 0
        self._started_at = time.time()
        self._closed = False

        manifest_data = dict(manifest or {})
        manifest_data.setdefault("method", method)
        manifest_data.setdefault("created_at", self._started_at)
        atomic_write_json(self.manifest_path, manifest_data)
        self._progress = self.progress_path.open("a", encoding="utf-8")
        self._write_status("running")

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "RunWriter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        if exc_type is RunCancelled:
            self._finalize("cancelled")
            self._close()
            sys.exit(CANCELLED_EXIT_CODE)
        if exc_type is not None:
            self._finalize("failed", error=f"{exc_type.__name__}: {exc}")
            self._close()
            return False
        if not self._closed:
            self.finish("completed")
        return False

    # -- STOP handling ---------------------------------------------------
    def stop_requested(self) -> bool:
        """True if the operator created a ``STOP`` file in the run directory."""
        return self.stop_path.exists()

    def raise_if_stopped(self, force: bool = False) -> None:
        """Check for STOP (rate-limited) and abort the run if present.

        Call this before every expensive model/API call. On cancellation it
        writes ``run_cancelled``, sets status ``cancelled``, and exits 130 — the
        contract the benchmark-runner skill expects.
        """
        now = time.time()
        if not force and (now - self._last_stop_check) < self._stop_interval:
            return
        self._last_stop_check = now
        if self.stop_path.exists():
            raise RunCancelled(CANCELLED_SENTINEL)

    # -- recording -------------------------------------------------------
    def start_task(self, task_id: str) -> None:
        self._append_progress({"event": "start", "task_id": str(task_id)})

    def record(self, *, task_id: str, score: Any = None, **fields: Any) -> None:
        """Append one raw scored row + a progress line; update status atomically.

        ``fields`` may carry ``prediction``/``answer``/``output``/``success`` etc.
        A ``method`` and ``task_id`` are always written so the row satisfies the
        full-scale evidence schema.
        """
        row: dict[str, Any] = {"method": self.method, "task_id": str(task_id)}
        if score is not None:
            row["score"] = score
        row.update(fields)
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._completed += 1
        passed = _coerce_pass(score if score is not None else fields.get("success"))
        self._append_progress(
            {"event": "done", "task_id": str(task_id), "pass": passed}
        )
        self._write_status("running")
        if self._echo:
            suffix = "" if passed is None else f" pass={passed}"
            print(f"[run] {self._completed} {self.method} {task_id} done{suffix}", flush=True)

    # -- lifecycle -------------------------------------------------------
    def finish(self, state: str = "completed") -> None:
        self._finalize(state)
        self._close()

    def _finalize(self, state: str, error: str | None = None) -> None:
        if state == "cancelled":
            (self.run_dir / CANCELLED_SENTINEL).write_text("", encoding="utf-8")
        self._write_status(state, error=error)

    def _write_status(self, state: str, error: str | None = None) -> None:
        status = {
            "state": state,
            "method": self.method,
            "task_count": self._completed,
            "rows_by_method": count_rows_by_method(self.results_path),
            "updated_at": time.time(),
            "elapsed_seconds": round(time.time() - self._started_at, 1),
        }
        if error:
            status["error"] = error
        atomic_write_json(self.status_path, status)

    def _append_progress(self, payload: dict[str, Any]) -> None:
        payload = {"ts": time.time(), "method": self.method, **payload}
        self._progress.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._progress.flush()
        os.fsync(self._progress.fileno())

    def _close(self) -> None:
        if not self._closed:
            try:
                self._progress.close()
            except Exception:  # pragma: no cover - best effort
                pass
            self._closed = True


def validate_run(run_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Return a self-audit summary of a run directory's contract compliance."""
    base = Path(run_dir)
    results = base / "results.jsonl"
    summary: dict[str, Any] = {
        "run_dir": str(base),
        "has_manifest": (base / "manifest.json").exists(),
        "has_status": (base / "status.json").exists(),
        "has_progress": (base / "progress.jsonl").exists(),
        "has_results": results.exists(),
        "rows_by_method": count_rows_by_method(results),
        "cancelled": (base / CANCELLED_SENTINEL).exists(),
    }
    status_path = base / "status.json"
    if status_path.exists():
        try:
            summary["state"] = json.loads(status_path.read_text(encoding="utf-8")).get("state")
        except (OSError, json.JSONDecodeError):
            summary["state"] = "unreadable"
    missing = [
        key
        for key in ("has_manifest", "has_status", "has_progress", "has_results")
        if not summary[key]
    ]
    summary["complete_contract"] = not missing
    summary["missing_artifacts"] = [key.removeprefix("has_") for key in missing]
    return summary


def _coerce_pass(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return 1 if value >= 0.5 else 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"pass", "true", "success", "correct", "1"}:
            return 1
        if lowered in {"fail", "false", "incorrect", "0"}:
            return 0
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Experiment run-directory utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    p_validate = sub.add_parser("validate", help="Audit a run directory's contract")
    p_validate.add_argument("run_dir")
    args = parser.parse_args(argv)
    if args.command == "validate":
        print(json.dumps(validate_run(args.run_dir), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
