"""First-party JS adapters belong to the shared release identity."""
from pathlib import Path

import pytest

from argus.release import _source_files, source_files


@pytest.mark.parametrize("relative", [
    "argus/webapi/plugin_desktop.js",
    "argus/agent_cli/pi_output_schema_extension.mjs",
    "argus/advisor/pi_extension.mjs",
    "argus/core/role_tool_bridge.mjs",
    "argus/life/experience_extension.mjs",
    "argus/messaging/pi_tools.mjs",
    "desktop-tauri/src-tauri/tauri.macos.conf.json",
])
def test_adapter_is_listed_in_the_release(tmp_path: Path, relative: str) -> None:
    source = tmp_path / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("/* product revision */\n", encoding="utf-8")
    assert source_files(tmp_path) == {relative: source.stat().st_size}


def test_adapter_patterns_do_not_include_dependencies(tmp_path: Path) -> None:
    for relative in (
        "argus/webapi/node_modules/dependency/index.js",
        "argus/agent_cli/node_modules/dependency/index.mjs",
        "argus/advisor/dist/generated.mjs",
    ):
        generated = tmp_path / relative
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("/* not a source input */\n", encoding="utf-8")
    assert list(_source_files(tmp_path)) == []


def test_external_packages_are_not_part_of_the_release(tmp_path: Path) -> None:
    for relative in (
        "external_service/service/gateway.py",
        "external_service/service/extension.mjs",
        "external_service/deploy/preview.py",
    ):
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# external service\n", encoding="utf-8")
    assert list(_source_files(tmp_path)) == []
