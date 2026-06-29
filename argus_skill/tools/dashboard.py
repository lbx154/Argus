"""Built-in live dashboard for all argus daemons.

`argus-skill --dashboard` auto-discovers every project under the life
root(s), scrapes each daemon's structured state, and serves a
self-contained HTML page that polls a normalized JSON snapshot. It is
**vertical-agnostic**: it reads whatever stages a project's
``research/PIPELINE_STATE.json`` declares (research, speedrun, or any
future vertical) instead of hardcoding a pipeline.

Data sources per project (all already emitted by the daemon):
  - ``<life>/daemon.status.json``  — pid, started, backend, caps
  - ``<life>/events.jsonl``        — mission count, cost, recent events
  - ``<life>/backlog.jsonl``       — task list + statuses
  - ``<life>/project.md``          — first line ``# <project root>``
  - ``<root>/research/PIPELINE_STATE.json`` — vertical + stage states

Optional, auto-detected enrichment (no per-project config):
  - ``<root>/paper/main.pdf`` present  → page count + TBD/figure/cite tally
  - ``<root>/attempts/*/results.csv``  → best scored metric + attempt count

Discovery roots default to the standard global root
(``~/.argus-skill``) plus any colon-separated paths in
``ARGUS_SKILL_DASHBOARD_ROOTS`` — so daemons launched with a custom
``--life-dir`` (e.g. ``~/.argus-skill-nanochat``) are included by
exporting that env var.

Pure stdlib: ``http.server`` + ``json`` + ``subprocess`` (nvidia-smi,
pdfinfo, ps). No web framework, no build step.
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _dashboard_roots() -> list[Path]:
    """Life roots to scan. Default global root + env-listed extras."""
    roots: list[Path] = []
    base = Path(os.environ.get("ARGUS_SKILL_HOME") or (Path.home() / ".argus-skill"))
    roots.append(base)
    extra = os.environ.get("ARGUS_SKILL_DASHBOARD_ROOTS", "")
    for p in extra.split(":"):
        p = p.strip()
        if p:
            roots.append(Path(p))
    # de-dup preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        rp = str(r.resolve()) if r.exists() else str(r)
        if rp not in seen:
            seen.add(rp)
            out.append(r)
    return out


def discover_life_dirs(roots: list[Path] | None = None) -> list[Path]:
    """Return every ``<root>/projects/<fingerprint>/`` that has a daemon.

    De-duplicates symlinked life dirs (e.g. a fingerprint dir that is a
    symlink to another) by resolved real path, so a project that moved
    and left a symlink behind is not shown twice.
    """
    roots = roots or _dashboard_roots()
    found: list[Path] = []
    seen_real: set[str] = set()
    for root in roots:
        pdir = root / "projects"
        if not pdir.exists():
            continue
        for life in sorted(pdir.iterdir()):
            if not (life.is_dir() and (life / "daemon.status.json").exists()):
                continue
            real = str(life.resolve())
            if real in seen_real:
                continue
            seen_real.add(real)
            found.append(life)
    return found


def _resolve_project_root(life_dir: Path) -> Path | None:
    pm = life_dir / "project.md"
    if not pm.exists():
        return None
    try:
        first = pm.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    first = first.lstrip("# ").strip()
    p = Path(first)
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# Per-source readers (fail-soft)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _iter_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _pid_etime(pid: int) -> str:
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "etime="],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _missions_cost(events: list[dict]) -> tuple[int, float]:
    n, cost = 0, 0.0
    for e in events:
        if (e.get("type") or e.get("event_type")) == "life.mission.completed":
            n += 1
            try:
                cost += float(e.get("cost_usd", 0) or 0)
            except (TypeError, ValueError):
                pass
    return n, round(cost, 2)


def _recent_events(events: list[dict], n: int = 12) -> list[dict]:
    out = []
    for e in events[-n:]:
        t = e.get("type") or e.get("event_type") or "?"
        txt = e.get("text") or e.get("reason") or e.get("objective") or ""
        txt = " ".join(str(txt).split())[:120]
        out.append({"type": str(t)[:30], "text": txt})
    return out


def _current_action(events: list[dict]) -> str:
    for e in reversed(events):
        if (e.get("type") or e.get("event_type")) != "engineer.progress":
            continue
        action = e.get("action_summary") or e.get("current_action")
        if action:
            return " ".join(str(action).split())[:120]
    return ""


def _active_role(events: list[dict]) -> str:
    last = (events[-1].get("type") or events[-1].get("event_type") or "") if events else ""
    last = str(last)
    if "planner" in last:
        return "planner"
    if "review" in last:
        return "reviewer"
    return "engineer"


# ---------------------------------------------------------------------------
# Auto-detected enrichment (vertical-agnostic, presence-based)
# ---------------------------------------------------------------------------

def _enrich(root: Path) -> dict:
    """Best-effort metrics from whatever artifacts the project happens to
    have. No per-project config: keyed on file presence only."""
    out: dict = {"panels": []}
    # paper
    pdf = root / "paper" / "main.pdf"
    tex = root / "paper" / "main.tex"
    if pdf.exists() or tex.exists():
        pages = None
        if pdf.exists():
            try:
                proc = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                      text=True, timeout=10)
                for line in proc.stdout.splitlines():
                    if line.startswith("Pages:"):
                        pages = int(line.split()[1])
            except Exception:
                pass
        tbd = figs = cites = 0
        if tex.exists():
            t = tex.read_text(errors="replace")
            tbd = t.count("\\TBD") + t.count("\\PLACEHOLDER") + t.count("\\TODO")
            figs = t.count("includegraphics")
            cites = t.count("\\citep{") + t.count("\\citet{")
        out["panels"].append({
            "kind": "paper",
            "chips": [
                {"v": pages if pages is not None else "—", "l": "PDF pages", "tone": "good"},
                {"v": tbd, "l": "TBD left", "tone": "good" if tbd == 0 else "warn"},
                {"v": figs, "l": "figures", "tone": ""},
                {"v": cites, "l": "citations", "tone": ""},
            ],
        })
    # speedrun attempts/leaderboard
    attempts = root / "attempts"
    if attempts.exists():
        scored: list[dict[str, str | float]] = []
        for d in sorted(attempts.iterdir()):
            cf = d / "results.csv"
            if not cf.exists():
                continue
            try:
                rows = list(csv.DictReader(cf.open()))
                bpb = [float(row["val_bpb"]) for row in rows if row.get("val_bpb")]
            except Exception:
                continue
            if bpb:
                scored.append({"name": d.name, "mean": round(statistics.mean(bpb), 4),
                               "kind": "ours"})
        # reference rows if present
        refcsv = root / "reference" / "results" / "val_bpb.csv"
        if refcsv.exists():
            try:
                agg: dict[str, list[float]] = {}
                for row in csv.DictReader(refcsv.open()):
                    agg.setdefault(row["label"], []).append(float(row["val_bpb"]))
                for k, v in agg.items():
                    scored.append({"name": k, "mean": round(statistics.mean(v), 4),
                                   "kind": "ref"})
            except Exception:
                pass
        scored.sort(key=lambda x: float(x["mean"]))
        best = next((float(s["mean"]) for s in scored if s["kind"] == "ours"), None)
        n_attempts = len([d for d in attempts.iterdir() if d.is_dir()])
        n_pm = len(list(attempts.rglob("INVENTION_POSTMORTEM.md")))
        out["panels"].append({
            "kind": "speedrun",
            "chips": [
                {"v": best if best is not None else "—", "l": "best (↓)",
                 "tone": "good" if best is not None else ""},
                {"v": n_attempts, "l": "attempts", "tone": ""},
                {"v": n_pm, "l": "invention", "tone": "good" if n_pm else ""},
            ],
            "leaderboard": scored[:9],
        })
    return out


def _gpu() -> list[dict]:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        out = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                out.append({"idx": int(parts[0]), "used_mb": int(parts[1]),
                            "total_mb": int(parts[2]), "util": int(parts[3])})
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Agent-team pool (rolling teammate pool) — vertical-agnostic, presence-based
# ---------------------------------------------------------------------------

def _clean_kernel(task_id: str) -> str:
    """Human-readable kernel name from a lane-prefixed task_id.

    ``<tid>::restock<n>_depth_<n>_<kernel>`` → ``<kernel>``; otherwise the
    part after ``::`` (or the whole id)."""
    k = task_id.split("::")[-1] if "::" in task_id else task_id
    if k.startswith("restock"):
        segs = k.split("_", 3)
        if len(segs) == 4 and segs[1] in ("depth", "breadth"):
            k = segs[3]
    return k[:52]


def _num_id(mid: str) -> int:
    digits = "".join(ch for ch in mid if ch.isdigit())
    return int(digits) if digits else 0


def _team_member_activity(life_dir: Path) -> dict:
    """Current role (engineer / reviewer), live ACTIVITY, action text + round for
    ONE teammate, from its own events.jsonl. Activity is a coarse, display-only
    label (thinking / reading / coding / profiling / evaluating / reviewing /
    diffing) inferred from the newest decisive event — it drives the little
    character's pose on the board, nothing in the research loop depends on it."""
    evs = _iter_jsonl(life_dir / "events.jsonl")
    if not evs:
        return {"role": "engineer", "activity": "starting", "action": "启动中…",
                "round": None, "idle": None}
    role = None
    activity = None
    action = ""
    rnd = None
    last_ts = None
    for e in reversed(evs):
        t = str(e.get("type") or "")
        kind = str(e.get("kind") or "")
        raw = e.get("action_summary") or e.get("text") or ""
        low = str(e.get("text") or "").lower()
        if last_ts is None:
            last_ts = e.get("ts") or e.get("time")
        if not action and raw:
            action = str(raw).strip().replace("\n", " ")[:110]
        if rnd is None and isinstance(e.get("round_index"), int):
            rnd = e["round_index"]
        if role is None:
            if t.startswith("round.review") or t.startswith("reviewer"):
                role = "reviewer"
            elif t.startswith("engineer") or t.startswith("round.engineer"):
                role = "engineer"
        if activity is None:
            if t.startswith("round.review") or t.startswith("reviewer"):
                activity = "reviewing"
            elif kind == "command_execution":
                if "eval_solution" in low or "result problem=" in low or re.search(r":(?:226\d|910\d)\b", low):
                    activity = "evaluating"
                elif "profile" in low or " ncu" in low or "nsys" in low or "torch.profiler" in low:
                    activity = "profiling"
                elif "git diff" in low or "git checkout" in low or "git apply" in low or "git revert" in low:
                    activity = "diffing"
                elif "triton" in low or "torch.compile" in low or "apply_patch" in low or "<<'py" in low or "<<py" in low or "cutlass" in low or ".cu" in low:
                    activity = "coding"
                elif any(k in low for k in ("sed -n", "cat ", "grep", "find ", " ls ", "head -", "tail -", "stat ", " wc ", "curl ", "nvidia-smi")):
                    activity = "reading"
                else:
                    activity = "coding"
            elif kind == "agent_message" or t.endswith("agent_message"):
                stripped = low.lstrip()
                activity = "reviewing" if (stripped.startswith('{"status"') or '"reason"' in stripped) else "thinking"
            elif "watchdog" in t:
                activity = "thinking"
        if role is not None and activity is not None and action and rnd is not None:
            break
    idle = int(time.time() - last_ts) if isinstance(last_ts, (int, float)) else None
    return {"role": role or "engineer", "activity": activity or "thinking",
            "action": action or "…", "round": rnd, "idle": idle}


