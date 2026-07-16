"""Register-level anti-AI tells (the layer that survives AIGC 查重 but reads as AI).

Each new zh/en structural cliché class must fire on a canonical example, stay a
NON-blocking model-seed note, and stay quiet on clean prose. These are the '细粒度'
tells the user flagged: '过了查重还是 AI 味'.
"""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.style_lint import check_style


def _classes(findings):
    return {f["cliche_class"] for f in findings}


def test_new_zh_register_tells_each_fire():
    assert "情绪涌动模板" in _classes(check_style("他心中涌起一股暖流。", {}, "zh"))
    assert "情绪涌动模板" in _classes(check_style("她心底泛起一丝酸楚。", {}, "zh"))
    assert "凝固时刻" in _classes(check_style("空气仿佛凝固了。", {}, "zh"))
    assert "时刻拔高" in _classes(check_style("就在这一刻，他明白了。", {}, "zh"))
    assert "虚化感受" in _classes(check_style("那是一种说不出的滋味。", {}, "zh"))
    assert "副词堆砌" in _classes(
        check_style("他无声地站着，静静地看着，缓缓地转身。", {}, "zh"))


def test_new_en_register_tells_each_fire():
    assert "frozen_moment" in _classes(
        check_style("The air seemed to freeze around them.", {}, "en"))
    assert "vague_feeling" in _classes(
        check_style("She felt an odd sense of unease.", {}, "en"))
    assert "vague_feeling" in _classes(
        check_style("He couldn't quite place the feeling.", {}, "en"))


def test_register_tells_are_nonblocking_model_seed():
    findings = check_style("他心中涌起一股暖流，就在这一刻，空气仿佛凝固了。", {}, "zh")
    assert findings
    assert all(not f["blocking"] for f in findings)
    assert all(f["type"] == "ai_tell" for f in findings)
    assert all(f["calibration"] == "model-seed (BCC-pending)" for f in findings)


def test_clean_prose_stays_quiet_under_new_tells():
    clean = "他推开门，屋里没有开灯。桌上放着一只碗，碗底还剩半口凉茶。"
    assert check_style(clean, {}, "zh") == []
    clean_en = "He pushed the door open. A single bowl sat on the table, half-empty."
    assert check_style(clean_en, {}, "en") == []
