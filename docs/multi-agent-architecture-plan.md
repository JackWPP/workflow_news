# 多 Agent 日报架构详细计划

> 日期：2026-06-13
> 状态：设计阶段
> 目标：从 EditorAgent（无搜索）切换到多 Agent 自主搜索架构

---

## 一、现状分析

### 1.1 当前架构问题

| 问题 | 根因 | 影响 |
|------|------|------|
| AI 日报缺失 | EditorAgent 无搜索能力，ArticlePool 中 AI 种子少 | AI 板块为空 |
| 板块覆盖不全 | 种子选择依赖 ArticlePool，某些板块种子不足 | 只有 academic |
| 只有固定信源 | EditorAgent 不做实时 web search | 内容来源单一 |
| 公众号缺失 | Feeddd 桥接不稳定 | 中文源缺失 |

### 1.2 已有可复用组件

| 组件 | 文件 | 能力 |
|------|------|------|
| AgentCore | agent_core.py | Agent 循环引擎 + 4 检查点 |
| WebSearchTool | tools.py | Bocha→Zhipu 级联搜索 |
| ReadPageTool | tools.py | 三层抓取（Jina→Trafilatura→HTTP） |
| EvaluateArticleTool | tools.py | LLM 评估文章价值 |
| WriteSectionTool | tools.py | LLM 撰写板块内容 |
| FinishTool | tools.py | 完成报告 |
| classify_source | source_quality.py | 5 维源质量分类 |
| candidate_score | candidate_scorer.py | 多维候选评分 |
| WorkingMemory | working_memory.py | Agent 认知状态管理 |
| Harness | harness.py | Agent 安全约束 |

### 1.3 数据模型现状

- **Report**: 日报主表，含 status/markdown_content/summary
- **ReportItem**: 日报条目，含 title/summary/source_tier/image_url 等
- **ArticlePool**: 文章池，含 url/title/content_hash/source_tier 等
- **AgentRun/AgentStep**: Agent 运行追踪

---

## 二、目标架构

### 2.1 核心设计

```
┌─────────────────────────────────────────────────────────────────────┐
│                            Orchestrator                              │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    高材制造板块                                 │ │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │ │
│  │  │  Explorer   │───▶│   Editor    │───▶│  Card (存储实体)     │ │ │
│  │  │  搜索+筛选   │    │  评审+入库   │    │  DB 持久化          │ │ │
│  │  │  +入选原因   │    │  +中间文档   │    │                     │ │ │
│  │  └─────────────┘    └─────────────┘    └─────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    清洁能源板块                                 │ │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │ │
│  │  │  Explorer   │───▶│   Editor    │───▶│  Card (存储实体)     │ │ │
│  │  └─────────────┘    └─────────────┘    └─────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    AI 板块                                      │ │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │ │
│  │  │  Explorer   │───▶│   Editor    │───▶│  Card (存储实体)     │ │ │
│  │  └─────────────┘    └─────────────┘    └─────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Summary Agent (总编辑)                       │ │
│  │  输入: 3 个板块的 Cards                                        │ │
│  │  输出: 精美 HTML + 总结分析 + 前瞻洞察                         │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Frontend (卡片展示)                          │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Agent 职责分工

#### Explorer Agent（每个板块一个）

**职责**：搜索、筛选、补充入选原因

**工具集**：
- WebSearchTool（搜索）
- ReadPageTool（读网页）
- EvaluateArticleTool（评估）
- classify_source（源质量过滤）

**搜索策略**：
```python
# 高材制造
queries = [
    "注塑机 新品 2026",
    "挤出设备 技术升级",
    "高分子材料 产能扩建",
    "复合材料 汽车轻量化",
    "polymer processing equipment new",
]

# 清洁能源
queries = [
    "塑料回收 政策 2026",
    "碳关税 塑料行业",
    "电池隔膜 高分子",
    "plastic recycling regulation",
]

