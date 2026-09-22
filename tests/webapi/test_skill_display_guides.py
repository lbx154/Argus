"""Chinese UI guides must match the real read-only bundled Skill catalog."""
import json
from pathlib import Path

from argus.skills.catalog import Library, catalog

ROOT = Path(__file__).resolve().parents[2]


def test_display_guides_match_real_catalog_names_and_scopes_without_rewriting_sources():
    guides = json.loads((ROOT / "frontend/web/src/lib/bundledSkillChinese.json").read_text(encoding="utf-8"))
    libraries = {"global:bundled": Library("global:bundled", "global", ROOT / "argus/builtin_skills", "bundled")}
    for guide in guides.values():
        source = guide["source"]
        if source.startswith("argus/builtin_skills/"):
            continue
        owner = source.split("/skills/", 1)[0]
        vertical = owner.split("/")[-1]
        prefix = "domain" if owner.startswith("argus/domains/") else "vertical"
        identity = f"{prefix}:{vertical}:bundled"
        libraries[identity] = Library(identity, "vertical", ROOT / owner / "skills", "bundled", vertical)
    index = catalog(list(libraries.values()))
    assert index["errors"] == []
    assert len(index["items"]) == len(guides)
    for item in index["items"]:
        key = f"global/{item['path']}" if item["scope"] == "global" else f"vertical/{item['vertical']}/{item['path']}"
        assert item["is_default"] is True
        assert guides[key]["originalName"] == item["name"]
        assert item["source"] == "bundled"
