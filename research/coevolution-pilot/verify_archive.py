"""Verify published evidence and regenerate measurements without model calls."""
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from paths import CODE_ROOT

root = CODE_ROOT
manifest = json.loads((root / 'archive-manifest.json').read_text())
# Only publication code/docs can differ from the original-byte manifest.
edited = {
    'README.md', 'Dockerfile', 'bench_common.py', 'finish_review.py', 'gateway.py',
    'oracles.py', 'plot_results.py', 'prepare.py', 'render_report.py', 'report.md',
    'report_results.py', 'run_experiment.py', 'verify_final.py',
}
checked = 0
traces = 0
for item in manifest['files']:
    path = root / item['published']
    assert path.is_file(), f'Missing artifact: {item["published"]}'
    if item['published'] in edited:
        continue
    raw = gzip.decompress(path.read_bytes()) if item['gzip'] else path.read_bytes()
    assert len(raw) == item['original_bytes'], f'Changed size: {item["published"]}'
    assert hashlib.sha256(raw).hexdigest() == item['sha256_original'], f'Changed bytes: {item["published"]}'
    checked += 1
    traces += int(path.name == 'trajectory.jsonl.gz')

for item in json.loads((root / 'benchmark-manifest.json').read_text()):
    path = root / 'benchmark' / item['path']
    assert path.is_file(), 'Run python prepare.py first'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], item['path']

with tempfile.TemporaryDirectory(prefix='coevolution-verify-') as directory:
    workspace = Path(directory)
    for name in ['runs', 'learning', 'benchmark', 'protocol.json', 'gateway-usage.jsonl',
                 'quality-probes.json', 'benchmark-audit.json']:
        (workspace / name).symlink_to(root / name, target_is_directory=(root / name).is_dir())
    env = {**os.environ, 'PILOT_ROOT': str(workspace)}
    subprocess.run([sys.executable, str(root / 'report_results.py')], env=env, check=True)
    before = json.loads((root / 'results.json').read_text())
    after = json.loads((workspace / 'results.json').read_text())
    after['generated_at'] = before['generated_at']
    assert before == after, 'Regenerated measurements differ from the recorded results'
    assert (root / 'scores.csv').read_bytes() == (workspace / 'scores.csv').read_bytes()
    assert (root / 'report.md').read_text() == (workspace / 'report.md').read_text()
    subprocess.run([sys.executable, str(root / 'verify_final.py')], env=env, check=True)

result = {'verified': True, 'unchanged_artifacts': checked, 'lossless_trajectories': traces,
          'results_regenerated': True, 'model_calls': 0}
(root / 'publication-verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
