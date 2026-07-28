from __future__ import annotations

from types import SimpleNamespace

from argus_skill.core.runner_errors import (
    is_unrecoverable_resume_error,
    result_has_unrecoverable_resume_state,
)


def test_encrypted_manager_thread_failure_is_unrecoverable() -> None:
    error = (
        '{"error":{"message":"The encrypted content gAAA... could not be '
        'verified. Reason: Encrypted content could not be decrypted or parsed.",'
        '"code":"invalid_request_body"}}'
    )

    assert is_unrecoverable_resume_error(error)
    assert result_has_unrecoverable_resume_state(
        SimpleNamespace(fatal_error=error, stderr_lines=[])
    )


def test_ordinary_provider_failure_does_not_rotate_resume_state() -> None:
    error = "rate limit exceeded; retry later"

    assert not is_unrecoverable_resume_error(error)
    assert not result_has_unrecoverable_resume_state(
        SimpleNamespace(fatal_error=error, stderr_lines=[])
    )
