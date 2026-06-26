#!/usr/bin/env bash
# ux_demo.sh — runnable BEFORE→AFTER proof for the 2026-06-26 REPL UX overhaul.
#
# Each block states the BEFORE symptom (observed by dogfooding the live REPL)
# and runs the AFTER behaviour in an ISOLATED life-dir, so nothing here touches
# a real daemon or your ~/.argus-skill projects. Run:  bash scripts/ux_demo.sh
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
export PYTHONPATH="$HERE"
DOG=$(mktemp -d /tmp/argus-ux-demo.XXXX)
trap 'for f in "$DOG"/projects/*/daemon.pid; do p=$(cat "$f" 2>/dev/null|tr -dc 0-9); [ -n "$p" ] && kill "$p" 2>/dev/null; done; rm -rf "$DOG"' EXIT
hr(){ printf '\n────────────────────────────────────────────────────────\n'; }

hr; echo "T1  daemon↔session coupling  (THE 卡住 bug)"
echo "BEFORE: bare argus-skill opened session s-XXXX but auto-spawned the daemon"
echo "        on the cwd-legacy project — two backlogs, task never ran, REPL froze."
echo "AFTER:  daemon's life_dir == the REPL session, so the task is the one drained:"
printf '帮我看下情况\n' | timeout 25 argus-skill --life-dir "$DOG" 2>&1 \
  | grep -iE "daemon started|new session" | sed 's/^/   /'
for p in "$DOG"/projects/*/; do
  echo "   → backlog landed in $(basename "$p"); daemon.pid present: $([ -f "$p/daemon.pid" ] && echo yes || echo no)"
done

hr; echo "T2  honest no-executor message  (no lie, no 600s freeze)"
echo "BEFORE: 'queued — daemon executing' even with no daemon, then a 10-min hang."
echo "AFTER:  (with --no-daemon, i.e. no executor) it tells the truth and returns:"
D2=$(mktemp -d /tmp/argus-ux-demo2.XXXX)
printf '优化点啥\n/exit\n' | timeout 25 argus-skill --life-dir "$D2" --no-daemon 2>&1 \
  | grep -iE "NO daemon|will NOT execute|--daemon|/doctor" | sed 's/^/   /'
rm -rf "$D2"

hr; echo "T3  surface a live daemon from a fresh session"
echo "BEFORE: a fresh session said 'no daemon' while the real daemon was invisible."
echo "AFTER:  the banner points straight at the running work:"
mkdir -p "$DOG/projects/s-running1"
printf '{"id":"s-running1","display_name":"优化 079 kernel","created":100,"last_active":100}' > "$DOG/projects/s-running1/session.json"
echo $$ > "$DOG/projects/s-running1/daemon.pid"
printf '%s' '{"daemon_pid":'$$',"started_at":0}' > "$DOG/projects/s-running1/daemon.status.json"
printf '/daemons\n/exit\n' | timeout 25 argus-skill --life-dir "$DOG" --no-daemon 2>&1 \
  | grep -iE "already running|--continue|live daemons|s-running1" | sed 's/^/   /'

hr; echo "T12  /doctor  (was: stuck with no path; now: the exact fix)"
printf '/doctor\n/exit\n' | timeout 25 argus-skill --life-dir "$DOG" --no-daemon 2>&1 \
  | sed -n '/doctor —/,/recommended/p' | sed 's/^/   /'

hr; echo "T18  empty-session litter GC  (move to trash, reversible)"
echo "BEFORE: every bare launch left an empty projects/<id>/ dir (73 piled up)."
echo "AFTER:  GC sweeps content-less, lockless dirs — but NEVER a live daemon:"
PYTHONPATH="$HERE" python3 - "$DOG" <<'PY'
import sys; from pathlib import Path
from argus_skill.core.project_gc import gc_stale_projects
gr=Path(sys.argv[1])
(gr/"projects"/"s-empty-litter").mkdir(parents=True, exist_ok=True)
would=gc_stale_projects(gr, dry_run=True)
print("   would sweep:", [w for w in would if "empty" in w])
print("   live daemon s-running1 swept? ->", "s-running1" in would, " (must be False)")
PY

hr; echo "All AFTER behaviours above are locked by tests:"
echo "   test_ux_daemon_coupling / test_ux_live_daemon / test_ux_litter_gc /"
echo "   test_ux_chat_clean / test_plan_mode / test_doctor"
hr
