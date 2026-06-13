"""
summary_agent.py — 总编辑 Agent

从已评估的卡片生成精美的交互式日报。
职责：总结 + 前瞻 + HTML 生成。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import date
from typing import Any

from app.services.agent_core import AgentCore, AgentResult
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.tools import FinishTool, Tool, ToolCall, ToolResult, WriteSectionTool
from app.services.working_memory import ArticleSummary, WorkingMemory

logger = logging.getLogger(__name__)

_SECTION_LABELS = {
    "industry": "产业动态与设备",
    "policy": "下游应用与政策",
    "academic": "前沿技术与学术",
}

_SECTION_EMOJIS = {
    "industry": "🏭",
    "policy": "📢",
    "academic": "🔬",
}

_CATEGORY_EMOJIS = {
    "高材制造": "🧪",
    "清洁能源": "⚡",
    "AI": "🤖",
}


class SummaryAgent:
    """总编辑 Agent — 生成精美的交互式日报。"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def generate(self, cards: list[dict[str, Any]], run_id: int | None = None) -> dict[str, Any]:
        """
        生成精美的交互式日报。

        输入: 3 个板块的卡片列表
        输出: {
            'html': str,
            'summary': str,
            'foresight': str,
            'trends': list[str],
            'follow_up': list[str]
        }
        """
        if not cards:
            return self._empty_result()

        grouped = self._group_by_section(cards)

        analysis = await self._analyze(grouped, cards)

        html = self._render_html(grouped, cards, analysis)

        return {
            "html": html,
            "summary": analysis.get("summary", ""),
            "foresight": analysis.get("foresight", ""),
            "trends": analysis.get("trends", []),
            "follow_up": analysis.get("follow_up", []),
        }

    def _group_by_section(self, cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for card in cards:
            section = card.get("section") or "industry"
            grouped[section].append(card)
        return dict(grouped)

    async def _analyze(
        self,
        grouped: dict[str, list[dict[str, Any]]],
        all_cards: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self._llm.enabled:
            return self._heuristic_analysis(grouped)

        article_digest = "\n".join(
            f"- [{c.get('section', '?')}] {c.get('title', '')}（{c.get('domain', '')}）：{c.get('key_finding', '') or c.get('summary', '')[:80]}"
            for c in all_cards[:12]
        )

        system_prompt = (
            "你是高分子材料加工领域的资深总编辑。\n"
            "基于今日已筛选的文章卡片，生成以下内容：\n"
            "1. summary: 200字以内的总结性分析，概括今日行业动态全貌\n"
            "2. foresight: 100字以内的前瞻性洞察，基于当前信号预测未来动向\n"
            "3. trends: 3-5条趋势判断（每条15字以内）\n"
            "4. follow_up: 3-5条后续追踪建议（每条20字以内）\n\n"
            "要求：\n"
            "- 术语准确，中文输出，无「机翻感」\n"
            "- 不得补充素材中没有的事实、数字或预测\n"
            "- 如果素材不足以支撑趋势判断，仅做事实概括\n\n"
            "输出 JSON：{\"summary\": \"...\", \"foresight\": \"...\", \"trends\": [\"...\"], \"follow_up\": [\"...\"]}"
        )

        try:
            result = await asyncio.wait_for(
                self._llm.simple_json_completion(system_prompt, article_digest, temperature=0.3),
                timeout=30.0,
            )
            return {
                "summary": result.get("summary", ""),
                "foresight": result.get("foresight", ""),
                "trends": result.get("trends", []) if isinstance(result.get("trends"), list) else [],
                "follow_up": result.get("follow_up", []) if isinstance(result.get("follow_up"), list) else [],
            }
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("SummaryAgent LLM analysis failed: %s", exc)
            return self._heuristic_analysis(grouped)

    @staticmethod
    def _heuristic_analysis(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        section_counts = {s: len(items) for s, items in grouped.items()}
        total = sum(section_counts.values())

        parts = []
        for section, count in section_counts.items():
            label = _SECTION_LABELS.get(section, section)
            parts.append(f"{label} {count} 条")
        summary = f"今日共收录 {total} 篇文章，覆盖{'、'.join(parts)}。"

        trends = []
        if section_counts.get("industry", 0) >= 2:
            trends.append("产业动态持续活跃")
        if section_counts.get("academic", 0) >= 2:
            trends.append("学术研究产出稳定")
        if section_counts.get("policy", 0) >= 1:
            trends.append("政策环境有所变化")

        return {
            "summary": summary,
            "foresight": "建议持续关注行业动态变化，把握技术发展趋势。",
            "trends": trends or ["行业整体保持平稳"],
            "follow_up": ["持续跟踪今日重点话题", "关注相关学术进展"],
        }

    def _render_html(
        self,
        grouped: dict[str, list[dict[str, Any]]],
        all_cards: list[dict[str, Any]],
        analysis: dict[str, Any],
    ) -> str:
        today = date.today().isoformat()
        trends_html = "\n".join(
            f'<li style="margin-bottom:6px;">{t}</li>' for t in analysis.get("trends", [])
        )
        follow_up_html = "\n".join(
            f'<li style="margin-bottom:6px;">{f}</li>' for f in analysis.get("follow_up", [])
        )

        nav_items = []
        section_blocks = []
        for section in ("industry", "policy", "academic"):
            items = grouped.get(section, [])
            if not items:
                continue
            label = _SECTION_LABELS.get(section, section)
            emoji = _SECTION_EMOJIS.get(section, "")
            nav_items.append(
                f'<a href="#section-{section}" style="color:#fff;text-decoration:none;'
                f'padding:6px 14px;border-radius:4px;background:rgba(255,255,255,0.15);'
                f'font-size:14px;">{emoji} {label}</a>'
            )

            cards_html = self._render_cards(items)
            section_blocks.append(
                f'<div id="section-{section}" style="margin-bottom:32px;">'
                f'<h2 style="font-size:20px;color:#1a1a2e;border-bottom:2px solid #e94560;'
                f'padding-bottom:8px;margin-bottom:16px;">{emoji} {label}</h2>'
                f'{cards_html}</div>'
            )

        nav_html = "\n".join(nav_items)
        sections_html = "\n".join(section_blocks)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>高分子加工全视界日报 — {today}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#f5f6fa; color:#1a1a2e; line-height:1.6; }}
  .container {{ max-width:960px; margin:0 auto; padding:20px; }}
  .header {{ background:linear-gradient(135deg,#0f3460,#16213e); color:#fff;
             padding:28px 24px; border-radius:12px; margin-bottom:24px; }}
  .header h1 {{ font-size:24px; margin-bottom:8px; }}
  .header .date {{ font-size:14px; opacity:0.8; }}
  .nav {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:24px; }}
  .card {{ background:#fff; border-radius:10px; padding:18px; margin-bottom:14px;
           box-shadow:0 2px 8px rgba(0,0,0,0.06); transition:transform 0.15s; }}
  .card:hover {{ transform:translateY(-2px); box-shadow:0 4px 16px rgba(0,0,0,0.1); }}
  .card-title {{ font-size:16px; font-weight:600; color:#0f3460; margin-bottom:6px; }}
  .card-title a {{ color:#0f3460; text-decoration:none; }}
  .card-title a:hover {{ text-decoration:underline; }}
  .card-meta {{ font-size:12px; color:#888; margin-bottom:8px; }}
  .card-summary {{ font-size:14px; color:#333; margin-bottom:8px; }}
  .card-reason {{ font-size:12px; color:#e94560; background:#fff5f5; padding:6px 10px;
                  border-radius:6px; margin-bottom:10px; }}
  .card-img {{ width:100%; max-height:200px; object-fit:cover; border-radius:8px; margin-bottom:10px; }}
  .card-keywords {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .card-keywords span {{ background:#e8f4f8; color:#0f3460; padding:2px 8px; border-radius:4px;
                          font-size:11px; }}
  .insight-box {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:20px;
                  box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
  .insight-box h3 {{ font-size:16px; color:#0f3460; margin-bottom:10px; }}
  .category-badge {{ display:inline-block; font-size:11px; padding:2px 8px; border-radius:4px;
                     margin-right:6px; }}
  .cat-高材制造 {{ background:#e8f5e9; color:#2e7d32; }}
  .cat-清洁能源 {{ background:#e3f2fd; color:#1565c0; }}
  .cat-AI {{ background:#f3e5f5; color:#7b1fa2; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>高分子加工全视界日报</h1>
    <div class="date">{today}</div>
  </div>

  <div class="nav">{nav_html}</div>

  <div class="insight-box">
    <h3>📊 今日总结</h3>
    <p style="font-size:14px;color:#333;">{analysis.get("summary", "")}</p>
  </div>

  {sections_html}

  <div class="insight-box">
    <h3>🔭 前瞻洞察</h3>
    <p style="font-size:14px;color:#333;margin-bottom:12px;">{analysis.get("foresight", "")}</p>
  </div>

  <div style="display:flex;gap:16px;flex-wrap:wrap;">
    <div class="insight-box" style="flex:1;min-width:280px;">
      <h3>📈 趋势判断</h3>
      <ul style="font-size:14px;color:#333;padding-left:18px;">{trends_html}</ul>
    </div>
    <div class="insight-box" style="flex:1;min-width:280px;">
      <h3>🎯 后续追踪</h3>
      <ul style="font-size:14px;color:#333;padding-left:18px;">{follow_up_html}</ul>
    </div>
  </div>

  <div style="text-align:center;padding:20px;color:#aaa;font-size:12px;">
    Generated by 高分子加工全视界 · {today}
  </div>
</div>
</body>
</html>"""

    def _render_cards(self, cards: list[dict[str, Any]]) -> str:
        parts = []
        for card in cards:
            title = card.get("title", "无标题")
            url = card.get("url") or card.get("resolved_url") or "#"
            domain = card.get("domain", "")
            summary = card.get("summary", "")
            key_finding = card.get("key_finding", "")
            evaluation_reason = card.get("evaluation_reason", "")
            image_url = card.get("image_url")
            category = card.get("category", "")
            keywords = card.get("keywords", [])

            img_html = ""
            if image_url:
                img_html = f'<img src="{image_url}" class="card-img" alt="{title}" onerror="this.style.display=\'none\'">'

            cat_badge = ""
            if category:
                cat_emoji = _CATEGORY_EMOJIS.get(category, "")
                cat_badge = f'<span class="category-badge cat-{category}">{cat_emoji} {category}</span>'

            reason_html = ""
            if evaluation_reason:
                reason_html = f'<div class="card-reason">💡 {evaluation_reason}</div>'

            keywords_html = ""
            if keywords:
                kw_items = "".join(f"<span>{kw}</span>" for kw in keywords[:5])
                keywords_html = f'<div class="card-keywords">{kw_items}</div>'

            finding_text = key_finding or summary[:100]

            parts.append(
                f'<div class="card">'
                f'{img_html}'
                f'<div class="card-title"><a href="{url}" target="_blank">{title}</a></div>'
                f'<div class="card-meta">{cat_badge}{domain}</div>'
                f'<div class="card-summary">{finding_text}</div>'
                f'{reason_html}'
                f'{keywords_html}'
                f'</div>'
            )
        return "\n".join(parts)

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {
            "html": "",
            "summary": "今日无可用文章。",
            "foresight": "",
            "trends": [],
            "follow_up": [],
        }
