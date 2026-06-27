#!/usr/bin/env python3
"""official_eval_server.py — thin HTTP wrapper around NVIDIA's OFFICIAL
SOL-ExecBench harness, run INSIDE the official docker image.

This is the env-parity realization for argus kernel work: the EVAL runs in the
EXACT official toolchain (CUDA 13.1.1 + cuDNN + CUTLASS 4.4.1 + uv-pinned
torch/triton) with the OFFICIAL timing protocol (cold-L2 flush, 10 warmup / 50
timed / 3 trials, locked GPU clocks), while the AGENT stays outside the
container and only POSTs candidates here — exactly the seam Argus already uses
(``./eval_solution.sh`` → POST to an eval port).

Run it INSIDE the official image, one per GPU:

    docker run -d --privileged --gpus '"device=0"' \
      -e CUDA_VISIBLE_DEVICES=0 -e EVAL_PORT=9100 \
      -v $REPO:/sol-execbench -w /sol-execbench \
      -e FLASHINFER_TRACE_DIR=/sol-execbench/data/flashinfer-trace \
      --entrypoint python sol-execbench:official official_eval_server.py

``--privileged`` is REQUIRED: nvidia-smi clock-locking is blocked at the
driver/cgroup layer unless the *nested* container has SYS_ADMIN — a privileged
pod alone is not enough (the pod's privilege does not propagate into a
``docker run`` child). Without it the official harness prints
"Clock locking failed — proceeding unlocked" and timings jitter.

Protocol (matches the argus eval-client contract):
  GET  /health            -> {"ok": true, "gpu": "<idx>", "n_problems": N}
  POST /compile           {"problem","solution"|"solution_json"} -> fast 1-workload smoke
  POST /eval              {"problem","solution"|"solution_json","max_workloads"?}
       -> {"correct": bool, "cand_ms": <geomean ms>, "per_workload_ms": [...],
           "result_line": "RESULT problem=... correct=... cand_ms=... status=...",
           "official": true, "clocks_locked": bool, "raw_summary": {...}}

``solution`` is python source with a top-level ``def run(...)``; ``solution_json``
is a full official JSON solution. The server stages a private copy of the
problem dir per request (concurrency- and tamper-safe) and runs the OFFICIAL
``scripts/run_dataset.py``.
"""
from __future__ import annotations

import glob
import json
import math
import os
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(os.environ.get("SOL_REPO", "/sol-execbench"))
BENCH = REPO / "data" / "benchmark"
TRACE_DIR = REPO / "data" / "flashinfer-trace"
PORT = int(os.environ.get("EVAL_PORT", "9100"))
GPU = os.environ.get("CUDA_VISIBLE_DEVICES", "0")
EVAL_TIMEOUT = int(os.environ.get("EVAL_TIMEOUT", "900"))


def _geomean(xs: list[float]) -> float:
    xs = [x for x in xs if x and x > 0]
    if not xs:
        return 0.0
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def _find_problem_dir(problem: str) -> Path | None:
    """Accept 'L1/053_...', '053_...', or a bare kernel name; resolve to its dir."""
    p = BENCH / problem
    if p.is_dir():
        return p
    # search by leaf name across categories
    leaf = problem.split("/")[-1]
    hits = [Path(d) for d in glob.glob(str(BENCH / "*" / leaf)) if Path(d).is_dir()]
    if hits:
        return hits[0]
    # prefix match (e.g. "053")
    hits = [Path(d) for d in glob.glob(str(BENCH / "*" / f"{leaf}*")) if Path(d).is_dir()]
    return hits[0] if hits else None


def _run(problem: str, solution_code: str | None, solution_json: dict | None,
         max_workloads: int | None) -> dict:
    src = _find_problem_dir(problem)
    if src is None:
        return {"ok": False, "error": f"no such problem: {problem}"}
    rel = src.relative_to(BENCH)  # e.g. L1/053_...
    with tempfile.TemporaryDirectory(dir="/dev/shm") as td:
        stage = Path(td) / src.name
        shutil.copytree(src, stage)
        if solution_json is not None:
            (stage / "solution.json").write_text(json.dumps(solution_json))
            sname = "solution.json"
        else:
            (stage / "solution.py").write_text(solution_code or "")
            sname = "solution.py"
        out = Path(td) / "out"
        cmd = ["python", "scripts/run_dataset.py", str(stage),
               "--solution-name", sname, "-o", str(out)]
        if max_workloads:
            cmd += ["--max-workloads", str(int(max_workloads))]
        env = dict(os.environ, FLASHINFER_TRACE_DIR=str(TRACE_DIR))
        try:
            proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                                  text=True, timeout=EVAL_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "eval timeout", "problem": str(rel)}
        stderr_tail = (proc.stderr or "")[-2000:]
        clocks_locked = "proceeding unlocked" not in (proc.stdout + proc.stderr).lower()
        # parse summary.json (list of {problem,total,passed,failed,latencies_ms})
        summ_files = glob.glob(str(out / "**" / "summary.json"), recursive=True) \
            or glob.glob(str(out / "summary.json"))
        if not summ_files:
            return {"ok": False, "error": "no summary.json (eval failed)",
                    "stderr": stderr_tail, "stdout": (proc.stdout or "")[-2000:],
                    "problem": str(rel)}
        summary = json.loads(Path(summ_files[0]).read_text())
        row = summary[0] if isinstance(summary, list) and summary else {}
        total = int(row.get("total", 0))
        passed = int(row.get("passed", 0))
        failed = int(row.get("failed", total))
        lat = [float(x) for x in (row.get("latencies_ms") or []) if x]
        correct = (failed == 0 and passed > 0)
        cand_ms = _geomean(lat) if correct else 0.0
        status = "PASSED" if correct else "FAILED"
        result_line = (f"RESULT problem={rel.name} correct={str(correct).lower()} "
                       f"status={status} cand_ms={cand_ms:.4f} "
                       f"workloads={passed}/{total} clocks_locked={clocks_locked} "
                       f"official=true")
        return {"ok": True, "correct": correct, "cand_ms": cand_ms,
                "per_workload_ms": lat, "workloads": f"{passed}/{total}",
                "status": status, "clocks_locked": clocks_locked, "official": True,
                "result_line": result_line, "problem": str(rel),
                "failure_reasons": row.get("failure_reasons", []),
                "stderr_tail": stderr_tail if not correct else ""}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            n = len(glob.glob(str(BENCH / "*" / "*")))
            return self._send(200, {"ok": True, "gpu": GPU, "n_problems": n,
                                    "official": True})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as exc:  # noqa: BLE001
            return self._send(400, {"ok": False, "error": f"bad request: {exc}"})
        problem = req.get("problem") or req.get("problem_dir") or ""
        sol_code = req.get("solution") or req.get("solution_py")
        sol_json = req.get("solution_json")
        path = self.path.rstrip("/")
        if path == "/compile":
            res = _run(problem, sol_code, sol_json, max_workloads=1)
            return self._send(200, res)
        if path == "/eval":
            res = _run(problem, sol_code, sol_json, req.get("max_workloads"))
            return self._send(200, res)
        self._send(404, {"ok": False, "error": "not found"})


def main() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[official_eval_server] OFFICIAL SOL-ExecBench eval on GPU {GPU}, "
          f"port {PORT}, repo {REPO}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
