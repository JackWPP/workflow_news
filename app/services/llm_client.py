"""
llm_client.py — 统一的 LLM 客户端，支持 tool-use 模式 + 多 Provider 路由

支持多个 API Provider（Moonshot/Kimi, OpenRouter 等）。
根据 model 名称自动路由到对应的 base_url 和 api_key。

路由规则：
  - kimi-* 模型  → Moonshot API (api.moonshot.cn)
  - 其他模型      → OpenRouter API (openrouter.ai)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRequest:
    """LLM 产生的一次工具调用请求。"""
    tool_name: str
    arguments: dict[str, Any]
    call_id: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolCallRequest):
            return NotImplemented
        return self.tool_name == other.tool_name and self.arguments == other.arguments


@dataclass
class LLMResponse:
    """LLM 的一次响应。"""
    content: str                                          # 自由文本（思考过程）
    reasoning_content: str = ""                           # 推理阶段的内部思考内容（kimi-k2.5/moonshot）
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    is_finish: bool = False                               # 是否调用了 finish 工具
    model_used: str = ""
    tokens_used: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def thought(self) -> str:
        """提取 Agent 的思考过程（第一和第二阶段推理内容）。"""
        return self.content.strip()


# ── Provider 配置 ─────────────────────────────────────────

@dataclass
class _ProviderConfig:
    """一个 API Provider 的连接配置。"""
    base_url: str
    api_key: str
    headers: dict[str, str]


def _resolve_provider(model: str) -> _ProviderConfig:
    """
    根据 model 名称路由到对应的 API Provider。

    路由规则：
      - kimi-* → Moonshot API
      - 其他   → OpenRouter API
    """
    if model.startswith("kimi-") or model.startswith("moonshot-"):
        api_key = settings.kimi_api_key
        base_url = settings.kimi_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        api_key = settings.openrouter_api_key
        base_url = settings.openrouter_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://workflow-news.local",
            "X-Title": "workflow_news_agent",
        }

    return _ProviderConfig(base_url=base_url, api_key=api_key, headers=headers)


def _adjust_temperature(model: str, temperature: float) -> float:
    """部分模型对 temperature 有限制，在此统一处理。"""
    # kimi-k2.5 只接受 temperature=1
    if model.startswith("kimi-") or model.startswith("moonshot-"):
        return 1.0
    return temperature


class LLMClient:
    """
    支持 tool-use 的 LLM 客户端。

    主要方法：
      - chat_with_tools(): 带工具定义的对话，LLM 可选择调用工具
      - simple_completion(): 普通文本补全（用于工具内部的 LLM 调用）

    支持多 Provider：每次请求根据 model 名自动选择 base_url + api_key。
    """

    def __init__(
        self,
        primary_model: str | None = None,
        fallback_model: str | None = None,
        timeout: int | None = None,
        # 兼容旧签名（忽略 api_key/base_url，改用 _resolve_provider）
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.primary_model = primary_model or settings.report_primary_model
        self.fallback_model = fallback_model or settings.report_fallback_model
        self.timeout = timeout or settings.openrouter_timeout_seconds

    @property
    def enabled(self) -> bool:
        """只要任一 provider 有 key 就可用。"""
        provider = _resolve_provider(self.primary_model)
        return bool(provider.api_key)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        temperature: float = 0.3,
    ) -> LLMResponse:
        """
        带工具定义的对话。LLM 可以：
          1. 直接回复（content 非空，tool_calls 为空）
          2. 选择调用工具（tool_calls 非空）
          3. 两者都有（先思考，再调用工具）

        这是 Agent 每步决策的核心调用。
        """
        if not self.enabled:
            return LLMResponse(content="LLM not configured", is_finish=True)

        models = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            models.append(self.fallback_model)

        last_exc: Exception | None = None
        for model in models:
            try:
                return await self._chat_with_tools_request(
                    model, messages, tool_definitions, temperature
                )
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("LLM tool-use call failed with %s: %s", model, exc)
                last_exc = exc

        logger.error("All LLM models failed: %s", last_exc)
        return LLMResponse(content=f"LLM error: {last_exc}", is_finish=True)

    async def _chat_with_tools_request(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        temperature: float,
    ) -> LLMResponse:
        provider = _resolve_provider(model)

        payload: dict[str, Any] = {
            "model": model,
            "temperature": _adjust_temperature(model, temperature),
            "messages": messages,
        }
        if tool_definitions:
            payload["tools"] = tool_definitions
            payload["tool_choice"] = "auto"

        import asyncio

        max_retries = 2
        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{provider.base_url}/chat/completions",
                    json=payload,
                    headers=provider.headers,
                )
                if resp.status_code == 429 and attempt < max_retries - 1:
                    wait = min(float(resp.headers.get("retry-after", "2")), 5.0)
                    logger.info("LLM %s rate limited (429), retrying in %.1fs", model, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code != 200:
                    body = resp.text[:500]
                    logger.warning("LLM %s returned %d: %s", model, resp.status_code, body)
                    resp.raise_for_status()
                data = resp.json()
                break

        if "choices" not in data or not data["choices"]:
            # Log error details if present (e.g., OpenRouter returns {"error": {...}})
            error_info = data.get("error", data)
            logger.warning("LLM %s response has no choices. Error: %s", model, error_info)
            raise ValueError(f"LLM response error from {model}: {error_info}")

        choice = data["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning_content = message.get("reasoning_content") or ""
        tool_calls_raw = message.get("tool_calls") or []
        finish_reason = choice.get("finish_reason", "")
        usage = data.get("usage") or {}

        tool_calls: list[ToolCallRequest] = []
        for tc in tool_calls_raw:
            try:
                fn = tc.get("function") or tc
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    arguments = json.loads(raw_args)
                else:
                    arguments = raw_args
                tool_calls.append(ToolCallRequest(
                    tool_name=tool_name,
                    arguments=arguments,
                    call_id=tc.get("id", ""),
                ))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("Failed to parse tool call: %s — %s", tc, exc)

        is_finish = (
            finish_reason == "stop"
            and not tool_calls
        ) or any(tc.tool_name == "finish" for tc in tool_calls)

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            is_finish=is_finish,
            model_used=model,
            tokens_used=usage.get("total_tokens", 0),
        )

    async def simple_completion(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """
        普通文本补全（不带工具定义）。
        用于工具内部需要 LLM 推理的场景，如：
          - evaluate_article：让 LLM 评估文章价值
          - write_section：让 LLM 写一个板块的内容
          - compare_sources：让 LLM 对比去重
        """
        if not self.enabled:
            return ""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        models = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            models.append(self.fallback_model)

        for model in models:
            try:
                provider = _resolve_provider(model)
                payload: dict[str, Any] = {
                    "model": model,
                    "temperature": _adjust_temperature(model, temperature),
                    "messages": messages,
                }
                if max_tokens:
                    payload["max_tokens"] = max_tokens

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{provider.base_url}/chat/completions",
                        json=payload,
                        headers=provider.headers,
                    )
                    if resp.status_code != 200:
                        logger.warning("LLM %s returned %d: %s", model, resp.status_code, resp.text[:500])
                        resp.raise_for_status()
                    data = resp.json()
                if "choices" not in data or not data["choices"]:
                    error_info = data.get("error", data)
                    raise ValueError(f"LLM response error from {model}: {error_info}")
                return data["choices"][0]["message"]["content"] or ""
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("simple_completion failed with %s: %s", model, exc)

        return ""

    async def simple_json_completion(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """
        返回 JSON 的文本补全。只在工具内部需要结构化输出时使用。
        与旧 llm.py 的 _invoke_structured 类似，但更轻量。
        """
        raw = await self.simple_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=temperature,
        )
        return self._extract_json(raw)

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        text = raw.strip()
        # 先尝试 markdown 代码块
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass
        # 再找 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}

    def build_tool_result_message(
        self,
        tool_call_id: str,
        result_content: str,
    ) -> dict[str, Any]:
        """构建工具结果消息，加入 message history。"""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        }
