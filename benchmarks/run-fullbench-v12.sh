#!/usr/bin/env bash
# argus-skill v12 — FULL TerminalBench v2 with Phase 4 (in-container reviewer + verifier).
#
# Phase 4 fixes baked in:
#   • reviewer = codex inside the same container (sees live fs)
#   • engineer auto-runs TB v2 /tests/test.sh as advisory; exit/stdout fed to reviewer
#   • reviewer cli_flags_arg fixed (no --full-auto, no duplicate -m)
#   • daemon-survival ops note (Phase 3.5)
#   • runtime probe (Phase 3.5)
#   • round timeout 1800s
set -uo pipefail

EXP_ROOT="/home/argustest/argus-skill/benchmarks/results/tb2-fullbench-2026-05-06-v12"
EXP_DIR="$EXP_ROOT/argus-skill-codex"
LOG_DIR="$EXP_DIR/logs"
LOG_FILE="$LOG_DIR/run.log"
PID_FILE="$LOG_DIR/run.pid"
DECISIONS_LOG="$LOG_DIR/decisions.jsonl"

mkdir -p "$LOG_DIR" "$EXP_DIR/skills" "$EXP_DIR/jobs"

cd /home/argustest/argus-skill

: "${OPENAI_API_KEY:?set OPENAI_API_KEY in your shell or a .env file before running this script}"
export OPENAI_BASE_URL='https://ai4m6.openai.azure.com/openai/v1/'
export PYTHONPATH=/home/argustest/argus-skill

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

CONCURRENCY=8

echo "==========================================" | tee "$LOG_FILE"
echo " argus-skill v12 FULL terminal-bench@2.0 (Phase 4 reviewer-in-container)" | tee -a "$LOG_FILE"
echo " started_at        : $(date -Iseconds)" | tee -a "$LOG_FILE"
echo " host              : $(hostname)" | tee -a "$LOG_FILE"
echo " exp_dir           : $EXP_DIR" | tee -a "$LOG_FILE"
echo " concurrency       : $CONCURRENCY" | tee -a "$LOG_FILE"
echo " scientist         : $ARGUS_SKILL_HARBOR_SCIENTIST_MODEL  (effort=$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT)" | tee -a "$LOG_FILE"
echo " reviewer (cont.)  : $ARGUS_SKILL_HARBOR_REVIEWER_MODEL  (effort=$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT)  [in-container]" | tee -a "$LOG_FILE"
echo " engineer (cont.)  : openai/gpt-5.4-mini  (effort=high)" | tee -a "$LOG_FILE"
echo " skills_dir        : $ARGUS_SKILL_HARBOR_SKILLS_DIR (cold start)" | tee -a "$LOG_FILE"
echo " jobs_dir          : $EXP_DIR/jobs" | tee -a "$LOG_FILE"
echo " round_timeout_s   : $ARGUS_SKILL_HARBOR_ROUND_TIMEOUT" | tee -a "$LOG_FILE"
echo " max_rounds        : $ARGUS_SKILL_HARBOR_MAX_ROUNDS" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"

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
