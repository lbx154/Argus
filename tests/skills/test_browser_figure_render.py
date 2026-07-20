from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "argus_skill"
    / "verticals"
    / "research"
    / "skills"
    / "engineer"
    / "research_visual_scripts"
    / "browser_render.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("browser_render", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_browser_renderer_accepts_self_contained_svg() -> None:
    module = _module()

    module._validate_svg("<svg viewBox='0 0 100 50'><text>OK</text></svg>")


def test_browser_renderer_rejects_external_svg_dependency() -> None:
    module = _module()

    with pytest.raises(ValueError, match="external dependency"):
        module._validate_svg(
            "<svg viewBox='0 0 100 50'>"
            "<image href='https://example.com/image.png'/>"
            "</svg>"
        )

    with pytest.raises(ValueError, match="external dependency"):
        module._validate_svg(
            "<svg viewBox='0 0 100 50'><image href='image.png'/></svg>"
        )

    with pytest.raises(ValueError, match="external dependency"):
        module._validate_svg(
            "<svg viewBox='0 0 100 50'>"
            "<style>.x{fill:url(texture.png)}</style>"
            "</svg>"
        )


def test_browser_renderer_accepts_local_file_and_rejects_remote_url(
    tmp_path: Path,
) -> None:
    module = _module()
    html = tmp_path / "figure.html"
    html.write_text("<html></html>\n", encoding="utf-8")

    assert module._local_url(str(html)).startswith("file://")
    with pytest.raises(ValueError, match="remote figure URLs"):
        module._local_url("https://example.com/figure")
