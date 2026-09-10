from argus_skill.core.token_usage import TokenUsage
from argus_skill.core.usage import UsageRecord, build_usage_record

ERROR = "Process exited with code 1 before turn completion.\nNot inside a trusted directory and --skip-git-repo-check was not specified."


def receipt():
    return {
        "type": "agent.io.complete",
        "backend": "codex",
        "call_id": "test-call",
        "run_label": "main",
        "exit_code": 1,
        "turn_failed": True,
        "turn_completed": False,
        "thread_id": None,
        "fatal_error": "Process exited with code 1 before turn completion.",
        "tool_activity_observed": False,
        "agent_message_count": 0,
        "stdout_line_count": 0,
        "json_event_count": 0,
        "command": ["codex", "exec", "--json", "-"],
    }


def record(tmp_path, **kw):
    return build_usage_record(
        call_id="test-call",
        project_root=tmp_path,
        mission_id=None,
        provider="codex",
        model="gpt-5.6-luna",
        run_label="main",
        started_at=1,
        completed_at=2,
        status="error",
        error=ERROR,
        **kw,
    )


def test_proven_local_refusal_releases_only_unobserved_cost(tmp_path):
    r = record(tmp_path, startup_receipt=receipt())
    assert r.pricing_status == "not_billed" and r.cost_usd == 0
    old = r.to_jsonable()
    old.update(pricing_status="partial", cost_usd=None)
    assert UsageRecord.from_jsonable(old, startup_receipt=receipt()).pricing_status == "not_billed"


def test_error_text_alone_does_not_claim_zero_usage(tmp_path):
    assert record(tmp_path).pricing_status != "not_billed"
    r = receipt()
    r["stdout_line_count"] = 1
    assert record(tmp_path, startup_receipt=r).pricing_status != "not_billed"


def test_observed_usage_is_never_erased(tmp_path):
    usage = TokenUsage(
        input_tokens=100, output_tokens=10, input_tokens_present=True, output_tokens_present=True
    )
    r = record(tmp_path, startup_receipt=receipt(), token_usage=usage)
    assert r.pricing_status != "not_billed" and r.cost_usd > 0
