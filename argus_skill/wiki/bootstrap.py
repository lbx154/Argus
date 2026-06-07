"""Initialize a per-project .autors/<project>/wiki/ tree from templates."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

_TEMPLATE_PACKAGE = "argus_skill.wiki.templates"

_DIRS = (
    "sources/papers",
    "sources/repos",
    "sources/runs",
    "pages/techniques",
    "pages/conflicts",
    "pages/patterns",
    "queries",
    "data",
    "scripts",
)

_FILES = {
    "data/schema.yaml": "schema.yaml",
    "data/tags.yaml": "tags.yaml",
    "query_pack.md": "query_pack.md",
    "README.md": "README.md",
}


def is_initialized_wiki(root: Path) -> bool:
    required_dirs = (
        "sources",
        "sources/papers",
        "sources/repos",
        "sources/runs",
        "pages",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
        "queries",
        "data",
    )
    required_files = ("data/schema.yaml", "query_pack.md")
    return all((root / rel).is_dir() for rel in required_dirs) and all(
        (root / rel).is_file() for rel in required_files
    )


def init_wiki(project: str, *, base: Path | None = None) -> Path:
    base = base or Path.cwd()
    root = base / ".autors" / project / "wiki"
    for sub in _DIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    pkg = resources.files(_TEMPLATE_PACKAGE)
    for rel, template_name in _FILES.items():
        target = root / rel
        if target.exists():
            continue  # idempotent -- never overwrite user edits
        target.write_text(
            pkg.joinpath(template_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return root
