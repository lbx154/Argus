#!/usr/bin/env bash
# argus-skill SWE-Bench-Pro pilot — 50 tasks, concurrency=8.
#
# Goals before the full 731-task run:
#   1. validate per-task token capture (engineer/reviewer/scientist)
#   2. validate USD cost computation
#   3. validate verifier outcome capture (pass/fail/error/not_run)
#   4. validate Phase-2 skill revision firing (writeback + auto-promote)
#   5. extrapolate cost & time to 731 tasks
#
# Sample is "first 50" across repos (deterministic for reproducibility).
# Switch to the full run by removing --max-tasks-per-repo and bumping the
# output dir name.
set -uo pipefail

EXP_ROOT="/home/argustest/argus-skill/benchmarks/results"
EXP_DIR="$EXP_ROOT/swebpro-pilot-2026-05-06"
LOG_DIR="$EXP_DIR/logs"
LOG_FILE="$LOG_DIR/run.log"
PID_FILE="$LOG_DIR/run.pid"
DECISIONS_LOG="$LOG_DIR/decisions.jsonl"

mkdir -p "$LOG_DIR" "$EXP_DIR/skills" "$EXP_DIR/jobs" "$EXP_DIR/pending_lessons"

cd /home/argustest/argus-skill

: "${OPENAI_API_KEY:?set OPENAI_API_KEY in your shell or a .env file before running this script}"
export OPENAI_BASE_URL='https://ai4m6.openai.azure.com/openai/v1/'
export PYTHONPATH=/home/argustest/argus-skill

# --- Argus-skill config (mirrors v12, but with skill-revision ENABLED for the pilot)
export ARGUS_SKILL_HARBOR_SCIENTIST_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT=high
export ARGUS_SKILL_HARBOR_REVIEWER_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_REVIEWER_EFFORT=high
export ARGUS_SKILL_HARBOR_SKILLS_DIR="$EXP_DIR/skills"
export ARGUS_SKILL_HARBOR_DECISIONS_LOG="$DECISIONS_LOG"
export ARGUS_SKILL_HARBOR_DISTILL_BUDGET=120
export ARGUS_SKILL_HARBOR_REVIEWER_BUDGET=60
# SWE-Bench-Pro tasks need longer rounds than short terminal-command tasks:
# multi-file repo edits take time. 1800s/round + max_rounds=2 = 60min cap.
export ARGUS_SKILL_HARBOR_ROUND_TIMEOUT=1800
export ARGUS_SKILL_HARBOR_MAX_ROUNDS=2
export ARGUS_SKILL_HARBOR_REVIEWER_GATE=0

# --- Phase-2 skill mutation (the whole point of measuring it)
export ARGUS_SKILL_REVISE_ON_WRITEBACK=1
export ARGUS_SKILL_AUTO_PROMOTE_LESSON=1

# --- SWE-Bench-Pro runner
export ARGUS_SKILL_SWEBPRO_RUN_SCRIPTS=/home/argustest/skill-agent/.swebench-pro-eval/run_scripts
export ARGUS_SKILL_SWEBPRO_WORKERS=8
# Azure pricing for cost estimation (USD per million tokens, [input, output]).
# These are placeholders close to public Azure GPT-5.4 prices; override
# via the same env var when the contract numbers are confirmed.
export ARGUS_SKILL_SWEBPRO_PRICES_JSON='{"gpt-5.4":[1.25,10.0],"gpt-5.4-mini":[0.25,2.0]}'

CONCURRENCY=8
MAX_TASKS_PER_REPO=5  # ~11 repos × 5 ≈ 55 tasks for the pilot

{
  echo "=========================================="
  echo " argus-skill SWE-Bench-Pro pilot (50 tasks, conc=$CONCURRENCY)"
  echo " started_at        : $(date -Iseconds)"
  echo " host              : $(hostname)"
  echo " exp_dir           : $EXP_DIR"
  echo " engineer (cont.)  : openai/gpt-5.4-mini  effort=high"
  echo " reviewer (cont.)  : $ARGUS_SKILL_HARBOR_REVIEWER_MODEL  effort=$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT"
  echo " scientist (host)  : $ARGUS_SKILL_HARBOR_SCIENTIST_MODEL  effort=$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT"
  echo " skills_dir        : $ARGUS_SKILL_HARBOR_SKILLS_DIR (cold start)"
  echo " writeback-revise  : $ARGUS_SKILL_REVISE_ON_WRITEBACK"
  echo " auto-promote      : $ARGUS_SKILL_AUTO_PROMOTE_LESSON"
  echo " round_timeout_s   : $ARGUS_SKILL_HARBOR_ROUND_TIMEOUT"
  echo " max_rounds        : $ARGUS_SKILL_HARBOR_MAX_ROUNDS"
  echo "=========================================="
} | tee "$LOG_FILE"

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
  ARGUS_SKILL_REVISE_ON_WRITEBACK='$ARGUS_SKILL_REVISE_ON_WRITEBACK' \
  ARGUS_SKILL_AUTO_PROMOTE_LESSON='$ARGUS_SKILL_AUTO_PROMOTE_LESSON' \
  ARGUS_SKILL_SWEBPRO_RUN_SCRIPTS='$ARGUS_SKILL_SWEBPRO_RUN_SCRIPTS' \
  ARGUS_SKILL_SWEBPRO_WORKERS='$ARGUS_SKILL_SWEBPRO_WORKERS' \
  ARGUS_SKILL_SWEBPRO_PRICES_JSON='$ARGUS_SKILL_SWEBPRO_PRICES_JSON' \
  python -m benchmarks.swebench_pro \
    --max-tasks-per-repo $MAX_TASKS_PER_REPO \
    --engineer-model gpt-5.4-mini \
    --engineer-effort high \
    --reviewer-model gpt-5.4 \
    --reviewer-effort high \
    --max-rounds 2 \
    --round-timeout 1800 \
    --workers $CONCURRENCY \
    --output-dir '$EXP_DIR' \
    -v
" 2>&1 | tee -a "$LOG_FILE"

echo "==========================================" | tee -a "$LOG_FILE"
echo " finished_at       : $(date -Iseconds)" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
