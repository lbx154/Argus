from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_readme_has_distinct_platform_install_contracts() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    windows = _section(text, "### Windows 10/11", "### macOS")
    macos = _section(text, "### macOS", "### Linux")
    linux = _section(text, "### Linux", "### Backend notes")

    assert "pip install --upgrade" in windows
    assert "py -m pip install --upgrade" in windows
    assert "--force-reinstall" in windows
    assert "py -3.11" not in windows
    assert "-m venv" not in windows
    assert "$Argus --setup" in windows
    assert 'Join-Path $Scripts "argus.exe"' in windows
    assert "uv tool install" in macos
    assert "uv tool dir --bin" in macos
    assert "uv tool update-shell" in macos
    assert "uv venv" not in macos
    assert "python3 -m venv .venv" in linux
    assert 'ARGUS_BIN="$HOME/Argus/.venv/bin/argus"' in linux
    assert '"$ARGUS_BIN" --setup' in linux
    assert "Node.js **22.12+**" in text


def test_chinese_readme_matches_platform_install_contracts() -> None:
    text = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    windows = _section(text, "### Windows 10/11", "### macOS")
    macos = _section(text, "### macOS", "### Linux")
    linux = _section(text, "### Linux", "### Backend 说明")

    assert "pip install --upgrade" in windows
    assert "py -m pip install --upgrade" in windows
    assert "--force-reinstall" in windows
    assert "py -3.11" not in windows
    assert "不创建虚拟环境" in windows
    assert "uv tool install" in macos
    assert "uv tool dir --bin" in macos
    assert "python3 -m venv .venv" in linux
    assert 'ARGUS_BIN="$HOME/Argus/.venv/bin/argus"' in linux
    assert "Node.js **22.12+**" in text


def test_agent_install_uses_os_specific_executables() -> None:
    text = (ROOT / "docs" / "agent-install.md").read_text(encoding="utf-8")
    windows = _section(text, "## Windows 10/11", "## macOS")
    macos = _section(text, "## macOS", "## Linux")
    linux = _section(text, "## Linux", "## OpenAI-compatible endpoint")

    assert "pip install --upgrade" in windows
    assert "py -m pip install --upgrade" in windows
    assert "--force-reinstall" in windows
    assert "py -3.11" not in windows
    assert "-m venv" not in windows
    assert "& $Argus --setup" in windows
    assert "uv tool install" in macos
    assert "uv tool install --force" in macos
    assert "uv tool dir --bin" in macos
    assert '"$ARGUS_BIN" doctor --deep --advisor auto' in linux
    assert "real Agent-turn smoke" in text
    assert "Node.js 22.12+" in text


def test_install_guides_cover_updates_paths_models_and_doctor_semantics() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    agent = (ROOT / "docs" / "agent-install.md").read_text(encoding="utf-8")
    desktop = (ROOT / "docs" / "windows-desktop.md").read_text(encoding="utf-8")

    for text in (readme, chinese, agent):
        assert "`qoder`" in text
        assert "`dsh`" in text
        assert "--config-help" in text
        assert "--advisor none --verify" in text

    update = _section(readme, "## Update", "## Uninstall")
    assert 'py -m pip install --upgrade --force-reinstall' in update
    assert "uv tool install --force" in update
    assert '"$HOME/Argus/.venv/bin/argus" update' in update
    assert "uv tool upgrade argus-skill" not in update
    assert "\npip install " not in update

    assert "If the Releases page has no matching installer asset" in desktop


def test_readmes_surface_the_wechat_qr_before_installation() -> None:
    asset = ROOT / "docs" / "assets" / "argus-wechat-group-2.jpg"
    assert asset.is_file()
    assert asset.stat().st_size > 100_000

    for name, heading in (
        ("README.md", "## WeChat community"),
        ("README.zh-CN.md", "## 微信群"),
    ):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert text.count(heading) == 1
        assert text.count('src="docs/assets/argus-wechat-group-2.jpg"') == 1
        assert text.index(heading) < text.index("## Quick Install" if name == "README.md" else "## 快速安装")
        assert "Docker" in text


def test_readmes_recommend_agent_assisted_installation_before_manual_steps() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "> [!TIP]" in english
    assert "**Recommended: let the Code Agent" in english
    assert english.index("### Recommended: Agent-assisted installation") < english.index(
        "### Windows 10/11"
    )
    assert "> [!TIP]" in chinese
    assert "**推荐：让你正在使用的 Code Agent" in chinese
    assert chinese.index("### 推荐：使用 Agent 一键安装") < chinese.index(
        "### Windows 10/11"
    )
    for text in (english, chinese):
        assert "https://github.com/lbx154/Argus/blob/main/docs/agent-install.md" in text
