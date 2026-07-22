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
    module = importlib.import_module(module_name)
    for stage, checks in module.STAGE_CHECKS.items():
        for description, command in checks:
            assert not _CONTENT_SCANNER.search(command), (
                f"{module_name}.{stage} ({description}) embeds a content scanner; "
                "use structural test/find checks or a typed validator module"
            )
