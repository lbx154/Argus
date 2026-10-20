"""User-approved publication. Preserve Mac assets and reject concurrent changes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

OUT=Path(os.environ['RUNNER_TEMP'])/'argus019publication'
REPO='lbx154/Argus'
plan=json.loads((OUT/'PUBLICATION-PLAN.json').read_text(encoding='utf-8'))
before=json.loads((OUT/'v0.1.9-release-before.json').read_text(encoding='utf-8'))
patch=json.loads((OUT/'release-update.json').read_text(encoding='utf-8'))
assert plan['remote_metadata_replacement_confirmed_by_user'] and plan['latest_promotion_confirmed_by_user']
assert set(plan['assets'])=={'Argus-0.1.9-setup.exe','Argus-0.1.9-setup.exe.sig','latest.json','SHA256SUMS'}
assert json.loads((OUT/'BUILD-ELIGIBILITY.json').read_text())['source_commit']==plan['source_commit']
assert json.loads((OUT/'SIGNATURE-VERIFIED.json').read_text())['updater_signature_verified']
for name,digest in plan['assets'].items():
    with (OUT/'publish'/name).open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==digest
baseline={asset['name']:asset for asset in before['assets']}
protected={name:asset for name,asset in baseline.items() if name not in {'latest.json','SHA256SUMS'}}

def api(path,*args):
    return json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/{path}',*args]))

def current():
    release=api('releases/tags/v0.1.9')
    assert release['id']==plan['release_id'] and not release['draft'] and not release['prerelease']
    assert release['body'] in {before['body'],patch['body']},'Concurrent release-note edit; refusing to overwrite'
    assert release['name'] in {before['name'],patch['name']},'Concurrent title edit; refusing to overwrite'
    assets={asset['name']:asset for asset in release['assets']}
    assert set(assets)<=set(baseline)|set(plan['assets']),'Unexpected concurrent asset additions'
    for name,old in protected.items():
        new=assets.get(name)
        assert new is not None and all(new[field]==old[field] for field in ['id','name','size','digest']),'Mac asset changed during publication'
    tag=api('git/ref/tags/v0.1.9')
    assert tag['object']['sha']==plan['original_tag_commit'],'Existing macOS tag changed; refusing to move or replace it'
    return release,assets

def upload(name,replace=False):
    _,assets=current()
    existing=assets.get(name)
    desired='sha256:'+plan['assets'][name]
    if existing and existing['state']=='uploaded' and existing['digest']==desired:
        print('Already verified on GitHub:',name,flush=True);return
    args=['gh','release','upload','v0.1.9',str(OUT/'publish'/name),'--repo',REPO]
    if existing:
        assert replace and name in {'latest.json','SHA256SUMS'},'Refusing to overwrite an existing installer or signature'
        assert existing['digest']==baseline[name]['digest'],'Unexpected metadata revision; refusing to clobber'
        marker=OUT/('authorized-replacement-'+name.replace('.','_')+'.json')
        if not marker.exists():
            with marker.open('x',encoding='utf-8') as stream:
                json.dump({'name':name,'previous_id':existing['id'],'previous_digest':existing['digest'],'replacement_digest':desired,'user_confirmed':True},stream,indent=2)
        args.append('--clobber')
    print(('Replacing confirmed update metadata: ' if replace else 'Uploading new signed asset: ')+name,flush=True)
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    # A timeout is not proof that upload did not happen. Inspect before retrying.
    for _ in range(12):
        _,after=current()
        asset=after.get(name)
        if asset and asset['state']=='uploaded' and asset['digest']==desired:return
        time.sleep(3)
    raise RuntimeError(f'Upload not verified for {name}; exit={result.returncode}. Latest promotion withheld; inspect remote state before resuming.')

current()
assert api('releases/latest')['tag_name'] in {'v0.1.8','v0.1.9'}, 'A different release became Latest; refuse to replace it'
upload('Argus-0.1.9-setup.exe')
upload('Argus-0.1.9-setup.exe.sig')
upload('latest.json',replace=True)
upload('SHA256SUMS',replace=True)
release,assets=current()
for name,digest in plan['assets'].items():assert assets[name]['digest']=='sha256:'+digest
# Atomically update the title/notes and promote the completed mixed-platform feed.
print('Updating release notes and promoting the verified v0.1.9 feed to Latest...',flush=True)
api(f"releases/{plan['release_id']}",'--method','PATCH','--input',str(OUT/'release-update.json'))
release,assets=current()
assert release['name']==patch['name'] and release['body']==patch['body']
assert api('releases/latest')['tag_name']=='v0.1.9'

stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
download=OUT/f'published-verification-{stamp}';assert not download.exists()
print('Downloading the actual public release assets for independent verification...',flush=True)
command=['gh','release','download','v0.1.9','--repo',REPO,'--dir',str(download)]
for name in plan['assets']:command+=['--pattern',name]
subprocess.run(command,check=True)
verification=OUT/f'PUBLIC-DOWNLOAD-VERIFIED-{stamp}.json'
subprocess.run(['node',str(Path(os.environ['GITHUB_WORKSPACE'])/'.github/scripts/verify-published-019.mjs'),str(download),str(OUT/'PUBLICATION-PLAN.json'),str(verification)],check=True)
# Check the exact URL embedded in already-installed clients, not only the tag URL.
feed=OUT/f'LATEST-ENDPOINT-{stamp}.json'
subprocess.run(['curl','--fail','--silent','--show-error','--location','--output',str(feed),'https://github.com/lbx154/Argus/releases/latest/download/latest.json'],check=True)
assert hashlib.sha256(feed.read_bytes()).hexdigest()==plan['assets']['latest.json']
manifest=json.loads(feed.read_text(encoding='utf-8'))
assert manifest['version']=='0.1.9' and 'windows-x86_64' in manifest['platforms']
report={'release_url':release['html_url'],'tag':'v0.1.9','latest':True,'source_commit':plan['source_commit'],'build_run':f"https://github.com/{REPO}/actions/runs/{plan['run_id']}",'assets':{name:{'sha256':plan['assets'][name],'url':assets[name]['browser_download_url'],'bytes':assets[name]['size']} for name in plan['assets']},'public_download_verified':str(verification),'latest_endpoint_verified':True,'mac_assets_unchanged':True,'mac_platforms_preserved':True,'original_tag_unchanged':True,'updated_at':datetime.now(timezone.utc).isoformat()}
with (OUT/'PUBLISHED.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
