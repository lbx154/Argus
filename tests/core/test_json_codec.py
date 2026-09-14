from __future__ import annotations

import pytest

from argus.core.event_catalog import validate_event_envelope
from argus.core.json_codec import loads_finite_json


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_nonfinite_json_is_rejected_in_nested_fields(token):
    with pytest.raises(ValueError, match="non-finite JSON number"):
        loads_finite_json('{"extension": {"values": [' + token + ']}}')


def test_finite_numbers_and_literal_number_names_are_preserved():
    assert loads_finite_json(b'{"text":"NaN Infinity", "values":[1,1.5,1e20,null,true]}') == {
        "text": "NaN Infinity", "values": [1, 1.5, 1e20, None, True],
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10 ** 400])
def test_event_producer_rejects_nonfinite_timestamp_and_payload_numbers(value):
    event = {"type": "round.review.completed", "status": "continue", "round_index": 1, "ts": value}
    assert "ts must be finite" in validate_event_envelope(event).errors
    assert not validate_event_envelope({**event, "ts": 1, "round_index": value}).valid
