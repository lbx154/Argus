"""Tests for the Signal-B recurring-infra-failure detector
(``argus_skill.life.failure_signature_detector``).

Pure detector: given mission output text, return the bounded set of
infrastructure failure-class signatures, with generic wrapper symptoms
suppressed when a specific root-cause signature co-occurs.
"""
from __future__ import annotations

from argus_skill.life.failure_signature_detector import (
    FailureSignature,
    scan_failure_signatures,
)


def _sigs(text: str) -> set[str]:
    return {s.signature for s in scan_failure_signatures(agent_messages=[text])}


# ---------------------------------------------------------------------------
# Each specific failure class is detected
# ---------------------------------------------------------------------------


def test_detects_cuda_oom_torch_error() -> None:
    text = (
        "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate "
        "340.00 MiB. GPU 0 has a total capacity of 79.25 GiB"
    )
    assert "cuda_oom" in _sigs(text)


def test_detects_cuda_oom_plain_phrase() -> None:
    assert "cuda_oom" in _sigs("RuntimeError: CUDA out of memory")


def test_detects_device_side_assert() -> None:
    assert "cuda_device_assert" in _sigs(
        "RuntimeError: CUDA error: device-side assert triggered"
    )


def test_detects_nccl_error() -> None:
    assert "nccl_dist_error" in _sigs("ProcessGroupNCCL: NCCL error in: ...")


def test_detects_dist_timeout() -> None:
    assert "nccl_dist_error" in _sigs(
        "torch.distributed collective operation timed out after 1800000ms"
    )


def test_detects_flash_attn_unavailable() -> None:
    assert "hf_flash_attn_unavailable" in _sigs(
        "ImportError: FlashAttention2 has been toggled on, but it cannot be "
        "used due to the following error: the package ... doesn't seem to be "
        "installed."
    )


# ---------------------------------------------------------------------------
# Generic wrappers: detected alone, suppressed when a specific sig co-occurs
# ---------------------------------------------------------------------------


def test_vllm_engine_init_detected_alone() -> None:
    assert _sigs("RuntimeError: Engine core initialization failed.") == {
        "vllm_engine_init_failed"
    }


def test_ray_worker_died_detected_alone() -> None:
    assert _sigs("ray.exceptions.RayActorError: The actor died unexpectedly") == {
        "ray_worker_died"
    }


def test_generic_wrapper_suppressed_when_specific_present() -> None:
    # OOM is the root cause; vLLM engine-init + ray-actor-died are wrappers.
    text = (
        "torch.OutOfMemoryError: CUDA out of memory.\n"
        "RuntimeError: Engine core initialization failed.\n"
        "ray.exceptions.RayActorError: The actor died unexpectedly\n"
    )
    sigs = _sigs(text)
    assert sigs == {"cuda_oom"}, sigs


# ---------------------------------------------------------------------------
# Dedup + no false positives
# ---------------------------------------------------------------------------


def test_same_signature_deduped_within_mission() -> None:
    text = "CUDA out of memory\n... CUDA out of memory again\n"
    sigs = scan_failure_signatures(agent_messages=[text])
    assert [s.signature for s in sigs] == ["cuda_oom"]


def test_no_signal_on_success_text() -> None:
    assert _sigs("All 247 tests passed. Training step 1 reward=0.48") == set()


def test_empty_inputs_yield_nothing() -> None:
    assert scan_failure_signatures() == []


# ---------------------------------------------------------------------------
# Reads all the mission sources (events, check tails, fatal_error)
# ---------------------------------------------------------------------------


def test_reads_events_output_excerpt() -> None:
    events = [{"type": "x", "output_excerpt": "torch.OutOfMemoryError: CUDA out of memory"}]
    sigs = {s.signature for s in scan_failure_signatures(events=events)}
    assert "cuda_oom" in sigs


def test_reads_fatal_error_and_check_tails() -> None:
    a = {s.signature for s in scan_failure_signatures(fatal_error="CUDA out of memory")}
    b = {
        s.signature
        for s in scan_failure_signatures(
            check_output_tails=["device-side assert triggered"]
        )
    }
    assert "cuda_oom" in a and "cuda_device_assert" in b


def test_signature_carries_category_and_evidence() -> None:
    sigs = scan_failure_signatures(agent_messages=["CUDA out of memory"])
    assert isinstance(sigs[0], FailureSignature)
    assert sigs[0].category == "gpu_memory"
    assert sigs[0].evidence  # non-empty
