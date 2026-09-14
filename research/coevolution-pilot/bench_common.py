import json
import os
import subprocess

from paths import ROOT

IMAGE = os.environ.get('PILOT_IMAGE', 'argus-coevolution:pilot-20260914')
OUTPUTS = {
    'sales-pivot-analysis': '/root/demographic_analysis.xlsx',
    'manufacturing-codebook-normalization': '/app/output/solution.json',
    'invoice-fraud-detection': '/root/fraud_report.json',
    'pdf-excel-diff': '/root/diff_report.json',
    'xlsx-recover-data': '/root/nasa_budget_recovered.xlsx',
}

def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)

def cwd(task):
    return '/app' if task == 'manufacturing-codebook-normalization' else '/root'

def start(name, task, mounts=()):
    args = ['docker', 'run', '-d', '--name', name, '--network', 'none', '--cpus', '2',
            '--memory', '4g', '--pids-limit', '128', '--entrypoint', 'sleep']
    for source, target, readonly in mounts:
        args.extend(['-v', f'{source}:{target}' + (':ro' if readonly else '')])
    command([*args, IMAGE, 'infinity'])
    command(['docker', 'exec', name, 'mkdir', '-p', '/app/data', '/app/output', '/logs/verifier'])
    environment = ROOT / 'benchmark/tasks' / task / 'environment'
    for path in environment.iterdir():
        if path.name in ['Dockerfile', 'skills', 'groundtruth']:
            continue
        if path.name == 'data':
            command(['docker', 'cp', str(path) + '/.', name + ':/app/data'])
        elif path.is_file():
            command(['docker', 'cp', str(path), name + ':/root/' + path.name])

def stop(name):
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

def grade(name, task, destination):
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['docker', 'exec', '-w', cwd(task), name, 'python', '-m', 'pytest',
                             '/verifier/test_outputs.py', '--ctrf', '/logs/verifier/ctrf.json',
                             '-rA', '-q'], capture_output=True, text=True, timeout=300)
    (destination / 'pytest.txt').write_text(result.stdout + '\n' + result.stderr)
    command(['docker', 'cp', name + ':/logs/verifier/ctrf.json', str(destination / 'ctrf.json')])
    data = json.loads((destination / 'ctrf.json').read_text())['results']['summary']
    executed = data['passed'] + data['failed']
    score = {'passed': data['passed'], 'failed': data['failed'], 'tests': data['tests'],
             'skipped': data.get('skipped', 0),
             'pass_rate': data['passed'] / executed if executed else 0,
             'reward': int(result.returncode == 0), 'pytest_exit': result.returncode}
    (destination / 'score.json').write_text(json.dumps(score, indent=2) + '\n')
    return score
