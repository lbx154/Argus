from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "argus"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front, separator, _body = text[4:].partition("\n---\n")
    assert separator
    value = yaml.safe_load(front)
    assert isinstance(value, dict)
    return value


def test_dual_manifests_share_identity_version_and_skills() -> None:
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude = _json(PLUGIN / ".claude-plugin" / "plugin.json")

    assert codex["name"] == claude["name"] == "argus"
    assert codex["version"] == claude["version"] == "0.1.1"
    assert codex["skills"] == claude["skills"] == "./skills/"
    assert codex["mcpServers"] == "./.mcp.json"
    assert claude["mcpServers"] == "./mcp/claude.json"


def test_host_mcp_wrappers_launch_the_same_bundled_command() -> None:
    codex = _json(PLUGIN / ".mcp.json")
    claude = _json(PLUGIN / "mcp" / "claude.json")

    assert set(codex) == {"mcpServers"}
    assert set(claude) == {"mcpServers"}
    codex_server = codex["mcpServers"]["argus"]
    claude_server = claude["mcpServers"]["argus"]
    assert codex_server == {
        "command": "node",
        "args": ["${PLUGIN_ROOT}/bin/argus-plugin-mcp.mjs"],
    }
    assert claude_server == {
        "command": "node",
        "args": ["${CLAUDE_PLUGIN_ROOT}/bin/argus-plugin-mcp.mjs"],
    }

    launcher = PLUGIN / "bin" / "argus-plugin-mcp"
    assert os.access(launcher, os.X_OK)
    launcher_text = launcher.read_text(encoding="utf-8")
    assert "argus-plugin-mcp.mjs" in launcher_text
    assert (PLUGIN / "bin" / "argus-plugin-mcp.cmd").is_file()
    node_launcher = (PLUGIN / "bin" / "argus-plugin-mcp.mjs").read_text(
        encoding="utf-8"
    )
    assert "ARGUS_PLUGIN_PYTHON" in node_launcher
    assert "venv', 'Scripts', 'python.exe" in node_launcher
    assert "argus_skill.plugin.mcp_server" in node_launcher


def test_node_launcher_resolves_explicit_python_cross_platform() -> None:
    completed = subprocess.run(
        ["node", str(PLUGIN / "bin" / "argus-plugin-mcp.mjs")],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ARGUS_PLUGIN_PYTHON": sys.executable,
            "ARGUS_PLUGIN_LAUNCHER_DRY_RUN": "1",
            "PYTHONPATH": os.pathsep.join(
                filter(None, (str(ROOT), os.environ.get("PYTHONPATH")))
            ),
        },
    )

    selected = json.loads(completed.stdout)
    assert Path(selected["command"]).resolve() == Path(sys.executable).resolve()
    assert selected["args"] == ["-m", "argus_skill.plugin.mcp_server"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal propagation")
def test_node_launcher_exits_after_forwarded_termination_signal(
    tmp_path: Path,
) -> None:
    fake_python = tmp_path / "python"
    ready = tmp_path / "child-ready"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
        f"touch {shlex.quote(str(ready))}\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    process = subprocess.Popen(
        ["node", str(PLUGIN / "bin" / "argus-plugin-mcp.mjs")],
        env={**os.environ, "ARGUS_PLUGIN_PYTHON": str(fake_python)},
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file()
        process.send_signal(signal.SIGTERM)
        returncode = process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert returncode in {0, -signal.SIGTERM}


def test_python_package_installs_plugin_server_entrypoint() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["argus-plugin-server"] == (
        "argus_skill.plugin.mcp_server:main"
    )
    assert "mcp>=1.20,<2" in pyproject["project"]["dependencies"]
    assert "pydantic-settings>=2.5.2,<2.15" in pyproject["project"]["dependencies"]


def test_repo_marketplaces_publish_the_same_plugin_directory() -> None:
    codex = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude = _json(ROOT / ".claude-plugin" / "marketplace.json")

    codex_entry = next(row for row in codex["plugins"] if row["name"] == "argus")
    claude_entry = next(row for row in claude["plugins"] if row["name"] == "argus")
    assert codex_entry["source"] == {
        "source": "local",
        "path": "./plugins/argus",
    }
    assert codex_entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert claude_entry["source"] == "./plugins/argus"


def test_shared_skills_use_cross_host_frontmatter() -> None:
    expected = {"argus-run", "argus-status", "target-disease-research"}
    skill_paths = sorted((PLUGIN / "skills").glob("*/SKILL.md"))

    assert {path.parent.name for path in skill_paths} == expected
    for path in skill_paths:
        front = _frontmatter(path)
        assert set(front) == {"name", "description"}
        assert front["name"] == path.parent.name
        assert str(front["description"]).strip()


def test_plugin_glossary_keeps_runtime_and_medical_vertical_distinct() -> None:
    context = (PLUGIN / "CONTEXT.md").read_text(encoding="utf-8")
    normalized = " ".join(context.split())

    assert "Argus plugin" in context
    assert "Argus runtime" in context
    assert "Medical vertical" in context
    assert "owns its own stage machine" in normalized
    assert "not a separate plugin" in context


def test_target_disease_skill_routes_manager_to_medical_vertical() -> None:
    skill = (PLUGIN / "skills" / "target-disease-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "built-in `medical` vertical" in skill
    assert "built-in `medical` domain" not in skill
    assert "`research` workflow with" not in skill
    assert "Call `argus_message` exactly once" in skill
    assert "Do not dispatch while resolving the project" in skill


def test_documentation_covers_both_hosts_and_medical_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    plugin_readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "docs/plugin.md" in readme
    assert "docs/plugin.md" in readme_zh
    assert "install.sh" in plugin_readme
    assert "install.ps1" in plugin_readme
    assert "medical` vertical" in plugin_readme


def test_one_command_installer_and_short_guide() -> None:
    installer = PLUGIN / "install.sh"
    windows_installer = PLUGIN / "install.ps1"
    guide = ROOT / "docs" / "plugin.md"

    assert installer.is_file()
    assert windows_installer.is_file()
    assert os.access(installer, os.X_OK)
    if shutil.which("sh"):
        subprocess.run(["sh", "-n", str(installer)], check=True)

    installer_text = installer.read_text(encoding="utf-8")
    windows_installer_text = windows_installer.read_text(encoding="utf-8")
    launcher_text = (PLUGIN / "bin" / "argus-plugin-mcp.mjs").read_text(
        encoding="utf-8"
    )
    guide_text = guide.read_text(encoding="utf-8")

    assert "ARGUS_HOME" in installer_text
    assert "Node.js 22.12+" in installer_text
    assert 'repo="lbx154/Argus"' in installer_text
    assert 'codex plugin marketplace add "$repo" --ref main' in installer_text
    assert 'claude plugin marketplace add "$repo"' in installer_text
    assert "py -m pip install --upgrade --force-reinstall" in windows_installer_text
    assert "python3 -m venv" not in windows_installer_text
    assert "Node.js 22.12+" in windows_installer_text
    assert "ARGUS_PLUGIN_PYTHON" in launcher_text
    assert "argus_skill.plugin.mcp_server" in launcher_text
    assert "install.sh | sh -s -- codex" in guide_text
    assert "install.sh | sh -s -- claude" in guide_text
    assert "install.sh | sh -s -- all" in guide_text
    assert "argus-plugin-install.ps1" in guide_text
    assert "Node.js 22.12+" in guide_text
    assert "target-disease-research" in guide_text
