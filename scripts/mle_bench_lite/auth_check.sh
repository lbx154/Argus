#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/config.env"

export KAGGLE_CONFIG_DIR=/root/.kaggle
credential="$KAGGLE_CONFIG_DIR/access_token"
if [[ ! -s "$credential" ]]; then
  echo "Kaggle access token missing: $credential" >&2
  exit 2
fi
mode=$(stat -c %a "$credential")
if [[ "$mode" != "600" ]]; then
  echo "Kaggle credential must have mode 600; found $mode" >&2
  exit 2
fi

# This calls the Kaggle CLI and proves authentication without printing secrets.
"$KAGGLE_BIN" competitions list --search aerial-cactus-identification --csv \
  >/tmp/argus-mle-kaggle-auth.csv
grep -q 'aerial-cactus-identification' /tmp/argus-mle-kaggle-auth.csv
rm -f /tmp/argus-mle-kaggle-auth.csv
echo "Kaggle CLI authentication: OK"
