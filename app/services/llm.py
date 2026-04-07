from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import settings


class PlannedQuery(BaseModel):
    section: str
    language: str
    query: str
    rationale: str


class PlannerOutput(BaseModel):
    queries: list[PlannedQuery] = Field(default_factory=list)
    priority_domains: list[str] = Field(default_factory=list)
    round_goal: str | None = None
    focus_mode: str | None = None
    preferred_sections: list[str] = Field(default_factory=list)
    section_targets: dict[str, int] = Field(default_factory=dict)
    image_targets: dict[str, int] = Field(default_factory=dict)


class ResearchCandidateDecision(BaseModel):
    candidate_id: int
    section: str
    keep: bool
    priority_score: float
    rationale: str | None = None
    image_query: str | None = None


class ResearcherOutput(BaseModel):
    decisions: list[ResearchCandidateDecision] = Field(default_factory=list)


class ArticleDecision(BaseModel):
    article_id: int
    section: str
    keep: bool
    freshness_score: float
    relevance_score: float
    source_trust_score: float
    research_value_score: float
    novelty_score: float
    combined_score: float
    rationale: str | None = None
    research_signal: str | None = None
    review_label: str = "publishable"
    review_reason: str | None = None


class ScorerOutput(BaseModel):
    decisions: list[ArticleDecision] = Field(default_factory=list)


class WriterItemDecision(BaseModel):
    article_id: int
    section: str
    rank: int
    summary: str
    research_signal: str | None = None


class WriterOutput(BaseModel):
    title: str
    summary: str
    markdown_content: str
    items: list[WriterItemDecision] = Field(default_factory=list)


class CuratedImageDecision(BaseModel):
    article_id: int
    keep: bool
    image_url: str | None = None
    image_source_url: str | None = None
    image_origin_type: str | None = None
    image_relevance_score: float = 0.0
    image_caption: str | None = None
    image_license_note: str | None = None
    visual_verdict: str | None = None
    context_verdict: str | None = None
    selected_for_publish: bool = False
    image_reason: str | None = None
    rationale: str | None = None


class ImageCuratorOutput(BaseModel):
    selections: list[CuratedImageDecision] = Field(default_factory=list)


class SupervisorOutput(BaseModel):
    action: str
    rationale: str | None = None
    round_goal: str | None = None
    preferred_sections: list[str] = Field(default_factory=list)
    allow_borderline: bool = False


