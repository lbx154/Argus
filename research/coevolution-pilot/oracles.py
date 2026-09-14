import json

from bench_common import ROOT, command, cwd, grade, start, stop

protocol = json.loads((ROOT / 'protocol.json').read_text())
results = {}
for task in protocol['training_tasks'] + protocol['heldout_tasks']:
    name = 'coevo-oracle-' + task
    target = ROOT / 'oracle-checks' / task
    target.mkdir(parents=True, exist_ok=True)
    try:
        base = ROOT / 'benchmark/tasks' / task
        start(name, task, [(base / 'oracle', '/oracle', True), (base / 'verifier', '/verifier', True)])
        solve = command(['docker', 'exec', '-w', cwd(task), name, 'bash', '/oracle/solve.sh'], timeout=300)
        (target / 'solve.txt').write_text(solve.stdout + '\n' + solve.stderr)
        results[task] = grade(name, task, target)
        print(json.dumps({'task': task, **results[task]}), flush=True)
    finally:
        stop(name)
(ROOT / 'oracle-checks/results.json').write_text(json.dumps(results, indent=2) + '\n')
if not all(result['reward'] for result in results.values()):
    raise SystemExit('An upstream oracle did not pass; inspect before agent runs')
