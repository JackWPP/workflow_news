from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

MAP_SYSTEM_PROMPT_ZH = """你是高分子材料加工领域的专业研究助理。请对以下文章逐一进行结构化评估。

对每篇文章，输出以下字段：
- index: 文章在输入中的序号（从0开始，对应 [N] 标记）
- quality_score: 0-1之间的质量分
  - 0.8-1.0: 领域高度相关、来源权威、有实质内容
  - 0.5-0.8: 领域相关、来源可信、有一定信息量
  - 0.3-0.5: 边缘相关或内容较浅
  - 0.0-0.3: 不相关或低质量
- section: "industry"（行业动态）/ "policy"（政策法规）/ "academic"（学术研究）
- category: "高材制造" / "清洁能源" / "AI" / "其他"
- key_finding: 一句话概括核心发现（不超过50字）
- relevance_rationale: 简要说明相关性判断理由（不超过30字）

高分子材料加工领域涵盖：塑料、橡胶、纤维、复合材料、薄膜、涂层、注塑、挤出、吹塑、3D打印材料等。
清洁能源材料（电池、光伏、储氢）、AI在材料科学中的应用属于相关交叉领域。
纯金融报道、公司人事变动、与材料完全无关的新闻应打低分。

必须严格输出JSON数组格式，示例：
[{"index": 0, "quality_score": 0.85, "section": "industry", "category": "高材制造", "key_finding": "...", "relevance_rationale": "..."}]"""

MAP_SYSTEM_PROMPT_EN = """You are a research assistant specializing in polymer materials processing. Evaluate each article systematically.

For each article, output:
- index: the article's position in the input (0-based, matching the [N] marker)
- quality_score: 0-1 quality rating
  - 0.8-1.0: highly relevant to polymer/materials, authoritative source, substantial content
  - 0.5-0.8: relevant, credible source, informative
  - 0.3-0.5: marginally relevant or shallow
  - 0.0-0.3: irrelevant or low quality
- section: "industry" / "policy" / "academic"
- category: "高材制造" / "清洁能源" / "AI" / "其他"
- key_finding: one-sentence summary (max 100 chars)
- relevance_rationale: brief reason (max 60 chars)

Polymer materials processing includes: plastics, rubber, fibers, composites, films, coatings, injection molding, extrusion, blow molding, 3D printing materials.
Clean energy materials (batteries, solar, hydrogen storage) and AI in materials science are relevant cross-domains.
Pure financial reports, personnel changes, entirely unrelated news should score low.

Output MUST be a valid JSON array. Example:
[{"index": 1, "quality_score": 0.82, "section": "industry", "category": "高材制造", "key_finding": "...", "relevance_rationale": "..."}]"""

REDUCE_SYSTEM_PROMPT_ZH = """你是高分子材料加工领域的资深编辑。请对以下已评估的文章进行综合排序和去重处理。

任务：
1. 按重要性和质量重新排序
2. 识别语义重复的文章对（同一事件的不同报道，或多平台转载）
3. 对"同一事件不同视角"的文章，判断应合并为一篇还是保留多篇
4. 输出最终排序列表

输出JSON格式：
{
  "ranked": [
    {
      "id": 3,
      "quality_score": 0.88,
      "section": "industry",
      "category": "高材制造",
      "key_finding": "更新的关键发现摘要",
      "rank": 1,
      "duplicate_of": null,
      "merge_note": null
    }
  ],
  "duplicate_groups": [
    {"kept_id": 3, "removed_ids": [7, 12], "reason": "同一事件的不同来源报道"}
  ]
}

排序原则：
- 质量分高 + 领域核心相关 + 时效性强的排前面
- 学术突破 > 重大政策 > 行业趋势 > 一般动态
- 避免同一子话题过度集中（最多3篇讲同一细分话题）
- 中英文内容应都有覆盖

去重原则：
- 同一事件的多家报道，保留质量最高（quality_score最高）的1篇
- 同一研究成果的多平台转载，保留原始来源
- 观点不同或角度互补的分析可保留多篇（在merge_note中说明关系）

直接输出JSON，不要添加任何说明文字。"""

