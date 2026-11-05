#!/usr/bin/env python3
"""Summarize source-bound replay responses, retaining extracted proof costs."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('queries', type=Path, help='replay baseline-loop-queries.json')
parser.add_argument('--function', action='append', default=[], help='function substring; repeat to include extracted lemmas')
args = parser.parse_args()
record = json.loads(args.queries.read_text())
if record['exit_code'] != 0:
    raise SystemExit(f"Replay failed: exit {record['exit_code']}")
selected = [q for q in record['queries'] if not args.function or any(f in q['function'] for f in args.function)]
for q in selected:
    response = q['response'].splitlines()
    result = next((line for line in response if line in ('sat', 'unsat', 'unknown')), 'missing')
    print(json.dumps({key: q[key] for key in ('function', 'span', 'seconds', 'rlimit_delta')} | {'result': result}))
print(json.dumps({'label': 'serialized replay sum; not native aggregate', 'queries': len(selected), 'seconds': sum(q['seconds'] for q in selected), 'resource_units': sum(q['rlimit_delta'] for q in selected)}))
