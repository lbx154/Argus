"""The shipped 红楼 example canon must stay a VALID, self-consistent seed — a live
fixture, not rotting demo data. Guards story_state against the schema + the
temporal gate, and the style_profile against its schema.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from argus_skill.verticals.fiction_writing.state import validate_state
from argus_skill.verticals.fiction_writing.temporal import check_temporal_consistency

_FW = Path(__file__).resolve().parents[1] / "argus_skill" / "verticals" / "fiction_writing"
_EX = _FW / "examples" / "honglou"
_SCHEMAS = _FW / "schemas"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_honglou_story_state_is_schema_valid():
    state = _load(_EX / "story_state.json")
    validate_state(state)  # raises PatchError on schema violation
    # sanity: the canon actually has the principal cast + locations + items
    assert len(state["characters"]) >= 20
    assert state["locations"] and state["items"]


def test_honglou_story_state_is_temporally_consistent():
    state = _load(_EX / "story_state.json")
    findings = check_temporal_consistency(state)
    assert findings == [], f"canon has temporal contradictions: {findings}"


def test_honglou_style_profile_is_schema_valid():
    profile = _load(_EX / "style_profile.json")
    schema = _load(_SCHEMAS / "style_profile.schema.json")
    jsonschema.validate(profile, schema)
