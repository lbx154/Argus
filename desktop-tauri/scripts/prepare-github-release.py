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
    for path in sorted(assets.glob("latest-*.json")):
        platform = json.loads(path.read_text())
        assert platform["version"] == manifest["version"], "Mismatched platform release versions"
        assert not manifest["platforms"].keys() & platform["platforms"].keys(), "Duplicate platform"
        manifest["platforms"].update(platform["platforms"])
    assert set(manifest["platforms"]) == {
        "windows-x86_64", "darwin-aarch64", "darwin-x86_64", "linux-x86_64",
    }
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
        metadata = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
        wheel_version = next(
            line.split(":", 1)[1].strip()
            for line in wheel.read(metadata).decode("utf-8").splitlines()
            if line.startswith("Version:")
        )
    assert wheel_version == version
    installers = [assets / f"Argus-{version}-setup.exe",
                  assets / f"Argus-{version}-macos-aarch64.dmg",
                  assets / f"Argus-{version}-macos-x86_64.dmg",
                  assets / f"Argus-{version}-linux-x86_64.AppImage",
                  assets / f"Argus-{version}-linux-x86_64.deb"]
    assert all(path.is_file() for path in installers)
    manifest["notes"] = "更清晰的日夜图标、自适应任务标题、试用 Key 更换入口，以及输入框下方的后端和模型显示。"
    (assets / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    hashes = []
    for path in sorted(assets.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            hashes.append(f"{hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}  {path.name}")
    (assets / "SHA256SUMS").write_text("\n".join(hashes) + "\n")
    args.notes.write_text(f"""## Argus {version} · Windows, Mac & Linux

- 日夜切换改用清晰的线条太阳 / 月亮图标，便于与设置齿轮区分。
- 任务标题随中间面板的可用宽度展开，拖动侧栏后可显示完整标题；空间不足时才省略。
- 在「设置 → 试用账号」和输入框下方增加「更换 Key」入口，复用验证与保存流程，保留项目和聊天记录。
- 输入框下方显示当前后端、模型和推理强度；试用模式显示 **GPT-5.5 · high**，普通模式跟随当前活动角色的配置。
- 保留内部测试 Key、自动准备 Copilot 和内置工作台，无需命令行或个人 Coding 账号。
- 包含最新 main 的工作台和任务状态改进，以及暂停 / 完成边界修复。

### Downloads

| System | Installer |
|---|---|
| Windows x64 | [{installers[0].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[0].name}) |
| Mac Apple Silicon (macOS 13+) | [{installers[1].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[1].name}) |
| Mac Intel (macOS 13+) | [{installers[2].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[2].name}) |
| Linux x86_64 · AppImage | [{installers[3].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[3].name}) |
| Ubuntu 22.04+ / compatible Debian x86_64 | [{installers[4].name}](https://github.com/lbx154/Argus/releases/download/v{version}/{installers[4].name}) |

Windows: run the installer. Mac: open the DMG and drag Argus to Applications.
Linux: install the `.deb` with your software installer, or enable execution in the
AppImage file's properties and open it. Linux builds require a desktop session.
Then choose **使用内部测试 Key**, paste your invitation Key and select **开始试用**.
Keys are distributed privately; none are embedded in these packages.

The Mac build is for internal testing and is not Apple Developer ID notarized.
If macOS blocks first launch, use **System Settings → Privacy & Security → Open Anyway**.
Desktop updater packages for all four architecture targets carry the existing Argus updater signature.
`SHA256SUMS` lists the downloadable artifact checksums.

Package version: `{wheel_version}`.
""", encoding="utf-8")
    print(f"Prepared {version} with Windows, both Mac architectures and Linux updater targets")


if __name__ == "__main__":
    main()
