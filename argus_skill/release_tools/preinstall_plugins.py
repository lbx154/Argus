"""Install declared plugins synchronously so an image build can bake them in.

    ARGUS_SKILL_HOME=/tmp/argus-build \\
    python -m argus_skill.release_tools.preinstall_plugins crystalpilot --root /opt/argus-plugins

The command runs the same verified install as the plugin center: it downloads
the wheel pinned in the catalog, checks its SHA-256, builds the isolated
scientific environment, runs the plugin's automatic setup and activates the
release under ``<root>/extensions``. Every tenant then points at that root
through ``ARGUS_WORKBENCH_HOST_ROOT`` and keeps ``ARGUS_PLUGINS_PREINSTALL``
set, so the web server confirms the copy at startup and the interface presents
the workbench as provided by the service. Exit status is 0 only when every
named plugin is installed, enabled and current.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv=None):
    from ..core import plugin_manager as manager

    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.release_tools.preinstall_plugins",
        description="Install the plugins a deployment declares and wait for them to finish.",
    )
    parser.add_argument(
        "ids", nargs="*", help=f"catalog ids; defaults to {manager.PREINSTALL_ENV}"
    )
    parser.add_argument(
        "--root",
        help="installation host root (default: ARGUS_WORKBENCH_HOST_ROOT or the Argus home)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10800,
        help="seconds to wait for one plugin's install to finish (default: 10800)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    if args.root:
        os.environ["ARGUS_WORKBENCH_HOST_ROOT"] = str(Path(args.root).expanduser().resolve())
    ids = args.ids or manager.preinstalled_ids()
    if not ids:
        parser.error(f"name at least one catalog id or set {manager.PREINSTALL_ENV}")
    results = manager.preinstall(args.root, ids=ids, wait=True, timeout=args.timeout)
    print(json.dumps({"root": str(manager.host_root(args.root)), "plugins": results}, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] in {"ready", "completed"} for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
