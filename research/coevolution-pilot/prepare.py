"""Fetch the recorded benchmark subset without changing the experiment protocol."""
import hashlib
import json
import urllib.request

from paths import CODE_ROOT, ROOT

protocol = json.loads((CODE_ROOT / 'protocol.json').read_text())
manifest = json.loads((CODE_ROOT / 'benchmark-manifest.json').read_text())
ROOT.mkdir(parents=True, exist_ok=True)
protocol_path = ROOT / 'protocol.json'
if protocol_path.exists():
    assert json.loads(protocol_path.read_text()) == protocol, 'Existing protocol differs from the recorded pilot'
else:
    protocol_path.write_bytes((CODE_ROOT / 'protocol.json').read_bytes())

external = {
    'tasks/pdf-excel-diff/environment/employees_current.xlsx',
    'tasks/pdf-excel-diff/environment/employees_backup.pdf',
}
for item in manifest:
    path = item['path']
    target = ROOT / 'benchmark' / path
    if target.exists():
        raw = target.read_bytes()
    else:
        if path in external:
            location = f"XuandongZhao/skillsbench-files/{protocol['external_data_revision']}/pdf-excel-diff/{target.name}"
        else:
            location = f"benchflow-ai/skillsbench/{protocol['revision']}/{path}"
        with urllib.request.urlopen('https://raw.githubusercontent.com/' + location, timeout=30) as response:
            raw = response.read()
    assert hashlib.sha256(raw).hexdigest() == item['sha256'], f'Benchmark bytes differ: {path}'
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
print(json.dumps({'verified_benchmark_files': len(manifest), 'revision': protocol['revision']}))