REDUCE_SYSTEM_PROMPT_EN = """You are a senior editor in polymer materials processing. Rank and deduplicate the following evaluated articles.

Tasks:
1. Re-rank articles by importance and quality
2. Identify semantically duplicate article pairs (same event, different outlets, or multi-platform reprints)
3. For "same event, different perspective" articles, decide whether to merge or keep both
4. Output final ranked list

Output JSON format:
{
  "ranked": [
    {
      "id": 0,
      "quality_score": 0.91,
      "section": "academic",
      "category": "高材制造",
      "key_finding": "Updated key finding summary",
      "rank": 1,
      "duplicate_of": null,
      "merge_note": null
    }
  ],
  "duplicate_groups": [
    {"kept_id": 0, "removed_ids": [2], "reason": "Same event covered by different outlets"}
  ]
}

Ranking principles:
- High quality + core relevant + timely articles first
- Academic breakthroughs > major policies > industry trends > general news
- Avoid over-concentration on a single sub-topic (max 3 on the same narrow topic)
- Balance Chinese and English coverage

Dedup principles:
- For same event covered by multiple outlets, keep the highest quality one
- For same research reprinted across platforms, keep the original source
- Articles with different analytical perspectives may both be kept (note the relationship in merge_note)

Output raw JSON only, no explanatory text."""