# AI
queries = [
    "polymer machine learning property prediction",
    "materials informatics polymer",
    "injection molding AI optimization",
    "聚合物 人工智能 研究",
]
```

**过滤策略**：
- source_tier: 排除 D 级
- page_kind: 排除 homepage/product/search
- 关键词: 必须包含领域相关词

**输出格式**：
```python
{
    "title": "某公司宣布建设高分子材料回收产线",
    "url": "https://...",
    "source": "company newsroom",
    "source_tier": "A",
    "why_selected": "该公司是行业头部，此产线将影响再生料供应格局",
    "key_finding": "年产能 10 万吨，预计 2027 年投产",
    "summary": "...",
    "image_url": "...",
}
```

#### Editor Agent（每个板块一个）

**职责**：评审、入库、撰写中间文档、生成卡片

**工具集**：
- EvaluateArticleTool（评估）
- CompareSourcesTool（对比）
- WriteSectionTool（写板块）
- CheckCoverageTool（检查覆盖）

**工作流程**：
1. 消费 Explorer 的候选文章
2. 评审：去重、排序、最终筛选
3. 入库：写入 ReportCard 表
4. 撰写中间文档：板块摘要、趋势分析
5. 生成板块卡片

#### Summary Agent（总体一个）

**职责**：生成精美的交互式日报

**工具集**：
- WriteSectionTool（写总结）
- FinishTool（完成）

**输出**：
- 精美 HTML 日报
- 总结性分析
- 前瞻性洞察
- 趋势判断
- 后续追踪建议

---

## 三、数据模型设计

### 3.1 新增 ReportCard 模型

```python
class ReportCard(Base):
    """单篇文章卡片 — 每个板块 Editor 生成"""
    __tablename__ = "report_cards"

    id: int = Column(Integer, primary_key=True)
    section: str = Column(String(32), nullable=False)  # industry/policy/academic
    category: str = Column(String(32), nullable=False)  # 高材制造/清洁能源/AI

    # 内容
    title: str = Column(Text, nullable=False)
    url: str = Column(String(2048), nullable=False)
    domain: str = Column(String(255))
    source_name: str = Column(String(255))
    summary: str = Column(Text)
    key_finding: str = Column(Text)

    # 评估
    source_tier: str = Column(String(16))
    source_kind: str = Column(String(64))
    why_selected: str = Column(Text)
    credibility: str = Column(Text)

    # 配图
    image_url: str = Column(String(2048))
    image_caption: str = Column(Text)

    # 元数据
    published_at: datetime = Column(DateTime)
    discovered_at: datetime = Column(DateTime, default=datetime.utcnow)
    explorer_reasoning: str = Column(Text)
    editor_notes: str = Column(Text)

    # 状态
    status: str = Column(String(32), default="draft")  # draft/approved/published
    report_id: int = Column(Integer, ForeignKey("reports.id"))
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 3.2 修改 Report 模型

新增字段：
```python
summary_analysis: str = Column(Text)  # 总结性分析
foresight: str = Column(Text)  # 前瞻性洞察
html_content: str = Column(Text)  # 交互式 HTML
```

---

## 四、实施计划

### Phase 1：创建新分支，清理 git

1. commit 当前改动
2. 创建新分支 `multi-agent-architecture`

### Phase 2：实现 Explorer Agent

1. 新文件：`app/services/explorer_agent.py`
2. 封装 AgentCore + 特定搜索策略
3. 输出候选文章列表 + 入选原因

### Phase 3：实现 Editor Agent

1. 新文件：`app/services/section_editor_agent.py`
2. 封装 AgentCore + 评审逻辑
3. 输出板块卡片

### Phase 4：实现 Summary Agent

1. 新文件：`app/services/summary_agent.py`
2. 生成精美 HTML + 总结分析

### Phase 5：实现 Orchestrator

1. 新文件：`app/services/daily_orchestrator.py`
2. 协调 3 个 Explorer + 3 个 Editor + 1 个 Summary

### Phase 6：修改数据模型

1. 新增 ReportCard 模型
2. 修改 Report 模型

### Phase 7：修改 main.py

1. 切换 pipeline 到 Orchestrator
2. 更新 API 端点

### Phase 8：测试验证

1. 单元测试
2. 集成测试
3. 端到端测试

### Phase 9：清理

1. 删除不再需要的代码
2. 更新文档

---

## 五、验收标准

| 指标 | 目标值 | 验收方式 |
|------|--------|----------|
| 板块覆盖 | >= 3 板块 | 跑日报后检查 |
| 每板块文章数 | >= 2 条 | 检查 ReportCard |
| A/B 源占比 | >= 50% | 检查 source_tier |
| 配图率 | >= 50% | 检查 image_url |
| 日报 HTML | 可交互 | 前端展示 |
| 测试通过 | 100% | pytest |

---

## 六、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Token 消耗增加 | 成本上升 | 设置 Harness 限制 |
| Agent 行为不可控 | 质量下降 | 4 检查点机制 |
| 搜索结果质量差 | 内容质量低 | source_quality 过滤 |
| 并行执行复杂 | 调试困难 | 详细日志 |

---

## 七、可复用组件清单

| 组件 | 文件 | 复用方式 |
|------|------|----------|
| AgentCore | agent_core.py | 直接复用 |
| WebSearchTool | tools.py | 直接复用 |
| ReadPageTool | tools.py | 直接复用 |
| EvaluateArticleTool | tools.py | 直接复用 |
| WriteSectionTool | tools.py | 直接复用 |
| FinishTool | tools.py | 直接复用 |
| classify_source | source_quality.py | 直接复用 |
| candidate_score | candidate_scorer.py | 直接复用 |
| WorkingMemory | working_memory.py | 直接复用 |
| Harness | harness.py | 直接复用 |
| report_persistence | report_persistence.py | 需修改 |
