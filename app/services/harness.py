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


# Default domain keywords — Agent 的内容必须与这些领域相关
DEFAULT_DOMAIN_KEYWORDS: list[str] = [
    "高分子", "塑料", "橡胶", "复合材料", "树脂", "改性",
    "薄膜", "包装", "注塑", "挤出", "吹塑", "成型",
    "回收", "再生", "生物基", "降解",
    "polymer", "plastic", "rubber", "composite", "resin",
    "recycling", "biodegradable", "processing",
    "injection molding", "extrusion", "additive manufacturing",
]

# Default blocked domains (safety/quality boundary)
DEFAULT_BLOCKED_DOMAINS: list[str] = [
    "openpr.com", "bilibili.com", "cn.investing.com",
    "coherentmarketinsights.com", "gminsights.com",
    "grandviewresearch.com", "baike.baidu.com",
    "prnewswire.com", "prnasia.com", "businesswire.com",
    "globenewswire.com", "zhuanlan.zhihu.com",
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

    ✅ 设置资源上限（预算）
    ✅ 设置领域边界（必须相关）
    ✅ 设置安全边界（禁止域名）
    ✅ 设置质量底线（必须有引用）
    ❌ 不规定每一步做什么（那是 Rails，不是 Harness）
    """

    # ── 资源限制 ──────────────────────────────────────────
    max_steps: int = 40
    """Agent 最多执行多少步（每次工具调用算一步）。"""

    max_search_calls: int = 15
    """最多搜索多少次（web_search + search_images 合计）。"""

    max_page_reads: int = 12
    """最多深度阅读多少个页面（read_page + follow_references 合计）。"""

    max_duration_seconds: float = 300.0
    """最长运行时间（秒）。超时后结束当前步骤并尝试输出。"""

    max_llm_calls: int = 25
    """最多 LLM 调用次数（含工具选择和工具内部 LLM 调用）。"""

    # ── 领域边界 ──────────────────────────────────────────
    domain_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAIN_KEYWORDS))
    """内容必须与这些关键词之一相关（逐 URL 内容检查在工具层完成）。"""

    blocked_domains: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_DOMAINS))
    """禁止访问这些域名（无论 Agent 想不想）。"""

    # ── 质量底线 ──────────────────────────────────────────
    min_sources_for_publish: int = 2
    """发布至少需要多少条不同来源。"""

    must_have_citations: bool = True
    """最终输出必须包含引用。"""

    max_single_source_ratio: float = 0.6
    """单一来源在最终报告中占比不能超过 60%。"""

    # ── System Prompt ─────────────────────────────────────
    system_prompt: str = ""
    """Agent 的角色设定和任务说明。由具体 Agent 类填充。"""

    def __post_init__(self) -> None:
        self._violations: list[HarnessViolation] = []
        self._search_count: int = 0
        self._read_count: int = 0
        self._step_count: int = 0
        self._llm_call_count: int = 0
        self._start_time: float = time.time()

    # ── 计数器 API ────────────────────────────────────────
    def record_search(self) -> None:
        self._search_count += 1

    def record_read(self) -> None:
        self._read_count += 1

    def record_step(self) -> None:
        self._step_count += 1

    def record_llm_call(self) -> None:
        self._llm_call_count += 1

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_steps - self._step_count)

    @property
    def timed_out(self) -> bool:
        return self.elapsed_seconds >= self.max_duration_seconds

    @property
    def violations(self) -> list[HarnessViolation]:
        return list(self._violations)

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
            return False, f"Budget exhausted: {self._step_count}/{self.max_steps} steps used"

        # 2. 超时
        if self.timed_out:
            return False, f"Timeout: {self.elapsed_seconds:.0f}s elapsed (limit {self.max_duration_seconds}s)"

        # 3. 搜索配额
        if tool_call.tool_name in {"web_search", "search_images"}:
            if self._search_count >= self.max_search_calls:
                return False, f"Search quota exhausted: {self._search_count}/{self.max_search_calls}"

        # 4. 阅读配额
        if tool_call.tool_name in {"read_page", "follow_references"}:
            if self._read_count >= self.max_page_reads:
                return False, f"Read quota exhausted: {self._read_count}/{self.max_page_reads}"

        # 5. LLM 调用配额
        if tool_call.tool_name in {"evaluate_article", "compare_sources", "write_section"}:
            if self._llm_call_count >= self.max_llm_calls:
                return False, f"LLM call quota exhausted: {self._llm_call_count}/{self.max_llm_calls}"

        # 6. Blocked domain 检查
        url_or_query = (
            tool_call.arguments.get("url")
            or tool_call.arguments.get("query")
            or ""
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
            "search_count": self._search_count,
            "read_count": self._read_count,
            "llm_call_count": self._llm_call_count,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "budget_remaining": self.budget_remaining,
            "violations": [v.to_dict() for v in self._violations],
            "timed_out": self.timed_out,
        }


# ── Preset Harnesses ──────────────────────────────────────


def make_daily_report_harness() -> Harness:
    """日报生成 Agent 的 Harness 配置。"""
    from app.services.daily_report_agent import DAILY_REPORT_SYSTEM_PROMPT  # lazy import
    return Harness(
        max_steps=40,
        max_search_calls=15,
        max_page_reads=12,
        max_duration_seconds=300.0,
        max_llm_calls=20,
        system_prompt=DAILY_REPORT_SYSTEM_PROMPT,
    )


def make_research_harness() -> Harness:
    """研究型 Agent 的 Harness 配置。"""
    from app.services.research_agent import RESEARCH_SYSTEM_PROMPT  # lazy import
    return Harness(
        max_steps=25,
        max_search_calls=10,
        max_page_reads=8,
        max_duration_seconds=180.0,
        max_llm_calls=15,
        min_sources_for_publish=2,
        must_have_citations=True,
        system_prompt=RESEARCH_SYSTEM_PROMPT,
    )
