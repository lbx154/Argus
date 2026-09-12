"""The narrative prompt restates runner receipts plainly and leads with findings."""

from argus_skill.webapi import map_narrative


def _capture_prompt(monkeypatch) -> str:
    captured = {}

    def fake_run(prompt, output_schema, config, **kwargs):
        captured["prompt"] = prompt
        return {
            "cards": {"k": {"title": "t", "summary": "s", "detail": "d", "reader_brief": {
                "why": "A recorded purpose", "concept": None,
                "scope": "Independent review is not recorded", "next": "Next step not recorded",
            }}},
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
    assert map_narrative.PROMPT_VERSION == 12


def test_prompt_speaks_of_any_kind_of_work_not_only_research(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "演示文稿" in prompt
    assert "不把每件事都写成" in prompt
    assert "工作段落" in prompt


def test_prompt_restates_runner_receipts_as_one_plain_sentence(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "回执" in prompt
    assert "环境变量" in prompt
    assert "换了个新会话接着做" in prompt


def test_prompt_leads_with_the_finding_not_the_activity(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "发现X不成立" in prompt
    assert "进行了X的检查" in prompt


def test_prompt_teaches_with_concrete_examples_without_defining_jargon_using_more_jargon(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "不要用新的未解释术语定义这个术语" in prompt
    assert "具体对象、小数字或可跟随的动作" in prompt
    assert "背景教学和示意例子不是本次研究发现" in prompt
