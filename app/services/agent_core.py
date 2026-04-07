"""
agent_core.py — Agent Loop 引擎

这是整个 Agent 系统的核心循环。每一次运行流程：
  1. 构建 system message + 任务 message
  2. 调用 LLM（带工具定义），让 LLM 自由选择下一步
  3. LLM 选择工具 → 检查 Harness → 执行工具 → 观察结果
  4. 更新 WorkingMemory + message history
  5. 持久化 AgentStep 到数据库
  6. 重复，直到 LLM 调用 finish 或 budget/timeout 耗尽

与旧 pipeline.py 的根本区别：
  - 旧 pipeline：每一步做什么由代码决定（确定性）
  - Agent Loop：每一步做什么由 LLM 决定（自主性）
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.harness import Harness
from app.services.llm_client import LLMClient, LLMResponse, ToolCallRequest
from app.services.tools import FinishTool, Tool, ToolCall, ToolResult
from app.services.working_memory import StepRecord, WorkingMemory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """一次 Agent 运行的最终结果。"""
    success: bool
    title: str
    summary: str
    articles: list[dict[str, Any]] = field(default_factory=list)
    sections_content: dict[str, str] = field(default_factory=dict)
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    harness_status: dict[str, Any] = field(default_factory=dict)
    finished_reason: str = "unknown"   # "complete" | "budget_exhausted" | "timeout" | "error" | "finish_tool"
    step_count: int = 0
    total_tokens: int = 0

    @property
    def is_publishable(self) -> bool:
        return self.success and len(self.articles) >= 1

    def to_debug_payload(self) -> dict[str, Any]:
        return {
            "finished_reason": self.finished_reason,
            "step_count": self.step_count,
            "total_tokens": self.total_tokens,
            "article_count": len(self.articles),
            "sections": list(self.sections_content.keys()),
            "harness": self.harness_status,
            "memory": self.memory_snapshot,
        }


class AgentCore:
    """
    Tool-use Agent Loop 引擎。

    这不是一个确定性的流水线，而是一个让 LLM 自主决策的循环。
    LLM 在每一步自由选择调用哪个工具，直到调用 finish 或 budget 耗尽。
    """

    def __init__(
        self,
        tools: list[Tool],
        llm_client: LLMClient,
        harness: Harness,
    ) -> None:
        self.tools: dict[str, Tool] = {t.name: t for t in tools}
        self.llm = llm_client
        self.harness = harness
        self._tool_definitions = [t.to_openai_schema() for t in tools]

    async def run(
        self,
        task: str,
        session: Any | None = None,
        agent_run_id: int | None = None,
    ) -> AgentResult:
        """
        启动 Agent 运行。

        Args:
            task: 给 Agent 的任务描述（自然语言）
            session: 数据库会话（用于持久化 AgentStep，可选）
            agent_run_id: AgentRun 的数据库 ID（用于关联 AgentStep）

        Returns:
            AgentResult: 最终结果
        """
        memory = WorkingMemory()
        total_tokens = 0
        step_index = 0

        # 构建初始 message history
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_message()},
            {"role": "user", "content": task},
        ]

        logger.info("[AgentCore] Starting agent run. Task: %s", task[:100])

        while self.harness.budget_remaining > 0 and not self.harness.timed_out:
            step_index += 1
            step_start = time.time()

            # === Step 1: LLM 决策 ===
            self.harness.record_llm_call()
            llm_response = await self._get_llm_decision(messages, memory)
            total_tokens += llm_response.tokens_used

            # 记录 LLM 的思考
            if llm_response.thought:
                memory.record_thought(llm_response.thought)
                logger.debug("[AgentCore] Step %d thought: %s", step_index, llm_response.thought[:200])

            # === Step 2: 检查是否结束 ===
            if llm_response.is_finish or not llm_response.has_tool_calls:
                # LLM 决定结束，或没有工具调用（纯文本回复）
                try:
                    finish_result = self._extract_finish_result(llm_response, memory)
                except StopIteration as e:
                    logger.info("[AgentCore] Intercepted premature finish: %s", e)
                    messages.append({
                        "role": "user",
                        "content": f"系统提示：你试图调用 finish，但是被拒绝。原因：{str(e)} 请继续使用 web_search 探索新方向并使用 evaluate_article 评估文章。"
                    })
                    continue

                if finish_result:
                    logger.info("[AgentCore] Agent finished via finish tool at step %d", step_index)
                    return self._build_result(
                        memory, finish_result, "finish_tool", step_index, total_tokens
                    )
                # 纯文本回复（没有工具调用），继续循环但记录
                messages.append({"role": "assistant", "content": llm_response.content})
                logger.debug("[AgentCore] LLM gave text response without tool calls at step %d", step_index)
                # 如果连续没有工具调用，给出提示
                if step_index > 3:
                    messages.append({
                        "role": "user",
                        "content": "请记得使用可用的工具继续探索，或者调用 finish 完成报告。"
                    })
                continue

            # === Step 3: 执行工具调用 ===
            tool_result_messages: list[dict[str, Any]] = []

            # 把 LLM 的 assistant 消息加入历史（包含 tool_calls）
            assistant_message = self._build_assistant_message(llm_response)
            messages.append(assistant_message)

            for tool_call_req in llm_response.tool_calls:
                self.harness.record_step()
                tool_call = ToolCall(
                    tool_name=tool_call_req.tool_name,
                    arguments=tool_call_req.arguments,
                )

                # Harness 检查
                allowed, deny_reason = self.harness.allows(tool_call)
                if not allowed:
                    logger.info("[AgentCore] Harness blocked %s: %s", tool_call.tool_name, deny_reason)
                    tool_result = ToolResult(
                        success=False,
                        summary=f"[Harness 拦截] {deny_reason}",
                        data={"harness_blocked": True, "reason": deny_reason},
                    )
                    step_record = StepRecord(
                        step_index=step_index,
                        tool_name=tool_call.tool_name,
                        arguments=tool_call.arguments,
                        result_summary=f"Harness blocked: {deny_reason}",
                        duration_seconds=0.0,
                        harness_blocked=True,
                        block_reason=deny_reason,
                    )
                else:
                    # 执行工具
                    tool = self.tools.get(tool_call.tool_name)
                    if tool is None:
                        tool_result = ToolResult(
                            success=False,
                            summary=f"未知工具: {tool_call.tool_name}",
                            data={},
                        )
                    else:
                        # 更新计数器（搜索/阅读分类计数）
                        if tool_call.tool_name in {"web_search", "search_images"}:
                            self.harness.record_search()
                        elif tool_call.tool_name in {"read_page", "follow_references"}:
                            self.harness.record_read()

                        try:
                            tool_result = await tool.execute(memory=memory, **tool_call.arguments)
                        except Exception as exc:
                            logger.error("[AgentCore] Tool %s raised: %s", tool_call.tool_name, exc, exc_info=True)
                            tool_result = ToolResult(
                                success=False,
                                summary=f"工具执行异常: {exc}",
                                data={"error": str(exc)},
                            )

                    step_duration = time.time() - step_start
                    step_record = StepRecord(
                        step_index=step_index,
                        tool_name=tool_call.tool_name,
                        arguments=tool_call.arguments,
                        result_summary=tool_result.summary[:500],
                        duration_seconds=step_duration,
                        harness_blocked=False,
                    )

                memory.record_step(step_record)

                # 持久化 AgentStep
                if session is not None and agent_run_id is not None:
                    self._persist_step(session, agent_run_id, step_record, llm_response.thought)

                # 在 memory context summary 后面附上工具结果
                context_update = f"\n\n[当前状态]\n{memory.to_context_summary()}"
                result_content = tool_result.to_message()
                if tool_call.tool_name == "check_coverage":
                    result_content = f"{result_content}{context_update}"

                tool_result_messages.append(
                    self.llm.build_tool_result_message(
                        tool_call_id=tool_call_req.call_id,
                        result_content=result_content,
                    )
                )

                # 检查 finish 工具
                if tool_call.tool_name == "finish" and tool_result.success:
                    logger.info("[AgentCore] finish tool executed at step %d", step_index)
                    return self._build_result(
                        memory, tool_result.data, "finish_tool", step_index, total_tokens
                    )

                logger.debug("[AgentCore] Step %d [%s]: %s", step_index, tool_call.tool_name, tool_result.summary[:100])

            messages.extend(tool_result_messages)

        # === Budget 耗尽或超时 ===
        finished_reason = "timeout" if self.harness.timed_out else "budget_exhausted"
        logger.info("[AgentCore] Agent stopped: %s at step %d", finished_reason, step_index)

        # 尝试用当前 memory 生成兜底报告
        return self._build_fallback_result(memory, finished_reason, step_index, total_tokens)

    def _build_system_message(self) -> str:
        """构建包含工具说明和工作记忆提示的 system message。"""
        tool_names = ", ".join(self.tools.keys())
        harness_info = (
            f"资源限制: 最多 {self.harness.max_steps} 步, "
            f"{self.harness.max_search_calls} 次搜索, "
            f"{self.harness.max_page_reads} 次阅读。"
        )
        base_prompt = self.harness.system_prompt or "你是一个专业的新闻研究员助手。"
        return f"{base_prompt}\n\n可用工具: {tool_names}\n{harness_info}"

    async def _get_llm_decision(
        self, messages: list[dict[str, Any]], memory: WorkingMemory
    ) -> LLMResponse:
        """调用 LLM 获取下一步决策。"""
        # 每隔 5 步注入一次工作记忆摘要，帮助 LLM 了解当前状态
        if len(memory.step_history) > 0 and len(memory.step_history) % 5 == 0:
            memory_msg = {
                "role": "user",
                "content": f"[工作记忆更新]\n{memory.to_context_summary()}",
            }
            messages_with_memory = messages + [memory_msg]
        else:
            messages_with_memory = messages

        return await self.llm.chat_with_tools(
            messages=messages_with_memory,
            tool_definitions=self._tool_definitions,
            temperature=0.3,
        )

    def _build_assistant_message(self, llm_response: LLMResponse) -> dict[str, Any]:
        """构建 assistant 消息（包含 tool_calls）。"""
        msg: dict[str, Any] = {"role": "assistant"}
        msg["content"] = llm_response.content or ""
        
        # 兼容 kimi-k2.5 等 reasoning models 的历史上下文要求
        if llm_response.reasoning_content or llm_response.model_used.startswith("kimi-") or llm_response.model_used.startswith("moonshot-"):
            msg["reasoning_content"] = llm_response.reasoning_content or ""

        if llm_response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.call_id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.tool_name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for i, tc in enumerate(llm_response.tool_calls)
            ]
        return msg

    def _extract_finish_result(
        self, llm_response: LLMResponse, memory: WorkingMemory
    ) -> dict[str, Any] | None:
        """从 LLM 的 finish 工具调用中提取结果。如果搜的不够多，拒绝 finish。"""
        for tc in llm_response.tool_calls:
            if tc.tool_name == "finish":
                # 检查是否过早结束（仅在有 harness 配置阈值时检查）
                min_searches = getattr(self.harness, "min_searches_before_finish", 6)
                min_articles = getattr(self.harness, "min_articles_before_finish", 4)
                if min_searches > 0 and len(memory.searched_queries) < min_searches:
                    raise StopIteration(f"搜索次数不足（{len(memory.searched_queries)}/{min_searches}），请继续搜索更多方向。")
                if min_articles > 0 and len(memory.publishable_articles()) < min_articles:
                    raise StopIteration(f"可发布文章不足（{len(memory.publishable_articles())}/{min_articles}），请继续阅读和评估文章。")
                return tc.arguments
        return None

    def _enrich_articles_with_images(self, memory: WorkingMemory) -> None:
        """将 memory 中已验证的图片写入对应 ArticleSummary 对象。"""
        for article in memory.discovered_articles:
            if article.worth_publishing and not article.image_url:
                best = memory.best_image_for_article(article.url)
                if best:
                    article.image_url = best.image_url
                    article.has_image = best.verified

    def _build_result(
        self,
        memory: WorkingMemory,
        finish_data: dict[str, Any],
        finished_reason: str,
        step_count: int,
        total_tokens: int,
    ) -> AgentResult:
        """从 finish 工具的数据和 memory 构建最终结果。"""
        self._enrich_articles_with_images(memory)
        articles = [a.to_dict() for a in memory.publishable_articles()]

        # 板块内容：优先使用 memory 缓存（write_section 写的），
        # 再用 finish 参数覆盖/补充（如果 LLM 也传了的话）
        sections_from_memory = memory.get_all_sections_content()
        sections_from_finish = finish_data.get("sections_content", {})
        merged_sections: dict[str, str] = {**sections_from_memory, **sections_from_finish}

        return AgentResult(
            success=True,
            title=finish_data.get("title", "高分子加工全视界日报"),
            summary=finish_data.get("summary", ""),
            articles=articles,
            sections_content=merged_sections,
            memory_snapshot=memory.snapshot(),
            harness_status=self.harness.to_status_dict(),
            finished_reason=finished_reason,
            step_count=step_count,
            total_tokens=total_tokens,
        )

    def _build_fallback_result(
        self,
        memory: WorkingMemory,
        finished_reason: str,
        step_count: int,
        total_tokens: int,
    ) -> AgentResult:
        """Budget/timeout 耗尽时，用当前 memory 生成兆底结果。"""
        self._enrich_articles_with_images(memory)
        articles = memory.publishable_articles()
        success = len(articles) >= 1

        if articles:
            from datetime import date
            title = f"高分子加工全视界日报（{date.today().isoformat()}）"
        else:
            title = "日报生成未完成"

        return AgentResult(
            success=success,
            title=title,
            summary=f"Agent 因 {finished_reason} 停止，已收集 {len(articles)} 篇文章",
            articles=[a.to_dict() for a in articles],
            sections_content=memory.get_all_sections_content(),  # 尽量保留已写内容
            memory_snapshot=memory.snapshot(),
            harness_status=self.harness.to_status_dict(),
            finished_reason=finished_reason,
            step_count=step_count,
            total_tokens=total_tokens,
        )

    def _persist_step(
        self,
        session: Any,
        agent_run_id: int,
        step_record: StepRecord,
        thought: str,
    ) -> None:
        """持久化 AgentStep 到数据库。"""
        try:
            from app.models import AgentStep
            step = AgentStep(
                agent_run_id=agent_run_id,
                stage_name=step_record.tool_name,
                status="completed" if not step_record.harness_blocked else "blocked",
                round_index=step_record.step_index,
                decision_type="tool_call",
                decision_summary=step_record.result_summary[:500],
                duration_seconds=step_record.duration_seconds,
                fallback_triggered=step_record.harness_blocked,
                input_payload={"arguments": step_record.arguments, "thought": thought[:1000] if thought else ""},
                output_payload={"result_summary": step_record.result_summary[:500]},
                error_message=step_record.block_reason if step_record.harness_blocked else None,
            )
            session.add(step)
            session.flush()
        except Exception as exc:
            logger.warning("[AgentCore] Failed to persist AgentStep: %s", exc)
