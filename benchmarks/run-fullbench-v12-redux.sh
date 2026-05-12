#!/usr/bin/env bash
# argus-skill v12 REDUX — FULL TerminalBench v2, re-runs v12 baseline against
# the restored default config + raw-evidence pipeline (engineer self-report +
# runtime probe + official verifier with "ground truth, trust this" framing).
#
# Code restoration commit: see HEAD around 2026-05-22; the changes vs v12 run
# script are:
#   - Defaults (effort/model) are now baked into harbor_adapter.py
#   - Raw-evidence three-section block reconstructed (was lost between v12
#     and current HEAD; see RETROSPECTIVE-v12-vs-current.md)
#
# This script intentionally re-exports the v12 env vars _explicitly_ so it
# stays a self-contained, reproducible artifact even if defaults drift.
set -uo pipefail

EXP_ROOT="/home/argustest/argus-skill/benchmarks/results/tb2-fullbench-2026-05-22-v12-redux"
EXP_DIR="$EXP_ROOT/argus-skill-codex"
LOG_DIR="$EXP_DIR/logs"
LOG_FILE="$LOG_DIR/run.log"
PID_FILE="$LOG_DIR/run.pid"
DECISIONS_LOG="$LOG_DIR/decisions.jsonl"

mkdir -p "$LOG_DIR" "$EXP_DIR/skills" "$EXP_DIR/jobs"

cd /home/argustest/argus-skill

# --- credentials -----------------------------------------------------------
# Source order: 1) existing env (shell export), 2) ~/.codex/auth.json.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f /home/argustest/.codex/auth.json ]; then
  OPENAI_API_KEY="$(python3 -c "import json,sys; print(json.load(open('/home/argustest/.codex/auth.json'))['OPENAI_API_KEY'])")"
  export OPENAI_API_KEY
fi
: "${OPENAI_API_KEY:?could not find OPENAI_API_KEY (env unset and ~/.codex/auth.json unreadable)}"

export OPENAI_BASE_URL='https://ai4m6.openai.azure.com/openai/v1/'
export PYTHONPATH=/home/argustest/argus-skill

# --- v12 baseline config (matches benchmarks/run-fullbench-v12.sh) ---------
export ARGUS_SKILL_HARBOR_SCIENTIST_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT=high
export ARGUS_SKILL_HARBOR_REVIEWER_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_REVIEWER_EFFORT=medium
export ARGUS_SKILL_HARBOR_SKILLS_DIR="$EXP_DIR/skills"
export ARGUS_SKILL_HARBOR_DECISIONS_LOG="$DECISIONS_LOG"
export ARGUS_SKILL_HARBOR_DISTILL_BUDGET=120
export ARGUS_SKILL_HARBOR_REVIEWER_BUDGET=60
export ARGUS_SKILL_HARBOR_ROUND_TIMEOUT=1800
export ARGUS_SKILL_HARBOR_MAX_ROUNDS=2
export ARGUS_SKILL_HARBOR_REVIEWER_GATE=0
# v12 phase-4 (restored): runtime probe + official verifier auto-run.
# Both default ON in code, but we set them explicitly so this script
# stays reproducible if defaults move again.
export ARGUS_SKILL_HARBOR_RUNTIME_PROBE=1
export ARGUS_SKILL_HARBOR_V12_VERIFIER=1

CONCURRENCY=8

{
  echo "=========================================="
  echo " argus-skill v12 REDUX terminal-bench@2.0 (defaults + raw-evidence restored)"
  echo " started_at        : $(date -Iseconds)"
  echo " host              : $(hostname)"
  echo " exp_dir           : $EXP_DIR"
  echo " concurrency       : $CONCURRENCY"
  echo " scientist         : $ARGUS_SKILL_HARBOR_SCIENTIST_MODEL  (effort=$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT)"
  echo " reviewer          : $ARGUS_SKILL_HARBOR_REVIEWER_MODEL  (effort=$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT)"
  echo " engineer (cont.)  : openai/gpt-5.4-mini  (effort=high)"
  echo " skills_dir        : $ARGUS_SKILL_HARBOR_SKILLS_DIR (cold start)"
  echo " jobs_dir          : $EXP_DIR/jobs"
  echo " round_timeout_s   : $ARGUS_SKILL_HARBOR_ROUND_TIMEOUT"
  echo " max_rounds        : $ARGUS_SKILL_HARBOR_MAX_ROUNDS"
  echo " runtime_probe     : $ARGUS_SKILL_HARBOR_RUNTIME_PROBE"
  echo " v12_verifier      : $ARGUS_SKILL_HARBOR_V12_VERIFIER"
  echo " v12 baseline target: reward 0.5955, \$0.139/trial (tb2-fullbench-2026-05-06)"
  echo "=========================================="
} | tee "$LOG_FILE"

# Record PID for later monitoring
echo $$ > "$PID_FILE"

sg docker -c "
cd /home/argustest/argus-skill && \
  OPENAI_API_KEY='$OPENAI_API_KEY' \
  OPENAI_BASE_URL='$OPENAI_BASE_URL' \
  PYTHONPATH='$PYTHONPATH' \
  ARGUS_SKILL_HARBOR_SCIENTIST_MODEL='$ARGUS_SKILL_HARBOR_SCIENTIST_MODEL' \
  ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT='$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT' \
  ARGUS_SKILL_HARBOR_REVIEWER_MODEL='$ARGUS_SKILL_HARBOR_REVIEWER_MODEL' \
  ARGUS_SKILL_HARBOR_REVIEWER_EFFORT='$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT' \
  ARGUS_SKILL_HARBOR_SKILLS_DIR='$ARGUS_SKILL_HARBOR_SKILLS_DIR' \
  ARGUS_SKILL_HARBOR_DECISIONS_LOG='$ARGUS_SKILL_HARBOR_DECISIONS_LOG' \
  ARGUS_SKILL_HARBOR_DISTILL_BUDGET='$ARGUS_SKILL_HARBOR_DISTILL_BUDGET' \
  ARGUS_SKILL_HARBOR_REVIEWER_BUDGET='$ARGUS_SKILL_HARBOR_REVIEWER_BUDGET' \
  ARGUS_SKILL_HARBOR_ROUND_TIMEOUT='$ARGUS_SKILL_HARBOR_ROUND_TIMEOUT' \
  ARGUS_SKILL_HARBOR_MAX_ROUNDS='$ARGUS_SKILL_HARBOR_MAX_ROUNDS' \
  ARGUS_SKILL_HARBOR_REVIEWER_GATE='$ARGUS_SKILL_HARBOR_REVIEWER_GATE' \
  ARGUS_SKILL_HARBOR_RUNTIME_PROBE='$ARGUS_SKILL_HARBOR_RUNTIME_PROBE' \
  ARGUS_SKILL_HARBOR_V12_VERIFIER='$ARGUS_SKILL_HARBOR_V12_VERIFIER' \
  harbor run \
    --dataset terminal-bench@2.0 \
    --agent-import-path benchmarks.harbor_adapter:ArgusSkillCodex \
    --model openai/gpt-5.4-mini \
    --ak reasoning_effort=high \
    --agent-setup-timeout-multiplier 3 \
    -n $CONCURRENCY \
    --jobs-dir '$EXP_DIR/jobs' \
    -y
" 2>&1 | tee -a "$LOG_FILE"

rc=${PIPESTATUS[0]}
echo "[$(date -Iseconds)] sg docker exited rc=$rc" | tee -a "$LOG_FILE"
exit "$rc"