_CAND_RE = re.compile(
    r"correct=true.{0,80}?cand_ms=([0-9.]+).{0,90}?clocks_locked=True.{0,40}?official=true"
)


def _kernel_progress(team_dir: Path, task_id: str) -> dict:
    """Verified baseline / best cand_ms + speedup for one kernel, scanned from its
    official.log audit trail (correct=true + clocks_locked + official=true ONLY —
    no unbacked numbers)."""
    wd = team_dir / "work" / task_id
    base = best = None
    verified = 0
    adir = wd / "attempts"
    if adir.is_dir():
        for L in sorted(adir.glob("*/official.log")):
            try:
                txt = L.read_text(errors="replace")
            except Exception:
                continue
            for m in _CAND_RE.finditer(txt):
                v = float(m.group(1))
                if base is None:
                    base = v
                best = v if best is None else min(best, v)
                verified += 1
    rj = _read_json(wd / "result.json")
    if best is None and isinstance(rj.get("metric"), (int, float)):
        best = float(rj["metric"])
    spd = round(base / best, 2) if (base and best and best > 0) else None
    return {"best": best, "base": base, "speedup": spd, "verified": verified}


def _scrape_teams(root: Path) -> dict:
    """The project's active rolling teammate pool, ENRICHED: for every live
    teammate, which kernel it owns, whether its engineer or reviewer is acting
    right now, that role's current action, and the kernel's verified best/speedup.

    Reads live ``teammate_entry`` processes directly (their cmdline carries
    ``--root/--member-id/--task-id``). Works for any team campaign whose ``--root``
    is a real team dir (has ``roster.json``), not just ``experiments/teams``."""
    try:
        r = subprocess.run(["ps", "-eo", "etimes=,args="],
                           capture_output=True, text=True, timeout=6)
        lines = r.stdout.splitlines()
    except Exception:
        lines = []
    raw: list[tuple[str, str, int | None, Path]] = []
    for ln in lines:
        if "argus_skill.team.teammate_entry" not in ln:
            continue
        parts = ln.split()
        if not parts:
            continue
        try:
            etimes: int | None = int(parts[0])
        except ValueError:
            etimes = None
        toks = parts[1:]
        mid = tid = rt = ""
        for i, t in enumerate(toks):
            if t == "--member-id" and i + 1 < len(toks):
                mid = toks[i + 1]
            elif t == "--task-id" and i + 1 < len(toks):
                tid = toks[i + 1]
            elif t == "--root" and i + 1 < len(toks):
                rt = toks[i + 1]
        if not (mid and rt):
            continue
        rtp = Path(rt)
        # this project's team campaign: rt is a real team dir AND resolves under
        # (or equals) the project root. Generalized beyond experiments/teams.
        if not (rtp / "roster.json").exists():
            continue
        if not (rtp == Path(root) or (root / rt).exists() or str(rtp).startswith(str(root))):
            continue
        raw.append((mid, tid, etimes, rtp))
    if not raw:
        return {}
    team_dir = raw[0][3]
    pool = _read_json(team_dir / "pool.json")
    agents = []
    for mid, tid, age, rtp in raw:
        act = _team_member_activity(team_dir / "life" / mid)
        prog = _kernel_progress(team_dir, tid) if tid else {}
        agents.append({
            "id": mid,
            "kernel": _clean_kernel(tid) if tid else "?",
            "task": tid,
            "age": age,
            "role": act["role"],
            "activity": act["activity"],
            "action": act["action"],
            "round": act["round"],
            "idle": act["idle"],
            "best": prog.get("best"),
            "base": prog.get("base"),
            "speedup": prog.get("speedup"),
            "verified": prog.get("verified", 0),
        })
    agents.sort(key=lambda a: _num_id(a["task"]) or _num_id(a["id"]))
    now = time.time()
    lead_hb = pool.get("lead_heartbeat_ts")
    improved = [a for a in agents if a["speedup"] and a["speedup"] > 1.03]
    best_win = max((a["speedup"] for a in improved), default=None)
    return {
        "team_id": team_dir.name,
        "width": pool.get("width"),
        "state": pool.get("state") or "",
        "lead_age": int(now - lead_hb) if lead_hb else None,
        "running": len(agents),
        "improved": len(improved),
        "best_speedup": best_win,
        "agents": agents[:60],
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _infer_vertical(pipe: dict, stage_rows: list[dict]) -> str:
    """Vertical name from the explicit field, else inferred from stage names.

    Keeps the dashboard vertical-agnostic: a new vertical only needs its
    stage names present in PIPELINE_STATE for the label to resolve, and
    falls back to 'custom' rather than '?' for anything unrecognized.
    """
    explicit = pipe.get("vertical")
    if explicit:
        return str(explicit).split("-needed", 1)[0]
    names = {r["name"] for r in stage_rows}
    if {"research", "draft", "submission"} & names:
        return "research"
    if {"setup", "optimize", "measure", "report"} & names:
        return "speedrun"
    return "custom" if names else "?"


def scrape_project(life_dir: Path) -> dict:
    status = _read_json(life_dir / "daemon.status.json")
    pid = int(status.get("pid", 0) or 0)
    etime = _pid_etime(pid) if pid else ""
    events = _iter_jsonl(life_dir / "events.jsonl")
    backlog = _iter_jsonl(life_dir / "backlog.jsonl")
    n_missions, cost = _missions_cost(events)

    root = _resolve_project_root(life_dir)
    pipe = _read_json(root / "research" / "PIPELINE_STATE.json") if root else {}
    stages_state = pipe.get("stages", {}) if isinstance(pipe.get("stages"), dict) else {}
    stage_rows = [{"name": k, "status": (v or {}).get("status", "pending")}
                  for k, v in stages_state.items()]
    vertical = _infer_vertical(pipe, stage_rows)

    enrich = _enrich(root) if root else {"panels": []}

    return {
        "life_dir": str(life_dir),
        "fingerprint": life_dir.name,
        "root": str(root) if root else "",
        "title": root.name if root else life_dir.name,
        "vertical": vertical,
        "alive": bool(etime),
        "pid": pid,
        "etime": etime,
        "backend": status.get("backend", ""),
        "current_stage": pipe.get("current_stage", "?"),
        "mission_sha": pipe.get("mission_sha", ""),
        "stages": stage_rows,
        "missions": n_missions,
        "cost": cost,
        "teams": _scrape_teams(root) if root else {},
        "backlog": [{"status": b.get("status", "?"), "title": (b.get("title") or "")[:130]}
                    for b in backlog[-6:]],
        "events": _recent_events(events),
        "active_role": _active_role(events),
        "current_action": _current_action(events),
        "enrich": enrich,
    }


def scrape_all(roots: list[Path] | None = None) -> dict:
    projects = []
    for life in discover_life_dirs(roots):
        try:
            projects.append(scrape_project(life))
        except Exception as exc:  # noqa: BLE001 — one bad project must not break all
            projects.append({"life_dir": str(life), "title": life.name,
                             "error": repr(exc), "alive": False, "stages": [],
                             "events": [], "backlog": [], "enrich": {"panels": []}})
    # alive first, then by mission count
    projects.sort(key=lambda p: (not p.get("alive"), -p.get("missions", 0)))
    return {
        "generated_at": int(time.time()),
        "generated_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "gpu": _gpu(),
        "n_projects": len(projects),
        "projects": projects,
    }


# ---------------------------------------------------------------------------
# Deep per-project detail (drill-down behind a click)
# ---------------------------------------------------------------------------

def _mission_timeline(events: list[dict], limit: int = 30) -> list[dict]:
    out = []
    for e in events:
        t = e.get("type") or e.get("event_type")
        if t in ("life.mission.completed", "life.mission.failed"):
            out.append({
                "status": e.get("status") or ("done" if t.endswith("completed") else "failed"),
                "ok": bool(e.get("success", t.endswith("completed"))),
                "rounds": e.get("rounds"),
                "cost": round(float(e.get("cost_usd", 0) or 0), 2),
                "ts": e.get("ts"),
                "reason": " ".join(str(e.get("reason") or e.get("summary") or "").split())[:200],
            })
    return out[-limit:][::-1]


def _attempt_detail(root: Path) -> list[dict]:
    adir = root / "attempts"
    if not adir.exists():
        return []
    out = []
    for d in sorted(adir.iterdir()):
        if not d.is_dir():
            continue
        rec: dict = {"name": d.name, "seeds": [], "mean": None,
                     "has_postmortem": (d / "INVENTION_POSTMORTEM.md").exists(),
                     "changes": ""}
        cf = d / "results.csv"
        if cf.exists():
            try:
                rows = list(csv.DictReader(cf.open()))
                vals = []
                for r in rows:
                    if r.get("val_bpb"):
                        v = float(r["val_bpb"])
                        vals.append(v)
                        rec["seeds"].append({"seed": r.get("seed", "?"),
                                             "val_bpb": round(v, 4),
                                             "wall": r.get("wall_seconds", "")})
                if vals:
                    rec["mean"] = round(statistics.mean(vals), 4)
            except Exception:
                pass
        ch = d / "CHANGES.md"
        if ch.exists():
            try:
                lines = [line for line in ch.read_text(errors="replace").splitlines() if line.strip()]
                rec["changes"] = " ".join(" ".join(lines[:6]).split())[:280]
            except Exception:
                pass
        # only include attempts that have a score or a postmortem (signal)
        if rec["mean"] is not None or rec["has_postmortem"]:
            out.append(rec)
    out.sort(key=lambda x: (x["mean"] is None, x["mean"] if x["mean"] is not None else 9))
    return out


def _paper_detail(root: Path) -> dict:
    out: dict = {"sections": [], "figures": [], "experiments": []}
    outline = root / "paper" / "DRAFT_OUTLINE.md"
    if outline.exists():
        try:
            from ..verticals.research.draft_outline import parse_outline
            o = parse_outline(outline.read_text(errors="replace"))
            out["sections"] = [{"title": s.title, "goal": s.goal[:120]} for s in o.sections]
            out["figures"] = [{"id": f.id, "src": f.data_source[:80]} for f in o.figures]
            out["experiments"] = [{"id": e.id, "spec": e.cell_spec[:100]} for e in o.experiments]
        except Exception:
            pass
    return out


def scrape_project_detail(life_dir: Path) -> dict:
    base = scrape_project(life_dir)
    events = _iter_jsonl(life_dir / "events.jsonl")
    memory = _iter_jsonl(life_dir / "memory.jsonl")
    backlog = _iter_jsonl(life_dir / "backlog.jsonl")
    root = Path(base["root"]) if base.get("root") else None
    pipe = _read_json(root / "research" / "PIPELINE_STATE.json") if root else {}

    # per-stage detail (reason + artifact)
    stages_state = pipe.get("stages", {}) if isinstance(pipe.get("stages"), dict) else {}
    stage_detail = []
    for name, v in stages_state.items():
        v = v or {}
        stage_detail.append({
            "name": name,
            "status": v.get("status", "pending"),
            "reason": " ".join(str(v.get("reason", "")).split())[:240],
            "artifact": v.get("artifact", ""),
        })

    detail = {
        **base,
        "caps": {
            "per_mission": (_read_json(life_dir / "daemon.status.json") or {}).get("per_mission_cap_usd"),
            "daily": (_read_json(life_dir / "daemon.status.json") or {}).get("daily_cap_usd"),
        },
        "stage_detail": stage_detail,
        "last_gate": pipe.get("last_gate", {}),
        "rollback_history": (pipe.get("rollback_history") or [])[-8:][::-1],
        "mission_timeline": _mission_timeline(events + memory),
        "backlog_full": [{"status": b.get("status", "?"), "title": b.get("title", "")}
                         for b in backlog[-25:][::-1]],
        "events_full": _recent_events(events, 40)[::-1],
        "attempts": _attempt_detail(root) if root else [],
        "paper": _paper_detail(root) if root else {"sections": [], "figures": [], "experiments": []},
    }
    return detail


def find_life_dir(fingerprint: str, roots: list[Path] | None = None) -> Path | None:
    for life in discover_life_dirs(roots):
        if life.name == fingerprint:
            return life
    return None


# ---------------------------------------------------------------------------
# Stage-level drill-down (click a stage → its concrete artifacts)
# ---------------------------------------------------------------------------

# Per-stage artifact hints: file paths (relative to project root) and glob
# patterns to surface for each stage. Vertical-agnostic union — only the
# files that actually exist are shown, so a research project simply has no
# speedrun artifacts and vice versa.
_STAGE_ARTIFACTS: dict[str, list[str]] = {
    # research vertical
    "research": ["research/RESEARCH_BRIEF.md", "research/LITERATURE_GROUNDING.json",
                 "research/SOURCE_DISCOVERY.md", "research/TREND_INSIGHTS.md"],
    "plan": ["research/EXPERIMENT_PLAN.md", "research/BASELINE_AND_BENCHMARK_PLAN.md",
             "paper/DRAFT_OUTLINE.md", "research/CLAIMS_TO_TEST.md"],
    "benchmark": ["experiments/BENCHMARK_PROVENANCE.json", "experiments/BENCHMARK_PROVENANCE.md",
                  "experiments/MATRIX.json", "bench/README.md"],
    "run": ["benchmarks/evidence/*/summary.tsv", "experiments/runs/", "research/BASELINE_REPRODUCTION.md"],
    "analysis": ["paper/RESULTS_REPORT.md", "paper/artifacts/results_table.tsv",
                 "paper/CLAIMS_EVIDENCE_AUDIT.tsv", "paper/LIT_BASELINES.tsv", "paper/figures/"],
    "draft": ["paper/main.tex", "paper/main.pdf", "paper/figures/"],
    "review": ["paper/REVIEW_REPORT.md", "paper/LAYOUT_REVIEW.json",
               "paper/ACADEMIC_LANGUAGE_REVIEW.json", "paper/FORMAT_PREFLIGHT.md"],
    "submission": ["paper/SUBMISSION_ASSURANCE.md", "paper/SUBMISSION_ASSURANCE.json", "paper/main.pdf"],
    # speedrun vertical
    "setup": ["mission/SETUP.md", "research/GROUND_TRUTH.md", "reference/results/val_bpb.csv"],
    "optimize": ["attempts/*/CHANGES.md", "attempts/*/train.py"],
    "measure": ["attempts/*/results.csv"],
    "report": ["RESULTS.md", "attempts/*/INVENTION_POSTMORTEM.md"],
}


def _artifact_record(p: Path) -> dict:
    """Summarize one artifact file/dir for display."""
    rec: dict = {"path": None, "kind": "file", "exists": p.exists(),
                 "snippet": "", "rows": None, "size": None}
    rec["path"] = str(p)
    if not p.exists():
        return rec
    if p.is_dir():
        rec["kind"] = "dir"
        try:
            children = [c for c in p.iterdir()]
            rec["rows"] = len(children)
            rec["snippet"] = ", ".join(sorted(c.name for c in children)[:8])
        except OSError:
            pass
        return rec
    try:
        rec["size"] = p.stat().st_size
    except OSError:
        pass
    suf = p.suffix.lower()
    if suf in (".tsv", ".csv"):
        rec["kind"] = "table"
        try:
            lines = p.read_text(errors="replace").splitlines()
            rec["rows"] = max(0, len([line for line in lines if line.strip()]) - 1)
            rec["snippet"] = " | ".join((lines[0].split("\t") if suf == ".tsv" else lines[0].split(","))[:6]) if lines else ""
        except OSError:
            pass
    elif suf == ".pdf":
        rec["kind"] = "pdf"
        try:
            r = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, timeout=8)
            for line in r.stdout.splitlines():
                if line.startswith("Pages:"):
                    rec["snippet"] = f"{line.split()[1]} 页"
        except Exception:
            pass
    elif suf in (".md", ".txt", ".json"):
        try:
            txt = p.read_text(errors="replace")
            rec["snippet"] = " ".join(txt.split())[:400]
        except OSError:
            pass
    return rec


