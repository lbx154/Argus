#!/usr/bin/env bash
# The milestone on a machine with only Node: pack the CLI, install it with `npm i -g` in a
# node:22-slim container (no Python, no network), and run a project from one sentence to the Manager's report
# with test/fixtures/fake-codex.mjs as codex.
set -euo pipefail
core=$(cd "$(dirname "$0")/.." && pwd)
pack=$(mktemp -d)
trap 'rm -rf "$pack"' EXIT
(cd "$core" && npm pack --loglevel warn --pack-destination "$pack")
docker run --rm --network none -v "$pack:/pack:ro" -v "$core/test/fixtures/fake-codex.mjs:/usr/local/bin/codex:ro" \
  node:22-slim sh -euc '
    if command -v python3 || command -v python; then echo "this machine has Python" >&2; exit 1; fi
    echo "node $(node -v), no python; $(du -h /pack/*.tgz)"
    npm i -g --offline /pack/*.tgz
    argus run "Write hello.txt that says hello" --dir /work | tee /tmp/run.log
    grep -q "^The work is done.$" /tmp/run.log
    argus status
    argus status | grep -q "^p-[0-9a-f]*  idle "
  '
