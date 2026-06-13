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
        total = len(all_cards)
        section_counts = {s: len(grouped.get(s, [])) for s in ("industry", "policy", "academic")}
        source_tiers = {}
        for c in all_cards:
            t = c.get("source_tier", "?")
            source_tiers[t] = source_tiers.get(t, 0) + 1

        trends_html = "\n".join(
            f'<div class="trend-item"><span class="trend-icon">▸</span>{t}</div>'
            for t in analysis.get("trends", [])
        )
        follow_up_html = "\n".join(
            f'<div class="follow-item"><span class="follow-icon">→</span>{f}</div>'
            for f in analysis.get("follow_up", [])
        )

        nav_items = []
        section_blocks = []
        for section in ("industry", "policy", "academic"):
            items = grouped.get(section, [])
            if not items:
                continue
            label = _SECTION_LABELS.get(section, section)
            emoji = _SECTION_EMOJIS.get(section, "")
            count = len(items)
            nav_items.append(
                f'<a href="#section-{section}" class="nav-pill">'
                f'{emoji} {label} <span class="nav-count">{count}</span></a>'
            )
            cards_html = self._render_cards(items)
            section_blocks.append(
                f'<section id="section-{section}" class="section-block">'
                f'<div class="section-header">'
                f'<h2>{emoji} {label}</h2>'
                f'<span class="section-count">{count} 条</span>'
                f'</div>'
                f'<div class="cards-grid">{cards_html}</div>'
                f'</section>'
            )

        nav_html = "\n".join(nav_items)
        sections_html = "\n".join(section_blocks)
        summary_text = analysis.get("summary", "")
        foresight_text = analysis.get("foresight", "")

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>高分子加工全视界日报 — {today}</title>
<style>
  :root {{
    --bg: #0f0f1a;
    --card-bg: #1a1a2e;
    --card-hover: #222240;
    --accent: #e94560;
    --accent2: #0f3460;
    --text: #e8e8e8;
    --text-dim: #8888aa;
    --text-bright: #ffffff;
    --border: rgba(255,255,255,0.06);
    --glow: rgba(233,69,96,0.15);
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html {{ scroll-behavior:smooth; }}
  body {{ font-family:'Inter','Noto Sans SC',-apple-system,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.7; }}
  .container {{ max-width:1080px; margin:0 auto; padding:24px 20px; }}

  /* Header */
  .header {{
    background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
    border:1px solid var(--border); border-radius:16px;
    padding:36px 32px; margin-bottom:28px; position:relative; overflow:hidden;
  }}
  .header::before {{
    content:''; position:absolute; top:-50%; right:-20%; width:300px; height:300px;
    background:radial-gradient(circle,rgba(233,69,96,0.08) 0%,transparent 70%);
  }}
  .header h1 {{ font-size:28px; font-weight:700; color:var(--text-bright); margin-bottom:6px; position:relative; }}
  .header .date {{ font-size:14px; color:var(--text-dim); position:relative; }}
  .header .stats {{
    display:flex; gap:24px; margin-top:20px; position:relative;
  }}
  .stat {{ text-align:center; }}
  .stat-num {{ font-size:28px; font-weight:700; color:var(--accent); }}
  .stat-label {{ font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:1px; }}

  /* Navigation */
  .nav {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:28px; }}
  .nav-pill {{
    display:inline-flex; align-items:center; gap:6px;
    padding:8px 16px; border-radius:20px; font-size:13px; font-weight:500;
    background:var(--card-bg); color:var(--text); text-decoration:none;
    border:1px solid var(--border); transition:all 0.2s;
  }}
  .nav-pill:hover {{ background:var(--card-hover); border-color:var(--accent); color:var(--text-bright); }}
  .nav-count {{
    background:var(--accent); color:#fff; font-size:11px; font-weight:600;
    padding:1px 7px; border-radius:10px;
  }}

  /* Summary Box */
  .summary-box {{
    background:linear-gradient(135deg,var(--card-bg),#1e1e38);
    border:1px solid var(--border); border-radius:14px;
    padding:24px 28px; margin-bottom:28px;
  }}
  .summary-box h3 {{ font-size:15px; color:var(--accent); margin-bottom:12px; font-weight:600; }}
  .summary-box p {{ font-size:14px; color:var(--text); line-height:1.8; }}

  /* Section */
  .section-block {{ margin-bottom:36px; }}
  .section-header {{
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid var(--border); padding-bottom:12px; margin-bottom:18px;
  }}
  .section-header h2 {{ font-size:18px; color:var(--text-bright); font-weight:600; }}
  .section-count {{ font-size:12px; color:var(--text-dim); }}

  /* Cards Grid */
  .cards-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:16px; }}

  /* Card */
  .card {{
    background:var(--card-bg); border:1px solid var(--border); border-radius:12px;
    padding:20px; transition:all 0.25s; cursor:pointer; position:relative; overflow:hidden;
  }}
  .card::before {{
    content:''; position:absolute; top:0; left:0; right:0; height:3px;
    background:linear-gradient(90deg,var(--accent),var(--accent2)); opacity:0; transition:opacity 0.25s;
  }}
  .card:hover {{
    transform:translateY(-3px); border-color:rgba(233,69,96,0.3);
    box-shadow:0 8px 32px rgba(0,0,0,0.3),0 0 20px var(--glow);
  }}
  .card:hover::before {{ opacity:1; }}
  .card-img {{
    width:100%; height:160px; object-fit:cover; border-radius:8px; margin-bottom:14px;
  }}
  .card-title {{
    font-size:15px; font-weight:600; color:var(--text-bright); margin-bottom:8px;
    line-height:1.4;
  }}
  .card-title a {{ color:var(--text-bright); text-decoration:none; }}
  .card-title a:hover {{ color:var(--accent); }}
  .card-meta {{ font-size:11px; color:var(--text-dim); margin-bottom:10px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  .card-summary {{ font-size:13px; color:var(--text); margin-bottom:10px; line-height:1.6; }}
  .card-reason {{
    font-size:11px; color:var(--accent); background:rgba(233,69,96,0.08);
    padding:6px 10px; border-radius:6px; margin-bottom:10px; border-left:2px solid var(--accent);
  }}
  .card-keywords {{ display:flex; gap:5px; flex-wrap:wrap; }}
  .card-keywords span {{
    background:rgba(15,52,96,0.4); color:#7eb8da; padding:2px 8px; border-radius:4px; font-size:10px;
  }}
  .category-badge {{
    display:inline-block; font-size:10px; padding:2px 8px; border-radius:4px; font-weight:500;
  }}
  .cat-高材制造 {{ background:rgba(46,125,50,0.15); color:#66bb6a; }}
  .cat-清洁能源 {{ background:rgba(21,101,192,0.15); color:#64b5f6; }}
  .cat-AI {{ background:rgba(123,31,162,0.15); color:#ce93d8; }}

  /* Insight Boxes */
  .insight-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:28px; }}
  .insight-box {{
    background:var(--card-bg); border:1px solid var(--border); border-radius:12px;
    padding:20px 24px;
  }}
  .insight-box h3 {{ font-size:14px; color:var(--accent); margin-bottom:14px; font-weight:600; }}
  .trend-item, .follow-item {{
    font-size:13px; color:var(--text); padding:6px 0; display:flex; gap:8px; align-items:baseline;
  }}
  .trend-icon {{ color:var(--accent); font-weight:bold; }}
  .follow-icon {{ color:var(--accent2); font-weight:bold; }}

  /* Footer */
  .footer {{
    text-align:center; padding:24px; color:var(--text-dim); font-size:11px;
    border-top:1px solid var(--border); margin-top:20px;
  }}

  @media (max-width:768px) {{
    .cards-grid {{ grid-template-columns:1fr; }}
    .insight-grid {{ grid-template-columns:1fr; }}
    .header .stats {{ flex-wrap:wrap; gap:16px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>高分子加工全视界日报</h1>
    <div class="date">{today}</div>
    <div class="stats">
      <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Articles</div></div>
      <div class="stat"><div class="stat-num">{section_counts.get("industry", 0)}</div><div class="stat-label">Industry</div></div>
      <div class="stat"><div class="stat-num">{section_counts.get("policy", 0)}</div><div class="stat-label">Policy</div></div>
      <div class="stat"><div class="stat-num">{section_counts.get("academic", 0)}</div><div class="stat-label">Academic</div></div>
    </div>
  </div>

  <div class="nav">{nav_html}</div>

  <div class="summary-box">
    <h3>📊 今日总结</h3>
    <p>{summary_text}</p>
  </div>

  {sections_html}

  <div class="summary-box">
    <h3>🔭 前瞻洞察</h3>
    <p>{foresight_text}</p>
  </div>

  <div class="insight-grid">
    <div class="insight-box">
      <h3>📈 趋势判断</h3>
      {trends_html}
    </div>
    <div class="insight-box">
      <h3>🎯 后续追踪</h3>
      {follow_up_html}
    </div>
  </div>

  <div class="footer">
    Generated by 高分子加工全视界 · Agent-Native Intelligence Platform · {today}
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
            evaluation_reason = card.get("evaluation_reason") or card.get("why_selected", "")
            image_url = card.get("image_url")
            category = card.get("category", "")
            keywords = card.get("keywords", [])
            source_tier = card.get("source_tier", "")

            img_html = ""
            if image_url:
                img_html = f'<img src="{image_url}" class="card-img" alt="{title}" onerror="this.style.display=\'none\'">'

            cat_badge = ""
            if category:
                cat_emoji = _CATEGORY_EMOJIS.get(category, "")
                cat_badge = f'<span class="category-badge cat-{category}">{cat_emoji} {category}</span>'

            tier_badge = ""
            if source_tier:
                tier_badge = f'<span style="font-size:10px;color:var(--text-dim);">[{source_tier}]</span>'

            reason_html = ""
            if evaluation_reason:
                reason_html = f'<div class="card-reason">{evaluation_reason}</div>'

            keywords_html = ""
            if keywords:
                kw_items = "".join(f"<span>{kw}</span>" for kw in keywords[:5])
                keywords_html = f'<div class="card-keywords">{kw_items}</div>'

            finding_text = key_finding or summary[:120]

            parts.append(
                f'<div class="card">'
                f'{img_html}'
                f'<div class="card-title"><a href="{url}" target="_blank">{title}</a></div>'
                f'<div class="card-meta">{cat_badge}{tier_badge}<span>{domain}</span></div>'
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
