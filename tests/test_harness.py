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
        for _ in range(91):
            h.record_step()
        assert h.should_wind_down is True

    def test_should_wind_down_low_time(self):
        h = Harness(max_steps=100, max_duration_seconds=0.5)
        time.sleep(0.4)
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
        assert budget > 0
        assert budget <= 50

    def test_effective_budget_capped_by_time(self):
        h = Harness(max_steps=100, max_duration_seconds=15)
        budget = h.effective_budget_remaining
        assert budget <= 2


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