class BatchEvaluator:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        cheap_model: str = "deepseek/deepseek-chat",
        strong_model: str | None = None,
    ):
        self._shared_client = llm_client
        self._cheap_model = cheap_model
        self._strong_model = strong_model or settings.report_primary_model
        self._map_client: LLMClient | None = None
        self._reduce_client: LLMClient | None = None

    def _get_map_client(self) -> LLMClient:
        if self._map_client is None:
            self._map_client = LLMClient(
                primary_model=self._cheap_model,
                strict_primary_model_for_all_llm=True,
            )
        return self._map_client

    def _get_reduce_client(self) -> LLMClient:
        if self._shared_client is not None:
            return self._shared_client
        if self._reduce_client is None:
            self._reduce_client = LLMClient(
                primary_model=self._strong_model,
                strict_primary_model_for_all_llm=True,
            )
        return self._reduce_client

    async def evaluate_batch(
        self,
        articles: list[dict],
        language: str = "zh",
        batch_size: int = 8,
        max_articles: int = 20,
    ) -> list[dict]:
        if not articles:
            return []

        article_limit = min(len(articles), max_articles * 2)

        batches = [
            articles[i : i + batch_size]
            for i in range(0, article_limit, batch_size)
        ]

        map_client = self._get_map_client()
        tasks = [self._map_articles(map_client, batch, language) for batch in batches]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        evaluated: list[dict] = []
        for result in batch_results:
            if isinstance(result, Exception):
                logger.warning("Map batch failed: %s", result)
                continue
            if isinstance(result, list):
                evaluated.extend(result)

        if not evaluated:
            return []

        reduce_client = self._get_reduce_client()
        ranked = await self._reduce_rank(reduce_client, evaluated, language)

        if not ranked:
            evaluated.sort(key=lambda a: a.get("quality_score", 0), reverse=True)
            return evaluated[:max_articles]

        return ranked[:max_articles]

    async def _map_articles(
        self,
        client: LLMClient,
        batch: list[dict],
        language: str,
    ) -> list[dict]:
        system_prompt = MAP_SYSTEM_PROMPT_ZH if language == "zh" else MAP_SYSTEM_PROMPT_EN

        articles_text_parts: list[str] = []
        for i, article in enumerate(batch):
            title = article.get("title", "")
            domain = article.get("domain", "")
            raw = article.get("raw_content") or article.get("snippet") or article.get("summary") or ""
            if raw:
                raw = raw[:600]
            published = article.get("published_at", "")
            parts = [f"[{i}] {title}"]
            if domain:
                parts.append(f"    来源: {domain}")
            if published:
                parts.append(f"    日期: {published}")
            if raw:
                parts.append(f"    摘要: {raw}")
            articles_text_parts.append("\n".join(parts))

        articles_text = "\n\n".join(articles_text_parts)

        if language == "zh":
            user_content = f"请评估以下{len(batch)}篇文章（领域：高分子材料加工）：\n\n{articles_text}"
        else:
            user_content = f"Evaluate the following {len(batch)} articles (domain: polymer materials processing):\n\n{articles_text}"

        result = await client.simple_json_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=0.1,
        )

        items: list[dict] = []
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            items = result.get("articles", result.get("results", result.get("items", [])))
            if not items and "ranked" not in result:
                items = [result]

        evaluated: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            original: dict = {}
            if isinstance(idx, int) and 0 <= idx < len(batch):
                original = batch[idx]

            evaluated.append({
                "url": original.get("url", item.get("url", "")),
                "title": original.get("title", item.get("title", "")),
                "quality_score": float(item.get("quality_score", 0.0)),
                "section": item.get("section", "industry"),
                "category": item.get("category", "其他"),
                "key_finding": item.get("key_finding", ""),
                "relevance_rationale": item.get("relevance_rationale", ""),
                "domain": original.get("domain", item.get("domain", "")),
                "source_type": original.get("source_type", ""),
                "language": original.get("language", language),
                "published_at": original.get("published_at", ""),
            })

        return evaluated

    async def _reduce_rank(
        self,
        client: LLMClient,
        evaluated: list[dict],
        language: str,
    ) -> list[dict]:
        system_prompt = REDUCE_SYSTEM_PROMPT_ZH if language == "zh" else REDUCE_SYSTEM_PROMPT_EN

        articles_json: list[dict] = []
        for i, article in enumerate(evaluated):
            articles_json.append({
                "id": i,
                "url": article.get("url", ""),
                "title": article.get("title", ""),
                "quality_score": article.get("quality_score", 0),
                "section": article.get("section", ""),
                "category": article.get("category", ""),
                "key_finding": article.get("key_finding", ""),
                "domain": article.get("domain", ""),
            })

        user_content = json.dumps(
            {"articles": articles_json},
            ensure_ascii=False,
            indent=2,
        )

        try:
            result = await client.simple_json_completion(
                system_prompt=system_prompt,
                user_content=user_content,
                temperature=0.1,
            )
        except Exception:
            logger.warning("Reduce ranking failed, using Map scores only")
            return []

        ranked_list = result.get("ranked", [])
        if not ranked_list:
            return []

        id_to_original: dict[int, dict] = {i: article for i, article in enumerate(evaluated)}
        duplicate_of_map: dict[int, int | None] = {}

        duplicate_groups = result.get("duplicate_groups", [])
        if isinstance(duplicate_groups, list):
            for group in duplicate_groups:
                if not isinstance(group, dict):
                    continue
                removed = group.get("removed_ids", [])
                if isinstance(removed, list):
                    for rid in removed:
                        if isinstance(rid, int):
                            duplicate_of_map[rid] = group.get("kept_id")

        output: list[dict] = []
        for item in ranked_list:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            base: dict = {}
            if isinstance(rid, int) and rid in id_to_original:
                base = dict(id_to_original[rid])
            else:
                base["url"] = item.get("url", "")
                base["title"] = item.get("title", "")
                base["domain"] = item.get("domain", "")
                base["source_type"] = ""
                base["language"] = language
                base["published_at"] = ""

            base["quality_score"] = float(item.get("quality_score", base.get("quality_score", 0)))
            base["section"] = item.get("section", base.get("section", "industry"))
            base["category"] = item.get("category", base.get("category", "其他"))
            base["key_finding"] = item.get("key_finding", base.get("key_finding", ""))
            base["rank"] = item.get("rank", len(output) + 1)
            base["duplicate_of"] = item.get("duplicate_of")
            base["merge_note"] = item.get("merge_note")

            output.append(base)

        return output
