"""HO-4: a Reviewer that writes its verdict but never submits it is asked again.

A reply with no ``submit_review`` call is a missing submission, not a
model-service fault: the retry tells the Reviewer plainly that it did not submit
a verdict and how to, and does not count toward the backend failure streak.
"""
from __future__ import annotations

from dataclasses import dataclass

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig

_NOTE = "You did not submit a verdict"


def _run(backend: MemoryBackend, tmp_path, *, events: list[dict] | None = None, **config):
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    return engine.run(
        objective="add a char_count helper",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(backend_failure_backoff_seconds=0.0, **config),
        workdir=tmp_path,
        on_event=(events if events is not None else []).append,
    )


@dataclass
class _RefusedCall(CannedResponse):
    """A turn whose submit_review call the host refuses; the agent sees the error and stops."""

    def render(self, prompt, options):
        try:
            return super().render(prompt, options)
        except ValueError:
            return CannedResponse(message=self.message).render(prompt, options)


def _reviewer_prompts(backend: MemoryBackend) -> list[str]:
    return [prompt for label, prompt, _ in backend.history if label == "reviewer"]


def test_a_review_written_but_not_submitted_is_asked_for_again(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="char_count is implemented"))
    backend.queue(
        "reviewer",
        CannedResponse(message="char_count works and its tests pass.\n→ done"),
        CannedResponse(review_action=("submit_review", {
            "review": "char_count is delivered; its tests were run.\n→ done",
        })),
    )
    events: list[dict] = []

    # The last round and a threshold of one: a backend failure here ends the task.
    status, rounds, _message, _reason, _thread = _run(
        backend, tmp_path, events=events, max_rounds=1, backend_failure_threshold=1,
    )

    assert status == "done"
    assert len(rounds) == 1 and rounds[0].review.status == "done"
    first, second = _reviewer_prompts(backend)
    assert _NOTE not in first
    assert _NOTE in second
    assert "submit_review" in second and "→ done" in second
    # Not a model-service failure: no operator alert, no Engineer rerun.
    assert not [e for e in events if e.get("type") == "round.reviewer_backend_failure"]
    assert [label for label, _, _ in backend.history].count("engineer-r1") == 1


def test_a_refused_submission_is_named_on_the_retry(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="char_count is implemented"))
    backend.queue(
        "reviewer",
        # The handoff line is missing, so the call is refused and nothing is recorded.
        _RefusedCall(
            message="Submitted.",
            review_action=("submit_review", {"review": "char_count is delivered."}),
        ),
        CannedResponse(review_action=("submit_review", {
            "review": "char_count is delivered.\n→ done",
        })),
    )

    status, _rounds, _message, _reason, _thread = _run(backend, tmp_path, max_rounds=1)

    assert status == "done"
    second = _reviewer_prompts(backend)[1]
    assert _NOTE in second
    assert "Your last submit_review call was refused: End the review with one line" in second


def test_a_reviewer_that_never_submits_stops_without_blaming_the_model_service(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="char_count is implemented"))
    backend.queue(
        "reviewer",
        CannedResponse(message="Looks good.\n→ done"),
        CannedResponse(message="Looks good, really.\n→ done"),
    )

    status, rounds, _message, reason, _thread = _run(
        backend, tmp_path, max_rounds=3, backend_failure_threshold=2,
    )

    assert status == "error"
    assert len(_reviewer_prompts(backend)) == 2
    assert reason.startswith("The Reviewer wrote its review 2 times without submitting a verdict")
    assert rounds[-1].stop_kind is None
    assert rounds[-1].review.review_source == "reviewer_unavailable"


def test_an_empty_reviewer_turn_is_still_a_backend_failure(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="char_count is implemented"))
    backend.queue("reviewer", CannedResponse(message=""))

    status, rounds, _message, _reason, _thread = _run(
        backend, tmp_path, max_rounds=1, backend_failure_threshold=1,
    )

    assert status == "error"
    assert rounds[-1].stop_kind == "backend_unavailable"
    assert len(_reviewer_prompts(backend)) == 1
