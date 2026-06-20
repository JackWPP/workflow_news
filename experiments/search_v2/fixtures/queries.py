"""真实生产查询样本 (从 config/search_templates.yaml 中分层抽样).

抽样原则:
- 覆盖 3 个 section (industry/academic/policy)
- 覆盖中英文 (3 zh + 3 en)
- 覆盖不同 query_family
- 优先选取 ingester 模板里出现的, 让结果反映真实生产分布
"""

# 6 个核心样本: 中英文 x 3 section 各 1 条
CORE_SAMPLE = [
    # 中文 industry
    {"query": "注塑机 新品发布", "language": "zh", "section": "industry", "family": "equipment_news"},
    # 中文 academic
    {"query": "高分子材料 研究进展", "language": "zh", "section": "academic", "family": "research_signal"},
    # 中文 policy
    {"query": "塑料污染 治理 政策", "language": "zh", "section": "policy", "family": "policy"},
    # 英文 industry
    {"query": "polymer industry news", "language": "en", "section": "industry", "family": "industry_news"},
    # 英文 academic
    {"query": "biodegradable polymer research", "language": "en", "section": "academic", "family": "biopolymer"},
    # 英文 policy
    {"query": "plastic recycling regulation", "language": "en", "section": "policy", "family": "recycling"},
]

# 扩展样本(实验 4/5 使用), 多覆盖一些 family
EXTENDED_SAMPLE = CORE_SAMPLE + [
    {"query": "锂电池 隔膜 材料", "language": "zh", "section": "industry", "family": "energy_materials"},
    {"query": "改性塑料 应用", "language": "zh", "section": "industry", "family": "application_news"},
    {"query": "AI 高分子材料 设计", "language": "zh", "section": "academic", "family": "ai_materials"},
    {"query": "lithium battery separator", "language": "en", "section": "industry", "family": "energy_materials"},
    {"query": "machine learning polymer design", "language": "en", "section": "academic", "family": "ai_materials"},
    {"query": "carbon neutrality plastic industry", "language": "en", "section": "policy", "family": "policy"},
]
