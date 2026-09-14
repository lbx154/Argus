"""First-party JS adapters must change the shared release identity."""
from pathlib import Path

import pytest

from argus_skill.release import _source_files, compute_source_digest


@pytest.mark.parametrize("relative", [
    "argus_skill/webapi/plugin_desktop.js",
    "argus_skill/agent_cli/pi_output_schema_extension.mjs",
    "argus_skill/trial/pi_training_extension.mjs",
    "argus_skill/advisor/pi_extension.mjs",
    "argus_skill/core/role_tool_bridge.mjs",
    "argus_skill/life/experience_extension.mjs",
    "argus_skill/messaging/pi_tools.mjs",
    "desktop-tauri/src-tauri/tauri.macos.conf.json",
])
def test_adapter_only_edit_changes_release_digest(tmp_path: Path, relative: str) -> None:
    source = tmp_path / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("/* first product revision */\n", encoding="utf-8")
    assert source in set(_source_files(tmp_path))
    initial = compute_source_digest(tmp_path)
    source.write_text("/* second product revision */\n", encoding="utf-8")
    assert compute_source_digest(tmp_path) != initial


def test_adapter_patterns_do_not_include_dependencies(tmp_path: Path) -> None:
    for relative in (
        "argus_skill/webapi/node_modules/dependency/index.js",
        "argus_skill/agent_cli/node_modules/dependency/index.mjs",
        "argus_skill/trial/dist/generated.mjs",
    ):
        generated = tmp_path / relative
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("/* not a source input */\n", encoding="utf-8")
    assert list(_source_files(tmp_path)) == []