class ReportLLMService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ):
        self.api_key = api_key or settings.openrouter_api_key
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.primary_model = primary_model or settings.report_primary_model
        self.fallback_model = fallback_model or settings.report_fallback_model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def plan_queries(
        self,
        target_date: date,
        sources: list[dict[str, Any]],
        section_meta: dict[str, dict[str, Any]],
        runtime: dict[str, Any],
    ) -> tuple[PlannerOutput | None, dict[str, Any]]:
        system_prompt = (
            "你是高分子材料加工日报的检索规划器。"
            "输出必须是 JSON 对象，只能包含 queries 和 priority_domains。"
            "query 总数不要超过 12。"
            "industry 最多 5 条，policy 最多 4 条，academic 最多 3 条。"
            "language 只能是 zh 或 en。"
            "query 要适合 Brave 搜索，不要带解释性文字。"
            "优先中文产业和政策可信来源，同时保留少量高价值学术分支。"
            "不要生成泛财经、泛社会、泛消费电子或明显跑题的检索词。"
            '示例: {"queries":[{"section":"industry","language":"zh","query":"高分子 材料 企业 扩产 设备","rationale":"覆盖中文产业动态"}],"priority_domains":["gov.cn","miit.gov.cn"]}'
        )
        user_payload = {
            "date": target_date.isoformat(),
            "sections": section_meta,
            "sources": sources,
            "constraints": {
                "max_queries": 12,
                "section_query_limits": {"industry": 5, "policy": 4, "academic": 3},
                "preferred_languages": ["zh", "en"],
                "priority": "favor Chinese industry and policy coverage while keeping one academic branch",
                "topic_slots": [
                    "材料与改性",
                    "注塑/挤出/吹塑设备",
                    "回收与循环经济",
                    "标准与政策",
                    "企业扩产/投产",
                    "加工相关学术",
                ],
            },
        }
        return await self._invoke_structured(
            "planner",
            system_prompt,
            user_payload,
            PlannerOutput,
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            temperature=0.2,
        )

    async def score_articles(
        self,
        target_date: date,
        articles: list[dict[str, Any]],
        runtime: dict[str, Any],
    ) -> tuple[ScorerOutput | None, dict[str, Any]]:
        system_prompt = (
            "你是高分子材料加工日报的候选评分器。"
            "输出必须是 JSON 对象，只能包含 decisions。"
            "对每篇 article 给出 keep、section、五项 0 到 1 的分数、combined_score、rationale、research_signal。"
            "section 只能是 academic、industry、policy。"
            "combined_score 需要综合时效、相关性、来源可信度、研究价值、信息新意。"
            "必须显式压低市场预测稿、PR 稿、泛财经稿、体育社会新闻和弱相关科技稿。"
            '示例: {"decisions":[{"article_id":1,"section":"policy","keep":true,"freshness_score":0.8,"relevance_score":0.8,"source_trust_score":0.9,"research_value_score":0.6,"novelty_score":0.5,"combined_score":0.72,"rationale":"与塑料回收政策直接相关","research_signal":"关注对高分子回收工艺和材料合规的影响"}]}'
        )
        user_payload = {
            "date": target_date.isoformat(),
            "articles": articles,
            "policy": {
                "prefer_chinese_industry_and_policy": True,
                "keep_max_candidates": min(len(articles), 9),
            },
        }
        return await self._invoke_structured(
            "scorer",
            system_prompt,
            user_payload,
            ScorerOutput,
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            temperature=0.1,
        )

    async def supervise_round(
        self,
        target_date: date,
        round_payload: dict[str, Any],
        runtime: dict[str, Any],
    ) -> tuple[SupervisorOutput | None, dict[str, Any]]:
        system_prompt = (
            "你是高分子材料加工图文日报的 Supervisor。"
            "你不能自由扩展动作，只能选择 stop_and_publish、retry_for_policy、retry_for_images、retry_for_quality 其中一个 action。"
            "只有当首轮未达到内容或图片目标时，才允许触发第二轮。"
            "输出必须是 JSON 对象，只能包含 action、rationale、round_goal、preferred_sections、allow_borderline。"
        )
        user_payload = {
            "date": target_date.isoformat(),
            "round_state": round_payload,
            "constraints": {
                "max_rounds": 2,
                "allowed_actions": ["stop_and_publish", "retry_for_policy", "retry_for_images", "retry_for_quality"],
            },
        }
        return await self._invoke_structured(
            "supervisor",
            system_prompt,
            user_payload,
            SupervisorOutput,
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            temperature=0.1,
        )

    async def research_candidates(
        self,
        target_date: date,
        candidates: list[dict[str, Any]],
        runtime: dict[str, Any],
    ) -> tuple[ResearcherOutput | None, dict[str, Any]]:
        system_prompt = (
            "你是高分子材料加工日报的研究员。"
            "你只能对候选进行理解、筛选和图片检索提示，不直接写日报。"
            "输出必须是 JSON 对象，只能包含 decisions。"
            "对每个 candidate 给出 keep、section、priority_score、rationale、image_query。"
            "section 只能是 academic、industry、policy。"
            "必须优先保留强相关、可追溯、适合做图文日报的候选。"
            "对明显无图价值、跑题、PR、财经和弱相关内容必须拒绝。"
        )
        user_payload = {
            "date": target_date.isoformat(),
            "candidates": candidates,
            "requirements": {
                "goal": "support a visual daily report with at least three usable images when possible",
                "sections": ["industry", "policy", "academic"],
            },
        }
        return await self._invoke_structured(
            "researcher",
            system_prompt,
            user_payload,
            ResearcherOutput,
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            temperature=0.25,
        )

    async def curate_images(
        self,
        target_date: date,
        articles: list[dict[str, Any]],
        runtime: dict[str, Any],
    ) -> tuple[ImageCuratorOutput | None, dict[str, Any]]:
        system_prompt = (
            "你是高分子材料加工日报的图片编辑。"
            "输出必须是 JSON 对象，只能包含 selections。"
            "每条 selection 必须引用 article_id，并对候选图片做 keep、image_url、image_source_url、image_origin_type、image_relevance_score、image_caption、image_license_note。"
            "禁止选择验证码、logo、缩略图、装饰图和明显无关图片。"
            "优先文章原图、官方图，其次可信相关配图。"
        )
        vision_inputs: list[dict[str, str]] = []
        for article in articles[:8]:
            for index, candidate in enumerate((article.get("candidates") or [])[:3], start=1):
                image_url = str(candidate.get("image_url") or "").strip()
                if not image_url:
                    continue
                vision_inputs.append(
                    {
                        "label": (
                            f"article_id={article.get('article_id')} "
                            f"candidate={index} "
                            f"title={article.get('title') or ''} "
                            f"source_url={article.get('source_url') or ''} "
                            f"summary={article.get('summary') or ''}"
                        ),
                        "image_url": image_url,
                    }
                )

        user_payload = {
            "date": target_date.isoformat(),
            "articles": articles,
            "requirements": {
                "minimum_images_for_complete": 3,
                "allowed_origin_types": ["article_inline", "og_image", "official_related", "trusted_related"],
            },
            "__vision_inputs__": vision_inputs,
        }
        return await self._invoke_structured(
            "image_curator",
            system_prompt,
            user_payload,
            ImageCuratorOutput,
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            temperature=0.2,
        )

    async def write_report(
        self,
        target_date: date,
        report_title: str,
        articles: list[dict[str, Any]],
        runtime: dict[str, Any],
    ) -> tuple[WriterOutput | None, dict[str, Any]]:
        system_prompt = (
            "你是高分子材料加工日报写作器。"
            "输出必须是 JSON 对象，只能包含 title、summary、markdown_content、items。"
            "markdown_content 必须是一篇可直接展示的中文日报，带 section 标题。"
            "items 中每一项必须引用输入 article_id，rank 从 1 开始。"
            "宁缺毋滥，只保留高相关和可信来源条目，不要为了凑数扩写弱相关内容。"
            '示例: {"title":"高分子加工全视界日报（2026-03-24）","summary":"覆盖 3 个板块，共 6 条资讯","markdown_content":"# ...","items":[{"article_id":1,"section":"industry","rank":1,"summary":"设备升级关注注塑节拍与能效","research_signal":"关注设备参数对加工窗口的影响"}]}'
        )
        user_payload = {
            "date": target_date.isoformat(),
            "report_title": report_title,
            "articles": articles,
            "requirements": {
                "language": "zh-CN",
                "must_include_citations": True,
                "must_cover_at_least_two_sections_when_possible": True,
            },
        }
        return await self._invoke_structured(
            "writer",
            system_prompt,
            user_payload,
            WriterOutput,
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            temperature=0.3,
        )

    async def _invoke_structured(
        self,
        stage_name: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[BaseModel],
        primary_model: str,
        fallback_model: str,
        temperature: float,
    ) -> tuple[BaseModel | None, dict[str, Any]]:
        provider_errors: list[str] = []
        if not self.enabled:
            return None, {"used_model": None, "provider_errors": provider_errors, "fallback_triggered": True}

        models = [primary_model, fallback_model]
        deduped_models: list[str] = []
        for model in models:
            if model and model not in deduped_models:
                deduped_models.append(model)

        fallback_triggered = False
        for index, model in enumerate(deduped_models):
            if index > 0:
                fallback_triggered = True
            try:
                raw = await self._chat_completion(model, system_prompt, user_payload, temperature)
                payload = json.loads(self._extract_json_object(raw))
                normalized = self._normalize_stage_payload(stage_name, payload, user_payload)
                parsed = schema.model_validate(normalized)
                return parsed, {
                    "used_model": model,
                    "provider_errors": provider_errors,
                    "fallback_triggered": fallback_triggered,
                    "stage": stage_name,
                }
            except (httpx.HTTPError, ValidationError, ValueError, KeyError, json.JSONDecodeError) as exc:
                provider_errors.append(f"{stage_name}:{model}:{type(exc).__name__}:{exc}")

        return None, {
            "used_model": None,
            "provider_errors": provider_errors,
            "fallback_triggered": True,
            "stage": stage_name,
        }

    async def _chat_completion(
        self,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://workflow-news.local",
            "X-Title": "workflow_news",
        }
        user_message: dict[str, Any] = {
            "role": "user",
            "content": (
                "只返回 JSON 对象，不要使用 markdown 代码块。\n"
                + json.dumps(user_payload, ensure_ascii=False)
            ),
        }
        if user_payload.get("__vision_inputs__"):
            content: list[dict[str, Any]] = [
                {"type": "text", "text": user_message["content"]},
            ]
            for item in user_payload["__vision_inputs__"]:
                image_url = str(item.get("image_url") or "").strip()
                label = str(item.get("label") or "").strip()
                if label:
                    content.append({"type": "text", "text": label})
                if image_url:
                    content.append({"type": "image_url", "image_url": {"url": image_url}})
            user_message = {"role": "user", "content": content}
        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
        }
        async with httpx.AsyncClient(timeout=settings.openrouter_timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

    def _extract_json_object(self, raw: str) -> str:
        text = raw.strip()
        fenced = re.search(r"```json\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1)

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in model output")
        return text[start : end + 1]

    def _normalize_stage_payload(self, stage_name: str, payload: Any, user_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError(f"{stage_name} payload is not a JSON object")
        if stage_name == "planner":
            return self._normalize_planner_payload(payload, user_payload)
        if stage_name == "supervisor":
            return self._normalize_supervisor_payload(payload)
        if stage_name == "researcher":
            return self._normalize_researcher_payload(payload, user_payload)
        if stage_name == "scorer":
            return self._normalize_scorer_payload(payload, user_payload)
        if stage_name == "image_curator":
            return self._normalize_image_curator_payload(payload, user_payload)
        if stage_name == "writer":
            return self._normalize_writer_payload(payload, user_payload)
        return payload

    def _normalize_planner_payload(self, payload: dict[str, Any], user_payload: dict[str, Any]) -> dict[str, Any]:
        section_names = list(user_payload.get("sections", {}).keys()) or ["academic", "industry", "policy"]
        raw_queries = payload.get("queries", [])
        queries: list[dict[str, str]] = []

        if isinstance(raw_queries, dict):
            entries = []
            for section, rows in raw_queries.items():
                if isinstance(rows, list):
                    for row in rows:
                        entries.append((section, row))
                else:
                    entries.append((section, rows))
            for section, row in entries:
                normalized = self._normalize_planner_query(
                    row,
                    section_names=section_names,
                    fallback_section=section,
                    position=len(queries),
                    total=max(len(entries), 1),
                )
                if normalized:
                    queries.append(normalized)
        elif isinstance(raw_queries, list):
            total = max(len(raw_queries), 1)
            for index, row in enumerate(raw_queries):
                normalized = self._normalize_planner_query(
                    row,
                    section_names=section_names,
                    fallback_section=None,
                    position=index,
                    total=total,
                )
                if normalized:
                    queries.append(normalized)

        priority_domains: list[str] = []
        for row in payload.get("priority_domains", []):
            if isinstance(row, str) and row.strip():
                priority_domains.append(row.strip())
                continue
            if isinstance(row, dict):
                domain = row.get("domain") or row.get("host")
                if isinstance(domain, str) and domain.strip():
                    priority_domains.append(domain.strip())

        preferred_sections = []
        for row in payload.get("preferred_sections", []) or []:
            section = self._normalize_section(row, default=None)
            if section and section not in preferred_sections:
                preferred_sections.append(section)

        return {
            "queries": queries,
            "priority_domains": priority_domains,
            "round_goal": str(payload.get("round_goal") or payload.get("goal") or "").strip() or None,
            "focus_mode": str(payload.get("focus_mode") or payload.get("focus") or "").strip() or None,
            "preferred_sections": preferred_sections,
            "section_targets": {
                self._normalize_section(key, default="industry"): int(value)
                for key, value in (payload.get("section_targets") or {}).items()
                if isinstance(value, (int, float))
            },
            "image_targets": {
                self._normalize_section(key, default="industry"): int(value)
                for key, value in (payload.get("image_targets") or {}).items()
                if isinstance(value, (int, float))
            },
        }

    def _normalize_supervisor_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or payload.get("decision") or "stop_and_publish").strip()
        if action not in {"stop_and_publish", "retry_for_policy", "retry_for_images", "retry_for_quality"}:
            action = "stop_and_publish"
        preferred_sections = []
        for row in payload.get("preferred_sections", payload.get("sections", [])) or []:
            section = self._normalize_section(row, default=None)
            if section and section not in preferred_sections:
                preferred_sections.append(section)
        return {
            "action": action,
            "rationale": str(payload.get("rationale") or payload.get("reason") or "").strip() or None,
            "round_goal": str(payload.get("round_goal") or payload.get("goal") or "").strip() or None,
            "preferred_sections": preferred_sections,
            "allow_borderline": self._coerce_keep_value({"keep": payload.get("allow_borderline")}),
        }

    def _normalize_planner_query(
        self,
        row: Any,
        section_names: list[str],
        fallback_section: str | None,
        position: int,
        total: int,
    ) -> dict[str, str] | None:
        query = ""
        language = ""
        rationale = ""
        section = self._normalize_section(
            fallback_section,
            default=self._section_for_position(position, total, section_names),
        )

        if isinstance(row, str):
            query = row.strip()
        elif isinstance(row, dict):
            query = str(row.get("query") or row.get("text") or row.get("keyword") or "").strip()
            language = str(row.get("language") or row.get("lang") or "").strip().lower()
            rationale = str(row.get("rationale") or row.get("reason") or row.get("why") or "").strip()
            section = self._normalize_section(row.get("section") or row.get("category") or row.get("bucket"), default=section)
        elif isinstance(row, (list, tuple)) and row:
            if len(row) >= 2 and isinstance(row[0], str) and row[0].lower() in {"zh", "en"}:
                language = row[0].lower()
                query = str(row[1]).strip()
                if len(row) >= 3:
                    rationale = str(row[2]).strip()
            else:
                query = str(row[-1]).strip()
                if len(row) >= 2 and isinstance(row[0], str):
                    maybe_section = self._normalize_section(row[0], default=section)
                    if maybe_section != section:
                        section = maybe_section

        if not query:
            return None
        if not language:
            language = self._infer_language(query)
        if not rationale:
            rationale = f"补充 {section} 板块检索覆盖"

        return {
            "section": section,
            "language": language,
            "query": query,
            "rationale": rationale,
        }

    def _normalize_scorer_payload(self, payload: dict[str, Any], user_payload: dict[str, Any]) -> dict[str, Any]:
        article_lookup = {row["article_id"]: row for row in user_payload.get("articles", []) if row.get("article_id") is not None}
        raw_decisions = payload.get("decisions", payload.get("articles", []))
        decisions: list[dict[str, Any]] = []

        if isinstance(raw_decisions, dict):
            flattened = []
            for key, rows in raw_decisions.items():
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict) and "section" not in row:
                            row = {**row, "section": key}
                        flattened.append(row)
            raw_decisions = flattened

        if not isinstance(raw_decisions, list):
            raw_decisions = []

        for row in raw_decisions:
            normalized = self._normalize_scorer_decision(row, article_lookup)
            if normalized:
                decisions.append(normalized)

        return {"decisions": decisions}

    def _normalize_researcher_payload(self, payload: dict[str, Any], user_payload: dict[str, Any]) -> dict[str, Any]:
        candidate_lookup = {row["candidate_id"]: row for row in user_payload.get("candidates", []) if row.get("candidate_id") is not None}
        raw_decisions = payload.get("decisions", payload.get("candidates", []))
        decisions: list[dict[str, Any]] = []
        if not isinstance(raw_decisions, list):
            raw_decisions = []
        for row in raw_decisions:
            if not isinstance(row, dict):
                continue
            try:
                candidate_id = int(row.get("candidate_id") or row.get("id"))
            except (TypeError, ValueError):
                continue
            candidate = candidate_lookup.get(candidate_id, {})
            keep = self._coerce_keep_value(row)
            priority_score = self._coerce_score(
                row.get("priority_score") or row.get("score") or row.get("confidence"),
                default=0.75 if keep else 0.25,
            )
            decisions.append(
                {
                    "candidate_id": candidate_id,
                    "section": self._normalize_section(row.get("section") or row.get("category"), default=candidate.get("section", "industry")),
                    "keep": keep,
                    "priority_score": priority_score,
                    "rationale": str(row.get("rationale") or row.get("reason") or "").strip() or None,
                    "image_query": str(row.get("image_query") or row.get("image_hint") or candidate.get("title") or "").strip() or None,
                }
            )
        return {"decisions": decisions}

    def _normalize_image_curator_payload(self, payload: dict[str, Any], user_payload: dict[str, Any]) -> dict[str, Any]:
        article_lookup = {row["article_id"]: row for row in user_payload.get("articles", []) if row.get("article_id") is not None}
        raw_rows = payload.get("selections", payload.get("images", []))
        selections: list[dict[str, Any]] = []
        if not isinstance(raw_rows, list):
            raw_rows = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            try:
                article_id = int(row.get("article_id") or row.get("id"))
            except (TypeError, ValueError):
                continue
            article = article_lookup.get(article_id, {})
            image_url = str(row.get("image_url") or row.get("url") or "").strip() or None
            image_source_url = str(row.get("image_source_url") or row.get("source_url") or article.get("source_url") or "").strip() or None
            image_origin_type = str(row.get("image_origin_type") or row.get("origin_type") or "og_image").strip() or "og_image"
            selections.append(
                {
                    "article_id": article_id,
                    "keep": self._coerce_keep_value(row) if image_url else False,
                    "image_url": image_url,
                    "image_source_url": image_source_url,
                    "image_origin_type": image_origin_type,
                    "image_relevance_score": self._coerce_score(row.get("image_relevance_score") or row.get("score"), default=0.82 if image_url else 0.0),
                    "image_caption": str(row.get("image_caption") or row.get("caption") or article.get("title") or "").strip() or None,
                    "image_license_note": str(row.get("image_license_note") or row.get("license_note") or "来源可追溯").strip() or None,
                    "visual_verdict": str(row.get("visual_verdict") or row.get("visual_check") or "").strip() or ("pass" if image_url else "reject"),
                    "context_verdict": str(row.get("context_verdict") or row.get("context_check") or "").strip() or ("pass" if image_url else "reject"),
                    "selected_for_publish": self._coerce_keep_value({"keep": row.get("selected_for_publish", row.get("keep"))}) if image_url else False,
                    "image_reason": str(row.get("image_reason") or row.get("reason") or "").strip() or None,
                    "rationale": str(row.get("rationale") or row.get("reason") or "").strip() or None,
                }
            )
        return {"selections": selections}

    def _normalize_scorer_decision(self, row: Any, article_lookup: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None

        article_id = row.get("article_id") or row.get("id")
        try:
            article_id = int(article_id)
        except (TypeError, ValueError):
            return None

        article = article_lookup.get(article_id, {})
        section = self._normalize_section(row.get("section") or row.get("category") or row.get("bucket"), default=article.get("section", "industry"))
        keep = self._coerce_keep_value(row)

        score_seed = self._coerce_score(
            row.get("combined_score")
            or row.get("score")
            or row.get("total_score")
            or row.get("confidence")
        )
        if score_seed is None:
            score_seed = 0.75 if keep else 0.25

        freshness_score = self._coerce_score(row.get("freshness_score"), default=score_seed)
        relevance_score = self._coerce_score(row.get("relevance_score"), default=score_seed)
        source_trust_score = self._coerce_score(row.get("source_trust_score"), default=score_seed)
        research_value_score = self._coerce_score(row.get("research_value_score"), default=score_seed)
        novelty_score = self._coerce_score(row.get("novelty_score"), default=score_seed)
        combined_score = self._coerce_score(
            row.get("combined_score"),
            default=round(
                (freshness_score + relevance_score + source_trust_score + research_value_score + novelty_score) / 5,
                3,
            ),
        )

        rationale = str(row.get("rationale") or row.get("reason") or row.get("analysis") or row.get("decision") or "").strip() or None
        research_signal = (
            str(
                row.get("research_signal")
                or row.get("research_note")
                or row.get("signal")
                or article.get("research_signal")
                or ""
            ).strip()
            or None
        )

        return {
            "article_id": article_id,
            "section": section,
            "keep": keep,
            "freshness_score": freshness_score,
            "relevance_score": relevance_score,
            "source_trust_score": source_trust_score,
            "research_value_score": research_value_score,
            "novelty_score": novelty_score,
            "combined_score": combined_score,
            "rationale": rationale,
            "research_signal": research_signal,
            "review_label": self._normalize_review_label(row.get("review_label") or row.get("label"), keep),
            "review_reason": str(row.get("review_reason") or row.get("review_rationale") or rationale or "").strip() or None,
        }

    def _normalize_writer_payload(self, payload: dict[str, Any], user_payload: dict[str, Any]) -> dict[str, Any]:
        article_lookup = {row["article_id"]: row for row in user_payload.get("articles", []) if row.get("article_id") is not None}
        report_title = str(user_payload.get("report_title") or "高分子加工全视界日报")
        raw_items = payload.get("items", payload.get("selected_items", []))
        items: list[dict[str, Any]] = []

        if isinstance(raw_items, dict):
            flattened = []
            for key, rows in raw_items.items():
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict) and "section" not in row:
                            row = {**row, "section": key}
                        flattened.append(row)
            raw_items = flattened

        if not isinstance(raw_items, list):
            raw_items = []

        for index, row in enumerate(raw_items, start=1):
            normalized = self._normalize_writer_item(row, index, article_lookup)
            if normalized:
                items.append(normalized)

        if not items:
            for index, article in enumerate(user_payload.get("articles", [])[:5], start=1):
                normalized = self._normalize_writer_item({"article_id": article.get("article_id"), "rank": index}, index, article_lookup)
                if normalized:
                    items.append(normalized)

        markdown_content = str(payload.get("markdown_content") or payload.get("markdown") or "").strip()
        if not markdown_content:
            markdown_content = self._build_fallback_markdown(report_title, items, article_lookup)

        summary = str(payload.get("summary") or "").strip()
        if not summary:
            sections = sorted({item["section"] for item in items})
            summary = f"入选 {len(items)} 条资讯；覆盖 {len(sections)} 个板块"

        title = str(payload.get("title") or report_title).strip() or report_title
        return {
            "title": title,
            "summary": summary,
            "markdown_content": markdown_content,
            "items": items,
        }

    def _normalize_review_label(self, raw_label: Any, keep: bool) -> str:
        label = str(raw_label or "").strip().lower()
        mapping = {
            "publishable": "publishable",
            "accept": "publishable",
            "keep": "publishable",
            "borderline": "borderline",
            "maybe": "borderline",
            "review": "borderline",
            "reject": "reject",
            "drop": "reject",
        }
        if label in mapping:
            return mapping[label]
        return "publishable" if keep else "reject"

    def _normalize_writer_item(
        self,
        row: Any,
        position: int,
        article_lookup: dict[int, dict[str, Any]],
    ) -> dict[str, Any] | None:
        article_id: int | None = None
        rank = position
        section = None
        summary = ""
        research_signal = ""

        if isinstance(row, int):
            article_id = row
        elif isinstance(row, dict):
            try:
                article_id = int(row.get("article_id") or row.get("id"))
            except (TypeError, ValueError):
                return None
            try:
                rank = int(row.get("rank") or position)
            except (TypeError, ValueError):
                rank = position
            section = row.get("section") or row.get("category")
            summary = str(row.get("summary") or row.get("abstract") or row.get("reason") or "").strip()
            research_signal = str(row.get("research_signal") or row.get("signal") or "").strip()
        else:
            return None

        article = article_lookup.get(article_id, {})
        if not article:
            return None

        return {
            "article_id": article_id,
            "section": self._normalize_section(section, default=article.get("section", "industry")),
            "rank": rank,
            "summary": summary or str(article.get("summary") or article.get("title") or "").strip(),
            "research_signal": research_signal or str(article.get("research_signal") or "").strip() or None,
        }

    def _build_fallback_markdown(
        self,
        report_title: str,
        items: list[dict[str, Any]],
        article_lookup: dict[int, dict[str, Any]],
    ) -> str:
        lines = [f"# {report_title}", ""]
        grouped: dict[str, list[dict[str, Any]]] = {"academic": [], "industry": [], "policy": []}
        for item in items:
            grouped.setdefault(item["section"], []).append(item)
        section_titles = {
            "academic": "## 前沿技术与学术",
            "industry": "## 产业动态与设备",
            "policy": "## 政策与下游应用",
        }
        for section in ("academic", "industry", "policy"):
            rows = grouped.get(section, [])
            if not rows:
                continue
            lines.append(section_titles.get(section, f"## {section}"))
            lines.append("")
            for item in sorted(rows, key=lambda row: row["rank"]):
                article = article_lookup.get(item["article_id"], {})
                lines.append(f"### {item['rank']}. {article.get('title', '未命名条目')}")
                lines.append(item["summary"])
                source_name = article.get("source_name") or article.get("domain")
                source_url = article.get("source_url")
                if source_name or source_url:
                    citation_bits = [bit for bit in [source_name, source_url] if bit]
                    lines.append(f"来源：{' | '.join(citation_bits)}")
                lines.append("")
        return "\n".join(lines).strip()

    def _normalize_section(self, value: Any, default: str = "industry") -> str:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"academic", "industry", "policy"}:
                return lowered
            if any(token in lowered for token in {"学术", "academic", "research", "paper"}):
                return "academic"
            if any(token in lowered for token in {"政策", "法规", "标准", "policy", "regulation"}):
                return "policy"
            if any(token in lowered for token in {"产业", "设备", "industry", "equipment", "press"}):
                return "industry"
        return default

    def _section_for_position(self, position: int, total: int, sections: list[str]) -> str:
        if not sections:
            return "industry"
        bucket = min(position * len(sections) // max(total, 1), len(sections) - 1)
        return self._normalize_section(sections[bucket], default="industry")

    def _infer_language(self, text: str) -> str:
        return "zh" if any("\u4e00" <= char <= "\u9fff" for char in text) else "en"

    def _coerce_keep_value(self, row: dict[str, Any]) -> bool:
        for key in ("keep", "selected", "include"):
            value = row.get(key)
            if isinstance(value, bool):
                return value
        decision = str(row.get("decision") or "").strip().lower()
        if decision in {"keep", "include", "selected", "relevant", "yes", "accept"}:
            return True
        if decision in {"drop", "reject", "exclude", "irrelevant", "no", "discard"}:
            return False
        numeric = self._coerce_score(row.get("combined_score") or row.get("score") or row.get("confidence"))
        return bool(numeric is None or numeric >= 0.5)

    def _coerce_score(self, value: Any, default: float | None = None) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            if default is None:
                return None
            return max(0.0, min(1.0, float(default)))
        return max(0.0, min(1.0, numeric))
