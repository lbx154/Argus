"""Migration notice for the retired standalone SVG framework-figure command."""
from __future__ import annotations

import shlex
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = shlex.join([sys.executable, "-m", "argus_skill.tools.ppt_master", "status"])
    message = (
        "The old standalone SVG framework-figure command has been removed.\n"
        "Use Method D: an image design blueprint followed by native editable PPT "
        "through PPT Master. If no image interface is available, use Method B "
        "direct native PPT design.\n"
        "Read engineer/paper-framework-figure-studio.md and "
        "engineer/presentation-master.md in the active skill library.\n"
        "Locate and check the installed PPT Master with:\n"
        f"  {command}\n"
        "Then follow the selected skill to produce the native PPTX and matching "
        "PDF/PNG. This retired command creates no figure files.\n"
        "Quantitative plotting and PPT Master's internal SVG conversion remain available."
    )
    help_requested = args in (["--help"], ["-h"])
    print(message, file=sys.stdout if help_requested else sys.stderr)
    return 0 if help_requested else 2


if __name__ == "__main__":
    raise SystemExit(main())
