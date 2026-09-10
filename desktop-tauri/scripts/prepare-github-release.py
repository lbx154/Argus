"""Combine platform updater metadata and describe exactly the staged release."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("assets", type=Path)
    parser.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args()
    assets = args.assets
    manifest = json.loads((assets / "latest.json").read_text(encoding="utf-8-sig"))
    for path in sorted(assets.glob("latest-darwin-*.json")):
        platform = json.loads(path.read_text())
        assert platform["version"] == manifest["version"], "Mismatched platform release versions"
        assert not manifest["platforms"].keys() & platform["platforms"].keys(), "Duplicate platform"
        manifest["platforms"].update(platform["platforms"])
    assert set(manifest["platforms"]) == {"windows-x86_64", "darwin-aarch64", "darwin-x86_64"}
    version = manifest["version"]
    for entry in manifest["platforms"].values():
        url = urlparse(entry["url"])
        assert url.scheme == "https" and url.netloc == "github.com"
        assert url.path.startswith(f"/lbx154/Argus/releases/download/v{version}/")
        package = assets / Path(url.path).name
        assert package.is_file() and package.stat().st_size > 1_000_000
        assert package.with_name(package.name + ".sig").read_text().strip() == entry["signature"]
    wheels = list(assets.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        identity = json.loads(wheel.read("argus_skill/release_manifest.json"))
    assert identity["package_version"] == version
    installers = [assets / f"Argus-{version}-setup.exe",
                  assets / f"Argus-{version}-macos-aarch64.dmg",
                  assets / f"Argus-{version}-macos-x86_64.dmg"]
    assert all(path.is_file() for path in installers)
    manifest["notes"] = "修复工作台布局和样式；Copilot 首次下载增加实际进度与网络停滞提示。"
    (assets / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    hashes = []
    for path in sorted(assets.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            hashes.append(f"{hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}  {path.name}")
    (assets / "SHA256SUMS").write_text("\n".join(hashes) + "\n")
    args.notes.write_text(f"""## Argus {version} · Windows & Mac

- 修复 Windows / Mac 工作台布局：按中间区域实际可用宽度调整分栏，避免两侧栏展开时挤压标题和卡片。
- 修复全局样式覆盖工作台的问题，恢复标题字号、按钮和内容间距。
- Copilot 首次下载显示实际进度；连续 30 秒没有进展时提示检查网络或开启系统代理 / TUN。
- 保留内部测试 Key 入口、自动安装 Copilot 和内置工作台，无需命令行或个人 Coding 账号。
- 同一 Key 可跨设备使用，累计额度 100 万 token；上游账号凭据保留在服务器。

### Downloads

| System | Installer |
|---|---|
| Windows x64 | [{installers[0].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[0].name}) |
| Mac Apple Silicon (macOS 13+) | [{installers[1].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[1].name}) |
| Mac Intel (macOS 13+) | [{installers[2].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[2].name}) |

Windows: run the installer. Mac: open the DMG and drag Argus to Applications.
Then choose **使用内部测试 Key**, paste your invitation Key and select **开始试用**.
Keys are distributed privately; none are embedded in these packages.

The Mac build is for internal testing and is not Apple Developer ID notarized.
If macOS blocks first launch, use **System Settings → Privacy & Security → Open Anyway**.
Desktop updater packages for all three platforms carry the existing Argus updater signature.
`SHA256SUMS` lists the downloadable artifact checksums.

Release identity: `{identity['release_id']}`.
""", encoding="utf-8")
    print(f"Prepared {version} with Windows and both Mac updater targets")


if __name__ == "__main__":
    main()
