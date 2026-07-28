#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/config.env"
echo "== GPUs =="
nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv,noheader
echo "== prepare =="
test -f "$CAMPAIGN_ROOT/prepare-state.json" && cat "$CAMPAIGN_ROOT/prepare-state.json" || echo not-started
echo "== campaign =="
test -f "$CAMPAIGN_ROOT/campaign-state.json" && cat "$CAMPAIGN_ROOT/campaign-state.json" || echo not-started
echo "== processes =="
ps -eo pid,etimes,args | grep -E 'campaign.py|run_competition|argus-skill --daemon-fg' | grep -v grep || true
