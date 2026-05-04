#!/usr/bin/env bash
# argus-skill A-lite microbench: 11-task subset (5 wins + 6 losses from
# tb2-cap-2026-05-02), same as skill-agent's tb2-microbench-2026-05-03.
#
# Compare against:
#   - bare-mini control:          benchmarks/results/tb2-cap-2026-05-02/bare-mini/
#   - Phase A skill-cap treatment: benchmarks/results/tb2-microbench-2026-05-03/skill-cap-phaseA/
#
# Decision line (post rubber-duck): argus-skill_pass - bare-mini_pass >= 3
# would clear the 0.022 lift gap that Phase C missed.
set -uo pipefail

EXP_ROOT="/home/argustest/argus-skill/benchmarks/results/tb2-microbench-2026-05-04"
EXP_DIR="$EXP_ROOT/argus-skill-codex"
LOG_DIR="$EXP_DIR/logs"
LOG_FILE="$LOG_DIR/run.log"
PID_FILE="$LOG_DIR/run.pid"
DECISIONS_LOG="$LOG_DIR/decisions.jsonl"

mkdir -p "$LOG_DIR" "$EXP_DIR/skills" "$EXP_DIR/jobs"

cd /home/argustest/argus-skill

# --- Same Azure endpoint as yesterday's run-microbench.sh.
export OPENAI_API_KEY='REDACTED_AZURE_OPENAI_KEY'
export OPENAI_BASE_URL='https://ai4m6.openai.azure.com/openai/v1/'
export PYTHONPATH=/home/argustest/argus-skill

# --- Host-side prep / reviewer config
export ARGUS_SKILL_HARBOR_SCIENTIST_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT=high
export ARGUS_SKILL_HARBOR_REVIEWER_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_REVIEWER_EFFORT=medium
export ARGUS_SKILL_HARBOR_SKILLS_DIR="$EXP_DIR/skills"
export ARGUS_SKILL_HARBOR_DECISIONS_LOG="$DECISIONS_LOG"

# --- Loop budgets (tight; rubber-duck warned about budget overrun)
export ARGUS_SKILL_HARBOR_DISTILL_BUDGET=120
export ARGUS_SKILL_HARBOR_REVIEWER_BUDGET=60
export ARGUS_SKILL_HARBOR_ROUND_TIMEOUT=200
export ARGUS_SKILL_HARBOR_MAX_ROUNDS=2

# --- 11 tasks (mirrors skill-agent's tb2-microbench-2026-05-03 set)
TASKS=(
  build-cython-ext
  chess-best-move
  circuit-fibsqrt
  path-tracing
  sparql-university
  hf-model-inference
  nginx-request-logging
  sanitize-git-repo
  schemelike-metacircular-eval
  video-processing
  winning-avg-corewars
)

INCLUDE_FLAGS=()
for t in "${TASKS[@]}"; do
  INCLUDE_FLAGS+=("-i" "$t")
done

{
  echo "=========================================="
  echo " argus-skill A-lite microbench (codex backend, 2-round reviewer-loop)"
  echo " started_at        : $(date -Iseconds)"
  echo " host              : $(hostname)"
  echo " exp_dir           : $EXP_DIR"
  echo " concurrency       : 8"
  echo " scientist         : $ARGUS_SKILL_HARBOR_SCIENTIST_MODEL  (effort=$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT)"
  echo " reviewer          : $ARGUS_SKILL_HARBOR_REVIEWER_MODEL    (effort=$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT)"
  echo " engineer (cont.)  : openai/gpt-5.4-mini  (effort=high)"
  echo " skills_dir        : $ARGUS_SKILL_HARBOR_SKILLS_DIR (cold start)"
  echo " jobs_dir          : $EXP_DIR/jobs"
  echo " max_rounds        : $ARGUS_SKILL_HARBOR_MAX_ROUNDS"
  echo " distill_budget_s  : $ARGUS_SKILL_HARBOR_DISTILL_BUDGET"
  echo " reviewer_budget_s : $ARGUS_SKILL_HARBOR_REVIEWER_BUDGET"
  echo " round_timeout_s   : $ARGUS_SKILL_HARBOR_ROUND_TIMEOUT"
  echo " decisions         : $DECISIONS_LOG"
  echo " tasks             : ${TASKS[*]}"
  echo "=========================================="
} | tee -a "$LOG_FILE"

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
  harbor run \
    --dataset terminal-bench@2.0 \
    --agent-import-path benchmarks.harbor_adapter:ArgusSkillCodex \
    --model openai/gpt-5.4-mini \
    --ak reasoning_effort=high \
    -n 8 \
    --jobs-dir '$EXP_DIR/jobs' \
    ${INCLUDE_FLAGS[*]} \
    -y
" 2>&1 | tee -a "$LOG_FILE"

rc=${PIPESTATUS[0]}
echo "[$(date -Iseconds)] sg docker exited rc=$rc" | tee -a "$LOG_FILE"
exit "$rc"
