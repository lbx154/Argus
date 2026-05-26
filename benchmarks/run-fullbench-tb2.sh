#!/usr/bin/env bash
# Generic TB2 fullbench launcher wrapper.
#
# Usage:
#   benchmarks/run-fullbench-tb2.sh argus-v12-redux
#   benchmarks/run-fullbench-tb2.sh argus-v12-true
#   benchmarks/run-fullbench-tb2.sh bare-gpt54
#   benchmarks/run-fullbench-tb2.sh bare-gpt54-mini
#   benchmarks/run-fullbench-tb2.sh --condition argus-v12-true --condition bare-gpt54 --replicates 3
set -euo pipefail

if [ "${1:-}" = "" ]; then
  echo "usage: $0 <condition>" >&2
  exit 2
fi

CONDITION="$1"

cd /home/argustest/argus-skill

# Source order: 1) existing env (shell export), 2) ~/.codex/auth.json.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f /home/argustest/.codex/auth.json ]; then
  OPENAI_API_KEY="$(python3 -c "import json,sys; print(json.load(open('/home/argustest/.codex/auth.json'))['OPENAI_API_KEY'])")"
  export OPENAI_API_KEY
fi
: "${OPENAI_API_KEY:?could not find OPENAI_API_KEY (env unset and ~/.codex/auth.json unreadable)}"

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://ai4m6.openai.azure.com/openai/v1/}"
export PYTHONPATH=/home/argustest/argus-skill
export ARGUS_SKILL_TB2_PREFLIGHT_MODE="${ARGUS_SKILL_TB2_PREFLIGHT_MODE:-auto}"

if [ "${1:-}" != "" ] && [[ "$1" == --* ]]; then
  python3 -m benchmarks.tb2_fullbench_matrix_launcher "$@"
else
  python3 -m benchmarks.tb2_fullbench_launcher --condition "$CONDITION"
fi
