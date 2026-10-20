import hashlib
import json
import os
from pathlib import Path
import subprocess

root=Path(os.environ['GITHUB_WORKSPACE'])
scripts=root/'.github/scripts'
plan=json.loads((scripts/'windows-019-release-plan.json').read_text(encoding='utf-8'))
work=Path(os.environ['RUNNER_TEMP'])/'argus019publication';work.mkdir()
def api(path):return json.loads(subprocess.check_output(['gh','api','repos/lbx154/Argus/'+path]))
# Only CI/publication support may differ from the already signed product commit.
changed=subprocess.check_output(['git','diff','--name-only',plan['product_sha'],'HEAD'],cwd=root,text=True).splitlines()
assert changed and all(name.startswith('.github/') for name in changed)
assert json.loads((root/'argus/release_manifest.json').read_text())==plan['source_release']
run=api(f"actions/runs/{plan['run_id']}")
assert run['status']=='completed' and run['head_sha']==plan['product_sha']
jobs=api(f"actions/runs/{plan['run_id']}/jobs")['jobs']
job=next(item for item in jobs if item['name']=='build · windows-x86_64 desktop')
required=['Build frozen backend','Build signed desktop updates','Stage Windows installer','Verify reviewed Windows source and artifacts','Run actions/upload-artifact@v4']
assert all(next(step for step in job['steps'] if step['name']==name)['conclusion']=='success' for name in required)
failures=[step['name'] for step in job['steps'] if step['conclusion']=='failure']
assert failures in [[],['Post Run actions/setup-python@v5']], 'A product/build gate failed'
artifacts=api(f"actions/runs/{plan['run_id']}/artifacts")['artifacts']
artifact=next(item for item in artifacts if item['id']==plan['artifact_id'])
assert artifact['name']=='desktop-windows-x86_64' and not artifact['expired'] and artifact['digest']==plan['artifact_digest']
assets=work/'ci-artifacts'
subprocess.run(['gh','run','download',str(plan['run_id']),'--repo','lbx154/Argus','--name',artifact['name'],'--dir',str(assets)],check=True)
subprocess.run(['node',str(scripts/'verify-artifact-019.mjs'),str(assets),str(work/'SIGNATURE-VERIFIED.json')],check=True)
with (work/'BUILD-ELIGIBILITY.json').open('x',encoding='utf-8') as stream:
    json.dump({'run_id':plan['run_id'],'source_commit':plan['product_sha'],'release':plan['source_release'],'build_signature_source_upload_steps_verified':True,'nonproduct_failures':failures},stream,indent=2)
with (work/'v0.1.9-release-before.json').open('x',encoding='utf-8') as stream:json.dump(plan['before'],stream,ensure_ascii=False,indent=2)
old=work/'existing-v0.1.9';old.mkdir()
(old/'latest.json').write_text(plan['old_manifest_text'],encoding='utf-8')
(old/'SHA256SUMS').write_text(plan['old_checksums_text'],encoding='utf-8')
manifest=json.loads(plan['old_manifest_text'])
for entry in manifest['platforms'].values():
    name=entry['url'].rsplit('/',1)[1]+'.sig'
    (old/name).write_text(entry['signature']+'\n',encoding='utf-8')
# All replacement checksums refer to the original immutable Mac assets.
subprocess.run(['python',str(scripts/'prepare-publication-019.py')],check=True)
subprocess.run(['python',str(scripts/'publish-verified-019.py')],check=True)
