"""Input/output contract checks, not a claim of generated teaching quality."""


import pytest

from argus.webapi.reader_foundation_prompt import (
    foundation_request,
)


def test_task_or_event_payloads_are_not_accepted_as_foundation_inputs():
    with pytest.raises(TypeError):
        foundation_request("Explain the original question", "en-US", task={"id": "later-task"})
    with pytest.raises(TypeError):
        foundation_request("Explain the original question", "en-US", events=[{"text": "A proof trace"}])
