from __future__ import annotations

import json

import pytest

from argus.webapi import map_model, map_narrative

BRIEF = {"why": "Why it matters.", "concept": None, "scope": "What was done.", "next": "What follows."}


def test_a_well_formed_brief_is_left_alone():
    assert map_narrative._normalize_reader_brief_shape(dict(BRIEF)) == BRIEF


def test_next_nested_inside_scope_is_moved_beside_it():
    nested = {"why": BRIEF["why"], "concept": None, "scope": {"scope": BRIEF["scope"], "next": BRIEF["next"]}}
    assert map_narrative._normalize_reader_brief_shape(nested) == BRIEF


def test_a_section_written_as_named_parts_is_joined_in_the_models_own_order():
    parts = {**BRIEF, "scope": {"name": "Scope", "restriction": "Only RULER.", "open_issues": "Seeds differ."}}
    flat = map_narrative._normalize_reader_brief_shape(parts)
    assert flat["scope"] == "Scope\n\nOnly RULER.\n\nSeeds differ."
    assert map_narrative._reader_brief(flat)["scope"] == flat["scope"]


@pytest.mark.parametrize("scope", [{}, {"a": "text", "b": 3}, {"a": "text", "b": " "}, ["text"]])
def test_anything_else_still_fails_as_an_invalid_brief(scope):
    with pytest.raises(ValueError):
        map_narrative._reader_brief(map_narrative._normalize_reader_brief_shape({**BRIEF, "scope": scope}))


def test_the_shape_is_put_right_before_the_schema_sees_it():
    output_schema = map_narrative.schema(["t0"], ["t0"])
    card = {"title": "T", "summary": "S", "detail": "D",
            "reader_brief": {**BRIEF, "scope": {"name": "Scope", "restriction": "Only RULER."}}}
    raw = json.dumps({"cards": {"t0": card}, "relations": []})
    with pytest.raises(ValueError):
        map_model._parse_document(raw, output_schema)
    value = map_model._parse_document(raw, output_schema, map_narrative._prepare_cards)
    assert value["cards"]["t0"]["reader_brief"]["scope"] == "Scope\n\nOnly RULER."
    # The schema's limits still hold for the joined text.
    long = {**card, "reader_brief": {**BRIEF, "scope": {"a": "x" * 400, "b": "y" * 400}}}
    with pytest.raises(ValueError):
        map_model._parse_document(json.dumps({"cards": {"t0": long}, "relations": []}), output_schema,
                                  map_narrative._prepare_cards)
