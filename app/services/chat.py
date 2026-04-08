from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import desc, or_, select

from app.config import settings
from app.models import Article, Conversation, Message, Report, ReportItem
from app.services.brave import BraveSearchClient


class ChatService:
    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.model = settings.report_primary_model
        self.brave = BraveSearchClient()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def retrieve_local_context(self, session, prompt: str, limit: int = 5) -> list[dict[str, Any]]:
        keywords = self._extract_keywords(prompt)
        if not keywords:
            return self._recent_report_items(session, limit)

        conditions = []
        for keyword in keywords:
            like = f"%{keyword}%"
            conditions.extend(
                [
                    ReportItem.title.ilike(like),
                    ReportItem.summary.ilike(like),
                    ReportItem.research_signal.ilike(like),
                ]
            )

        stmt = (
            select(ReportItem, Report.report_date)
            .join(Report, Report.id == ReportItem.report_id)
            .where(or_(*conditions))
            .order_by(desc(Report.report_date), desc(ReportItem.combined_score))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        if not rows:
            return self._recent_report_items(session, limit)
        return [
            {
                "title": item.title,
                "summary": item.summary,
                "research_signal": item.research_signal,
                "source_url": item.source_url,
                "source_name": item.source_name,
                "report_date": report_date.isoformat(),
            }
            for item, report_date in rows
        ]

    def _recent_report_items(self, session, limit: int) -> list[dict[str, Any]]:
        stmt = (
            select(ReportItem, Report.report_date)
            .join(Report, Report.id == ReportItem.report_id)
            .order_by(desc(Report.report_date), desc(ReportItem.combined_score))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return [
            {
                "title": item.title,
                "summary": item.summary,
                "research_signal": item.research_signal,
                "source_url": item.source_url,
                "source_name": item.source_name,
                "report_date": report_date.isoformat(),
            }
            for item, report_date in rows
        ]

    def _extract_keywords(self, prompt: str) -> list[str]:
        latin_words = [word.strip() for word in re.split(r"\W+", prompt) if len(word.strip()) > 1]
        cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", prompt)
        cjk_tokens: list[str] = []
        for chunk in cjk_chunks:
            if len(chunk) <= 4:
                cjk_tokens.append(chunk)
                continue
            cjk_tokens.extend(chunk[index : index + 4] for index in range(0, len(chunk) - 3))
            cjk_tokens.extend(chunk[index : index + 2] for index in range(0, len(chunk) - 1))

        keywords: list[str] = []
        for token in latin_words + cjk_tokens:
            if token not in keywords:
                keywords.append(token)
            if len(keywords) >= 8:
                break
        return keywords

    async def build_answer(self, session, prompt: str, retrieval_mode: str = "local_first") -> tuple[str, list[dict[str, Any]], str]:
        """
        通过 ResearchAgent 进行多步研究并回答用户问题。
        如果 Agent 不可用或 agent_mode 关闭，降级到旧的单轮补全。
        """
        if settings.agent_mode:
            try:
                from app.services.research_agent import ResearchAgent
                agent = ResearchAgent(session=session)
                result = await agent.research(question=prompt)
                answer, citations, mode = result.to_chat_response()
                if answer and answer != "研究过程遇到问题":
                    return answer, citations, mode
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("ResearchAgent failed, falling back to legacy: %s", exc)

        # ── 降级：旧单轮补全 ──────────────────────────────
        return await self._legacy_build_answer(session, prompt, retrieval_mode)

    async def _legacy_build_answer(self, session, prompt: str, retrieval_mode: str) -> tuple[str, list[dict[str, Any]], str]:
        """旧版单轮补全（保留作为 fallback）。"""
        citations = self.retrieve_local_context(session, prompt)
        mode = "local"

        if not citations and retrieval_mode == "local_first":
            mode = "external"

        if not citations and mode == "external":
            citations = await self._external_lookup(prompt)

        if self.enabled:
            try:
                answer = await self._llm_answer(prompt, citations, mode)
                return answer, citations, mode
            except Exception:
                pass

        if citations:
            lines = ["我基于当前可用资料整理了以下要点：", ""]
            for index, item in enumerate(citations, start=1):
                lines.append(f"{index}. {item['title']}：{item.get('summary') or item.get('research_signal')}")
            lines.append("")
            lines.append("如果你愿意，我可以继续按某个细分方向展开。")
            return "\n".join(lines), citations, mode

        return "当前没有命中本地日报，也没有可用的外部检索结果。请换个问法，或先生成新的日报。", [], mode

    async def _external_lookup(self, prompt: str) -> list[dict[str, Any]]:
        if not self.brave.enabled:
            return []

        rows = await self.brave.search(prompt, search_type="news", count=4, search_lang=settings.brave_search_lang)
        return [
            {
                "title": row["title"],
                "summary": row.get("snippet") or "外部检索结果暂无摘要。",
                "research_signal": "该结果来自外部检索，请结合原文进一步核验。",
                "source_url": row["url"],
                "source_name": row.get("domain") or "external",
                "report_date": datetime.now(UTC).date().isoformat(),
            }
            for row in rows[:3]
        ]

    async def _llm_answer(self, prompt: str, citations: list[dict[str, Any]], mode: str) -> str:
        system_prompt = (
            "你是一个服务于高分子材料加工领域的研究助手。"
            "你只能基于提供的 citations 回答，必须保持简洁，并指出资料范围。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": prompt, "retrieval_mode": mode, "citations": citations},
                    ensure_ascii=False,
                ),
            },
        ]
        payload = {"model": self.model, "messages": messages}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=settings.openrouter_timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]


def create_conversation(session, user_id: int, title: str | None = None) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title or "新对话")
    session.add(conversation)
    session.flush()
    return conversation


def append_message(
    session,
    conversation: Conversation,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    retrieval_mode: str = "local_first",
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        citations=citations or [],
        retrieval_mode=retrieval_mode,
    )
    session.add(message)
    session.flush()
    conversation.last_message_at = message.created_at
    return message
