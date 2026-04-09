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

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from app.services.harness import Harness
from app.services.llm_client import LLMClient, LLMResponse, ToolCallRequest
from app.services.tools import FinishTool, Tool, ToolCall, ToolResult
from app.services.working_memory import StepRecord, WorkingMemory
from app.utils import now_local

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
    editorial: str = ""
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    harness_status: dict[str, Any] = field(default_factory=dict)
    finished_reason: str = "unknown"   # "complete" | "budget_exhausted" | "timeout" | "error" | "finish_tool"
    step_count: int = 0
    total_tokens: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)

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
            "diagnostics": self.diagnostics,
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
        event_queue: asyncio.Queue | None = None,
    ) -> None:
        self.tools: dict[str, Tool] = {t.name: t for t in tools}
        self.llm = llm_client
        self.harness = harness
        self._event_queue = event_queue
        self._tool_definitions = [t.to_openai_schema() for t in tools]

    async def run(
        self,
        task: str,
        agent_run_id: int | None = None,
        memory: WorkingMemory | None = None,
    ) -> AgentResult:
        """
        启动 Agent 运行。

        Args:
            task: 给 Agent 的任务描述（自然语言）
            agent_run_id: AgentRun 的数据库 ID（用于关联 AgentStep）
            memory: 外部 WorkingMemory（可选，用于 multi-agent 共享状态）

        Returns:
            AgentResult: 最终结果
        """
        memory = memory or WorkingMemory()
        total_tokens = 0
        step_index = 0
        consecutive_finish_rejects = 0
        consecutive_no_tool_responses = 0
        llm_no_tool_stall_count = 0
        _wind_down_warned = False

        # 构建初始 message history
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_message()},
            {"role": "user", "content": task},
        ]

        logger.info("[AgentCore] Starting agent run. Task: %s", task[:100])

        while self.harness.effective_budget_remaining > 0 and not self.harness.timed_out:
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
                    consecutive_finish_rejects += 1
                    if consecutive_finish_rejects > self.harness.max_consecutive_finish_rejects:
                        logger.info("[AgentCore] Force-accepting finish after %d consecutive rejects", consecutive_finish_rejects)
                        # 提取 finish 参数（放宽限制）
                        for tc in llm_response.tool_calls:
                            if tc.tool_name == "finish":
                                finish_result = tc.arguments
                                break
                        else:
                            finish_result = {}
                    else:
                        logger.info("[AgentCore] Intercepted premature finish (%d/%d): %s",
                                    consecutive_finish_rejects, self.harness.max_consecutive_finish_rejects, e)
                        messages.append({
                            "role": "user",
                            "content": f"系统提示：你试图调用 finish，但是被拒绝。原因：{str(e)} 请继续使用 web_search 探索新方向并使用 evaluate_article 评估文章。"
                        })
                        continue

                if finish_result:
                    logger.info("[AgentCore] Agent finished via finish tool at step %d", step_index)
                    return self._build_result(
                        memory,
                        finish_result,
                        "finish_tool",
                        step_index,
                        total_tokens,
                        diagnostics={"llm_no_tool_stall_count": llm_no_tool_stall_count},
                    )
                # 纯文本回复（没有工具调用），继续循环但记录
                # 注意：必须携带 reasoning_content，否则 kimi-k2.5 在后续请求中会报 400
                consecutive_no_tool_responses += 1
                messages.append({
                    "role": "assistant",
                    "content": llm_response.content,
                    "reasoning_content": llm_response.reasoning_content or llm_response.content or "[assistant response]",
                })
                logger.debug("[AgentCore] LLM gave text response without tool calls at step %d", step_index)
                if consecutive_no_tool_responses >= 3:
                    llm_no_tool_stall_count += 1
                    logger.warning("[AgentCore] Agent stalled with %d consecutive no-tool replies", consecutive_no_tool_responses)
                    if self._event_queue:
                        try:
                            self._event_queue.put_nowait({
                                "type": "warning",
                                "warning_code": "llm_no_tool_stall",
                                "message": "LLM 连续多轮未调用工具，已提前收敛。",
                                "step_index": step_index,
                            })
                        except Exception:
                            pass
                    return self._build_fallback_result(
                        memory,
                        "llm_no_tool_stall",
                        step_index,
                        total_tokens,
                        diagnostics={"llm_no_tool_stall_count": llm_no_tool_stall_count},
                    )
                # 如果连续没有工具调用，给出提示
                if step_index > 3:
                    messages.append({
                        "role": "user",
                        "content": "请记得使用可用的工具继续探索，或者调用 finish 完成报告。"
                    })
                continue

            # === Step 3: 执行工具调用 ===
            tool_result_messages: list[dict[str, Any]] = []
            consecutive_finish_rejects = 0  # 只要有非 finish 工具调用就重置
            consecutive_no_tool_responses = 0

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
                            timeout = self.harness.tool_timeout(tool_call.tool_name)
                            tool_result = await asyncio.wait_for(
                                tool.execute(memory=memory, **tool_call.arguments),
                                timeout=timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.warning("[AgentCore] Tool %s timed out (%.0fs)", tool_call.tool_name, timeout)
                            tool_result = ToolResult(
                                success=False,
                                summary=f"工具超时({tool_call.tool_name}, 限制{timeout:.0f}秒)",
                                data={"error_type": "timeout", "tool": tool_call.tool_name},
                            )
                        except httpx.HTTPStatusError as exc:
                            status = exc.response.status_code
                            error_type = "rate_limit" if status == 429 else "http_error"
                            logger.warning("[AgentCore] Tool %s HTTP %d", tool_call.tool_name, status)
                            tool_result = ToolResult(
                                success=False,
                                summary=f"HTTP错误 {status}",
                                data={"error_type": error_type, "status": status},
                            )
                        except httpx.TimeoutException:
                            logger.warning("[AgentCore] Tool %s network timeout", tool_call.tool_name)
                            tool_result = ToolResult(
                                success=False,
                                summary="网络请求超时",
                                data={"error_type": "network_timeout"},
                            )
                        except json.JSONDecodeError as exc:
                            logger.warning("[AgentCore] Tool %s JSON parse error: %s", tool_call.tool_name, exc)
                            tool_result = ToolResult(
                                success=False,
                                summary="返回格式解析错误",
                                data={"error_type": "parse_error"},
                            )
                        except Exception as exc:
                            logger.error("[AgentCore] Tool %s unexpected error: %s", tool_call.tool_name, exc, exc_info=True)
                            tool_result = ToolResult(
                                success=False,
                                summary=f"工具异常: {exc}",
                                data={"error_type": "unexpected", "error": str(exc)},
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
                if agent_run_id is not None:
                    self._persist_step(agent_run_id, step_record, llm_response.thought)

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
                        memory,
                        tool_result.data,
                        "finish_tool",
                        step_index,
                        total_tokens,
                        diagnostics={"llm_no_tool_stall_count": llm_no_tool_stall_count},
                    )

                logger.debug("[AgentCore] Step %d [%s]: %s", step_index, tool_call.tool_name, tool_result.summary[:100])

            messages.extend(tool_result_messages)

            # 动态预算感知：资源即将耗尽时注入收尾提示（只提示一次）
            if self.harness.should_wind_down and not _wind_down_warned:
                _wind_down_warned = True
                remaining_time = max(0, self.harness.max_duration_seconds - self.harness.elapsed_seconds)
                wind_down_msg = (
                    f"⚠️ 资源即将耗尽（剩余约 {self.harness.effective_budget_remaining} 步，"
                    f"{remaining_time:.0f}s）。"
                    "请尽快使用 write_section 撰写已收集的内容并调用 finish 完成报告。"
                )
                messages.append({"role": "user", "content": wind_down_msg})

        # === Budget 耗尽或超时 ===
        finished_reason = "timeout" if self.harness.timed_out else "budget_exhausted"
        logger.info("[AgentCore] Agent stopped: %s at step %d", finished_reason, step_index)

        # 尝试用当前 memory 生成兜底报告
        return self._build_fallback_result(
            memory,
            finished_reason,
            step_index,
            total_tokens,
            diagnostics={"llm_no_tool_stall_count": llm_no_tool_stall_count},
        )

    def _build_system_message(self) -> str:
        """构建包含工具说明和预算信息的 system message。"""
        tool_info_parts = []
        for t in self.tools.values():
            # 取 description 的第一句话作为简要说明
            first_sentence = t.description.split("。")[0] if t.description else ""
            tool_info_parts.append(f"  - {t.name}: {first_sentence}")
        tool_info = "\n".join(tool_info_parts)

        harness_info = (
            f"资源限制: 最多 {self.harness.max_steps} 步, "
            f"{self.harness.max_search_calls} 次搜索, "
            f"{self.harness.max_page_reads} 次阅读, "
            f"{self.harness.max_duration_seconds:.0f} 秒超时。"
        )
        base_prompt = self.harness.system_prompt or "你是一个专业的新闻研究员助手。"
        return f"{base_prompt}\n\n可用工具:\n{tool_info}\n\n{harness_info}"

    @staticmethod
    def _trim_messages(
        messages: list[dict[str, Any]],
        keep_recent: int = 15,
        max_total_chars: int = 80000,
    ) -> list[dict[str, Any]]:
        """
        裁剪消息历史，防止 context window 溢出。

        保留:
          - system message (index 0)
          - task message (index 1)
          - 最近 keep_recent 条完整消息
        中间消息的工具结果压缩为摘要。
        """
        if len(messages) <= keep_recent + 2:
            # 检查总字符数
            total = sum(len(m.get("content", "") or "") for m in messages)
            if total <= max_total_chars:
                return messages

        # 保留: system + task + 最近 N 条
        system = messages[0] if messages else None
        task = messages[1] if len(messages) > 1 else None
        recent = messages[-keep_recent:] if len(messages) > keep_recent else messages[2:]

        # 压缩中间消息
        middle = messages[2:-keep_recent] if len(messages) > keep_recent + 2 else []
        compressed: list[dict[str, Any]] = []
        for msg in middle:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "tool" and len(content) > 200:
                # 工具结果压缩
                compressed.append({
                    "role": role,
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": content[:200] + "...[已截断]",
                })
            elif role == "assistant" and msg.get("tool_calls"):
                # 保留 assistant 消息但截断长内容
                compressed.append(msg)
            elif role == "user" and len(content) > 200:
                compressed.append({
                    "role": role,
                    "content": content[:200] + "...[已截断]",
                })
            else:
                compressed.append(msg)

        result: list[dict[str, Any]] = []
        if system:
            result.append(system)
        if task:
            result.append(task)
        result.extend(compressed)
        result.extend(recent)
        return result

    async def _get_llm_decision(
        self, messages: list[dict[str, Any]], memory: WorkingMemory
    ) -> LLMResponse:
        """调用 LLM 获取下一步决策。"""
        # 裁剪消息历史防止 context window 溢出
        messages = self._trim_messages(messages)

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

        # 始终携带 reasoning_content（即使是空字符串），避免 kimi-k2.5 报错：
        # "thinking is enabled but reasoning_content is missing"
        msg["reasoning_content"] = llm_response.reasoning_content or llm_response.content or "[tool planning]"

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
                min_searches = self.harness.min_searches_before_finish
                min_articles = self.harness.min_articles_before_finish
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
        diagnostics: dict[str, Any] | None = None,
    ) -> AgentResult:
        """从 finish 工具的数据和 memory 构建最终结果。"""
        self._enrich_articles_with_images(memory)
        articles = [a.to_dict() for a in memory.publishable_articles()]

        # 板块内容：优先使用 memory 缓存（write_section 写的），
        # 再用 finish 参数覆盖/补充（如果 LLM 也传了的话）
        sections_from_memory = memory.get_all_sections_content()
        raw_sections = finish_data.get("sections_content", {})
        sections_from_finish: dict[str, str] = raw_sections if isinstance(raw_sections, dict) else {}
        merged_sections: dict[str, str] = {**sections_from_memory, **sections_from_finish}

        return AgentResult(
            success=True,
            title=finish_data.get("title", "高分子加工全视界日报"),
            summary=finish_data.get("summary", ""),
            editorial=finish_data.get("editorial", ""),
            articles=articles,
            sections_content=merged_sections,
            memory_snapshot=memory.snapshot(),
            harness_status=self.harness.to_status_dict(),
            finished_reason=finished_reason,
            step_count=step_count,
            total_tokens=total_tokens,
            diagnostics=diagnostics or {},
        )

    def _build_fallback_result(
        self,
        memory: WorkingMemory,
        finished_reason: str,
        step_count: int,
        total_tokens: int,
        diagnostics: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Budget/timeout 耗尽时，用当前 memory 生成兆底结果。"""
        self._enrich_articles_with_images(memory)
        articles = memory.publishable_articles()
        success = len(articles) >= 1

        if articles:
            title = f"高分子加工全视界日报（{now_local().date().isoformat()}）"
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
            diagnostics=diagnostics or {},
        )

    def _persist_step(
        self,
        agent_run_id: int,
        step_record: StepRecord,
        thought: str,
    ) -> None:
        """
        持久化 AgentStep 到数据库。

        使用独立短生命周期 session，避免长事务占用 SQLite 写锁。
        """
        try:
            from app.models import AgentStep
            from app.database import session_scope

            with session_scope() as step_session:
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
                step_session.add(step)
                step_session.flush()
        except Exception as exc:
            logger.warning("[AgentCore] Failed to persist AgentStep: %s", exc)

        # 推送实时事件到 SSE 队列
        if self._event_queue:
            try:
                self._event_queue.put_nowait({
                    "type": "step",
                    "tool_name": step_record.tool_name,
                    "thought": (thought or "")[:200],
                    "result_summary": step_record.result_summary[:300],
                    "duration": round(step_record.duration_seconds, 2),
                    "step_index": step_record.step_index,
                    "harness_blocked": step_record.harness_blocked,
                })
            except Exception:
                pass  # 不影响主流程
