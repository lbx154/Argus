from __future__ import annotations

import pytest

from argus.core.event_catalog import validate_event_envelope


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10 ** 400])
def test_event_producer_rejects_nonfinite_timestamp_and_payload_numbers(value):
    event = {"type": "round.review.completed", "status": "continue", "round_index": 1, "ts": value}
    assert "ts must be finite" in validate_event_envelope(event).errors
    assert not validate_event_envelope({**event, "ts": 1, "round_index": value}).valid
