from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend" / "web" / "public" / "math-vertical"
DIST = ROOT / "frontend" / "web" / "dist" / "math-vertical"


class _AssetLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.values.extend(value for key in ("href", "src") if (value := values.get(key)))


def test_math_vertical_demo_is_story_first_and_honest() -> None:
    page = (PUBLIC / "index.html").read_text(encoding="utf-8")

    assert "<h1>让 Argus 学会做数学研究</h1>" in page
    assert "证据不足时拒绝宣布成功" in page
    assert "这一步做完了。这个问题还没有。" in page
    assert "Math Vertical · Mechanism Demo" not in page
    assert "三种专属机制，一套通用生命周期" not in page
    assert "五个核心 Skill 组合出九类数学能力" not in page

    products = json.loads((PUBLIC / "PRODUCTS.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in products["items"]}
    assert products["project_status"] == "open_a_star_f4"
    assert by_id["theorem-proof"]["status"] == "reviewed"
    assert by_id["theorem-proof"]["novelty"] == "mixed_unverified"
    assert by_id["lean-certificate"]["status"] == "in_progress"
    assert by_id["exact-verifier"]["status"] == "passed_bounded"
    assert "The A*/F4 target studied by this project remains open." in products["non_claims"]
    assert products["items"][2]["scope"].endswith("congruence and coordinate floors for r=0..20")


def test_math_vertical_demo_links_and_manifest_are_complete() -> None:
    parser = _AssetLinks()
    parser.feed((PUBLIC / "index.html").read_text(encoding="utf-8"))
    for value in parser.values:
        parsed = urlparse(value)
        if parsed.scheme or value.startswith("#"):
            continue
        assert (PUBLIC / parsed.path).exists(), value

    manifest = json.loads((PUBLIC / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["asset_count_excluding_manifest"] == len(manifest["assets"])
    for item in manifest["assets"]:
        content = (PUBLIC / item["path"]).read_bytes()
        assert len(content) == item["bytes"]
        assert hashlib.sha256(content).hexdigest() == item["sha256"]

    public_files = {
        path.relative_to(PUBLIC)
        for path in PUBLIC.rglob("*")
        if path.is_file()
    }
    dist_files = {
        path.relative_to(DIST)
        for path in DIST.rglob("*")
        if path.is_file()
    }
    assert dist_files == public_files
    for relative in public_files:
        assert (DIST / relative).read_bytes() == (PUBLIC / relative).read_bytes()
