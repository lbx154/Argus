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
_DASHBOARD_HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Argus · live</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=IBM+Plex+Sans:wght@300;400;600&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0a0a;--panel:rgba(255,255,255,.035);--panel2:rgba(255,255,255,.06);--line:rgba(255,255,255,.10);--ink:#f5f5f5;--muted:#8a8a8a;--dim:#5a5a5a;--gold:#e8c547;--cyan:#67e8c8;--red:#ff7766;--green:#8eda9d;--display:'Instrument Serif',Georgia,serif;--sans:'IBM Plex Sans',system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5;background:radial-gradient(60% 45% at 25% 0%,rgba(232,197,71,.10) 0%,transparent 70%),radial-gradient(55% 45% at 100% 25%,rgba(103,232,200,.09) 0%,transparent 70%),var(--bg);background-attachment:fixed}
.wrap{max-width:1320px;margin:0 auto;padding:28px clamp(16px,3vw,40px) 80px}
.hdr{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:16px;margin-bottom:28px}
.hdr h1{font-family:var(--display);font-style:italic;font-size:clamp(34px,4.5vw,58px);line-height:1;background:linear-gradient(120deg,#fff 30%,var(--gold) 60%,var(--cyan) 95%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr .sub{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.12em;text-transform:uppercase;margin-top:6px}
.hdr .meta{font-family:var(--mono);font-size:11px;color:var(--muted);text-align:right}
.hdr .live{color:var(--green);animation:pulse 2.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}
.gpustrip{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:26px}
.gpu{flex:1;min-width:170px;padding:12px 16px;background:var(--panel);border:1px solid var(--line);border-radius:10px;font-family:var(--mono)}
.gpu .top{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);text-transform:uppercase}
.gpu .bar{height:6px;background:rgba(255,255,255,.08);border-radius:3px;margin-top:8px;overflow:hidden}
.gpu .fill{height:100%;border-radius:3px;transition:width .6s}
.gpu .nums{display:flex;justify-content:space-between;font-size:12px;margin-top:6px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media (max-width:960px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;border-top:3px solid var(--cyan);padding:24px clamp(16px,2vw,28px);display:flex;flex-direction:column;gap:16px}
.card.research{border-top-color:var(--gold)}
.chead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.chead h2{font-family:var(--display);font-style:italic;font-weight:400;font-size:clamp(20px,2.3vw,28px);line-height:1.1}
.chead .vtag{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.1em;padding:3px 9px;border-radius:999px;border:1px solid var(--line);color:var(--cyan)}
.card.research .vtag{color:var(--gold)}
.pidline{font-family:var(--mono);font-size:11px;color:var(--muted)}
.pidline .ok{color:var(--green)}.pidline .dead{color:var(--red)}
.pipe{display:flex;gap:5px;flex-wrap:wrap}
.pipe .st{flex:1;min-width:48px;text-align:center;font-family:var(--mono);font-size:9.5px;text-transform:uppercase;padding:7px 3px;border-radius:7px;border:1px solid var(--line);color:var(--dim);background:rgba(255,255,255,.02)}
.pipe .st.done{color:var(--green);border-color:rgba(142,218,157,.3);background:rgba(142,218,157,.06)}
.pipe .st.running,.pipe .st.ready{color:var(--gold);border-color:rgba(232,197,71,.45);background:rgba(232,197,71,.08);animation:pulse 1.8s ease-in-out infinite}
.pipe .st.blocked{color:var(--red);border-color:rgba(255,119,102,.4);background:rgba(255,119,102,.07)}
.loop{display:flex;gap:8px;align-items:center;font-family:var(--mono);font-size:11px}
.loop .ag{flex:1;text-align:center;padding:9px 4px;border-radius:8px;border:1px solid var(--line);color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.loop .ag.active{color:var(--ink);border-color:var(--cyan);background:rgba(103,232,200,.08);box-shadow:0 0 16px rgba(103,232,200,.15)}
.loop .arr{color:var(--dim)}
.chips{display:flex;gap:10px;flex-wrap:wrap}
.chip{flex:1;min-width:80px;padding:11px 13px;background:var(--panel2);border:1px solid var(--line);border-radius:10px}
.chip .v{font-family:var(--mono);font-size:21px;font-weight:600}
.chip .l{font-family:var(--mono);font-size:9.5px;color:var(--muted);text-transform:uppercase;margin-top:2px}
.chip.good .v{color:var(--green)}.chip.warn .v{color:var(--gold)}.chip.bad .v{color:var(--red)}
.lb{font-family:var(--mono);font-size:12px}
.lb .row{display:flex;justify-content:space-between;padding:4px 8px;border-bottom:1px solid var(--line)}
.lb .row.ours{background:rgba(103,232,200,.07);color:var(--cyan);border-radius:5px}
.lb .row.ref{color:var(--dim)}
.lb .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:62%}
.sec-t{font-family:var(--mono);font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}
.bl .it{display:flex;gap:8px;font-family:var(--mono);font-size:12px;padding:3px 0}
.bl .it .s{flex-shrink:0;width:56px;font-size:10px;text-transform:uppercase}
.bl .it .s.done{color:var(--green)}.bl .it .s.running{color:var(--gold)}.bl .it .s.failed{color:var(--red)}.bl .it .s.pending{color:var(--dim)}
.bl .it .t{opacity:.85;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev{font-family:var(--mono);font-size:11px;max-height:150px;overflow-y:auto}
.ev .e{display:flex;gap:8px;padding:2px 0;color:var(--muted)}
.ev .e .ty{flex-shrink:0;width:128px;color:var(--cyan);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev .e .tx{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.foot{margin-top:30px;font-family:var(--mono);font-size:11px;color:var(--dim);text-align:center}
</style></head><body><div class="wrap">
<div class="hdr"><div><h1>Argus · live</h1><div class="sub">7×24 supervised research daemons · auto-discovered</div></div>
<div class="meta"><span class="live">● auto-refresh 20s</span><br><span id="gen">—</span></div></div>
<div class="gpustrip" id="gpustrip"></div><div class="grid" id="grid"></div><div class="foot" id="foot">loading…</div>
</div><script>
function gpuColor(u){return u>70?'var(--red)':u>30?'var(--gold)':'var(--green)'}
function renderGPU(g){const el=document.getElementById('gpustrip');if(!g||!g.length){el.innerHTML='';return}
el.innerHTML=g.map(x=>{const p=Math.round(100*x.used_mb/x.total_mb);return `<div class="gpu"><div class="top"><span>GPU ${x.idx}</span><span>${x.util}%</span></div><div class="bar"><div class="fill" style="width:${p}%;background:${gpuColor(x.util)}"></div></div><div class="nums"><span>${(x.used_mb/1024).toFixed(1)} GB</span><span>${(x.total_mb/1024).toFixed(0)} GB</span></div></div>`}).join('')}
function sc(s){s=(s||'').toLowerCase();return s==='done'?'done':(s==='running'||s==='ready')?'running':s==='blocked'?'blocked':''}
function chips(panels){return (panels||[]).map(p=>{const c=(p.chips||[]).map(ch=>`<div class="chip ${ch.tone||''}"><div class="v">${ch.v}</div><div class="l">${ch.l}</div></div>`).join('');let lb='';if(p.leaderboard&&p.leaderboard.length){lb=`<div><div class="sec-t">leaderboard · lower better</div><div class="lb">${p.leaderboard.map(r=>`<div class="row ${r.kind}"><span class="nm">${r.name}</span><span>${r.mean.toFixed(4)}</span></div>`).join('')}</div></div>`}return `<div class="chips">${c}</div>${lb}`}).join('')}
function card(d){const stages=(d.stages||[]).map(s=>`<div class="st ${sc(s.status)}">${s.name}</div>`).join('');const roles=['planner','engineer','reviewer'];const loop=roles.map((r,i)=>`<div class="ag ${d.active_role===r?'active':''}">${r}</div>${i<2?'<span class="arr">→</span>':''}`).join('');const bl=(d.backlog||[]).slice(-5).reverse().map(b=>`<div class="it"><span class="s ${b.status}">${b.status}</span><span class="t">${b.title}</span></div>`).join('');const ev=(d.events||[]).slice().reverse().map(e=>`<div class="e"><span class="ty">${e.type}</span><span class="tx">${e.text||''}</span></div>`).join('');const cls=d.vertical==='research'?'research':'';return `<div class="card ${cls}"><div class="chead"><div><h2>${d.title}</h2><div class="pidline">stage <b>${d.current_stage}</b> · ${d.missions||0} missions · $${d.cost||0} · <span class="${d.alive?'ok':'dead'}">${d.alive?'● pid '+d.pid+' '+(d.etime||''):'● DOWN'}</span></div></div><div class="vtag">${d.vertical||'?'}</div></div>${stages?`<div class="pipe">${stages}</div>`:''}<div class="loop">${loop}</div>${chips((d.enrich||{}).panels)}<div><div class="sec-t">backlog</div><div class="bl">${bl||'<div class="it"><span class="t" style="color:var(--dim)">—</span></div>'}</div></div><div><div class="sec-t">events</div><div class="ev">${ev}</div></div></div>`}
async function tick(){try{const r=await fetch('/data.json?_='+Date.now());const d=await r.json();document.getElementById('gen').textContent=d.generated_str;renderGPU(d.gpu);document.getElementById('grid').innerHTML=(d.projects||[]).map(card).join('');const tot=(d.projects||[]).reduce((a,x)=>a+(x.cost||0),0).toFixed(2);const ms=(d.projects||[]).reduce((a,x)=>a+(x.missions||0),0);document.getElementById('foot').textContent=`${d.n_projects} projects · ${ms} missions · $${tot} · argus-skill --dashboard`}catch(e){document.getElementById('foot').textContent='fetch error: '+e}}
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
