"""Frontmatter integrity for every shipped Skill document.

The runtime deliberately does not parse Skill documents, so nothing at run time
notices a malformed header. These checks run over the source tree instead, on
exactly the set of documents ``seed_*`` would install.

The motivating defect: an unquoted ``description: <prefix>: <suffix>`` line is a
valid YAML *mapping*, not a string. A tool that read one of those back and handed
it to :meth:`Skill.render` produced a document whose description was a JSON
object, and the damage then survived every later round because no reader
complained. Quoting every scalar removes the ambiguity at the source.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from argus_skill.skills.builtins import (
    iter_builtin_skill_texts,
    iter_vertical_skill_texts,
    vertical_skill_source_path,
)
from argus_skill.skills.store import Skill
from argus_skill.verticals import __file__ as _VERTICALS_INIT

_HEADER = re.compile(r'\A---\nname: (?P<name>.*)\ndescription: (?P<description>.*)\n---\n')

# The description is the first thing an Agent reads when deciding whether to open
# a Skill. It is a routing line, not an abstract; the authoring guide asks for a
# sharp document rather than a padded one. The bound is generous on purpose so it
# catches a whole body pasted into the header, not merely a long sentence.
_MAX_DESCRIPTION = 1200


def _bundled_verticals() -> list[str]:
    """Names of the in-tree verticals, per the ``verticals/<v>/skills`` layout."""
    root = Path(_VERTICALS_INIT).parent
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(("_", "."))
        and vertical_skill_source_path(entry.name).is_dir()
    )


def _shipped_skills() -> list[tuple[str, str]]:
    """Skill *documents* only.

    Verticals also ship executable assets (figure renderers and the like) beside
    their Skills. Those are code, not documents an Agent reads as a Skill, so the
    header contract does not apply to them.
    """
    seen: dict[str, str] = {}
    for filename, text in iter_builtin_skill_texts():
        if filename.endswith(".md"):
            seen.setdefault(f"builtin:{filename}", text)
    verticals = _bundled_verticals()
    assert verticals, "no bundled verticals discovered"
    for vertical in verticals:
        for filename, text in iter_vertical_skill_texts(vertical):
            if filename.endswith(".md"):
                seen.setdefault(f"{vertical}:{filename}", text)
    assert seen, "no shipped Skills discovered"
    return sorted(seen.items())


SHIPPED = _shipped_skills()


@pytest.mark.parametrize("label,text", SHIPPED, ids=[label for label, _ in SHIPPED])
def test_shipped_skill_header_is_two_quoted_scalars(label: str, text: str) -> None:
    match = _HEADER.match(text)
    assert match, f"{label}: header must be exactly name then description"

    for field in ("name", "description"):
        raw = match.group(field)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - failure message
            pytest.fail(
                f"{label}: {field} must be a quoted scalar so a colon cannot turn "
                f"it into a mapping, got {raw!r} ({exc})"
            )
        assert isinstance(value, str), (
            f"{label}: {field} parsed to {type(value).__name__}, not a string. A "
            "mapping here means an unquoted 'key: value' line was read back and "
            "re-rendered."
        )
        assert value.strip(), f"{label}: {field} must not be empty"
        assert not value.lstrip().startswith("{"), (
            f"{label}: {field} still holds a serialized mapping"
        )

    description = json.loads(match.group("description"))
    assert len(description) <= _MAX_DESCRIPTION, (
        f"{label}: description is {len(description)} chars; it belongs in the "
        "body, not the header"
    )


@pytest.mark.parametrize("label,text", SHIPPED, ids=[label for label, _ in SHIPPED])
def test_shipped_skill_header_round_trips_through_render(label: str, text: str) -> None:
    """A shipped document must match what the runtime would write for it."""
    match = _HEADER.match(text)
    assert match, f"{label}: header must be exactly name then description"
    skill = Skill(
        name=json.loads(match.group("name")),
        description=json.loads(match.group("description")),
        content=text[match.end():],
    )
    assert skill.render().startswith(text[: match.end()].rstrip("\n")), (
        f"{label}: header does not match Skill.render() output"
    )


def test_render_refuses_a_mapping_description() -> None:
    with pytest.raises(TypeError, match="description must be a string"):
        Skill("n", {"a": "b"}, "body").render()  # type: ignore[arg-type]


def test_render_refuses_an_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        Skill("   ", "d", "body").render()
