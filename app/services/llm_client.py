"""
llm_client.py — 统一的 LLM 客户端，支持 tool-use 模式 + 多 Provider 路由

支持多个 API Provider（Moonshot/Kimi, OpenRouter 等）。
根据 model 名称自动路由到对应的 base_url 和 api_key。

路由规则：
  - kimi-* 模型  → Moonshot API (api.moonshot.cn)
  - 其他模型      → OpenRouter API (openrouter.ai)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _format_exc(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return f"{exc.__class__.__name__}: {text}"
    return exc.__class__.__name__


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


def _is_kimi_model(model: str) -> bool:
    """判断是否为 Kimi/Moonshot 模型。"""
    return model.startswith("kimi-") or model.startswith("moonshot-")


def _provider_kind(model: str) -> str:
    return "moonshot" if _is_kimi_model(model) else "openrouter"


def _provider_behavior(model: str) -> dict[str, Any]:
    kind = _provider_kind(model)
    return {
        "provider": kind,
        "requires_reasoning_content": kind == "moonshot",
        "supports_tool_history_replay": kind != "moonshot",
    }


def _build_payload_params(model: str, temperature: float) -> dict[str, Any]:
    """
    构建请求参数，处理模型间的差异。

    kimi-k2.5 限制：temperature, top_p, n, presence_penalty, frequency_penalty
    均不可修改，payload 中应省略这些参数。
    """
    params: dict[str, Any] = {"model": model}

    if _is_kimi_model(model):
        # kimi-k2.5 不可修改 temperature 等参数，省略即可使用服务端默认值
        # 显式启用 thinking 模式（kimi-k2.5 默认已启用，显式传递更安全）
        params["thinking"] = {"type": "enabled"}
    else:
        params["temperature"] = temperature

    return params


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
        max_concurrency: int = 3,
        api_key: str | None = None,
        base_url: str | None = None,
        strict_primary_model_for_tool_use: bool | None = None,
        strict_primary_model_for_all_llm: bool | None = None,
        tool_use_fallback_mode: str | None = None,
    ):
        self.primary_model = primary_model or settings.report_primary_model
        self.fallback_model = fallback_model or settings.report_fallback_model
        self.timeout = timeout or settings.openrouter_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.strict_primary_model_for_tool_use = (
            settings.strict_primary_model_for_tool_use
            if strict_primary_model_for_tool_use is None
            else strict_primary_model_for_tool_use
        )
        self.strict_primary_model_for_all_llm = (
            settings.strict_primary_model_for_all_llm
            if strict_primary_model_for_all_llm is None
            else strict_primary_model_for_all_llm
        )
        self.tool_use_fallback_mode = tool_use_fallback_mode or settings.tool_use_fallback_mode
        self._metrics: dict[str, Any] = {
            "model_fallbacks": [],
            "llm_bad_request_count": 0,
            "tool_use_model": self.primary_model,
            "tool_use_model_switch_attempted": False,
            "tool_use_history_reset_count": 0,
            "moonshot_reasoning_history_errors": 0,
            "kimi_rate_limit_errors": 0,
            "strict_primary_model_enabled": self.strict_primary_model_for_tool_use,
            "tool_use_fallback_mode": self.tool_use_fallback_mode,
        }

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
        if (
            not self.strict_primary_model_for_tool_use
            and self.fallback_model
            and self.fallback_model != self.primary_model
        ):
            models.append(self.fallback_model)

        last_exc: Exception | None = None
        for index, model in enumerate(models):
            try:
                response = await self._chat_with_tools_request(
                    model, messages, tool_definitions, temperature
                )
                if index > 0 and last_exc is not None:
                    self._record_model_fallback(model, last_exc)
                return response
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("LLM tool-use call failed with %s: %s", model, _format_exc(exc))
                last_exc = exc
                if self.strict_primary_model_for_tool_use and self.fallback_model and self.fallback_model != self.primary_model:
                    self._metrics["tool_use_model_switch_attempted"] = True
                    logger.warning(
                        "LLM tool-use model switch blocked by strict mode: %s -> %s",
                        self.primary_model,
                        self.fallback_model,
                    )
                if self.strict_primary_model_for_tool_use:
                    break

        logger.error("All LLM models failed: %s", _format_exc(last_exc) if last_exc else "unknown_error")
        return LLMResponse(content=f"LLM error: {last_exc}", is_finish=True)

    async def _chat_with_tools_request(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        temperature: float,
    ) -> LLMResponse:
        provider = _resolve_provider(model)

        payload = _build_payload_params(model, temperature)
        payload["messages"] = self._sanitize_messages_for_model(messages, model)
        if tool_definitions:
            payload["tools"] = tool_definitions
            payload["tool_choice"] = "auto"

        max_retries = 3
        data: dict[str, Any] = {}
        history_reset_retry_used = False
        for attempt in range(max_retries):
            async with self._semaphore:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{provider.base_url}/chat/completions",
                        json=payload,
                        headers=provider.headers,
                    )
                    if resp.status_code == 429 and attempt < max_retries - 1:
                        self._metrics["kimi_rate_limit_errors"] += 1
                        wait = self._retry_wait_for_attempt(resp, attempt)
                        logger.info("LLM %s 429 overload, retrying in %.1fs", model, wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code == 400:
                        # 400 是请求格式错误（如 reasoning_content 缺失），重试无意义
                        # 直接 raise 让外层 fallback 到下一个模型
                        body = resp.text[:500]
                        self._metrics["llm_bad_request_count"] += 1
                        if (
                            _is_kimi_model(model)
                            and not history_reset_retry_used
                            and "reasoning_content is missing" in body.lower()
                        ):
                            history_reset_retry_used = True
                            self._metrics["moonshot_reasoning_history_errors"] += 1
                            self._metrics["tool_use_history_reset_count"] += 1
                            payload["messages"] = self._build_history_reset_retry_messages(messages, model)
                            logger.warning(
                                "LLM %s returned Moonshot reasoning history error; retrying with history reset",
                                model,
                            )
                            continue
                        logger.warning("LLM %s returned 400 (non-retryable): %s", model, body)
                        resp.raise_for_status()
                    if resp.status_code == 429:
                        self._metrics["kimi_rate_limit_errors"] += 1
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

    @staticmethod
    def _sanitize_messages_for_model(
        messages: list[dict[str, Any]], model: str
    ) -> list[dict[str, Any]]:
        """
        清理消息历史，确保兼容目标模型的要求。

        kimi-k2.5 在 thinking 模式下要求所有 assistant 消息必须包含
        reasoning_content 字段，否则返回 400:
        "thinking is enabled but reasoning_content is missing in assistant
        tool call message at index N"
        """
        behavior = _provider_behavior(model)
        cleaned = []
        for msg in messages:
            normalized = dict(msg)
            if normalized.get("role") == "assistant":
                normalized["content"] = normalized.get("content") or ""
                if behavior["requires_reasoning_content"]:
                    normalized["reasoning_content"] = LLMClient._normalized_reasoning_content(normalized)
                elif "reasoning_content" in normalized:
                    normalized = {k: v for k, v in normalized.items() if k != "reasoning_content"}

                if normalized.get("tool_calls"):
                    normalized["tool_calls"] = [
                        LLMClient._normalize_tool_call(call, idx)
                        for idx, call in enumerate(normalized.get("tool_calls") or [])
                    ]
            cleaned.append(normalized)
        return cleaned

    @staticmethod
    def _normalized_reasoning_content(message: dict[str, Any]) -> str:
        reasoning = (message.get("reasoning_content") or "").strip()
        if reasoning:
            return reasoning
        content = (message.get("content") or "").strip()
        if content:
            return content[:2000]
        if message.get("tool_calls"):
            return "[tool planning]"
        return "[assistant response]"

    @staticmethod
    def _normalize_tool_call(tool_call: dict[str, Any], index: int) -> dict[str, Any]:
        call = dict(tool_call)
        fn = dict(call.get("function") or {})
        raw_args = fn.get("arguments", "{}")
        if not isinstance(raw_args, str):
            raw_args = json.dumps(raw_args, ensure_ascii=False)
        fn["arguments"] = raw_args
        call["id"] = call.get("id") or f"call_{index}"
        call["type"] = call.get("type") or "function"
        call["function"] = fn
        return call

    def _build_history_reset_retry_messages(
        self,
        messages: list[dict[str, Any]],
        model: str,
        keep_recent_users: int = 2,
        keep_recent_tools: int = 4,
    ) -> list[dict[str, Any]]:
        if len(messages) <= 2:
            return self._sanitize_messages_for_model(messages, model)

        prefix = messages[:2]
        trailing_users: list[str] = []
        tool_summaries: list[str] = []

        for msg in messages[2:]:
            role = msg.get("role")
            if role == "user":
                content = (msg.get("content") or "").strip()
                if content:
                    trailing_users.append(content[:500])
            elif role == "tool":
                content = (msg.get("content") or "").strip()
                if content:
                    tool_summaries.append(content[:300])

        rebuilt: list[dict[str, Any]] = list(prefix)
        for content in trailing_users[-keep_recent_users:]:
            rebuilt.append({"role": "user", "content": content})
        if tool_summaries:
            rebuilt.append({
                "role": "user",
                "content": "[历史工具结果摘要]\n" + "\n".join(f"- {item}" for item in tool_summaries[-keep_recent_tools:]),
            })
        return self._sanitize_messages_for_model(rebuilt, model)

    @staticmethod
    def _message_chunks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        chunks: list[list[dict[str, Any]]] = []
        index = 0
        while index < len(messages):
            current = messages[index]
            role = current.get("role")
            if role == "assistant" and current.get("tool_calls"):
                chunk = [current]
                index += 1
                while index < len(messages) and messages[index].get("role") == "tool":
                    chunk.append(messages[index])
                    index += 1
                chunks.append(chunk)
                continue
            chunks.append([current])
            index += 1
        return chunks

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
        if (
            not self.strict_primary_model_for_all_llm
            and self.fallback_model
            and self.fallback_model != self.primary_model
        ):
            models.append(self.fallback_model)

        last_exc: Exception | None = None
        for index, model in enumerate(models):
            provider = _resolve_provider(model)
            payload = _build_payload_params(model, temperature)
            payload["messages"] = messages
            if max_tokens:
                payload["max_tokens"] = max_tokens

            max_retries = 3
            data: dict[str, Any] = {}
            for attempt in range(max_retries):
                try:
                    async with self._semaphore:
                        async with httpx.AsyncClient(timeout=self.timeout) as client:
                            resp = await client.post(
                                f"{provider.base_url}/chat/completions",
                                json=payload,
                                headers=provider.headers,
                            )
                            if resp.status_code == 429 and attempt < max_retries - 1:
                                wait = self._extract_retry_wait(resp)
                                logger.info("simple_completion %s rate limited (429), retrying in %.1fs (attempt %d/%d)", model, wait, attempt + 1, max_retries)
                                await asyncio.sleep(wait)
                                continue
                            if resp.status_code == 400:
                                self._metrics["llm_bad_request_count"] += 1
                            if resp.status_code != 200:
                                logger.warning("LLM %s returned %d: %s", model, resp.status_code, resp.text[:500])
                                resp.raise_for_status()
                            data = resp.json()
                    if "choices" not in data or not data["choices"]:
                        error_info = data.get("error", data)
                        raise ValueError(f"LLM response error from {model}: {error_info}")
                    if index > 0 and last_exc is not None:
                        self._record_model_fallback(model, last_exc)
                    return data["choices"][0]["message"]["content"] or ""
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    logger.warning("simple_completion failed with %s: %s (attempt %d/%d)", model, _format_exc(exc), attempt + 1, max_retries)
                    last_exc = exc
                    if attempt < max_retries - 1 and isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        continue
                    break
            if self.strict_primary_model_for_all_llm:
                break

        return ""

    @staticmethod
    def _extract_retry_wait(resp: httpx.Response) -> float:
        """
        从 429 响应中提取重试等待时间。

        Kimi API 429 错误类型：
          - engine_overloaded_error：节点过载，建议 2-3s
          - rate_limit_reached_error：配额限制，消息含 "please try again after Xs"
          - exceeded_current_quota_error：余额不足，不建议重试

        优先从 body 提取精确等待时间，其次用 retry-after header。
        """
        # 1. 尝试从 header 读取
        header_wait = float(resp.headers.get("retry-after", "0"))

        # 2. 尝试从 body 读取 Kimi 特定格式
        try:
            body = resp.json()
            error = body.get("error", {})
            message = error.get("message", "")

            # rate_limit_reached_error: "please try again after 5.04 seconds"
            match = re.search(r"try again after ([\d.]+)\s*s", message, re.IGNORECASE)
            if match:
                body_wait = float(match.group(1))
                return min(max(body_wait, 1.0), 10.0)

            # exceeded_current_quota_error: 余额不足，不重试
            if "exceeded_current_quota" in message.lower() or "quota" in (error.get("type") or "").lower():
                return 30.0  # 长等待，让后续模型接管
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass

        # 3. 使用 header 值或默认
        if header_wait > 0:
            return min(header_wait, 10.0)
        return 2.0

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

    def snapshot_metrics(self) -> dict[str, Any]:
        return {
            "model_fallbacks": list(self._metrics.get("model_fallbacks", [])),
            "llm_bad_request_count": int(self._metrics.get("llm_bad_request_count", 0)),
            "tool_use_model": self._metrics.get("tool_use_model", self.primary_model),
            "tool_use_model_switch_attempted": bool(self._metrics.get("tool_use_model_switch_attempted", False)),
            "tool_use_history_reset_count": int(self._metrics.get("tool_use_history_reset_count", 0)),
            "moonshot_reasoning_history_errors": int(self._metrics.get("moonshot_reasoning_history_errors", 0)),
            "kimi_rate_limit_errors": int(self._metrics.get("kimi_rate_limit_errors", 0)),
            "strict_primary_model_enabled": bool(self._metrics.get("strict_primary_model_enabled", True)),
            "tool_use_fallback_mode": self._metrics.get("tool_use_fallback_mode", "disabled"),
        }

    def _record_model_fallback(self, model: str, exc: Exception) -> None:
        self._metrics.setdefault("model_fallbacks", []).append({
            "from": self.primary_model,
            "to": model,
            "reason": str(exc)[:200],
        })

    def _retry_wait_for_attempt(self, resp: httpx.Response, attempt: int) -> float:
        if resp.status_code == 429:
            base = [2.0, 5.0, 10.0][min(attempt, 2)]
            return base + random.uniform(0.0, 0.5)
        return self._extract_retry_wait(resp)
