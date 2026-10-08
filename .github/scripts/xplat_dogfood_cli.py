"""Run a bounded CLI project end to end on the memory backend.

Every attempt runs with ``ARGUS_SKILL_RUNNER_BACKEND=memory`` (no
credentials) in a fresh work directory and an isolated ``--life-dir``. Each
command is time-boxed; exit codes, output and every ``events.jsonl`` produced
are saved to the output directory (argv[1]). Never fails the step itself.

Attempts, in order:
1. ``--daemon-fg --new --continuous --bounded --objective ...`` — the bounded
   one-shot mission (``--objective`` requires ``--continuous``).
2. ``--daemon-fg --new --bounded`` — an idle bounded daemon, stopped at the
   time box, to see whether the worker itself boots on this OS.
3. ``--ask hello`` and ``--status`` against the same life dir.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
BASE = Path(os.environ.get("RUNNER_TEMP") or str(OUT)) / "argus-cli-e2e"
HOME = BASE / "home"
WORK = BASE / "project"
HOME.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)
(WORK / "README.md").write_text("# dogfood\n\nA tiny project for the xplat dogfood run.\n", encoding="utf-8")

ARGUS = shutil.which("argus") or "argus"
ENV = dict(os.environ, ARGUS_SKILL_RUNNER_BACKEND="memory", PYTHONIOENCODING="utf-8")
OBJECTIVE = "Add a hello.py that prints hello world, then stop."
ATTEMPTS = [
    ("bounded_objective", ["--daemon-fg", "--new", "--continuous", "--bounded", "--objective", OBJECTIVE], 420),
    ("bounded_idle_daemon", ["--daemon-fg", "--new", "--bounded"], 60),
    ("ask_hello", ["--ask", "hello"], 120),
    ("status", ["--status"], 60),
]


def run(name: str, extra: list[str], limit: float) -> dict:
    cmd = [ARGUS, *extra, "--life-dir", str(HOME)]
    t0 = time.time()
    timed_out = False
    with open(OUT / f"cli_{name}.log", "wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=ENV, cwd=str(WORK))
        try:
            code = proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait()
    result = {"cmd": cmd, "exit_code": code, "timed_out": timed_out, "seconds": round(time.time() - t0, 1)}
    print(f"{name}: {json.dumps(result)}", flush=True)
    return result


report = {"attempts": {}}
for name, extra, limit in ATTEMPTS:
    report["attempts"][name] = run(name, extra, limit)

events = []
for path in BASE.rglob("events.jsonl"):
    dest = OUT / ("events__" + "__".join(path.relative_to(BASE).parts))
    shutil.copyfile(path, dest)
    with open(path, "rb") as fh:
        events.append({"src": str(path), "copied_to": dest.name, "lines": sum(1 for _ in fh)})
listing = sorted(str(p.relative_to(BASE)) for p in BASE.rglob("*") if p.is_file())
(OUT / "cli_tree.txt").write_text("\n".join(listing), encoding="utf-8")
report["events_files"] = events
report["hello_py_exists"] = (WORK / "hello.py").exists()
(OUT / "cli_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
