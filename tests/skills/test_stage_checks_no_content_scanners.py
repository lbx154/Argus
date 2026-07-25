from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "argus_skill" / "verticals"
_CONTENT_SCANNER = re.compile(
    r"\b(?:grep|egrep|fgrep|awk|sed|cat|head|tail|jq|perl|ruby)\b"
    r"|(?:python|python3|\{python\})\s+-c\b"
)


def _stage_modules() -> list[str]:
    modules = []
    for path in _VERTICALS_ROOT.rglob("stages.py"):
        relative = path.relative_to(_VERTICALS_ROOT).with_suffix("")
        modules.append("argus_skill.verticals." + ".".join(relative.parts))
    return sorted(modules)


@pytest.mark.parametrize("module_name", _stage_modules())
def test_stage_checks_do_not_embed_content_scanners(module_name: str) -> None:
    """A harness-run stage check must not scan CONTENT to form a judgment.

    Only the legacy ``STAGE_CHECKS`` shape (stage -> [(description, shell
    command)]) can express this anti-pattern. Verticals that migrated to typed
    ``ChecklistItem`` statements hand the judgment to the Reviewer by
    construction, so there is nothing here to guard — skip them rather than
    erroring, which is what silently retired this guard once ``STAGE_CHECKS``
    was renamed.
    """
    module = importlib.import_module(module_name)
    stage_checks = getattr(module, "STAGE_CHECKS", None)
    if not stage_checks:
        pytest.skip(f"{module_name} has no harness-run STAGE_CHECKS")
    for stage, checks in stage_checks.items():
        for description, command in checks:
            assert not _CONTENT_SCANNER.search(command), (
                f"{module_name}.{stage} ({description}) embeds a content scanner; "
                "use structural test/find checks or a typed validator module"
            )