def scrape_stage_detail(life_dir: Path, stage: str) -> dict:
    base = scrape_project(life_dir)
    root = Path(base["root"]) if base.get("root") else None
    pipe = _read_json(root / "research" / "PIPELINE_STATE.json") if root else {}
    stinfo = ((pipe.get("stages") or {}).get(stage) or {}) if isinstance(pipe.get("stages"), dict) else {}
    arts: list[dict] = []
    if root:
        for pat in _STAGE_ARTIFACTS.get(stage, []):
            if "*" in pat:
                for m in sorted(root.glob(pat))[:12]:
                    arts.append(_artifact_record(m))
            else:
                p = root / pat
                if p.exists():
                    arts.append(_artifact_record(p))
    return {
        "stage": stage,
        "title": base["title"],
        "status": stinfo.get("status", "pending"),
        "reason": " ".join(str(stinfo.get("reason", "")).split())[:400],
        "artifact_field": stinfo.get("artifact", ""),
        "artifacts": [a for a in arts if a["exists"]],
    }


# ---------------------------------------------------------------------------
# HTTP serving
# ---------------------------------------------------------------------------

def _html() -> str:
    return _DASHBOARD_HTML


def serve(port: int = 8787, roots: list[Path] | None = None) -> int:
    roots = roots or _dashboard_roots()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_HEAD(self):  # noqa: N802 — let probes (curl -I, health checks) succeed
            ctype = "application/json" if self.path.startswith("/data.json") else "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/data.json"):
                payload = json.dumps(scrape_all(roots)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif self.path.startswith("/detail"):
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                fp = (qs.get("fp") or [""])[0]
                life = find_life_dir(fp, roots) if fp else None
                if life is None:
                    payload = json.dumps({"error": "project not found"}).encode()
                    self.send_response(404)
                else:
                    try:
                        payload = json.dumps(scrape_project_detail(life)).encode()
                    except Exception as exc:  # noqa: BLE001
                        payload = json.dumps({"error": repr(exc)}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif self.path.startswith("/stage"):
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                fp = (qs.get("fp") or [""])[0]
                stage = (qs.get("stage") or [""])[0]
                life = find_life_dir(fp, roots) if fp else None
                if life is None or not stage:
                    payload = json.dumps({"error": "project or stage missing"}).encode()
                    self.send_response(404)
                else:
                    try:
                        payload = json.dumps(scrape_stage_detail(life, stage)).encode()
                    except Exception as exc:  # noqa: BLE001
                        payload = json.dumps({"error": repr(exc)}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                body = _html().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"argus-skill dashboard: http://localhost:{port}  "
          f"(scanning {len(discover_life_dirs(roots))} project(s) across "
          f"{len(roots)} root(s); /data.json regenerates per request)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nargus-skill dashboard: stopped")
    return 0


# The page is served live and polls /data.json. Kept as a module constant so
# the command has zero external asset dependencies.
_DASHBOARD_HTML = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Argus 工作台</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Noto+Sans+SC:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f4f7fc; --ink:#1a2942; --muted:#5b7090; --dim:#9aabc4;
  --line:#dde6f3; --card:#ffffff; --soft:#eef3fb;
  --blue:#2f6df0; --blue-d:#1c4fc0; --blue-soft:#e6efff;
  --green:#27a567; --green-soft:#e3f6ec;
  --amber:#e0930f; --amber-soft:#fbf0d8;
  --red:#e0544e; --red-soft:#fbe5e4;
  --violet:#7c5cff; --violet-soft:#ece7ff;
  --sans:'Outfit','Noto Sans SC',system-ui,sans-serif; --mono:'IBM Plex Mono',ui-monospace,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6;
  background:radial-gradient(70% 50% at 50% -5%,#e9f1ff 0%,transparent 70%),var(--bg);background-attachment:fixed}
.wrap{max-width:1240px;margin:0 auto;padding:30px clamp(16px,3vw,40px) 70px}
/* 顶栏 */
.hdr{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;margin-bottom:24px}
.hdr h1{font-size:clamp(24px,3vw,34px);font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:10px}
.hdr h1 .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,var(--blue),#5b96ff);display:inline-block}
.hdr .sub{font-size:13px;color:var(--muted);margin-top:2px;font-weight:400}
.hdr .meta{font-size:12px;color:var(--muted);text-align:right;font-family:var(--mono)}
.hdr .live{display:inline-flex;align-items:center;gap:5px;color:var(--green);font-weight:500}
.hdr .live .dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
/* 总览条 */
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px}
.kpi{flex:1;min-width:120px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;box-shadow:0 1px 3px rgba(30,60,120,.04)}
.kpi .v{font-size:26px;font-weight:700;color:var(--blue-d)}
.kpi .l{font-size:12px;color:var(--muted);margin-top:1px}
/* GPU */
.gpustrip{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.gpu{flex:1;min-width:200px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;box-shadow:0 1px 3px rgba(30,60,120,.04)}
.gpu .top{display:flex;justify-content:space-between;font-size:13px;font-weight:500}
.gpu .top .pct{font-family:var(--mono)}
.gpu .bar{height:8px;background:var(--soft);border-radius:5px;margin-top:9px;overflow:hidden}
.gpu .fill{height:100%;border-radius:5px;transition:width .6s}
.gpu .nums{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:6px;font-family:var(--mono)}
/* 卡片 */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media (max-width:920px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px clamp(16px,2vw,26px);
  display:flex;flex-direction:column;gap:18px;box-shadow:0 1px 2px rgba(30,60,120,.04)}
.chead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.chead h2{font-size:19px;font-weight:700}
.chead .meta2{font-size:12px;color:var(--muted);margin-top:3px;font-family:var(--mono)}
.tag{font-size:12px;padding:4px 11px;border-radius:999px;font-weight:500;white-space:nowrap}
.tag.research{background:var(--blue-soft);color:var(--blue-d)}
.tag.speedrun{background:var(--green-soft);color:var(--green)}
.tag.custom{background:var(--soft);color:var(--muted)}
.status{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:500}
.status .d{width:7px;height:7px;border-radius:50%}
.status.on{color:var(--green)} .status.on .d{background:var(--green)}
.status.off{color:var(--red)} .status.off .d{background:var(--red)}
/* 阶段流水线 */
.section-label{font-size:12px;color:var(--muted);font-weight:500;margin-bottom:8px}
.pipe{display:flex;gap:6px;flex-wrap:wrap}
.pipe .st{flex:1;min-width:52px;text-align:center;font-size:12px;padding:8px 4px;border-radius:9px;
  border:1px solid var(--line);color:var(--dim);background:var(--soft);font-weight:500;position:relative}
.pipe .st.done{color:var(--green);background:var(--green-soft);border-color:transparent}
.pipe .st.done::after{content:'✓';position:absolute;top:-6px;right:-3px;font-size:11px;background:var(--green);color:#fff;border-radius:50%;width:15px;height:15px;line-height:15px}
.pipe .st.running,.pipe .st.ready{color:var(--blue-d);background:var(--blue-soft);border-color:var(--blue);font-weight:700;
  box-shadow:0 0 0 3px rgba(47,109,240,.12)}
.pipe .st.blocked{color:var(--red);background:var(--red-soft);border-color:transparent}
/* 三角色循环 */
.loop{display:flex;gap:6px;align-items:center}
.loop .ag{flex:1;text-align:center;padding:10px 4px;border-radius:10px;border:1px solid var(--line);
  color:var(--muted);font-size:13px;background:var(--soft)}
.loop .ag.active{color:#fff;background:linear-gradient(135deg,var(--blue),#5b96ff);border-color:transparent;
  font-weight:600;box-shadow:0 3px 10px rgba(47,109,240,.25)}
.loop .arr{color:var(--dim);font-size:15px}
/* 团队智能体池 */
.teampool{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:9px;font-size:12px;color:var(--muted)}
.teampool .pill{padding:3px 11px;border-radius:999px;background:var(--blue-soft);color:var(--blue-d);font-weight:700;font-family:var(--mono)}
.teampool .pill.run{background:var(--green-soft);color:var(--green)}
.agents{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:7px;max-height:264px;overflow-y:auto}
.agent{display:flex;flex-direction:column;gap:1px;padding:7px 10px;background:var(--soft);border:1px solid var(--line);border-radius:9px;border-left:3px solid var(--blue)}
.agent .aid{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--blue-d)}
.agent .ak{font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agent .aage{font-size:10px;color:var(--dim);font-family:var(--mono)}
/* ── team war-room: 4-role command center + 24-teammate live grid ── */
.warroom{display:flex;gap:9px;flex-wrap:wrap;margin:2px 0 13px}
.wr{flex:1;min-width:235px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 13px;display:flex;gap:12px;align-items:center}
.wr .wr-body{flex:1;min-width:0}
.wr .guy{flex:none;transform:scale(1.12)}
.wr.mgr .guy .body{background:var(--amber)} .wr.mgr .guy .legL,.wr.mgr .guy .legR{background:#9a6a0a}
.wr.plan .guy .body{background:var(--blue-d)} .wr.plan .guy .legL,.wr.plan .guy .legR{background:#15368a}
.wr.score .guy .body{background:var(--green)} .wr.score .guy .legL,.wr.score .guy .legR{background:#176c45}
.wr .wr-role{font-size:10.5px;font-weight:700;letter-spacing:.6px;margin-bottom:6px;display:flex;align-items:center;gap:7px;text-transform:uppercase}
.wr.mgr .wr-role{color:var(--violet)} .wr.plan .wr-role{color:var(--blue-d)} .wr.score .wr-role{color:var(--green)}
.wr .wr-role .ic{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 0 3px color-mix(in srgb,currentColor 20%,transparent)}
.wr .wr-main{font-size:13px;font-weight:600;color:var(--ink);line-height:1.4}
.wr .wr-sub{font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.45;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.wr .wr-nums{display:flex;gap:18px;margin-top:5px}
.wr .wr-nums b{font-family:var(--mono);font-size:19px;color:var(--ink);line-height:1.1}
.wr .wr-nums span{font-size:10.5px;color:var(--dim);display:block;margin-top:1px}
.tmgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:9px;max-height:600px;overflow-y:auto;padding:2px}
.tm{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:10px 12px;border-top:3px solid var(--line)}
.tm.eng{border-top-color:var(--blue)} .tm.rev{border-top-color:var(--violet)}
.tm.win{box-shadow:0 0 0 1.5px var(--green) inset}
.tm .tm-h{display:flex;justify-content:space-between;align-items:baseline;gap:6px}
.tm .tm-k{font-size:12.5px;font-weight:700;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tm .tm-rnd{font-family:var(--mono);font-size:10px;color:var(--dim);white-space:nowrap}
.tm-pipe{display:flex;align-items:center;gap:5px;margin:8px 0 7px}
.tm-pipe .r{font-size:10px;font-weight:700;padding:3px 0;border-radius:7px;border:1px solid var(--line);color:var(--dim);background:var(--soft);flex:1;text-align:center}
.tm-pipe .r.on.eng{color:#fff;background:linear-gradient(135deg,var(--blue),#5b96ff);border-color:transparent}
.tm-pipe .r.on.rev{color:#fff;background:linear-gradient(135deg,var(--violet),#9b7bff);border-color:transparent}
.tm-pipe .ar{color:var(--dim);font-size:12px}
.tm .tm-act{font-size:11px;color:var(--muted);line-height:1.42;height:31px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.tm .tm-f{display:flex;justify-content:space-between;align-items:center;margin-top:8px;font-family:var(--mono)}
.tm .tm-ms{font-size:12px;color:var(--ink);font-weight:600}
.tm .tm-ms .lab{color:var(--dim);font-weight:400;font-size:10px}
.tm .spd{font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px;background:var(--green-soft);color:var(--green)}
.tm .spd.flat{background:var(--soft);color:var(--dim)}
/* ── pixel-art research lab: a little character per agent ── */
.lab{background:linear-gradient(#eef4ff,#e3ecfb);border:1px solid var(--line);border-radius:14px;padding:14px 12px 10px;
  background-image:linear-gradient(#eef4ff,#e3ecfb),repeating-linear-gradient(0deg,transparent 0 23px,#0001 23px 24px),repeating-linear-gradient(90deg,transparent 0 23px,#0001 23px 24px);
  image-rendering:pixelated}
.wsgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:11px}
.ws{display:flex;flex-direction:column;align-items:center;gap:2px;padding:8px 5px 7px;background:#ffffffcc;border:1px solid var(--line);
  border-radius:10px;border-bottom:3px solid var(--line);position:relative}
.ws.eng{border-bottom-color:var(--blue)} .ws.rev{border-bottom-color:var(--violet)}
.ws.win{box-shadow:0 0 0 2px var(--green) inset;background:#f3fdf7cc}
.ws .bubble{font-size:17px;line-height:1;height:21px;filter:drop-shadow(0 1px 0 #0002)}
.ws.act-thinking .bubble{animation:bpulse 1.5s ease-in-out infinite}
.ws.act-evaluating .bubble{animation:bspin 1.2s linear infinite}
.ws.act-profiling .bubble,.ws.act-reading .bubble{animation:bbob 1.5s ease-in-out infinite}
.guy{position:relative;width:26px;height:30px;animation:hop 2.4s ease-in-out infinite}
.guy i{position:absolute;display:block;image-rendering:pixelated}
.guy .hair{left:6px;top:-1px;width:14px;height:5px;background:#5a3a22;border-radius:3px 3px 0 0}
.guy .head{left:7px;top:2px;width:12px;height:10px;background:#f3c79a;border-radius:2px;
  background-image:radial-gradient(circle 1.1px at 4px 5px,#23344f 99%,transparent),radial-gradient(circle 1.1px at 9px 5px,#23344f 99%,transparent)}
.guy .body{left:5px;top:12px;width:16px;height:11px;border-radius:3px;background:var(--blue)}
.guy .armL,.guy .armR{top:13px;width:3px;height:8px;background:#f3c79a;border-radius:2px}
.guy .armL{left:3px} .guy .armR{right:3px}
.guy .legL,.guy .legR{top:22px;width:5px;height:7px;background:#34507a;border-radius:0 0 2px 2px}
.guy .legL{left:6px} .guy .legR{right:6px}
.ws.rev .guy .body{background:var(--violet)} .ws.rev .guy .legL,.ws.rev .guy .legR{background:#5b3aa8}
.ws.act-coding .guy .armL{animation:tapA .42s steps(2,jump-none) infinite}
.ws.act-coding .guy .armR{animation:tapB .42s steps(2,jump-none) infinite}
.ws.act-diffing .guy .armR{animation:tapB .55s steps(2,jump-none) infinite}
.ws.act-reading .guy{animation:lean 2.4s ease-in-out infinite}
.ws .desk{margin-top:3px;width:36px;height:21px;background:#bcd4f2;border:2px solid #34507a;border-radius:3px;
  display:flex;align-items:center;justify-content:center;position:relative}
.ws .desk .scr{font-family:var(--mono);font-size:8px;font-weight:700;color:#13357f}
.ws.act-evaluating .desk{animation:glow 1.1s ease-in-out infinite}
.ws .desk::after{content:'';position:absolute;bottom:-5px;left:50%;transform:translateX(-50%);width:11px;height:4px;background:#34507a;border-radius:0 0 2px 2px}
.ws .k{font-size:11px;font-weight:700;color:var(--ink);margin-top:7px;text-align:center;line-height:1.15}
.ws .doing{font-size:10.5px;color:var(--muted);text-align:center}
.ws .doing b{color:var(--ink)} .ws.rev .doing .rv{color:var(--violet);font-weight:700}
.ws .meta{display:flex;gap:6px;align-items:center;font-family:var(--mono);font-size:10px;color:var(--dim);margin-top:1px}
.ws .meta .spd2{font-weight:700;color:var(--green)}
@keyframes hop{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}}
@keyframes bbob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
@keyframes bpulse{0%,100%{opacity:.5;transform:scale(.9)}50%{opacity:1;transform:scale(1.12)}}
@keyframes bspin{to{transform:rotate(360deg)}}
@keyframes tapA{from{transform:translateY(0)}to{transform:translateY(-3px)}}
@keyframes tapB{from{transform:translateY(-3px)}to{transform:translateY(0)}}
@keyframes lean{0%,100%{transform:rotate(0)}50%{transform:rotate(4deg)}}
@keyframes glow{0%,100%{box-shadow:0 0 0 0 var(--green-soft)}50%{box-shadow:0 0 7px 1px var(--green)}}
/* 指标 chips */
.chips{display:flex;gap:10px;flex-wrap:wrap}
.chip{flex:1;min-width:78px;padding:13px 14px;background:var(--soft);border-radius:11px;text-align:center}
.chip .v{font-size:22px;font-weight:700;color:var(--ink)}
.chip .l{font-size:12px;color:var(--muted);margin-top:1px}
.chip.good .v{color:var(--green)} .chip.warn .v{color:var(--amber)} .chip.bad .v{color:var(--red)}
/* 排行榜 */
.lb{border:1px solid var(--line);border-radius:11px;overflow:hidden}
.lb .row{display:flex;justify-content:space-between;align-items:center;padding:7px 12px;font-size:13px;border-bottom:1px solid var(--line)}
.lb .row:last-child{border-bottom:none}
.lb .row.ours{background:var(--green-soft);font-weight:600}
.lb .row.ref{color:var(--muted)}
.lb .row .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:64%}
.lb .row .sc{font-family:var(--mono)}
.lb .row .tagm{font-size:10px;padding:1px 7px;border-radius:999px;margin-left:6px}
.lb .row.ours .tagm{background:var(--green);color:#fff}
/* 任务 + 事件 */
.bl .it{display:flex;gap:10px;align-items:center;padding:5px 0;font-size:13px}
.bl .it .s{flex-shrink:0;font-size:11px;padding:2px 9px;border-radius:999px;font-weight:500}
.bl .it .s.done{background:var(--green-soft);color:var(--green)}
.bl .it .s.running{background:var(--blue-soft);color:var(--blue-d)}
.bl .it .s.failed{background:var(--red-soft);color:var(--red)}
.bl .it .s.pending{background:var(--soft);color:var(--muted)}
.bl .it .t{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev{max-height:140px;overflow-y:auto;border:1px solid var(--line);border-radius:11px;padding:8px 12px;background:var(--soft)}
.ev .e{display:flex;gap:9px;padding:3px 0;font-size:12px}
.ev .e .ty{flex-shrink:0;width:108px;color:var(--blue);font-family:var(--mono);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev .e .tx{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.foot{margin-top:28px;font-size:12px;color:var(--dim);text-align:center;font-family:var(--mono)}
.empty{color:var(--dim);font-size:13px;padding:6px 0}
/* 可点击卡片 + 触感 */
.card{cursor:pointer;transition:transform .14s ease,box-shadow .14s ease,border-color .14s}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(30,60,120,.10);border-color:#c5d6f0}
.card:active{transform:translateY(0) scale(.995)}
.card .more{font-size:12px;color:var(--blue);font-weight:500;margin-top:2px;display:inline-flex;align-items:center;gap:3px}
/* 详情抽屉 */
.scrim{position:fixed;inset:0;background:rgba(20,38,70,.42);backdrop-filter:blur(2px);opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}
.scrim.open{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;height:100vh;width:min(680px,94vw);background:var(--bg);
  box-shadow:-12px 0 40px rgba(20,38,70,.16);transform:translateX(100%);transition:transform .26s cubic-bezier(.4,0,.2,1);
  z-index:50;overflow-y:auto;display:flex;flex-direction:column}
.drawer.open{transform:translateX(0)}
.dwrap{padding:24px clamp(16px,3vw,30px) 60px}
.dhdr{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;position:sticky;top:0;
  background:var(--bg);padding-bottom:14px;margin-bottom:6px;border-bottom:1px solid var(--line);z-index:2}
.dhdr h2{font-size:21px;font-weight:700}
.dhdr .dmeta{font-size:12px;color:var(--muted);font-family:var(--mono);margin-top:4px}
.dhdr .path{font-size:11px;color:var(--dim);font-family:var(--mono);word-break:break-all;margin-top:2px}
.close{flex-shrink:0;width:34px;height:34px;border-radius:9px;border:1px solid var(--line);background:var(--card);
  color:var(--muted);font-size:18px;cursor:pointer;line-height:1;transition:background .14s}
.close:hover{background:var(--soft);color:var(--ink)}
/* 抽屉内分组：plain layout, divide 而非套卡片 */
.dsec{padding:18px 0;border-bottom:1px solid var(--line)}
.dsec:last-child{border-bottom:none}
.dsec h3{font-size:14px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:7px}
.dsec h3 .n{font-size:11px;color:var(--muted);font-weight:400}
/* 阶段详情行 */
.stagerow{display:grid;grid-template-columns:80px 1fr;gap:12px;padding:9px 0;border-top:1px solid var(--line);align-items:start}
.stagerow:first-child{border-top:none}
.stagerow .nm{font-size:13px;font-weight:600}
.stagerow .nm .b{display:inline-block;margin-top:3px;font-size:10px;padding:1px 8px;border-radius:999px;font-weight:500}
.stagerow .nm .b.done{background:var(--green-soft);color:var(--green)}
.stagerow .nm .b.running,.stagerow .nm .b.ready{background:var(--blue-soft);color:var(--blue-d)}
.stagerow .nm .b.blocked{background:var(--red-soft);color:var(--red)}
.stagerow .nm .b.pending{background:var(--soft);color:var(--muted)}
.stagerow .rsn{font-size:12.5px;color:var(--muted);line-height:1.5}
.stagerow .art{font-size:11px;color:var(--blue);font-family:var(--mono);margin-top:3px;word-break:break-all}
/* 时间线 */
.tl .ti{display:grid;grid-template-columns:auto 1fr auto;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:12.5px;align-items:center}
.tl .ti:first-child{border-top:none}
.tl .ti .dot{width:8px;height:8px;border-radius:50%}
.tl .ti .dot.ok{background:var(--green)} .tl .ti .dot.bad{background:var(--red)}
.tl .ti .txt{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tl .ti .cost{font-family:var(--mono);color:var(--muted);font-size:11px;white-space:nowrap}
/* 实验逐seed */
.att{border-top:1px solid var(--line);padding:11px 0}
.att:first-child{border-top:none}
.att .top{display:flex;justify-content:space-between;align-items:center;gap:10px}
.att .top .nm{font-size:13px;font-weight:600;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.att .top .mean{font-family:var(--mono);font-weight:700;color:var(--blue-d);white-space:nowrap}
.att .top .pm{font-size:10px;background:var(--amber-soft);color:var(--amber);padding:1px 7px;border-radius:999px;margin-left:6px}
.att .seeds{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}
.att .seeds .sd{font-family:var(--mono);font-size:10.5px;color:var(--muted);background:var(--soft);padding:2px 7px;border-radius:6px}
.att .chg{font-size:11.5px;color:var(--dim);margin-top:6px;line-height:1.5}
/* 列表通用 */
.list .li{padding:7px 0;border-top:1px solid var(--line);font-size:12.5px;display:flex;gap:9px}
.list .li:first-child{border-top:none}
.list .li .k{flex-shrink:0;color:var(--blue);font-family:var(--mono);font-size:11.5px;min-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.list .li .v{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* 骨架屏 */
.sk{background:linear-gradient(90deg,var(--soft) 25%,#e3ebf7 50%,var(--soft) 75%);background-size:200% 100%;
  animation:shimmer 1.3s infinite;border-radius:8px}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.sk.line{height:13px;margin:8px 0}
/* 阶段行可点击 + 二级产物 */
.stagerow.stage-clk{cursor:pointer;border-radius:9px;margin:0 -8px;padding-left:8px;padding-right:8px;transition:background .12s}
.stagerow.stage-clk:hover{background:var(--soft)}
.stagerow .exp{font-size:10px;color:var(--blue);font-weight:500;white-space:nowrap;margin-left:4px}
.stagebody{margin-top:0}
.stagebody:not(:empty){margin-top:9px;padding-top:9px;border-top:1px dashed var(--line)}
.artf{padding:7px 0;border-top:1px solid var(--line)}
.artf:first-child{border-top:none}
.artf .afh{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.artf .afn{font-family:var(--mono);font-size:11.5px;color:var(--blue-d);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.artf .afk{font-size:10px;color:var(--muted);white-space:nowrap}
.artf .afs{font-size:11.5px;color:var(--muted);margin-top:3px;line-height:1.5;max-height:54px;overflow:hidden}
.art2{font-size:12px;color:var(--muted);padding:4px 0}
/* prefers-reduced-motion (mandatory — taste §6.B, Impeccable, fdpro §2):
   honor OS "reduce motion" by stilling all pulse/shimmer/slide/lift. */
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
    transition-duration:.001ms!important;scroll-behavior:auto!important}
  .hdr .live .dot,.pipe .st.running,.pipe .st.ready,.sk{animation:none!important}
  .card:hover{transform:none}
  .drawer{transition:none}
}
</style></head><body><div class="wrap">
<div class="hdr">
  <div><h1><span class="logo"></span>Argus 工作台</h1><div class="sub">7×24 自主科研守护进程 · 自动发现全部项目</div></div>
  <div class="meta"><span class="live"><span class="dot"></span>每 20 秒自动刷新</span><br><span id="gen">—</span></div>
</div>
<div class="kpis" id="kpis"></div>
<div class="gpustrip" id="gpustrip"></div>
<div class="grid" id="grid"></div>
<div class="foot" id="foot">加载中…</div>
</div>
<div class="scrim" id="scrim" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer"><div class="dwrap" id="dwrap"></div></div>
<script>
// 中文映射
const STAGE_CN={research:'调研',plan:'规划',benchmark:'基准',run:'实验',analysis:'分析',draft:'撰写',review:'评审',submission:'投稿',
  setup:'准备',optimize:'优化',measure:'测量',report:'汇报'};
const VERT_CN={research:'论文',speedrun:'刷榜',custom:'自定义'};
const ROLE_CN={planner:'规划员',engineer:'工程师',reviewer:'评审员'};
const STATUS_CN={done:'完成',running:'进行中',failed:'失败',pending:'待办',ready:'就绪',blocked:'受阻'};
const CHIP_CN={'PDF pages':'论文页数','TBD left':'待填占位','figures':'插图数','citations':'引用数','best (↓)':'最佳分(越低越好)','attempts':'尝试次数','invention':'原创验证'};

function gpuColor(u){return u>70?'var(--red)':u>30?'var(--amber)':'var(--green)'}
function renderGPU(g){const el=document.getElementById('gpustrip');if(!g||!g.length){el.innerHTML='';return}
  el.innerHTML=g.map(x=>{const p=Math.round(100*x.used_mb/x.total_mb);return `<div class="gpu"><div class="top"><span>显卡 ${x.idx}</span><span class="pct">${x.util}% 占用</span></div><div class="bar"><div class="fill" style="width:${p}%;background:${gpuColor(x.util)}"></div></div><div class="nums"><span>已用 ${(x.used_mb/1024).toFixed(1)} GB</span><span>共 ${(x.total_mb/1024).toFixed(0)} GB</span></div></div>`}).join('')}
function sc(s){s=(s||'').toLowerCase();return s==='done'?'done':(s==='running'||s==='ready')?'running':s==='blocked'?'blocked':''}
function chipsHTML(panels){return (panels||[]).map(p=>{
  const c=(p.chips||[]).map(ch=>`<div class="chip ${ch.tone||''}"><div class="v">${ch.v}</div><div class="l">${CHIP_CN[ch.l]||ch.l}</div></div>`).join('');
  let lb='';if(p.leaderboard&&p.leaderboard.length){lb=`<div><div class="section-label">成绩排行 · 分数越低越好</div><div class="lb">${p.leaderboard.map(r=>`<div class="row ${r.kind}"><span class="nm">${r.name}${r.kind==='ours'?'<span class="tagm">本机</span>':''}</span><span class="sc">${r.mean.toFixed(4)}</span></div>`).join('')}</div></div>`}
  return `<div class="chips">${c}</div>${lb}`}).join('')}
function card(d){
  const stages=(d.stages||[]).map(s=>`<div class="st ${sc(s.status)}">${STAGE_CN[s.name]||s.name}</div>`).join('');
  const roles=['planner','engineer','reviewer'];
  const loop=roles.map((r,i)=>`<div class="ag ${d.active_role===r?'active':''}">${ROLE_CN[r]}</div>${i<2?'<span class="arr">→</span>':''}`).join('');
  const bl=(d.backlog||[]).slice(-5).reverse().map(b=>`<div class="it"><span class="s ${b.status}">${STATUS_CN[b.status]||b.status}</span><span class="t">${b.title}</span></div>`).join('');
  const ev=(d.events||[]).slice().reverse().map(e=>`<div class="e"><span class="ty">${e.type}</span><span class="tx">${e.text||''}</span></div>`).join('');
  const vt=d.vertical||'custom';
  const stg=STAGE_CN[d.current_stage]||d.current_stage;
  const act=d.current_action?`<div class="section-label">当前动作</div><div class="art2">${esc(d.current_action)}</div>`:'';
  const tm=d.teams||{};
  let teamsBlock='';
  if(tm.agents&&tm.agents.length){
    const fmt=v=>v==null?'—':(v<10?(+v).toFixed(4):(+v).toFixed(2));
    const ACT={thinking:['💭','构思中'],reading:['📖','读代码/查资料'],coding:['⌨️','写内核'],profiling:['🔬','profile 内核'],evaluating:['🚀','跑官方评分'],reviewing:['🔍','评审裁决'],diffing:['🔀','对比/回退'],starting:['✨','启动中']};
    const GUY='<div class="guy"><i class="hair"></i><i class="head"></i><i class="body"></i><i class="armL"></i><i class="armR"></i><i class="legL"></i><i class="legR"></i></div>';
    const cards=tm.agents.map(a=>{
      const win=a.speedup&&a.speedup>1.03, onR=a.role==='reviewer';
      const ac=ACT[a.activity]||ACT.thinking;
      const ms=a.best!=null?fmt(a.best)+' ms':'基线中';
      const spd=a.speedup?`<span class="spd2">${a.speedup}×</span>`:'';
      return `<div class="ws ${onR?'rev':'eng'} act-${a.activity} ${win?'win':''}" title="${esc(a.action||'')}">
        <div class="bubble">${ac[0]}</div>${GUY}
        <div class="desk"><span class="scr">${esc((a.kernel||'').slice(0,3))}</span></div>
        <div class="k">${esc(a.kernel)}</div>
        <div class="doing">${onR?'<span class="rv">评审员·</span>':''}<b>${ac[1]}</b></div>
        <div class="meta">${a.round!=null?'第'+a.round+'轮':''}<span>${ms}</span>${spd}</div></div>`;
    }).join('');
    const mgr=`<div class="wr mgr">${GUY}<div class="wr-body"><div class="wr-role"><span class="ic"></span>Manager · Curator 池</div>
      <div class="wr-nums"><span><b>${tm.running}</b>在岗</span><span><b>${tm.width||tm.running}</b>目标宽度</span></div>
      <div class="wr-sub">维持池满,teammate 死了自动补 · ${tm.state||'运行中'}</div></div></div>`;
    const plan=`<div class="wr plan">${GUY}<div class="wr-body"><div class="wr-role"><span class="ic"></span>Planner · ${ROLE_CN[d.active_role]||'监督'}</div>
      <div class="wr-main">${esc((d.current_action||'巡视全局…').slice(0,66))}</div>
      <div class="wr-sub">看全 ${tm.running} 路 · 重排优先级 · 跨 kernel 蒸馏过程数据</div></div></div>`;
    const score=`<div class="wr score">${GUY}<div class="wr-body"><div class="wr-role"><span class="ic"></span>战绩 · 官方验证</div>
      <div class="wr-nums"><span><b>${tm.improved||0}</b>已提速</span><span><b>${tm.best_speedup?tm.best_speedup+'×':'—'}</b>最佳加速</span></div>
      <div class="wr-sub">仅计 official=true 锁频背书的真实加速</div></div></div>`;
    teamsBlock=`<div><div class="section-label">作战室 · 四角色协作（Manager · Planner · Engineer ⇄ Reviewer）</div>
      <div class="warroom">${mgr}${plan}${score}</div>
      <div class="section-label">研究所 · ${tm.running} 个小人实时作业（🔵 工程师 / 🟣 评审员，看头顶气泡知道在干嘛）</div>
      <div class="lab"><div class="wsgrid">${cards}</div></div></div>`;
  }else if(tm.team_id){
    teamsBlock=`<div><div class="section-label">团队智能体</div><div class="empty">当前无在岗 agent（池子刚轮换/补充中，目标 ${tm.width||'?'} · ${tm.state||'—'}）</div></div>`;
  }
  return `<div class="card" onclick="openDrawer('${d.fingerprint}')">
    <div class="chead">
      <div><h2>${d.title}</h2>
        <div class="meta2">当前阶段「${stg}」· 已完成 ${d.missions||0} 个任务 · 花费 $${d.cost||0}
          · <span class="status ${d.alive?'on':'off'}"><span class="d"></span>${d.alive?'运行中 '+(d.etime||''):'已停止'}</span></div>
        <div class="more">点击查看完整细节 →</div>
      </div>
      <span class="tag ${vt}">${VERT_CN[vt]||vt}</span>
    </div>
    ${act}
    ${teamsBlock}
    ${stages?`<div><div class="section-label">流程进度</div><div class="pipe">${stages}</div></div>`:''}
    <div><div class="section-label">三角色协作 · 当前活跃</div><div class="loop">${loop}</div></div>
    ${chipsHTML((d.enrich||{}).panels)}
    <div><div class="section-label">最近任务</div><div class="bl">${bl||'<div class="empty">暂无</div>'}</div></div>
    <div><div class="section-label">实时事件</div><div class="ev">${ev||'<div class="empty">暂无</div>'}</div></div>
  </div>`}
async function tick(){try{
  const r=await fetch('/data.json?_='+Date.now());const d=await r.json();
  document.getElementById('gen').textContent='更新于 '+d.generated_str;
  renderGPU(d.gpu);
  const ps=d.projects||[];
  const alive=ps.filter(p=>p.alive).length;
  const ms=ps.reduce((a,x)=>a+(x.missions||0),0);
  const tot=ps.reduce((a,x)=>a+(x.cost||0),0).toFixed(2);
  document.getElementById('kpis').innerHTML=`
    <div class="kpi"><div class="v">${ps.length}</div><div class="l">监控项目</div></div>
    <div class="kpi"><div class="v">${alive}</div><div class="l">运行中</div></div>
    <div class="kpi"><div class="v">${ms}</div><div class="l">累计任务</div></div>
    <div class="kpi"><div class="v">$${tot}</div><div class="l">累计花费</div></div>`;
  document.getElementById('grid').innerHTML=ps.map(card).join('');
  document.getElementById('foot').textContent='argus-skill --dashboard · 自动发现 '+ps.length+' 个守护进程';
}catch(e){document.getElementById('foot').textContent='获取数据失败：'+e}}
// ===== 详情抽屉 =====
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function skeleton(){return `<div class="dhdr"><div style="flex:1"><div class="sk line" style="width:50%"></div><div class="sk line" style="width:30%"></div></div><button class="close" onclick="closeDrawer()">×</button></div>`+
  Array(4).fill('<div class="dsec"><div class="sk line" style="width:35%"></div><div class="sk line"></div><div class="sk line" style="width:80%"></div></div>').join('')}
async function openDrawer(fp){
  const dw=document.getElementById('dwrap');
  dw.innerHTML=skeleton();
  document.getElementById('scrim').classList.add('open');
  document.getElementById('drawer').classList.add('open');
  try{
    const r=await fetch('/detail?fp='+encodeURIComponent(fp)+'&_='+Date.now());
    const d=await r.json();
    if(d.error){dw.innerHTML=`<div class="dhdr"><h2>加载失败</h2><button class="close" onclick="closeDrawer()">×</button></div><div class="dsec">${esc(d.error)}</div>`;return}
    dw.innerHTML=renderDetail(d);
  }catch(e){dw.innerHTML=`<div class="dsec">获取详情失败：${esc(e)}</div>`}
}
function closeDrawer(){document.getElementById('scrim').classList.remove('open');document.getElementById('drawer').classList.remove('open')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer()});
function renderDetail(d){
  const vt=d.vertical||'custom';
  let h=`<div class="dhdr"><div>
      <h2>${esc(d.title)} <span class="tag ${vt}" style="vertical-align:middle">${VERT_CN[vt]||vt}</span></h2>
      <div class="dmeta">阶段「${STAGE_CN[d.current_stage]||d.current_stage}」· ${d.missions||0} 任务 · $${d.cost||0}
        · ${d.alive?'运行中 '+(d.etime||''):'已停止'} · pid ${d.pid||'—'}</div>
      <div class="path">${esc(d.root||d.life_dir)}</div>
    </div><button class="close" onclick="closeDrawer()">×</button></div>`;
  // 阶段详情（可再点进去看产物）
  if((d.stage_detail||[]).length){
    h+=`<div class="dsec"><h3>流程阶段 <span class="n">${d.stage_detail.length} 个 · 点阶段看产物</span></h3>`;
    h+=d.stage_detail.map(s=>`<div class="stagerow stage-clk" onclick="toggleStage('${d.fingerprint}','${s.name}',this)">
      <div class="nm">${STAGE_CN[s.name]||s.name}<br><span class="b ${sc(s.status)||s.status}">${STATUS_CN[s.status]||s.status}</span></div>
      <div><div class="rsn">${s.reason?esc(s.reason):'<span style="color:var(--dim)">—</span>'} <span class="exp">展开 ▾</span></div>${s.artifact?`<div class="art">${esc(s.artifact)}</div>`:''}<div class="stagebody"></div></div>
    </div>`).join('');
    h+=`</div>`;
  }
  // 最近裁决 / 回滚
  if(d.last_gate&&d.last_gate.verdict){
    h+=`<div class="dsec"><h3>最近裁决</h3><div class="rsn"><b>${esc(d.last_gate.verdict)}</b><br>${esc(d.last_gate.reason||'')}</div></div>`;
  }
  if((d.rollback_history||[]).length){
    h+=`<div class="dsec"><h3>回滚记录 <span class="n">${d.rollback_history.length} 次</span></h3><div class="list">`+
      d.rollback_history.map(r=>`<div class="li"><span class="k">${STAGE_CN[r.from_stage]||r.from_stage}→${STAGE_CN[r.to_stage]||r.to_stage}</span><span class="v">${esc(r.reason||'')}</span></div>`).join('')+`</div></div>`;
  }
  // 实验逐seed（speedrun）
  if((d.attempts||[]).length){
    h+=`<div class="dsec"><h3>实验记录 <span class="n">${d.attempts.length} 个</span></h3>`;
    h+=d.attempts.map(a=>`<div class="att">
      <div class="top"><span class="nm">${esc(a.name)}${a.has_postmortem?'<span class="pm">原创验证</span>':''}</span>
        <span class="mean">${a.mean!=null?a.mean.toFixed(4):'未评分'}</span></div>
      ${a.seeds&&a.seeds.length?`<div class="seeds">${a.seeds.map(s=>`<span class="sd">种子${s.seed}: ${s.val_bpb}</span>`).join('')}</div>`:''}
      ${a.changes?`<div class="chg">${esc(a.changes)}</div>`:''}
    </div>`).join('');
    h+=`</div>`;
  }
  // 论文结构（research）
  const pp=d.paper||{};
  if((pp.sections||[]).length||(pp.figures||[]).length){
    h+=`<div class="dsec"><h3>论文结构 <span class="n">${(pp.sections||[]).length}章 / ${(pp.figures||[]).length}图 / ${(pp.experiments||[]).length}实验</span></h3><div class="list">`+
      (pp.sections||[]).map(s=>`<div class="li"><span class="k">${esc(s.title)}</span><span class="v">${esc(s.goal)}</span></div>`).join('')+`</div></div>`;
  }
  // 任务历史
  if((d.mission_timeline||[]).length){
    h+=`<div class="dsec"><h3>任务历史 <span class="n">近 ${d.mission_timeline.length} 个</span></h3><div class="tl">`+
      d.mission_timeline.map(m=>`<div class="ti"><span class="dot ${m.ok?'ok':'bad'}"></span><span class="txt">${m.ok?'✓ 完成':'✗ '+(STATUS_CN[m.status]||m.status)}${m.reason?' · '+esc(m.reason):''}</span><span class="cost">${m.rounds?m.rounds+'轮 ':''}$${m.cost}</span></div>`).join('')+`</div></div>`;
  }
  // 全部待办
  if((d.backlog_full||[]).length){
    h+=`<div class="dsec"><h3>任务队列 <span class="n">${d.backlog_full.length} 条</span></h3><div class="bl">`+
      d.backlog_full.map(b=>`<div class="it"><span class="s ${b.status}">${STATUS_CN[b.status]||b.status}</span><span class="t">${esc(b.title)}</span></div>`).join('')+`</div></div>`;
  }
  // 完整事件
  if((d.events_full||[]).length){
    h+=`<div class="dsec"><h3>事件流 <span class="n">近 ${d.events_full.length} 条</span></h3><div class="ev" style="max-height:280px">`+
      d.events_full.map(e=>`<div class="e"><span class="ty">${esc(e.type)}</span><span class="tx">${esc(e.text||'')}</span></div>`).join('')+`</div></div>`;
  }
  return h;
}
// 阶段二级下钻：点阶段行 → 拉 /stage 看该阶段的具体产物
const ARTKIND_CN={file:'文件',dir:'目录',table:'数据表',pdf:'PDF',md:'文档'};
async function toggleStage(fp,stage,row){
  const body=row.querySelector('.stagebody');
  const exp=row.querySelector('.exp');
  if(body.dataset.open==='1'){body.innerHTML='';body.dataset.open='0';if(exp)exp.textContent='展开 ▾';return}
  body.dataset.open='1'; if(exp)exp.textContent='收起 ▴';
  body.innerHTML='<div class="sk line" style="width:60%"></div>';
  try{
    const r=await fetch('/stage?fp='+encodeURIComponent(fp)+'&stage='+encodeURIComponent(stage)+'&_='+Date.now());
    const d=await r.json();
    if(d.error){body.innerHTML='<div class="art2">'+esc(d.error)+'</div>';return}
    if(!d.artifacts||!d.artifacts.length){body.innerHTML='<div class="art2" style="color:var(--dim)">该阶段暂无可展示的产物文件</div>';return}
    body.innerHTML=d.artifacts.map(a=>{
      const nm=a.path.split('/').slice(-2).join('/');
      const k=ARTKIND_CN[a.kind]||a.kind;
      let meta='';
      if(a.kind==='table')meta=`${a.rows} 行`;
      else if(a.kind==='dir')meta=`${a.rows} 项`;
      else if(a.kind==='pdf')meta=a.snippet;
      else if(a.size!=null)meta=(a.size/1024).toFixed(1)+' KB';
      return `<div class="artf"><div class="afh"><span class="afn">${esc(nm)}</span><span class="afk">${k}${meta?' · '+meta:''}</span></div>${a.snippet&&a.kind!=='pdf'?`<div class="afs">${esc(a.snippet)}</div>`:''}</div>`;
    }).join('');
  }catch(e){body.innerHTML='<div class="art2">加载失败：'+esc(e)+'</div>'}
}
tick();setInterval(tick,20000);
</script></body></html>"""


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="argus-skill-dashboard")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--once", action="store_true",
                   help="print one JSON snapshot and exit (no server)")
    args = p.parse_args(argv)
    if args.once:
        print(json.dumps(scrape_all(), indent=2))
        return 0
    return serve(port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
