#!/usr/bin/env bash
# The milestone on a machine with only Node: pack Argus, install it with `npm i -g` in a
# node:22-slim container (no Python, no network), start it and run a project through its web API
# from one sentence to the Manager's report, with test/fixtures/fake-codex.mjs as codex.
set -euo pipefail
core=$(cd "$(dirname "$0")/.." && pwd)
pack=$(mktemp -d)
trap 'rm -rf "$pack"' EXIT
(cd "$core" && npm pack --loglevel warn --pack-destination "$pack")
docker run --rm --network none -v "$pack:/pack:ro" -v "$core/test/fixtures/fake-codex.mjs:/usr/local/bin/codex:ro" \
  -v "$core/test/fixtures/e2e.mjs:/e2e.mjs:ro" \
  node:22-slim sh -euc '
    if command -v python3 || command -v python; then echo "this machine has Python" >&2; exit 1; fi
    echo "node $(node -v), no python; $(du -h /pack/*.tgz)"
    npm i -g --offline /pack/*.tgz
    mkdir /work && node /e2e.mjs "Write hello.txt that says hello" /work | tee /tmp/run.log
    grep -q "^The work is done.$" /tmp/run.log
    grep -q "^status: idle$" /tmp/run.log
  '
