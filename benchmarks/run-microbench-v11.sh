#!/usr/bin/env bash
# argus-skill v11 microbench (Phase 4: in-container reviewer + TB v2 official verifier signal).
# Inherits v10 fixes (timeout 1800, runtime probe, daemon nudge) and adds:
#   - reviewer = codex inside the same container (sees live fs + can shell-poke services).
#   - engineer auto-runs TB v2 /tests/test.sh when present; advisory exit code piped to reviewer.
# Tests pre-uploaded by ContainerCodexRunner; harbor wipes /tests + /logs/verifier before its own
# verifier run, so this is non-destructive.
# tb2-cap-2026-05-02), same as skill-agent's tb2-microbench-2026-05-03.
#
# v7 design (post-rubber-duck on v6 plateau):
#   * R1 prompt mirrors skill-cap-phaseA's exact shape (no Reporting
#     requirements, no round-X-of-Y tail).
#   * Reviewer is diagnostic-only by default (no longer gates rounds);
#     set ARGUS_SKILL_HARBOR_REVIEWER_GATE=1 to restore old behaviour.
#   * R2 fires only on objective R1 failure (timeout / non-zero exit /
#     empty output) — not on reviewer disagreement with the engineer's
#     prose. This was the regression source from v3-v6.
#   * Concurrency dropped 11 → 6 to reduce docker/apt-get contention
#     (sparql setup-error in v6 was a symptom).
#
# Compare against:
#   - bare-mini control:           tb2-cap-2026-05-02/bare-mini/      (6/11)
#   - skill-cap-phaseA reference:  tb2-microbench-2026-05-03/         (9/11)
#   - argus-skill v6 (last loop):  tb2-microbench-2026-05-06-v11/__v6/    (5/11)
#
# Decision line (post rubber-duck): argus-skill_pass - bare-mini_pass >= 3
# would clear the 0.022 lift gap that Phase C missed.
set -uo pipefail

EXP_ROOT="/home/argustest/argus-skill/benchmarks/results/tb2-microbench-2026-05-06-v11"
EXP_DIR="$EXP_ROOT/argus-skill-codex"
LOG_DIR="$EXP_DIR/logs"
LOG_FILE="$LOG_DIR/run.log"
PID_FILE="$LOG_DIR/run.pid"
DECISIONS_LOG="$LOG_DIR/decisions.jsonl"

mkdir -p "$LOG_DIR" "$EXP_DIR/skills" "$EXP_DIR/jobs"

cd /home/argustest/argus-skill

# --- Same Azure endpoint as yesterday's run-microbench.sh.
: "${OPENAI_API_KEY:?set OPENAI_API_KEY in your shell or a .env file before running this script}"
export OPENAI_BASE_URL='https://ai4m6.openai.azure.com/openai/v1/'
export PYTHONPATH=/home/argustest/argus-skill

# --- Host-side prep / reviewer config
export ARGUS_SKILL_HARBOR_SCIENTIST_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT=high
export ARGUS_SKILL_HARBOR_REVIEWER_MODEL=gpt-5.4
export ARGUS_SKILL_HARBOR_REVIEWER_EFFORT=high
export ARGUS_SKILL_HARBOR_SKILLS_DIR="$EXP_DIR/skills"
export ARGUS_SKILL_HARBOR_DECISIONS_LOG="$DECISIONS_LOG"

# --- Loop budgets.
# Per-task agent timeouts on this 11-task subset are 900s-3600s. With
# distill 120 + r1 600 + rev 60 + r2 600 = 1380s worst case we now
# comfortably fit even the 900s tasks, and r1/r2 each have enough
# headroom that the slow tasks (cython compile, corewars bench) can
# actually finish a round instead of always cliffing on a timeout.
export ARGUS_SKILL_HARBOR_DISTILL_BUDGET=120
export ARGUS_SKILL_HARBOR_REVIEWER_BUDGET=60
export ARGUS_SKILL_HARBOR_ROUND_TIMEOUT=1800
export ARGUS_SKILL_HARBOR_MAX_ROUNDS=2
# v7: explicit OFF — reviewer logs verdicts but does NOT gate rounds.
export ARGUS_SKILL_HARBOR_REVIEWER_GATE=0

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
  echo " argus-skill v11 microbench (3 fixes: timeout 1800s, runtime probe, daemon-survival nudge)"
  echo " started_at        : $(date -Iseconds)"
  echo " host              : $(hostname)"
  echo " exp_dir           : $EXP_DIR"
  echo " concurrency       : 6"
  echo " scientist         : $ARGUS_SKILL_HARBOR_SCIENTIST_MODEL  (effort=$ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT)"
  echo " reviewer          : $ARGUS_SKILL_HARBOR_REVIEWER_MODEL    (effort=$ARGUS_SKILL_HARBOR_REVIEWER_EFFORT) [diagnostic only]"
  echo " engineer (cont.)  : openai/gpt-5.4-mini  (effort=high)"
  echo " skills_dir        : $ARGUS_SKILL_HARBOR_SKILLS_DIR (cold start)"
  echo " jobs_dir          : $EXP_DIR/jobs"
  echo " max_rounds        : $ARGUS_SKILL_HARBOR_MAX_ROUNDS"
  echo " reviewer_gate     : $ARGUS_SKILL_HARBOR_REVIEWER_GATE (R2 fires on objective failure only)"
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
  ARGUS_SKILL_HARBOR_REVIEWER_GATE='$ARGUS_SKILL_HARBOR_REVIEWER_GATE' \
  harbor run \
    --dataset terminal-bench@2.0 \
    --agent-import-path benchmarks.harbor_adapter:ArgusSkillCodex \
    --model openai/gpt-5.4-mini \
    --ak reasoning_effort=high \
    --agent-setup-timeout-multiplier 3 \
    -n 6 \
    --jobs-dir '$EXP_DIR/jobs' \
    ${INCLUDE_FLAGS[*]} \
    -y
" 2>&1 | tee -a "$LOG_FILE"

rc=${PIPESTATUS[0]}
echo "[$(date -Iseconds)] sg docker exited rc=$rc" | tee -a "$LOG_FILE"
exit "$rc"
