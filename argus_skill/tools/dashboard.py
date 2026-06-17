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
                r = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                   text=True, timeout=10)
                for line in r.stdout.splitlines():
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
        scored = []
        for d in sorted(attempts.iterdir()):
            cf = d / "results.csv"
            if not cf.exists():
                continue
            try:
                rows = list(csv.DictReader(cf.open()))
                bpb = [float(r["val_bpb"]) for r in rows if r.get("val_bpb")]
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
                for r in csv.DictReader(refcsv.open()):
                    agg.setdefault(r["label"], []).append(float(r["val_bpb"]))
                for k, v in agg.items():
                    scored.append({"name": k, "mean": round(statistics.mean(v), 4),
                                   "kind": "ref"})
            except Exception:
                pass
        scored.sort(key=lambda x: x["mean"])
        best = next((s["mean"] for s in scored if s["kind"] == "ours"), None)
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
        "backlog": [{"status": b.get("status", "?"), "title": (b.get("title") or "")[:130]}
                    for b in backlog[-6:]],
        "events": _recent_events(events),
        "active_role": _active_role(events),
        "enrich": enrich,
    }


def scrape_all(roots: list[Path] | None = None) -> dict:
    projects = []
    for life in discover_life_dirs(roots):
        try:
            projects.append(scrape_project(life))
        except Exception as exc:  # noqa: BLE001 — one bad project must not break all
            projects.append({"life_dir": str(life), "title": life.name,
                             "error": repr(exc)[:120], "alive": False, "stages": [],
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
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f4f7fc; --ink:#1a2942; --muted:#5b7090; --dim:#9aabc4;
  --line:#dde6f3; --card:#ffffff; --soft:#eef3fb;
  --blue:#2f6df0; --blue-d:#1c4fc0; --blue-soft:#e6efff;
  --green:#27a567; --green-soft:#e3f6ec;
  --amber:#e0930f; --amber-soft:#fbf0d8;
  --red:#e0544e; --red-soft:#fbe5e4;
  --sans:'Noto Sans SC',system-ui,sans-serif; --mono:'IBM Plex Mono',ui-monospace,monospace;
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
  display:flex;flex-direction:column;gap:18px;box-shadow:0 2px 10px rgba(30,60,120,.05)}
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
</style></head><body><div class="wrap">
<div class="hdr">
  <div><h1><span class="logo"></span>Argus 工作台</h1><div class="sub">7×24 自主科研守护进程 · 自动发现全部项目</div></div>
  <div class="meta"><span class="live"><span class="dot"></span>每 20 秒自动刷新</span><br><span id="gen">—</span></div>
</div>
<div class="kpis" id="kpis"></div>
<div class="gpustrip" id="gpustrip"></div>
<div class="grid" id="grid"></div>
<div class="foot" id="foot">加载中…</div>
</div><script>
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
  return `<div class="card">
    <div class="chead">
      <div><h2>${d.title}</h2>
        <div class="meta2">当前阶段「${stg}」· 已完成 ${d.missions||0} 个任务 · 花费 $${d.cost||0}
          · <span class="status ${d.alive?'on':'off'}"><span class="d"></span>${d.alive?'运行中 '+(d.etime||''):'已停止'}</span></div>
      </div>
      <span class="tag ${vt}">${VERT_CN[vt]||vt}</span>
    </div>
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
