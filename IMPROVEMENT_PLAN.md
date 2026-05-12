# 项目改进方案（初步）

## 一、问题诊断

### 1.1 架构层面的根本问题

当前架构的核心矛盾：**把 LLM Agent 的推理循环用在了确定性操作上。**

```
现状：LLM 决策每一步 → 搜索 → LLM 看结果 → LLM 决定下一步 → 搜索 → ...
问题：搜索是广度优先的确定性操作，不需要推理。用 LLM 做这件事 = 高成本 + 不稳定。
```

具体浪费点：

| 浪费来源 | 位置 | 估算占比 |
|---------|------|---------|
| Phase 1 AgentCore 搜索循环（12+ 轮 LLM 调用） | `daily_report_agent.py:572` | ~40% token |
| 每 5 步注入 working memory context | `agent_core.py` | ~10% token |
| kimi-k2.5 reasoning_content 全程携带 | `agent_core.py` | ~15% token |
| Phase 2.5A Supervisor Loop（覆盖不足时再跑一轮 AgentCore） | `daily_report_agent.py` | ~20% token |
| 每篇文章单独调用 LLM 评估（逐个而非批量） | `article_agent.py` | ~15% token |

### 1.2 结果不稳定的根因

LLM agent 的搜索策略**不可复现**——同样的 prompt，今天搜 12 个 query 找到了好内容，明天可能搜 8 个就提前 finish 了。这不是 prompt 写得不好，而是**让 LLM 决定搜索策略本身就引入了随机性**。

### 1.3 老师反馈的需求分析

老师的需求本质上是**表层结构调整**，不要求架构变更：

| 老师需求 | 对应改动 | 影响范围 |
|---------|---------|---------|
| 界面去除英文，统一中文 | 前端文案替换 | 仅前端 |
| 区分"全球日报"和"实验室日报" | Report 表加 `report_type` 字段，搜索阶段区分 source_type | 前端 + 后端数据模型 |
| 按"高材制造/清洁能源/AI"分类 | evaluate_article 输出 category 字段，或搜索阶段按分类构建 query | 前端 + 评估逻辑 |
| 同时检索全球文献和实验室文章 | Phase 1 搜索时增加实验室文章源（arXiv, 知网, 本地数据库） | 搜索源配置 |
| 中英文文献分开展示 | ReportItem 已有语言标记，前端按语言分组渲染 | 前端 |

---

## 二、改进原则

> **确定性工作用 workflow，判断性工作用 agent。**

| 工作类型 | 应该用什么 | 当前做法 | 建议做法 |
|---------|-----------|---------|---------|
| 搜索什么关键词 | 模板/规则/RSS | LLM agent 自主决定 ❌ | 预定义搜索模板 ✓ |
| 内容提取 | 确定性 pipeline | 确定性 pipeline ✓ | 保持 |
| 文章质量评估 | LLM（可批量） | LLM 逐篇评估 | 规则预筛 + 批量 LLM 评估 |
| 去重 + 趋势分析 | LLM | LLM ✓ | 保持 |
| 报告撰写 | LLM | LLM ✓ | 保持 |
| 配图搜索 | 规则 | LLM agent ❌ | 确定性 API 调用 ✓ |

---

## 三、分阶段改进方案

### Phase 0：稳定现有 Agent（已有计划，优先完成）

这是 `New_PLAN.md` 中已经规划但尚未完成的工作，应该在架构调整前先落地：

- [ ] P0: 工具执行超时保护（`agent_core.py` + `harness.py`）
- [ ] P0: Finish 拒绝死循环修复
- [ ] P0: 分类异常处理
- [ ] P1: 消息历史管理（防 context window 溢出）
- [ ] P1: 动态预算感知
- [ ] P1: 重写 System Prompt（三层结构：角色 → 节奏 → 质量标准）
- [ ] P1: 熔断器（Brave/Firecrawl/zhipu）

**预估工作量**：2-3 天  
**预期效果**：减少 agent 因超时/死循环导致的失败，但不解决 token 浪费问题

### Phase 1：搜索去 Agent 化（核心改动，省 50-70% token）

**目标**：把 Phase 1 从 "LLM agent 搜索循环" 改为 "确定性模板搜索"。

**改动方案**：

```
现状：
  搜索阶段 System Prompt（1000+ tokens）
  → AgentCore 循环（LLM 决策 → web_search → LLM 看结果 → LLM 决定下一步）
  → 最少 12 轮 LLM 调用
  → 每轮携带完整 message history + tool definitions

改为：
  预定义搜索模板（6 维度 × 中英文 × 多关键词 = 30-40 个固定 query）
  → 直接批量调用搜索 API（Brave/Zhipu/Tavily）
  → 0 次 LLM 调用
  → 搜索结果规则过滤（域名黑白名单、日期、标题关键词）
```

**具体实现**：

