from __future__ import annotations

from app.services.report_persistence import _markdown_is_substantial


class TestMarkdownPublishGate:
    def test_rejects_fallback_markdown(self):
        assert _markdown_is_substantial("报告生成失败/内容不足。") is False

    def test_rejects_too_short_markdown(self):
        assert _markdown_is_substantial("### 产业动态\n内容太短") is False

    def test_accepts_report_like_markdown_with_source(self):
        markdown = (
            "### 产业动态\n\n"
            "聚合物加工设备企业发布新一代注塑解决方案，显示加工窗口优化继续成为行业重点。"
            "这一方向与实验室关注的成型过程控制和材料性能耦合有关。"
            "报道还提到设备端正在把在线传感、工艺参数闭环和低能耗塑化单元结合起来，"
            "对后续做材料-工艺-性能关联建模有直接参考价值。\n\n"
            "[原文](https://example.com/polymer-processing-news)"
        )
        assert _markdown_is_substantial(markdown) is True
