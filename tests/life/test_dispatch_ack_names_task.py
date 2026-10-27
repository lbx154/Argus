"""The dispatch acknowledgement names the task the Manager made."""
from __future__ import annotations

from argus.webapi.manager_dispatch_receipt import _dispatch_ack_text


def test_generic_ack_names_the_task_the_manager_made() -> None:
    item = {"id": "t1", "title": "按产品对比月度销售走势", "status": "pending"}
    text = _dispatch_ack_text(
        {"item": item}, {"rc": 0}, True,
        operator_text="把这张卡做成任务：想加一个功能：按产品分别画线，对比各产品的月度走势",
    )
    assert text == "收到，马上开始：按产品对比月度销售走势。"
    # A title that only repeats the request is not echoed back.
    same = _dispatch_ack_text({"item": item}, {"rc": 0}, True, operator_text="按产品对比月度销售走势")
    assert same == "收到，马上开始。"
