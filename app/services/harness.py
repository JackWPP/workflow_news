"""
harness.py — Agent 操场边界约束

Harness 的设计哲学：
  - 只规定边界，不规定路径
  - Agent 在边界内自由探索
  - 每一次工具调用被记录，每一次拦截被可观测

不是 Rails（严格轨道），而是 Harness（安全操场）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.tools import ToolCall


# ── 默认工具超时（秒） ──────────────────────────────────────
DEFAULT_TOOL_TIMEOUTS: dict[str, float] = {
    "web_search": 45.0,
    "read_page": 25.0,
    "search_images": 20.0,
    "evaluate_article": 25.0,
    "write_section": 45.0,
    "compare_sources": 45.0,
    "verify_image": 15.0,
    "follow_references": 5.0,
    "check_coverage": 5.0,
    "finish": 5.0,
}
DEFAULT_TOOL_TIMEOUT = 30.0


# Default domain keywords — Agent 的内容必须与这些领域相关
DEFAULT_DOMAIN_KEYWORDS: list[str] = [
    "高分子",
    "塑料",
    "橡胶",
    "复合材料",
    "树脂",
    "改性",
    "薄膜",
    "包装",
    "注塑",
    "挤出",
    "吹塑",
    "成型",
    "回收",
    "再生",
    "生物基",
    "降解",
    "polymer",
    "plastic",
    "rubber",
    "composite",
    "resin",
    "recycling",
    "biodegradable",
    "processing",
    "injection molding",
    "extrusion",
    "additive manufacturing",
]

# Default blocked domains (safety/quality boundary)
# 合并了质量低劣来源（PR、百科）和台湾地区媒体
DEFAULT_BLOCKED_DOMAINS: list[str] = [
    # ── PR / 营销类 ──
    "openpr.com",
    "prnewswire.com",
    "prnasia.com",
    "businesswire.com",
    "globenewswire.com",
    "coherentmarketinsights.com",
    "gminsights.com",
    "grandviewresearch.com",
    # ── 百科 / 社区 ──
    "baike.baidu.com",
    "zhuanlan.zhihu.com",
    "bilibili.com",
    # ── 财经 / 投资类（非行业内容）──
    "cn.investing.com",
    "investing.com",
    # ── B2B 电商平台（非新闻来源）──
    "made-in-china.com",
    "alibaba.com",
    "1688.com",
    "globalsources.com",
    "indiamart.com",
    "b2b168.com",
    "jdzj.com",
    "hbsztv.com",
    "stockstar.com",
    "eastmoney.com",
    "10jqka.com.cn",
    "china-packcon.com",
    "china-ipif.com",
    # ── 台湾媒体 ──
    "digitimes.com.tw",
    "udn.com",
    "ltn.com.tw",
    "chinatimes.com",
    "yahoo.com.tw",
    "tw.news.yahoo.com",
    "ctee.com.tw",
    "money.udn.com",
    "technews.tw",
    "bnext.com.tw",
    "ettoday.net",
    "setn.com",
    "storm.mg",
    "cna.com.tw",
    "taiwannews.com.tw",
    # ── 已知不可访问站点（反爬/404/超时）──
    "21cp.com",
    "info.21cp.com",
    "aibang.com",
    "www.aibang.com",
    "polymer.cn",
    "www.polymer.cn",
]


@dataclass
class HarnessViolation:
    """记录一次 Harness 拦截事件。"""

    tool_name: str
    argument_key: str
    argument_value: str
    reason: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "argument_key": self.argument_key,
            "argument_value": self.argument_value,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass
class Harness:
    """
    Agent 的边界约束。

    ✅ 设置资源上限（步骤数 + 超时）
    ✅ 设置领域边界（blocked domains）
    ❌ 不规定每一步做什么（那是 Rails，不是 Harness）
    """

    # ── 资源限制 ──────────────────────────────────────────
    max_steps: int = 50
    """Agent 最多执行多少步（每次工具调用算一步）。"""

    max_duration_seconds: float = 600.0
    """最长运行时间（秒）。超时后结束当前步骤并尝试输出。"""

    tool_timeouts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_TOOL_TIMEOUTS)
    )
    """每个工具的超时时间（秒）。未列出的工具使用 DEFAULT_TOOL_TIMEOUT。"""

    # ── 领域边界 ──────────────────────────────────────────
    domain_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_DOMAIN_KEYWORDS)
    )
    """内容必须与这些关键词之一相关（逐 URL 内容检查在工具层完成）。"""

    blocked_domains: list[str] = field(
        default_factory=lambda: list(DEFAULT_BLOCKED_DOMAINS)
    )
    """禁止访问这些域名（无论 Agent 想不想）。"""

    # ── 质量底线 ──────────────────────────────────────────
    min_sources_for_publish: int = 2
    """发布至少需要多少条不同来源。"""

    # ── Finish 前置条件 ─────────────────────────────────────
    max_consecutive_finish_rejects: int = 1
    """连续拒绝 finish 的最大次数。超过后强制接受。"""

    # ── System Prompt ─────────────────────────────────────
    system_prompt: str = ""
    """Agent 的角色设定和任务说明。由具体 Agent 类填充。"""

    def __post_init__(self) -> None:
        self._violations: list[HarnessViolation] = []
        self._step_count: int = 0
        self._start_time: float = time.time()

    # ── 计数器 API ────────────────────────────────────────
    def record_step(self) -> None:
        self._step_count += 1

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_steps - self._step_count)

    @property
    def effective_budget_remaining(self) -> int:
        """综合考虑步骤预算和剩余时间的有效预算。"""
        step_budget = max(0, self.max_steps - self._step_count)
        if self.max_duration_seconds <= 0:
            return step_budget
        remaining_seconds = self.max_duration_seconds - self.elapsed_seconds
        # 平均每步约 10 秒
        time_based_budget = max(0, int(remaining_seconds / 10))
        return min(step_budget, time_based_budget)

    @property
    def should_wind_down(self) -> bool:
        """资源即将耗尽，应该进入收尾阶段。"""
        if self.effective_budget_remaining < 10:
            return True
        if self.max_duration_seconds > 0:
            remaining = self.max_duration_seconds - self.elapsed_seconds
            if remaining < 120:
                return True
        return False

    @property
    def timed_out(self) -> bool:
        return self.elapsed_seconds >= self.max_duration_seconds

    @property
    def violations(self) -> list[HarnessViolation]:
        return list(self._violations)

    def tool_timeout(self, tool_name: str) -> float:
        """返回指定工具的超时时间（秒）。"""
        return self.tool_timeouts.get(tool_name, DEFAULT_TOOL_TIMEOUT)

    # ── 主检查入口 ────────────────────────────────────────
    def allows(self, tool_call: "ToolCall") -> tuple[bool, str]:
        """
        检查一个工具调用是否在边界内。

        Returns:
            (True, "") if allowed
            (False, reason) if blocked
        """
        # 1. 步数预算
        if self._step_count >= self.max_steps:
            return (
                False,
                f"Budget exhausted: {self._step_count}/{self.max_steps} steps used",
            )

        # 2. 超时
        if self.timed_out:
            return (
                False,
                f"Timeout: {self.elapsed_seconds:.0f}s elapsed (limit {self.max_duration_seconds}s)",
            )

        # 3. Blocked domain 检查
        url_or_query = (
            tool_call.arguments.get("url") or tool_call.arguments.get("query") or ""
        )
        blocked = self._check_blocked_domain(url_or_query)
        if blocked:
            violation = HarnessViolation(
                tool_name=tool_call.tool_name,
                argument_key="url" if "url" in tool_call.arguments else "query",
                argument_value=url_or_query,
                reason=f"Blocked domain: {blocked}",
            )
            self._violations.append(violation)
            return False, f"Blocked domain: {blocked}"

        return True, ""

    def _check_blocked_domain(self, url_or_query: str) -> str | None:
        """返回匹配的 blocked domain，如未命中返回 None。"""
        lowered = url_or_query.lower()
        for domain in self.blocked_domains:
            if domain.lower() in lowered:
                return domain
        return None

    def to_status_dict(self) -> dict[str, Any]:
        """返回当前 harness 状态，用于写入 AgentRun.debug_payload。"""
        return {
            "step_count": self._step_count,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "budget_remaining": self.budget_remaining,
            "violations": [v.to_dict() for v in self._violations],
            "timed_out": self.timed_out,
        }


# ── Preset Harnesses ──────────────────────────────────────


def make_daily_report_harness() -> Harness:
    """日报生成 Agent 的 Harness 配置。"""
    from app.services.daily_report_agent import (
        DAILY_REPORT_SYSTEM_PROMPT,
    )  # lazy import

    return Harness(
        max_steps=50,
        max_duration_seconds=600.0,
        system_prompt=DAILY_REPORT_SYSTEM_PROMPT,
    )


def make_research_harness() -> Harness:
    """研究型 Agent 的 Harness 配置。"""
    from app.services.research_agent import RESEARCH_SYSTEM_PROMPT  # lazy import

    return Harness(
        max_steps=25,
        max_duration_seconds=180.0,
        min_sources_for_publish=2,
        system_prompt=RESEARCH_SYSTEM_PROMPT,
    )
