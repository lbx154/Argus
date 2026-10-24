"""Reply language follows the conversation; answers to a paused question speak to the person."""
from argus.core.mission_view import conversation_is_chinese, say, written_language
from argus.core.mission_view._wording import remember_language
from argus.webapi.manager_dispatch_receipt import _dispatch_ack_text


def test_a_bare_choice_carries_no_language():
    assert written_language("2") == ""
    assert written_language("ok") == ""
    assert written_language("好") == "zh"
    assert written_language("Use the second option") == "en"


def test_reply_to_a_number_keeps_the_conversation_language():
    assert conversation_is_chinese("2", "给 moneyfmt 的金额加上发票格式")
    assert not conversation_is_chinese("2", "Add invoice formatting to moneyfmt")
    item = {"title": "给 moneyfmt 的金额和百分比输出加上更适合发票的格式", "status": "pending"}
    ack = _dispatch_ack_text({"item": item}, None, False, operator_text="2")
    assert ack == say("ack_starting", True)


def test_remembered_language_survives_a_bare_number():
    view: dict = {}
    remember_language(view, "帮我研究视频去噪")
    remember_language(view, "2")
    assert view["language"] == "zh"


def test_answer_reply_speaks_to_the_person():
    text = say("answer_continue_with", True, choice="金额 1,234.50；百分比 12.50%")
    assert text.startswith("好的，按你的选择继续：")
    assert "操作员" not in text
