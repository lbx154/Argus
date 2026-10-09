"""Use Argus from the command line end to end (xplat-dogfood workflow).

The backend is whatever CLI is on PATH: the workflow installs a
credential-free fake ``codex`` there, so the Manager, Planner, Engineer and
Reviewer all run as real child processes. Each attempt gets a fresh project
directory and an isolated ``--life-dir``, is time-boxed, and has its exit
code, output and ``events.jsonl`` saved to the output directory (argv[1]).
Never fails the step itself.

Attempts, in order:
1. ``--ask 你好`` — one-shot Manager answer.
2. ``--daemon-fg --new --continuous --bounded --objective ...`` — a bounded
   foreground mission that should write and run ``hello.py`` and exit.
3. ``--daemon --new --continuous --bounded --objective ...`` — the same in a
   detached background daemon (the OS spawn path), polled with ``--status``.
4. ``--status`` and ``--daemon-stop``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
BASE = Path(os.environ.get("RUNNER_TEMP") or str(OUT)) / "argus-cli-e2e"
ARGUS = shutil.which("argus") or "argus"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
REPORT: dict = {"argus": ARGUS, "backend_bin": shutil.which("codex"), "attempts": {}}


def run(name: str, extra: list[str], limit: float, *, home: Path, work: Path) -> dict:
    cmd = [ARGUS, *extra, "--life-dir", str(home)]
    t0 = time.time()
    timed_out = False
    with open(OUT / f"cli_{name}.log", "wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=ENV, cwd=str(work))
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
    output = (OUT / f"cli_{name}.log").read_text(encoding="utf-8", errors="replace")
    result = {"cmd": cmd, "exit_code": code, "timed_out": timed_out,
              "seconds": round(time.time() - t0, 1), "tail": output[-1200:]}
    REPORT["attempts"][name] = result
    print(f"{name}: {json.dumps({k: v for k, v in result.items() if k != 'tail'})}", flush=True)
    print(output[-1200:], flush=True)
    return result


def fresh(name: str) -> tuple[Path, Path]:
    root = BASE / name
    home, work = root / "home", root / "project"
    home.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / "README.md").write_text("# dogfood\n\nA tiny project for the xplat dogfood run.\n", encoding="utf-8")
    return home, work


def check_script(work: Path, name: str) -> dict:
    path = work / name
    if not path.exists():
        return {"exists": False}
    proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60)
    return {"exists": True, "output": (proc.stdout or proc.stderr).strip()}


# 1. --ask
home, work = fresh("ask")
run("ask_nihao", ["--ask", "你好"], 180, home=home, work=work)


def argus_python() -> str:
    """The interpreter behind the installed ``argus`` launcher."""
    try:
        with open(ARGUS, "rb") as fh:
            first = fh.readline().decode("utf-8", "replace").strip()
        if first.startswith("#!") and "python" in first:
            return first[2:].strip().split()[0]
    except OSError:
        pass
    return sys.executable


# 1b. The same --ask with a stack dump every 8 s, to see where a slow call waits.
home, work = fresh("ask_stacks")
stacks = OUT / "ask_stacks.txt"
probe = (
    "import faulthandler, runpy, sys\n"
    f"fh = open({str(stacks)!r}, 'w')\n"
    "faulthandler.dump_traceback_later(8, repeat=True, file=fh)\n"
    f"sys.argv = ['argus', '--ask', 'hello', '--life-dir', {str(home)!r}]\n"
    "try:\n    runpy.run_module('argus', run_name='__main__')\n"
    "finally:\n    faulthandler.cancel_dump_traceback_later()\n"
)
t0 = time.time()
done = subprocess.run([argus_python(), "-c", probe], cwd=str(work), env=ENV, capture_output=True,
                      text=True, encoding="utf-8", errors="replace", timeout=240)
REPORT["ask_stacks"] = {"exit_code": done.returncode, "seconds": round(time.time() - t0, 1),
                        "python": argus_python(), "output": (done.stdout + done.stderr)[-800:]}

# 2. bounded foreground mission
home, work = fresh("bounded_fg")
run("bounded_fg", ["--daemon-fg", "--new", "--continuous", "--bounded", "--objective",
                   "Write hello.py that prints hello world, run it, then stop."], 480, home=home, work=work)
REPORT["bounded_fg_hello_py"] = check_script(work, "hello.py")
run("bounded_fg_status", ["--status"], 90, home=home, work=work)

# 3. bounded background daemon (detached spawn), polled with --status
home, work = fresh("bounded_bg")
run("bounded_bg_start", ["--daemon", "--new", "--continuous", "--bounded", "--objective",
                         "Write greet.py that prints hello and run it."], 120, home=home, work=work)
deadline = time.time() + 300
polls = 0
while time.time() < deadline:
    polls += 1
    name = f"bounded_bg_status_{polls:02d}"
    run(name, ["--status"], 90, home=home, work=work)
    text = (OUT / f"cli_{name}.log").read_text(encoding="utf-8", errors="replace")
    if (work / "greet.py").exists() and re.search(r"daemon\s*:\s*not running", text):
        break
    time.sleep(10)
REPORT["bounded_bg_polls"] = polls
REPORT["bounded_bg_greet_py"] = check_script(work, "greet.py")
run("bounded_bg_daemon_stop", ["--daemon-stop"], 90, home=home, work=work)

events = []
for path in BASE.rglob("events.jsonl"):
    dest = OUT / ("events__" + "__".join(path.relative_to(BASE).parts))
    shutil.copyfile(path, dest)
    with open(path, "rb") as fh:
        events.append({"src": str(path), "copied_to": dest.name, "lines": sum(1 for _ in fh)})
for path in BASE.rglob("*.log"):
    shutil.copyfile(path, OUT / ("log__" + "__".join(path.relative_to(BASE).parts)))
listing = sorted(str(p.relative_to(BASE)) for p in BASE.rglob("*") if p.is_file())
(OUT / "cli_tree.txt").write_text("\n".join(listing), encoding="utf-8")
tracebacks = []
for path in OUT.glob("*.log"):
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)", text, re.S):
        tracebacks.append(f"--- {path.name}\n{match.group(0)[:4000]}")
(OUT / "tracebacks.txt").write_text("\n\n".join(tracebacks) or "(none)", encoding="utf-8")
REPORT["events_files"] = events
(OUT / "cli_report.json").write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in REPORT.items() if k != "attempts"}, indent=2, ensure_ascii=False))
