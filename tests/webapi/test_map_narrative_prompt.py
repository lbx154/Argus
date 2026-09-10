"""The narrative prompt restates runner receipts plainly and leads with findings."""

from argus_skill.webapi import map_narrative


def _capture_prompt(monkeypatch) -> str:
    captured = {}

    def fake_run(prompt, output_schema, config, **kwargs):
        captured["prompt"] = prompt
        return {
            "cards": {"k": {"title": "t", "summary": "s", "detail": "d"}},
            "relations": [],
        }

    monkeypatch.setattr(map_narrative, "run_map_model", fake_run)
    map_narrative.generate(
        [{"key": "k"}],
        [{"id": "task"}],
        "zh-CN",
        config=None,
        project_root=None,
        global_root=None,
    )
    return captured["prompt"]


def test_prompt_version_bumped_for_readability_rules():
    assert map_narrative.PROMPT_VERSION == 8


def test_prompt_restates_runner_receipts_as_one_plain_sentence(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "回执" in prompt
    assert "环境变量" in prompt
    assert "换了个新会话接着做" in prompt


def test_prompt_leads_with_the_finding_not_the_activity(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "发现X不成立" in prompt
    assert "进行了X的检查" in prompt
