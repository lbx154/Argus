"""A chart request is answered by the chart: images lead the delivery."""
from __future__ import annotations

from argus.life.delivery import asks_for_figure, figures_first, is_image_path
from argus.webapi.manager_dispatch_receipt import _dispatch_ack_text


def test_figure_requests_are_recognised() -> None:
    assert asks_for_figure("按产品分别画线，对比月度走势的折线图")
    assert asks_for_figure("", "Plot monthly sales as a chart")
    assert not asks_for_figure("把 README 改成中文", "")


def test_images_lead_but_tests_never_do() -> None:
    targets = [
        {"path": "sales_tool/monthly_sales.py"},
        {"path": "sales_tool/README.md"},
        {"path": "tests/plots/test.png"},
        {"path": "sales_tool/output/monthly_sales_by_product.png"},
    ]
    ordered = [target["path"] for target in figures_first(targets)]
    assert ordered[0] == "sales_tool/output/monthly_sales_by_product.png"
    assert ordered[-1] == "tests/plots/test.png"
    assert is_image_path("a/b.SVG") and not is_image_path("a/b.py")


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
