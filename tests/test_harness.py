from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.services.harness import (
    DEFAULT_BLOCKED_DOMAINS,
    DEFAULT_TOOL_TIMEOUT,
    DEFAULT_TOOL_TIMEOUTS,
    Harness,
    HarnessViolation,
)


def _make_tool_call(tool_name: str = "web_search", url: str = "", query: str = "") -> MagicMock:
    tc = MagicMock()
    tc.tool_name = tool_name
    tc.arguments = {}
    if url:
        tc.arguments["url"] = url
    if query:
        tc.arguments["query"] = query
    return tc


class TestHarnessMaxSteps:
    def test_max_steps_limit(self):
        h = Harness(max_steps=3)
        for _ in range(3):
            h.record_step()
        allowed, reason = h.allows(_make_tool_call())
        assert allowed is False
        assert "Budget exhausted" in reason

    def test_under_limit_allows(self):
        h = Harness(max_steps=10)
        h.record_step()
        allowed, _ = h.allows(_make_tool_call())
        assert allowed is True

    def test_budget_remaining(self):
        h = Harness(max_steps=5)
        assert h.budget_remaining == 5
        h.record_step()
        h.record_step()
        assert h.budget_remaining == 3


class TestHarnessMaxDuration:
    def test_max_duration_limit(self):
        h = Harness(max_duration_seconds=0.1)
        time.sleep(0.15)
        allowed, reason = h.allows(_make_tool_call())
        assert allowed is False
        assert "Timeout" in reason

    def test_timed_out_property(self):
        h = Harness(max_duration_seconds=0.05)
        time.sleep(0.1)
        assert h.timed_out is True


class TestHarnessWindDown:
    def test_should_wind_down_low_budget(self):
        h = Harness(max_steps=100, max_duration_seconds=3600)
        # 阈值是 < 5 步，留 4 步即触发
        for _ in range(96):
            h.record_step()
        assert h.should_wind_down is True

    def test_should_wind_down_low_time(self):
        h = Harness(max_steps=100, max_duration_seconds=0.5)
        time.sleep(0.45)
        # 剩余 < 90s 时进入收尾。max_duration=0.5s 时几乎一开始就 < 90，
        # 但只有 elapsed > 0 接近 max_duration 时才确认 timed_out 之外这条路径有效。
        assert h.should_wind_down is True

    def test_no_wind_down_when_comfortable(self):
        h = Harness(max_steps=100, max_duration_seconds=3600)
        h.record_step()
        assert h.should_wind_down is False


class TestHarnessBlockedDomain:
    def test_blocked_domain_in_url(self):
        h = Harness()
        tc = _make_tool_call(url="https://prnewswire.com/release/123")
        allowed, reason = h.allows(tc)
        assert allowed is False
        assert "Blocked domain" in reason

    def test_blocked_domain_in_query(self):
        h = Harness()
        tc = _make_tool_call(query="site:prnewswire.com polymer")
        allowed, reason = h.allows(tc)
        assert allowed is False
        assert "Blocked domain" in reason

    def test_allowed_domain(self):
        h = Harness()
        tc = _make_tool_call(url="https://nature.com/articles/123")
        allowed, _ = h.allows(tc)
        assert allowed is True

    def test_custom_blocked_domains(self):
        h = Harness(blocked_domains=["bad-site.com"])
        tc = _make_tool_call(url="https://bad-site.com/article")
        allowed, _ = h.allows(tc)
        assert allowed is False


class TestHarnessToolTimeout:
    def test_known_tool_timeout(self):
        h = Harness()
        assert h.tool_timeout("web_search") == DEFAULT_TOOL_TIMEOUTS["web_search"]
        assert h.tool_timeout("read_page") == DEFAULT_TOOL_TIMEOUTS["read_page"]

    def test_unknown_tool_default_timeout(self):
        h = Harness()
        assert h.tool_timeout("unknown_tool") == DEFAULT_TOOL_TIMEOUT


class TestHarnessEffectiveBudget:
    def test_effective_budget_remaining(self):
        h = Harness(max_steps=50, max_duration_seconds=3600)
        budget = h.effective_budget_remaining
        assert budget == 50

    def test_effective_budget_only_step_based(self):
        """新策略：effective_budget_remaining 只反映剩余步骤，不被时间预算
        提前压低（旧实现的 'avg 10s/step' 折算曾导致 budget_exhausted 假阳性）。"""
        h = Harness(max_steps=50, max_duration_seconds=15)
        # 即使 max_duration 很小，只要还没真的 timed_out，剩余步骤就是 50。
        assert h.effective_budget_remaining == 50

    def test_effective_budget_decrements_with_steps(self):
        h = Harness(max_steps=10, max_duration_seconds=3600)
        assert h.effective_budget_remaining == 10
        h.record_step()
        h.record_step()
        assert h.effective_budget_remaining == 8

    def test_time_budget_remaining_property(self):
        h = Harness(max_steps=50, max_duration_seconds=3600)
        assert h.time_budget_remaining > 3590


class TestHarnessViolation:
    def test_violation_recorded(self):
        h = Harness(blocked_domains=["bad.com"])
        tc = _make_tool_call(url="https://bad.com/article")
        h.allows(tc)
        assert len(h.violations) == 1
        assert h.violations[0].tool_name == "web_search"

    def test_violation_to_dict(self):
        v = HarnessViolation(
            tool_name="web_search",
            argument_key="url",
            argument_value="https://bad.com",
            reason="Blocked domain: bad.com",
        )
        d = v.to_dict()
        assert d["tool_name"] == "web_search"
        assert "timestamp" in d


class TestHarnessStatus:
    def test_to_status_dict(self):
        h = Harness(max_steps=10)
        h.record_step()
        status = h.to_status_dict()
        assert status["step_count"] == 1
        assert status["budget_remaining"] == 9
        assert isinstance(status["violations"], list)

    def test_record_step_dual_counter(self):
        """record_step(tool_calls_in_step=N) 同时累计 LLM 轮数和 tool_call 数。"""
        h = Harness(max_steps=10)
        h.record_step(tool_calls_in_step=1)
        h.record_step(tool_calls_in_step=5)
        h.record_step(tool_calls_in_step=0)  # 纯文本回复，无工具
        assert h._step_count == 3   # 3 轮 LLM 决策
        assert h._tool_call_count == 6  # 1+5+0
        status = h.to_status_dict()
        assert status["step_count"] == 3
        assert status["tool_call_count"] == 6
