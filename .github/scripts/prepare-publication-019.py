"""Prepare a three-platform update manifest, preserving every existing Mac entry."""
import os
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

OUT=Path(os.environ['RUNNER_TEMP'])/'argus019publication'
source=OUT/'ci-artifacts'
checks=json.loads((OUT/'BUILD-ELIGIBILITY.json').read_text(encoding='utf-8'))
verified=json.loads((OUT/'SIGNATURE-VERIFIED.json').read_text(encoding='utf-8'))
assert checks['build_signature_source_upload_steps_verified'] and verified['updater_signature_verified']
assert checks['source_commit']=='321d1d6f946ab69cfbc20c62288a1e19f501851a'
subprocess.run(['git','diff','--quiet','HEAD'],cwd=os.environ['GITHUB_WORKSPACE'],check=True)
before=json.loads((OUT/'v0.1.9-release-before.json').read_text(encoding='utf-8'))
old=json.loads((OUT/'existing-v0.1.9/latest.json').read_text(encoding='utf-8'))
windows=json.loads((source/'latest.json').read_text(encoding='utf-8'))
assert old['version']==windows['version']=='0.1.9'
assert set(old['platforms'])=={'darwin-aarch64','darwin-x86_64'}
assert set(windows['platforms'])=={'windows-x86_64'}
for arch in old['platforms']:
    filename=old['platforms'][arch]['url'].rsplit('/',1)[1]
    assert filename in {asset['name'] for asset in before['assets']}
    assert old['platforms'][arch]['signature']==(OUT/'existing-v0.1.9'/f'{filename}.sig').read_text(encoding='utf-8').strip()
old_assets={asset['name']:asset for asset in before['assets']}
old_hashes={}
for row in (OUT/'existing-v0.1.9/SHA256SUMS').read_text().splitlines():
    match=re.fullmatch(r'([a-f0-9]{64})  (.+)',row);assert match
    digest,name=match.groups();assert old_assets[name]['digest']=='sha256:'+digest
    old_hashes[name]=digest
merged={**old,'notes':old['notes']+'\nWindows 0.1.9: updated desktop built from the operator-reviewed current source; frozen backend included; existing updater trust key retained.',
        'pub_date':windows['pub_date'],'platforms':{**old['platforms'],**windows['platforms']}}
for arch,entry in old['platforms'].items():assert merged['platforms'][arch]==entry
publish=OUT/'publish';publish.mkdir()
for name in ['Argus-0.1.9-setup.exe','Argus-0.1.9-setup.exe.sig']:
    with (source/name).open('rb') as src,(publish/name).open('xb') as dst:shutil.copyfileobj(src,dst)
    with (source/name).open('rb') as stream:expected=hashlib.file_digest(stream,'sha256').hexdigest()
    with (publish/name).open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    assert actual==expected;old_hashes[name]=actual
with (publish/'latest.json').open('x',encoding='utf-8',newline='\n') as stream:stream.write(json.dumps(merged,ensure_ascii=False,indent=2)+'\n')
old_hashes['latest.json']=hashlib.sha256((publish/'latest.json').read_bytes()).hexdigest()
with (publish/'SHA256SUMS').open('x',encoding='utf-8',newline='\n') as stream:
    stream.write(''.join(f'{digest}  {name}\n' for name,digest in sorted(old_hashes.items())))
body=f'''# Argus 0.1.9 — Windows & macOS

## Windows x64 · 2026-10-20 补充发行

下载 [Argus-0.1.9-setup.exe](https://github.com/lbx154/Argus/releases/download/v0.1.9/Argus-0.1.9-setup.exe)。
对应的 `.exe.sig` 与 `latest.json` 用于 Argus 签名更新器，校验值见 `SHA256SUMS`。
本次将此 Release 设为 Latest；更新清单同时保留 Mac Apple Silicon / Intel 平台条目。

升级前请在旧版选择 **停止本地后端并退出**；仅关闭窗口会隐藏到托盘。
运行安装器后，选择已安装并登录的 Agent CLI，或使用获邀的内部测试入口。
安装包包含冻结 Python 后端和当前源码的 Web 工作台，无需另外安装 Python。
桌面依赖 Microsoft Edge WebView2 Runtime，缺失时安装器按既有策略处理。
本次不包含新的 Linux 或 PyPI 发行物，也没有重建或替换既有 Mac 安装包。

### Windows 来源与验证

- 构建源码：[`{checks['source_commit']}`](https://github.com/lbx154/Argus/commit/{checks['source_commit']})。
- 运行身份：`{checks['release']['release_id']}`。
- 签名构建：[Windows release workflow](https://github.com/lbx154/Argus/actions/runs/{checks['run_id']})。
- 正式 NSIS 安装器已使用既有 Argus updater 签名材料签发，并以原有内置公钥独立验签；没有轮换公钥。
- 构建内冻结运行时、版本身份与源文件一致性检查通过；发布前和公开下载后均独立验签并核对 SHA-256。
- 安装器 SHA-256：`{verified['sha256']}`。
- 更新签名是 minisign/Tauri updater 签名，**不等于 Windows Authenticode 证书或 SmartScreen 信誉**。

本次未使用真实账号执行付费模型任务，未重新执行干净机器安装/升级验收。
上述构建的签名、打包、源码校验和产物上传步骤均成功；仅最后的无用 pip 缓存保存报错。
Windows 依赖使用 uv，后续已单独修正该 CI 缓存配置；该收尾错误不影响已验签安装器。
完整 GUI 自动化、真实通知交互和故障注入仍未全量验收；此前预览的健康与生命周期检查
不替代这些限制。现有 `v0.1.9` 标签和下方 Mac 资产保持原样，Windows 来源以上述精确提交为准。

## macOS · 保留既有发行

- [Apple Silicon DMG](https://github.com/lbx154/Argus/releases/download/v0.1.9/Argus-0.1.9-macos-aarch64.dmg)
- [Intel DMG](https://github.com/lbx154/Argus/releases/download/v0.1.9/Argus-0.1.9-macos-x86_64.dmg)

以下是 2026-09-22 的 Mac 发行说明原文。其“本次仅发布 Mac / 保留 Windows Latest”描述的是
当时的历史发布范围；本次 Windows 补充发行及 Latest 更新以上文为准。

<details>
<summary>展开原 macOS 发行说明（保留原文）</summary>

{before['body'].rstrip()}

</details>
'''
with (OUT/'release-notes.md').open('x',encoding='utf-8',newline='\n') as stream:stream.write(body)
patch={'name':'Argus 0.1.9 — Windows & macOS','body':body,'make_latest':'true'}
with (OUT/'release-update.json').open('x',encoding='utf-8') as stream:json.dump(patch,stream,ensure_ascii=False,indent=2)
new_hashes={}
for path in publish.iterdir():
    with path.open('rb') as stream:new_hashes[path.name]=hashlib.file_digest(stream,'sha256').hexdigest()
with (OUT/'PUBLICATION-PLAN.json').open('x',encoding='utf-8') as stream:
    json.dump({'release_id':before['id'],'tag':'v0.1.9','source_commit':checks['source_commit'],'run_id':checks['run_id'],'assets':new_hashes,'preserved_mac_platforms':old['platforms'],'remote_metadata_replacement_confirmed_by_user':True,'latest_promotion_confirmed_by_user':True,'original_tag_commit':'d25dba8f0a4ae13962e7117728fa55951ceed2fe'},stream,ensure_ascii=False,indent=2)
print('Prepared signed Windows installer, signature, combined latest.json and updated checksums. Mac platform entries are byte-for-byte unchanged.')
