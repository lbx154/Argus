"""Real nanochat-B200 eval seam for the slim spine's ``candidate_fn``.

The orchestrator's ``candidate_fn(node) -> (metric, refs)`` is where the REAL
experiment plugs in (demo.py stubs it with canned numbers). This turns a
candidate ``train.py`` into a measured ``val_bpb`` by shelling out to the
mission's FROZEN ``eval_solution.sh`` on the B200 — the same scorer the live
mission uses. The measured number goes to the FrozenJudge, which alone owns the
win; ``refs`` are the files the candidate read, surfaced for the Judge's
anti-cheat scan.

Deliberately decoupled: pure stdlib, NO import of ``argus.core`` (so HAPI's churn
on the spine's dataclasses can't break this seam) — ``make_candidate_fn`` only
duck-types ``node.artifact``. Fail-CLOSED: any eval failure yields ``metric=None``
(→ ``+inf`` to the Judge), never a fabricated pass.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# eval_solution.sh prints `MEAN_VAL_BPB=<float>` on success, `MEAN_VAL_BPB=FAILED ...`
# when all seeds fail.
_BPB_RE = re.compile(r"MEAN_VAL_BPB=([0-9]+\.[0-9]+)")
_REF_RE = re.compile(r"""['"]([^'"\s]*\.(?:py|json|bin|parquet|txt|md))['"]""")


@dataclass
class NanochatEval:
    metric: float | None = None   # measured mean val_bpb (lower=better); None on failure
    refs: list[str] = field(default_factory=list)  # files the candidate reads (anti-cheat)
    ok: bool = False
    note: str = ""


def candidate_refs(candidate_train_py: str | Path) -> list[str]:
    """Best-effort: the data/answer file paths the candidate source references, so
    the FrozenJudge can check none is a forbidden published answer. A static scan
    (not a sandbox); a determined agent can evade it — the seal is the real guard."""
    try:
        src = Path(candidate_train_py).read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return []
    return sorted({m for m in _REF_RE.findall(src)})[:50]


def run_nanochat_eval(
    candidate_train_py: str | Path,
    mission_dir: str | Path,
    *,
    n_seed: int = 1,
    timeout: float = 900.0,
) -> NanochatEval:
    """Measure a candidate train.py via the mission's frozen ``eval_solution.sh``.

    1-seed by default (the operator's screening contract; multi-seed confirm is
    the operator's job). Returns a measured ``metric`` only when the scorer
    printed a real ``MEAN_VAL_BPB`` — otherwise ``ok=False, metric=None``.
    """
    mission = Path(mission_dir)
    train = Path(candidate_train_py)
    eval_sh = mission / "eval_solution.sh"
    if not eval_sh.is_file():
        return NanochatEval(note=f"no eval_solution.sh in {mission}")
    if not train.is_file():
        return NanochatEval(note=f"no candidate train.py at {train}")
    try:
        proc = subprocess.run(
            ["bash", str(eval_sh), str(train), str(n_seed)],
            cwd=str(mission),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return NanochatEval(note=f"eval timed out after {timeout:.0f}s")
    except Exception as exc:  # noqa: BLE001
        return NanochatEval(note=f"eval launch failed: {type(exc).__name__}: {exc}")

    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    m = _BPB_RE.search(out)
    if not m:
        tail = (proc.stdout or proc.stderr or "").strip()[-200:]
        return NanochatEval(note=f"no MEAN_VAL_BPB (exit {proc.returncode}): {tail!r}")
    metric = float(m.group(1))
    return NanochatEval(metric=metric, refs=candidate_refs(train), ok=True,
                        note=f"measured {metric:.6f} (n_seed={n_seed})")


def make_candidate_fn(mission_dir: str | Path, *, n_seed: int = 1):
    """Adapt :func:`run_nanochat_eval` to the spine's ``candidate_fn(node)``.

    Reads ``node.artifact`` (the candidate train.py path) and returns
    ``(metric, refs)``; a failed/missing measurement becomes ``+inf`` so the
    FrozenJudge records a clean non-win (never a fabricated pass).
    """
    def candidate_fn(node) -> tuple[float, list[str]]:
        artifact = getattr(node, "artifact", "") or ""
        res = run_nanochat_eval(artifact, mission_dir, n_seed=n_seed)
        return (res.metric if res.metric is not None else float("inf")), res.refs

    return candidate_fn
