"""Cross-platform optional-package installer smoke; never calls a model."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def main():
    from ..core import plugin_manager as manager
    from .build_plugins import ROOT

    with tempfile.TemporaryDirectory(prefix="argus-plugin-install-") as directory:
        root = Path(directory)
        os.environ["ARGUS_SKILL_HOME"] = str(root)
        os.environ["ARGUS_WORKBENCH_HOST_ROOT"] = str(root)
        if not os.environ.get("ARGUS_PLUGIN_CATALOG"):
            os.environ["ARGUS_PLUGIN_CATALOG"] = str(ROOT / "argus_skill/plugin_catalog.json")
        os.environ["ARGUS_SKILL_RUNNER_BACKEND"] = "pi"
        for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER"):
            os.environ["ARGUS_SKILL_" + role + "_BACKEND"] = "pi"
        assert not manager.installed(root)
        manager.mutate("crystalpilot", "install", root)
        deadline = time.monotonic() + 10800
        while time.monotonic() < deadline:
            row = manager.plugin_rows(root)[0]
            if row["operation"].get("status") == "failed":
                raise RuntimeError(row["operation"]["error"])
            if row["installed"] and row["operation"].get("status") == "completed":
                break
            time.sleep(1)
        else:
            raise TimeoutError("Plugin installation did not complete")
        plugin = manager.load_plugin("crystalpilot", root)
        assert plugin and plugin.vertical_module().ARGUS_VERTICAL_API_VERSION == 1
        health = row["health"]
        assert all(r["status"] == "ready" for r in health["components"] if r["automatic"]), health
        assert all(
            r["status"] == "missing" for r in health["components"] if r["license_required"]
        ), health
        software = root / "extensions/crystalpilot/resources/software.json"
        assert software.is_file()
        sentinel = root / "plugins/crystalpilot/retained.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("{}")
        manager.mutate("crystalpilot", "uninstall", root)
        assert not manager.installed(root) and sentinel.exists() and software.exists()
        print(
            json.dumps(
                {
                    "platform": os.name,
                    "installation": True,
                    "uninstall_preserves_data": True,
                    "free_software_verified": True,
                    "license_not_bundled": True,
                }
            )
        )


if __name__ == "__main__":
    main()
