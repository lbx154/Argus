"""Install declared verticals synchronously so an image build can bake them in.

    ARGUS_SKILL_HOME=/tmp/argus-build \\
    python -m argus.release_tools.preinstall_verticals materials chip_design --root /opt/argus-verticals

The command runs the same verified install as the Vertical Store: it fetches
the catalog, downloads each archive (dependencies first), checks its SHA-256
and size, extracts it with the store's guards and records it in
``<root>/registry.json``. Every tenant then points at that root through
``ARGUS_VERTICALS_HOST_ROOT`` and keeps ``ARGUS_VERTICALS_PREINSTALL`` set, so
the web server confirms the copy at startup and the interface presents the
verticals as provided by the service (install/update/remove disabled). Exit
status is 0 only when every named vertical is installed, current and enabled.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv=None):
    from ..verticals import store

    parser = argparse.ArgumentParser(
        prog="python -m argus.release_tools.preinstall_verticals",
        description="Install the verticals a deployment declares and wait for them to finish.",
    )
    parser.add_argument(
        "names", nargs="*", help=f"vertical names; defaults to {store.PREINSTALL_ENV}"
    )
    parser.add_argument(
        "--root",
        help=f"store root to prepare (default: {store.HOST_ROOT_ENV} or <ARGUS_SKILL_HOME>/verticals)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3600,
        help="seconds to wait for one vertical's install to finish (default: 3600)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    if args.root:
        os.environ[store.HOST_ROOT_ENV] = str(Path(args.root).expanduser().resolve())
    names = args.names or store.preinstalled_names()
    if not names:
        parser.error(f"name at least one vertical or set {store.PREINSTALL_ENV}")
    results = store.preinstall(names=names, wait=True, timeout=args.timeout)
    print(json.dumps({"root": str(store.store_root()), "verticals": results}, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] in {"ready", "done"} for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