```python
# 替换 Phase 1 的 AgentCore 搜索循环
# 新文件: app/services/search_orchestrator.py

SEARCH_TEMPLATES = {
    "industry": {
        "zh": [
            "注塑机 新品发布 {date}",
            "挤出设备 技术升级 {date}",
            "高分子材料 产能扩建 {date}",
            "塑料原料 价格行情 {date}",
            "复合材料 汽车轻量化 {date}",
        ],
        "en": [
            "injection molding machine new product",
            "polymer processing equipment innovation",
            "plastics recycling technology breakthrough",
        ],
    },
    "policy": {
        "zh": [
            "限塑令 最新政策 {date}",
            "碳关税 塑料行业 {date}",
            "环保法规 高分子材料 {date}",
        ],
        "en": [
            "EU plastic regulation policy",
            "carbon border tax polymer industry",
        ],
    },
    "academic": {
        "zh": [
            "高分子改性 研究进展 {date}",
            "聚合物 新材料 论文 {date}",
        ],
        "en": [
            "polymer composite materials science research",
            "plastics processing academic paper",
        ],
    },
}

class SearchOrchestrator:
    """确定性搜索编排器 —— 零 LLM 调用"""
    
    def __init__(self, brave, zhipu, tavily_api_key):
        self._search_tool = WebSearchTool(brave, zhipu, tavily_api_key)
    
    async def run(self, memory: WorkingMemory) -> int:
        """执行所有预定义搜索模板，结果存入 memory"""
        for section, queries in SEARCH_TEMPLATES.items():
            for lang, query_list in queries.items():
                for query in query_list:
                    result = await self._search_tool.execute(
                        memory, query=query
                    )
        return len(memory.search_results)
```

**同时保留 RSS 种子源**：`_seed_trusted_source_candidates()` 已经在做，扩大覆盖面即可。

**预估工作量**：1-2 天  
**预期效果**：
- Phase 1 token 消耗：~50,000 → 0（LLM 不再参与搜索决策）
- 搜索结果稳定性：大幅提升（固定 query，可复现）
- 仍然覆盖 6 个话题维度，且可以随时调整模板

### Phase 2：评估批量化 + 图片搜索去 Agent 化

**2.1 评估批量化**

```
现状：每篇文章一个 ArticleAgent → 单独调 LLM 评估
改为：
  1. 规则预筛（source_quality 已经在做）
  2. 通过预筛的文章打包，一次 LLM 调用批量评估
     "以下是 10 篇文章的标题和摘要，请分别评估..."
  3. 预估减少 60-70% 的评估 LLM 调用
```

**2.2 图片搜索去 Agent 化**

```
现状：SearchImagesTool → VerifyImageTool（可能触发 LLM vision 验证）
改为：直接调用 Brave Image Search API → 规则验证（尺寸/格式/URL 模式）
      只有不确定的图片才调 LLM vision
```

**预估工作量**：2-3 天  
**预期效果**：Phase 2 token 消耗减少 40-50%

### Phase 3：Agent 收窄至合成阶段

做完 Phase 1 和 Phase 2 后，AgentCore 的使用范围大幅缩小：

```
Phase 1: 确定性搜索（SearchOrchestrator）    ← 不再是 agent
Phase 2: 规则预筛 + 批量评估 + 确定性图片搜索  ← 不再是 agent
Phase 3: AgentCore 合成                      ← 保留 agent
  - compare_sources（去重去噪）
  - write_section（撰写章节）
  - finish（生成摘要和编辑语）
```

AgentCore 循环从 30+ 步缩减到 10 步以内，每一步都有明确的高价值产出。

### Phase 4：老师需求实现

这些大多是前端改动，可以并行进行：

```
1. 前端 UI 中文化 + 全球日报/实验室日报分拆
2. 数据模型：Report.report_type 字段（global / lab）
3. 搜索源扩展：增加实验室文章源
4. 分类标签：evaluate_article 输出 category（高材制造/清洁能源/AI）
5. 中英文分开展示
```

**预估工作量**：2-3 天（大部分是前端改动）

---

## 四、实施优先级

```
┌────────┬──────────────────────────────┬──────┬───────────┐
│ 优先级 │            任务              │ 风险 │  预期效果 │
├────────┼──────────────────────────────┼──────┼───────────┤
│ P0     │ Phase 0: 稳定现有 Agent      │  低  │ 减少失败  │
├────────┼──────────────────────────────┼──────┼───────────┤
│ P1     │ Phase 1: 搜索去 Agent 化     │  中  │ 省 50-70% │
│        │                              │      │ token     │
├────────┼──────────────────────────────┼──────┼───────────┤
│ P1     │ Phase 4: 老师需求（并行）    │  低  │ UI 改观   │
├────────┼──────────────────────────────┼──────┼───────────┤
│ P2     │ Phase 2: 评估批量化          │  中  │ 省 40-50% │
│        │                              │      │ token     │
├────────┼──────────────────────────────┼──────┼───────────┤
│ P3     │ Phase 3: Agent 收窄至合成    │  低  │ 架构清晰  │
└────────┴──────────────────────────────┴──────┴───────────┘
```

---

## 五、与 New_PLAN.md 的关系

`New_PLAN.md` 中已经规划了大量细节工作（超时保护、异常处理、system prompt 重写等），这些不与此方案冲突。此方案是在 New_PLAN 基础上的**方向性调整**：

- **New_PLAN 的 Phase 1-3**（可靠性 + prompt + 工具质量）→ 保留，作为此方案的 Phase 0
- **New_PLAN 的 Phase 4**（Multi-Agent）→ 调整方向：不再拆分更多 agent，而是缩小 agent 使用范围
- **New_PLAN 的 Phase 5**（可观测性）→ 保留

---

## 六、不做的事情

- ❌ 不从零重构——基础设施是扎实的
- ❌ 不引入 LangChain/LlamaIndex——已经证明不需要
- ❌ 不引入多 Agent 辩论框架（AutoGen, CrewAI）——问题不是 agent 不够多
- ❌ 不把 Phase 1 的搜索模板做得过于复杂——保持简单，需要时手动调整
- ❌ 不引入新的 LLM provider——现有的 OpenRouter + Kimi 足够
