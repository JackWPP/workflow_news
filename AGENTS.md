# AGENTS.md — 高分子材料加工每日资讯平台

> 最后更新：2026-05-15
> 当前阶段：**Zeabur 生产部署 + 调试飞轮**（global+lab 日报正常，AI 日报待修复）

---

## 一、项目身份

**高分子材料加工领域垂直研究情报平台**。每天自动检索全球范围内的高分子材料加工相关新闻、政策、学术成果，经过去重、评估、分类后，生成结构化中文日报。同时提供交互式研究助手对话。

**用户**：实验室师生（约 10-30 人），关注高材制造、清洁能源、AI 三个方向的行业动态。

**当前运行模式**：FastAPI 单体应用，APScheduler 每日 10:00 触发日报生成，Vue 3 SPA 前端展示。

---

## 二、架构原则

### 2.1 核心原则：Agent 自主 + 轻量检查点

> **让 Agent 自由探索，用检查点确保进度。不是 Rails，是带闹钟的操场。**

经过多轮迭代验证的架构（2026-05-12）：

```
┌─────────────────────────────────────────┐
│           ContinuousIngester             │
│  (RSS + Bocha 模板搜索, 每小时)          │
│  → ArticlePool (URL+MinHash去重)        │
└──────────────┬──────────────────────────┘
               │ seed (最多30条, 零LLM)
┌──────────────▼──────────────────────────┐
│           AgentCore (tool-use loop)      │
│  自主搜索 → 阅读 → 评估 → 找图 → 写作    │
│  4 个强制检查点确保进度:                  │
│    step 5:  必须开始阅读                  │
│    step 10: 必须开始评估                  │
│    step 16: 必须开始写作                  │
│    step 28: 未finish则强制收尾            │
│  Harness: 65步/1200s, 仅时间+步数限制    │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│               Report                     │
│  6-10 篇文章 × 3 板块 × 图片             │
└─────────────────────────────────────────┘
```

**为什么这样设计（来自踩坑经验）：**

| 尝试过的架构 | 结果 | 失败原因 |
|------------|------|---------|
| Phase 1/2/3 分段 | 2篇/degraded | Composer预评估60篇文章然后中断；Agent配额死板搜8次就停 |
| Agent 完全自主 | 2篇/degraded | Agent搜索12轮才开始评估，238K token全花在搜索上 |
| Agent + prompt约束 | 5篇/degraded | prompt说"搜3-5轮就写"但Agent不听，继续搜到预算耗尽 |
| **Agent + 4检查点** | **6篇/complete** | ✅ 检查点强制在关键节点推进，不依赖Agent自律 |

### 2.2 检查点设计

```python
# agent_core.py — run() 循环中，每次工具执行后
Checkpoint 0 (step 5):  没有 read_page → 强制 "停止搜索，开始阅读"
Checkpoint 1 (step 10): 没有 evaluate → 强制 "立即评估已读文章"
Checkpoint 2 (step 16): 没有 write_section → 强制 "停止搜索，开始写作"
Checkpoint 3 (step 28): 没有 finish → auto_finish 收尾
```

### 2.3 Harness：只管安全底线

不再限制搜索次数、阅读次数、LLM调用次数。只保留：
- `max_steps: 65` — 步数上限
- `max_duration_seconds: 1200` — 20分钟超时
- `blocked_domains` — 域名黑名单
- `should_wind_down` — 剩余<10步或<120秒时提示收尾

---

## 三、目标架构

### 3.0 业界验证的模式

通过对 TrendRadar (51K stars)、AI-news-Automation、news-agents (eugeneyan) 三个成功项目的调研，确认了以下共性模式：

**五大共性模式**：

1. **漏斗式逐层缩减** — 三个项目都遵循"大量采集(100+) → 过滤筛选(20-30) → LLM 分析(8-15) → 最终产出(6-10)"。缩减工作主要靠确定性代码完成，LLM 介入前数据量已大幅减少。

2. **确定性与 AI 的清晰边界** — 凡是可用规则/公式解决的问题（XML解析、URL去重、关键词过滤、权重计算、模板渲染），都不用 LLM。

3. **优雅降级** — AI 失败时回退到纯关键词匹配（TrendRadar）、保留原标题+链接（AI-news-Auto）、单 feed 失败不影响其他（news-agents）。AI 是增强层，不是核心依赖。

4. **配额与成本保护** — TrendRadar 硬限制 `max_news_for_analysis=150`；AI-news-Auto 先用 Flash 排序再选 Top 8 送给 Pro 摘要；news-agents 自定义解析器去噪音后再给 LLM。

5. **模板驱动一致性** — 三个项目都将 prompt 和输出格式独立于代码管理（YAML 配置文件、Python 模板字符串、Markdown 模板文件）。

**对我们的核心启示**：当前项目的问题不是"用了 Agent"，而是"把 LLM 放在了漏斗的最前端而不是最末端"。

### 3.1 系统全景

```
┌─────────────────────────────────────────────────────────────────┐
│                        Vue 3 Frontend                            │
│  全球日报 / 实验室日报 / 高材制造·清洁能源·AI 分类 / 研究助手     │
├─────────────────────────────────────────────────────────────────┤
│                      FastAPI Backend                              │
│                                                                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │ ContinuousIngester│  │ DailyComposer   │  │ ResearchAgent │  │
│  │ (APScheduler)     │  │ (APScheduler)   │  │ (Chat)        │  │
│  │ 每小时运行         │  │ 每日 10:00 运行  │  │ 按需触发       │  │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬───────┘  │
│           │                     │                     │          │
│  ┌────────▼─────────────────────▼─────────────────────▼───────┐  │
│  │                    Shared Services Layer                    │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │  │
│  │  │ Search   │ │ Content  │ │ Embed/   │ │ LLM Client   │  │  │
│  │  │ Engine   │ │ Extractor│ │ Dedup    │ │ (multi-prov) │  │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │           SQLite (WAL) + ChromaDB (向量)                     │  │
│  │  ArticlePool │ Reports │ Users │ EvalResults │ AgentTraces │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 核心模块

| 模块 | 文件 | 职责 | LLM 调用 |
|------|------|------|---------|
| `ContinuousIngester` | `ingester.py` | RSS + 模板搜索 → ArticlePool (URL+MinHash去重) | **零** |
| `DailyComposer` | `composer.py` | 从 ArticlePool 拉取种子 (URL+MinHash去重, 不评估) | **零** |
| `AgentCore` | `agent_core.py` | Agent 循环 + 4 检查点 (自主搜索→阅读→评估→写作) | **全部 LLM 调用通过此引擎** |
| `Harness` | `harness.py` | 安全底线 (步数+超时+域名黑名单, 不管配额) | **零** |
| `SemanticDedup` | `semantic_dedup.py` | URL + MinHash 去重 (Embedding 可选) | **零** (仅 embedding API) |
| `LLMClient` | `llm_client.py` | 统一 LLM 入口 (DeepSeek V4 Flash) | **所有 LLM 调用** |
| `Tools` | `tools.py` | web_search, read_page, evaluate_article, search_images, verify_image, write_section, check_coverage, finish | **按需** |
| `Content Extractor` | `scraper.py, jina_reader.py, content_extractor.py` | 三层提取 (Jina-first → Trafilatura → direct HTTP) | **零** |
| `BochaSearch` | `bocha_search.py` | Bocha web-search + ai-search, include/freshness | **零** |

**不再使用的模块** (保留代码但不参与主流程):
- `BatchEvaluator` — Map-Reduce 批量评估 (太重，已由 Agent evaluate_article 替代)
- `ArticleAgent` — 逐篇并发评估 (Agent 自主交织替代)
- `_run_article_agents`, `_extract_candidate_urls` 等 Phase 1/2/3 方法 (标记为 DEPRECATED)

### 3.3 数据流（单次日报生成）

```
ContinuousIngester (每小时)
  → RSS + Bocha 模板搜索
  → URL + MinHash 去重
  → 域名黑名单过滤
  → ArticlePool 表

DailyComposer.gather_seeds() (日报触发时)
  1. SELECT * FROM article_pool WHERE ingested_at > NOW() - 72h
  2. URL去重 → MinHash去重 (零 LLM)
  3. 返回最多 30 条种子 (RSS优先)

AgentCore.run() (自主循环)
  4. 从种子开始，自由搜索→阅读→评估→找图
  5. 4 个检查点强制推进 (step 5/10/16/28)
  6. evaluate_article: LLM判断是否纳入 + section + category
  7. search_images: 页面内联图 + Bocha thumbnail + HTML img 评分
  8. write_section × 3 → finish (或 auto_finish)

保存 Report + ReportItems → 前端展示
```

### 3.4 关键数据模型新增

```python
# ArticlePool: 原料池（新表）
class ArticlePool(Base):
    id: int
    url: str (unique)
    content_hash: str (indexed)  # SHA256，用于精确去重
    embedding: list[float] | None  # pgvector，用于语义去重
    title: str
    domain: str
    source_type: str  # "rss" | "template_search" | "academic_api"
    language: str  # "zh" | "en"
    raw_content: str | None
    published_at: datetime | None
    ingested_at: datetime
    quality_score: float | None  # 由 BatchEvaluator 填充
    section: str | None  # "industry" | "policy" | "academic"
    category: str | None  # "高材制造" | "清洁能源" | "AI"
    eval_metadata: JSON | None

# Report: 日报（修改现有）
class Report(Base):
    # 新增字段
    report_type: str  # "global" | "lab"
    categories: JSON  # ["高材制造", "清洁能源", "AI"]
    english_section_count: int  # 英文文章数
    chinese_section_count: int  # 中文文章数

# EvaluationRun: 评估运行（新表，替代现有 debug_payload）
class EvaluationRun(Base):
    report_id: int
    eval_date: datetime
    faithfulness_score: float  # LLM-as-Judge
    coverage_score: float
    duplication_score: float
    overall_score: float
    eval_details: JSON
```

---

## 四、技术栈决策

### 4.0 成本实测

| 指标 | 旧架构 (Phase 1/2/3) | 新架构 (Agent + 检查点) |
|------|----------------------|----------------------|
| 单次日报 token | ~0 (卡死在Composer) | **~250K** (~¥0.25/次) |
| 单次日报步数 | 0 | **23-28 步** |
| 日报产出 | 2篇/degraded | **6篇/complete** |
| 月 LLM 成本 | — | **~¥7.5** (30天) |
| DeepSeek API 调用 | — | **~15-20 次** (含评估+写作) |

### 4.1 当前技术栈

| 组件 | 状态 | 备注 |
|------|------|------|
| FastAPI + Vue 3 + Vite | ✅ | 保持不变 |
| SQLite (WAL) | ✅ | 年 7000 条，够用 |
| DeepSeek V4 Flash | ✅ | 日耗 ~¥0.25，直连 |
| Bocha Web Search | ✅ | include/freshness/ai-search |
| SiliconFlow BGE-M3 | ✅ | Embedding 去重 (API, 可选) |
| Trafilatura + Jina + HTTP | ✅ | 三层 fallback，中文站主要走 direct HTTP |
| 图片提取 (评分系统) | ✅ | 内容图优先，logo/模板硬拒绝，AI兜底 |
| RSS (14条中英文源) | ⚠️ | 英文源多数被墙，中文源部分 404 |
| Feeddd 微信桥接 | ⚠️ | 返回 0 条目，需验证 |
| Alembic | ❌ | 未引入，列迁移走 bootstrap.py |
| ChromaDB | ❌ | 未集成，用 SiliconFlow API 替代 |
| 离线评测集 | ❌ | Phase 0 未开始 |

### 4.2 与原始计划的差异

原始 AGENTS.md 设想的"三层漏斗 + BatchEvaluator + ArticleAgent 并发"在实测中被证明不工作：
- BatchEvaluator 预评估 60 篇文章太慢（DeepSeek API 扛不住），且 90% 文章 scraper 会失败
- ArticleAgent 逐篇并发评估与 AgentCore 的 evaluate_article 功能重叠
- Harness 的 8 次搜索/10 次阅读配额把 Agent 手脚捆死

实际跑通的架构是 **Agent 自主 + 轻量检查点**：
- 采集层 (Ingester) → 零 LLM，确定性
- 合成层 (AgentCore) → LLM 驱动，检查点确保进度
- 没有预评估、没有阶段分离、没有工具级配额

### 4.3 模型分层

```
便宜模型（批量、大量）
  用途：文章分类、质量初评、关键词提取
  候选：DeepSeek V3 (便宜), GPT-4o-mini, Gemini Flash, Haiku
  要求：支持 structured output (JSON schema)

强模型（少量、深度）
  用途：趋势分析、章节撰写、日报编辑
  候选：Sonnet 4, Opus 4, DeepSeek V3 (高质量模式)
  要求：长上下文、深度推理

评判模型（离线、非实时）
  用途：日报质量自动评估
  候选：Codex Opus 4（必须比生产模型强）
  要求：高准确率、支持 detailed rubric
```

### 4.4 Embedding 模型选择

| 模型 | 开发者 | 参数 | 开源 | 中英混合表现 | 推荐场景 |
|------|--------|------|------|------------|---------|
| **BGE-M3** | 智源(BAAI) | 568M | ✅ | ⭐⭐⭐ 最佳 | 本地部署，中英混合场景首选 |
| OpenAI text-embedding-3-large | OpenAI | 未披露 | ❌ | ⭐⭐⭐ | API 调用，快速集成 |
| Nomic-embed-text | Nomic AI | 137M | ✅ | ⭐⭐ | 资源受限环境 |
| Cohere Embed v4 | Cohere | 未披露 | ❌ | ⭐⭐ | 企业级检索 |

**推荐：BGE-M3**（本地部署，Ollama 一键 `ollama pull bge-m3`）。中英混合表现最优（MIRACL 多语言 nDCG@10 = 67.8），开源免费，1024 维向量，8192 token 上下文。

**向量存储：ChromaDB**（嵌入式，pip install 即可，零运维）。日均 30-50 条新文章的规模下，纯内存计算 cosine similarity 也是毫秒级。

**语义去重三级流水线**：
```
第一级：URL 精确去重（O(1)）→ 消除同源转载 → 省 30-40%
第二级：MinHash 指纹（O(n)）→ 快速过滤模板内容 → 省额外 20-30%  
第三级：BGE-M3 嵌入向量（O(n*m)）→ 双阈值判断 → 精确去重
```

**语义去重阈值**：
- 余弦相似度 ≥ 0.85 → 视为"高度重复"（保留一篇，其余作为元数据）
- 0.70-0.85 → 视为"同一事件不同视角"（进入灰区，由 LLM 判断是否合并）
- < 0.70 → 视为不同文章

---

## 五、实施进度

### 已完成 ✅

- [x] DeepSeek + Bocha + 诊断 API（2026-05-11）
- [x] ArticlePool + ContinuousIngester + RSS
- [x] SemanticDedup (URL + MinHash), Embedding 可选
- [x] **架构简化**：砍掉 Phase 1/2/3 → Agent 自主 + 4 检查点
- [x] Harness 简化：只保留步数+时间+域名
- [x] 图片评分 + AI 兜底图
- [x] Bocha include/freshness/ai_search 优化
- [x] Ingester 域名黑名单 + 关键词白名单
- [x] 三分类 + 语言标记
- [x] 前端中文化 + 三方向 Tab

### 待完成

- [ ] 连续 3 天稳定性验证
- [ ] 补充中文 RSS/桥接源（86pla、金发、万华等）
- [ ] 修复失效 RSS URL（中国石化、国家统计局、C&EN 等）
- [ ] 离线评测集（100 条标注）
- [ ] Alembic schema 管理
- [ ] RSSHub 自部署（中文桥接）
- [ ] Token 监控仪表板

---

## 六、开发规范

### 6.1 代码组织

```
app/
  log_context.py          # 日志上下文（run_id, request_id）
  config.py               # 配置（API keys, 模型设置）
  bootstrap.py            # DB 初始化 + SQLite 列迁移
  models.py               # SQLAlchemy 模型（ArticlePool, Report, ReportItem 等）
  database.py             # DB 引擎 + session_scope
  services/
    daily_report_agent.py # 主编排器（gather_seeds → AgentCore → persist）
    agent_core.py         # Agent 循环引擎 + 4 检查点
    composer.py           # DailyComposer（gather_seeds, 仅去重不评估）
    ingester.py           # ContinuousIngester（RSS + 模板搜索, 域名黑名单）
    harness.py            # 安全约束（仅步数+时间+域名, 无工具级配额）
    llm_client.py         # LLM 客户端（DeepSeek 直连）
    bocha_search.py       # Bocha API（web-search + ai-search + include/freshness）
    tools.py              # Agent 工具集（9 个工具）
    working_memory.py     # 工作记忆（ArticleSummary, CoverageState, StepRecord）
    jina_reader.py        # Jina Reader + direct HTTP fallback（含图片评分）
    scraper.py            # 三层抓取（Jina-first → Trafilatura → HTTP）
    content_extractor.py  # 独立内容提取路径
    rss.py                # RSS feed 拉取
    semantic_dedup.py     # URL + MinHash + Embedding（SiliconFlow API）
    source_quality.py     # 域名/来源质量分类
    # 辅助模块
    auth.py, repository.py, schemas.py, seed.py
    chat.py, research_agent.py
    link_checker.py, evaluation.py, eval_runner.py
    agent_observability.py
    # DEPRECATED（保留参考, 不参与主流程）
    article_agent.py, batch_evaluator.py
```

### 6.2 关键原则

1. **新代码不加注释**，除非 WHY 不明显。函数名和变量名应该自解释。
2. **一个函数只做一件事**。超过 50 行考虑拆分。
3. **不要在代码里硬编码 LLM prompt**。prompt 放到独立的 prompt 模块或配置中。
4. **所有外部 API 调用必须有超时 + 熔断**。不要在 `New_PLAN.md` 的 Phase 3.4 做完之前引入新 API。
5. **LLM 返回必须用 structured output（JSON schema）**，禁止自由文本后再 regex 解析。
6. **异步优先**。所有 I/O 操作用 `async/await`，CPU 密集操作用 `run_in_executor`。
7. **每条数据库记录要有 created_at**。方便后续做增量处理和回溯。

### 6.3 测试要求

- 每个新模块必须有单元测试（`tests/test_<module>.py`）
- 修改 prompt 或评估逻辑后，必须跑评测集验证
- 集成测试覆盖完整的"采集→去重→评估→合成"链路
- 使用确定性的 mock LLM 响应做回归测试

### 6.4 禁止事项

- ❌ 不要引入 LangChain / LlamaIndex
- ❌ 不要引入多 Agent 辩论框架（AutoGen, CrewAI 等）
- ❌ 不要在搜索/爬取/过滤阶段使用 LLM
- ❌ 不要硬编码 API key（一律走 `.env`）
- ❌ 不要在生产代码里写 `print()` 调试（用 `logging`）
- ❌ 不要在 PR 里混合无关改动（一个 PR 只做一件事）

### 6.5 自研 Agent 框架的致命陷阱（来自踩坑经验）

这些是在自研 Agent 框架中反复出现的问题，设计任何新模块时都必须规避：

1. **消息历史无限增长** — 迭代到第 10-15 轮时 context 塞满，质量崩溃且 token 暴涨。必须有 summarize-old-turns 或 sliding-window 机制，且默认开启。

2. **工具失败处理不当** — 每个工具必须分类错误：可重试（网络超时）/ 永久失败（API 422）/ 需要 LLM 决策（搜索结果为空）。不要写成"重试 3 次都失败就抛异常"。

3. **预算耗尽 ≠ 成功** — max_iterations 用完时，必须判断是否达到最低质量要求，否则报 partial failure。不要让只有 2 条内容的日报状态为"成功"。

4. **缺乏 replay 能力** — 所有 LLM 响应必须可缓存，给定相同输入能复现相同输出。改 prompt 之前先确认差异是预期的还是随机波动。

5. **Working memory schema 漂移** — 存在 working memory 里的字段格式会慢慢变化。必须严格 versioning，或每次发版清空。

6. **观测性不够** — 每次 LLM 调用的完整 prompt、response、token 数、耗时、模型版本都要记录。事后查问题能省 90% 时间。

### 6.6 关于配图（承认过度工程化）

之前的开发中"过分追求每一个条目都有配图"，导致图片搜索、验证逻辑大量堆叠，形成了代码中的"屎山"。新架构中的原则：

- 配图是**锦上添花**，不是日报质量的必要条件
- 图片搜索：确定性 API 调用（Brave Image Search）→ 规则过滤（尺寸/格式/URL 模式）→ 直接使用，**不经过 LLM 验证**
- 如果一篇文章找不到合适的图，**不留空、不占位、不降级**——直接不显示图
- 目标：≥ 50% 的文章有配图即可（当前需求），不需要 100%

---

## 七、评估体系

### 7.1 离线评测集

- **规模**：100 条历史文章，覆盖 1-2 个月（不同日期、不同板块、中英文混合）
- **标注**：2-3 人手工标注"应入选 / 不该入选"作为 ground truth
- **标注规范**：
  - ✅ 应入选：领域相关 + 时效性 ≤ 72h + 来源可信 + 有实质内容
  - ❌ 不该入选：领域无关 / 过时 / PR 稿 / 低质来源 / 无实质内容
- **指标**：precision@10, recall@10（日报通常展示 6-10 条，@10 合理）
- **使用**：每次改 prompt 或架构后跑一遍，收益不达预期就回滚

### 7.2 LLM-as-Judge 自动评估

每日日报生成后自动触发（非阻塞，异步运行）：

| 维度 | 评估方式 | 权重 | 评判标准 |
|------|---------|------|---------|
| 忠实度 (Faithfulness) | 两步法：① 提取 claims ② 逐条对账源文档 | 40% | 每条 claim 标记为"支持/不支持/矛盾" |
| 覆盖度 (Coverage) | 对比 ArticlePool 当天内容 vs Report 入选内容 | 25% | 是否遗漏了当天最重要的 3-5 个事件 |
| 去重质量 (Dedup Quality) | 检查 Report 内是否存在语义重复 | 15% | 报告内不应有同一事件的多篇重复报道 |
| 语言流畅性 (Fluency) | 单次评判 | 20% | 中文表达是否专业、流畅、无"机翻感" |

**关键约束**：评判模型必须比生产模型强。用 Sonnet 评 Sonnet 写的内容几乎拿不到有用信号。推荐使用 **Codex Opus 4** 作为评判模型。

**五维度 Rubric**：

| 维度 | 权重 | 评分方法 | 1 分 | 5 分 |
|------|------|---------|------|------|
| 事实准确性 | 30% | 提取 claims → 逐条对账源文档 | 严重幻觉 | >=95% claims 有支撑 |
| 覆盖度 | 25% | 对比 ArticlePool 当天内容 vs Report | 几乎未覆盖 | 所有关键话题已覆盖 |
| 去重质量 | 20% | 检查 Report 内语义重复 | 基本没有去重 | 完美合并无遗漏 |
| 语言流畅性 | 15% | 单次评判 | 不可读 | 专业流畅，术语准确 |
| 研究价值 | 10% | 评估"研究信号"深度 | 纯复述 | 深入分析+行业预判 |

**Rollup 分数计算**：`weighted_total = 0.30×faithfulness + 0.25×coverage + 0.20×dedup + 0.15×fluency + 0.10×research_value`

**告警阈值**：weighted_total < 3.0 或 faithfulness < 3.0 时触发告警。

**集成方式**（非阻塞，评估失败不影响日报生成）：
```python
# daily_report_agent.py 末尾，report 生成成功后：
if report and report.status == "complete":
    try:
        from app.services.eval_runner import EvalRunner
        runner = EvalRunner(judge_model="Codex-opus-4-7")
        result = await runner.evaluate_report(session, report)
    except Exception:
        logger.warning("Auto-evaluation skipped (non-fatal)")
```

### 7.3 用户反馈

- Thumbs up/down 按钮（已有）
- 每周问卷："过去一周最有价值的 3 条 / 最没价值的 3 条，为什么"
- 质性反馈比定量评分更能暴露结构性问题

### 7.4 跨语种摘要质量保障

中英文混合日报的特殊质量要求：
- 建立**中英双语垂直领域术语映射表**（如 "injection molding" → "注塑成型/注射成型"），强制 LLM 精确替换
- 采用 **SITR 范式**（Summarize → Improve → Translate → Refine）或 **思维链 CoT**：要求 LLM 先用英文简要推理，再生成中文
- 最终中文报告不得有"机翻感"，术语必须统一

---

## 八、环境与命令

### 8.1 开发环境

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python -m uvicorn main:app --host 0.0.0.0 --port 8765 --reload

# 前端开发
cd frontend && npm run dev

# 前端构建
cd frontend && npm run build
```

### 8.2 测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_evaluation.py -v

# 运行评测集
python -m pytest tests/test_evaluation.py::test_benchmark -v
```

### 8.3 数据库

```bash
# SQLite 备份（当前）
sqlite3 news.db ".backup 'backup/news_$(date +%Y%m%d).db'"
```

### 8.4 手动触发日报

```bash
curl -X POST http://localhost:8765/api/reports/run \
  -H "Content-Type: application/json" \
  -d '{"shadow_mode": false}'
```

### 8.5 日志配置

```bash
# 纯文本日志（默认）
LOG_FORMAT=plain LOG_LEVEL=INFO

# JSON 结构化日志（推荐用于调试，每行一个 JSON 对象）
LOG_FORMAT=json LOG_LEVEL=INFO

# 日志字段：ts, level, logger, msg, run_id（日报生成时）, request_id（HTTP 请求时）
```

### 8.6 诊断 API（Agent 自主迭代核心工具）

> **这些端点是 AI Agent 调试和迭代的主要手段。** 每次日报生成后，Agent 应按以下流程使用诊断 API 定位问题。

#### 完整调试流程

```bash
# Step 1: 检查系统健康（跑日报前先确认 API 可用）
curl http://localhost:8765/api/diagnostics/health
curl http://localhost:8765/api/diagnostics/health?deep=true  # 实际调用 API 验证连通性

# Step 2: 触发日报生成
curl -X POST http://localhost:8765/api/reports/run \
  -H "Content-Type: application/json" \
  -d '{"shadow_mode": false}'
# 返回 {"run_id": 42, "status": "running"}

# Step 3: 查看最近一次运行的摘要（关键入口）
curl http://localhost:8765/api/diagnostics/last-run
# 返回: scores, publish_grade, llm_errors, search_health, key_failures

# Step 4: 如果有问题，查看 LLM metrics 定位错误类型
curl http://localhost:8765/api/diagnostics/llm-metrics
# 返回: model_fallbacks, bad_request_count, rate_limit_errors

# Step 5: 查看逐步 timeline 定位具体哪一步出问题
curl http://localhost:8765/api/diagnostics/run/42/timeline
# 返回: 每步的 tool_name, duration, tokens_used, status, error

# Step 6: 查看完整 agent trace（包含 LLM 思考过程）
curl http://localhost:8765/api/agent-runs/42
```

#### 各端点返回的关键字段

**`/api/diagnostics/health`** — 系统健康
- `overall`: "healthy" | "degraded" | "unhealthy"
- `components.database/deepseek_api/bocha_api`: 各组件状态

**`/api/diagnostics/last-run`** — 最近运行摘要
- `publish_grade`: "complete" | "partial" | "partial_auto_publish" | "degraded" | "failed"
- `scores`: content_score, image_score, relevance_score, stability_score, daily_report_score
- `llm_errors.model_fallbacks`: 模型降级记录（如果非空说明主模型有问题）
- `llm_errors.bad_request_count`: 400 错误数（>0 说明请求格式有问题，如 reasoning_content 缺失）
- `search_health.bocha`: 搜索引擎健康状态
- `key_failures`: 自动提取的异常信号列表

**`/api/diagnostics/llm-metrics`** — LLM 专项指标
- `metrics.model_fallbacks`: 模型降级历史
- `metrics.llm_bad_request_count`: 请求格式错误数
- `metrics.kimi_rate_limit_errors`: 限流错误数
- `llm_metrics_on_crash`: 崩溃时的 LLM 状态（仅崩溃路径有值）

**`/api/diagnostics/run/{run_id}/timeline`** — 逐步时间线
- `steps[]`: 每步的 tool_name, duration_seconds, tokens_used, status, error
- `total_tokens`: 总 token 消耗
- `slowest_step`: 最慢的一步（定位性能瓶颈）
- `error_patterns[]`: 错误模式聚合（相同错误归类）

**`/api/agent-runs/{run_id}`** — 完整 trace
- `steps[].thought`: LLM 在每步的思考过程
- `steps[].arguments`: 工具调用参数
- `steps[].result_summary`: 工具返回摘要
- `memory_snapshot`: 运行结束时的完整工作记忆

#### Agent 自主迭代模式

```
1. 跑日报 → 拿到 run_id
2. GET /api/diagnostics/last-run → 看 overall status 和 key_failures
3. 如果 daily_report_score < 60 或 publish_grade != "complete":
   a. GET /api/diagnostics/run/{run_id}/timeline → 找失败步骤
   b. GET /api/agent-runs/{run_id} → 看失败步骤的 thought 和 result
   c. 定位根因（搜索无结果？内容提取失败？LLM 400？超时？）
   d. 修改对应代码
4. 重新跑日报 → 对比新旧 last-run 的 scores
5. 重复直到 daily_report_score >= 70 且 publish_grade 为 complete 或 partial_auto_publish
```

#### 常见问题诊断速查

| 现象 | 诊断方法 | 通常根因 |
|------|----------|---------|
| `publish_grade: "failed"` | 看 `key_failures` 和 timeline | 搜索无结果或 LLM 全部 400 |
| `bad_request_count > 0` | 看 `/api/diagnostics/llm-metrics` | DeepSeek reasoning_content 未回传 |
| `model_fallbacks` 非空 | 看 fallback 的 `reason` | 主模型 API 不可用或限流 |
| `search_health.bocha.state != "healthy"` | 看 `last_error` | Bocha API key 过期或网络问题 |
| `slowest_step.tool_name == "read_page"` | 看 timeline 的 duration | 目标网站响应慢，考虑调整 timeout |
| `total_tokens` 异常高 | 看 timeline 每步 tokens | LLM 多轮无工具调用（stall） |
| `key_failures` 含 "section_timeouts" | 看 timeline 中哪些步骤超时 | 章节撰写 LLM 调用超时 |

---

## 九、关键参考资料

- [Building Effective Agents (Anthropic, 2024)](https://www.anthropic.com/engineering/building-effective-agents) — 判断 agent vs workflow 的权威参考
- [TrendRadar](https://github.com/sansan0/TrendRadar) — AI 驱动的话题监控，模块化架构典范
- [AI-news-Automation](https://github.com/Deepender25/AI-news-Automation) — 极致流程控制，200s 完成采集到推送
- [arXiv Sanity Lite](https://github.com/karpathy/arxiv-sanity-lite) — Karpathy 的论文筛选，证明 LLM 不是必需品
- `feedback_docs/polymer-daily-consultation-feedback.md` — 外部咨询反馈（10 个问题的详细回答）
- `feedback_docs/项目答疑，基于调研文档.md` — 深度技术调研报告（嵌入模型、去重、评估框架）
- `IMPROVEMENT_PLAN.md` — 内部改进方案
- `EXTERNAL_CONSULTATION.md` — 外部咨询文档（项目概述 + 10 个问题）
- `New_PLAN.md` — 已规划的 Agent 可靠性改进（Phase 0 保留，Phase 4 Multi-Agent 废弃）

---

## 十、当前状态与下一步

**当前分支**：`refactor`
**状态**：架构简化完成，Agent 自主 + 轻量检查点稳定产出 complete 级日报。

**已完成（2026-05-11 ~ 2026-05-12）**：
- [x] DeepSeek V4 Flash 直连 + Bocha 搜索 + 诊断 API
- [x] **架构简化**：砍掉 Phase 1/2/3 分段、BatchEvaluator 预评估、ArticleAgent 并发、Harness 死板配额
- [x] **Agent 自主 + 4 检查点**：Agent 自由搜索→阅读→评估→写作，检查点强制推进
- [x] **图片评分系统**：内容图片优先，logo/模板图片硬拒绝，AI 生成分类兜底图
- [x] **RSS 源接入**：14 条中英文 RSS（Nature/ACS/ScienceDirect/北化大学报/Feeddd）
- [x] **Bocha 优化**：`include` 域名限定 + `freshness` 日期范围 + `ai_search` 端点
- [x] Ingester 域名黑名单 + 关键词白名单，ArticlePool 质量治理
- [x] 三分类（高材制造/清洁能源/AI）+ 语言标记（zh/en）
- [x] 前端全中文化 + 三方向 Tab + AI 兜底图

**稳定产出（实测）**：
| 指标 | 值 |
|------|-----|
| 文章数 | 6 篇 |
| 板块 | industry + academic + policy |
| 图片 | 3-4 张（真实图 + AI 兜底图）|
| 等级 | complete_auto_publish |
| Token | ~250K (~¥0.25) |
| 步数 | 23-28 |
| 完成方式 | finish_tool 或 auto_finish |

**下一步**：
1. 连续跑 3 天，验证稳定性
2. 补充更多中文 RSS/桥接源（86pla、金发、万华等）
3. 修复失效的 RSS URL（中国石化、国家统计局、C&EN 等 404）
4. Phase 0：构建离线评测集

---

## 十一、部署调试飞轮

### 11.1 生产环境

| 项目 | 值 |
|------|-----|
| 域名 | `https://buctyl.preview.aliyun-zeabur.cn/` |
| 平台 | Zeabur (香港区域) |
| 数据库 | PostgreSQL (Zeabur 内置) |
| 部署方式 | git push → Zeabur 自动构建部署 |
| 代码版本端点 | `GET /api/version` |

### 11.2 诊断 API 速查表

| 端点 | 用途 |
|------|------|
| `GET /api/version` | 确认当前部署的 git commit SHA |
| `GET /api/diagnostics/health` | 系统健康检查 |
| `GET /api/diagnostics/last-run` | 最近一次日报运行摘要 |
| `GET /api/reports?report_type=ai` | 检查 AI 日报是否生成 |
| `GET /api/reports?report_type=lab` | 检查实验室日报是否生成 |
| `GET /api/reports/today` | 获取当日 combined 日报 |
| `GET /api/diagnostics/run/{id}/timeline` | 单次运行时间线 |
| `GET /api/diagnostics/llm-metrics` | LLM 调用统计 |
| `GET /api/agent-runs/{id}` | Agent 完整 trace |

### 11.3 飞轮工作流

> Agent 通过 HTTP API 自主读取生产环境状态，无需用户中转。

```
Step 1 — 读部署状态
  GET /api/version        → 确认代码版本已部署
  GET /api/diagnostics/health → 确认系统健康
  GET /api/diagnostics/last-run → 获取最近运行摘要
  GET /api/reports?report_type=ai → 检查 AI 报告是否存在
  GET /api/reports?report_type=lab → 检查 Lab 报告是否存在

Step 2 — 分析问题
  对比期望 vs 实际，定位差异

Step 3 — 修改代码
  精确修复 + 添加诊断日志 (logger.info/exc_info=True)
  → 用户 git push → Zeabur 重建 → 回到 Step 1
```

### 11.4 飞轮规则

1. **先读后写**：先请求 `/api/version` 确认当前部署版本，再分析问题
2. **单问题聚焦**：每次只修复一个明确的问题
3. **增量修改**：优先通过添加诊断日志来定位，而非一次性大改
4. **状态追踪**：在下方"当前飞轮轮次"中记录状态
5. **用户最小化**：用户只需 `git push`，Agent 自主完成其他所有操作

### 11.5 当前飞轮轮次

#### Round 1 — AI 日报缺失

- **目标**：手动触发日报后，AI 日报（Juya AI RSS）同时生成并合并到 combined 日报中
- **部署版本**：待推送后通过 `/api/version` 确认
- **状态**：🔴 调试中
- **观察 (2026-05-15)**：
  - `/api/diagnostics/health` → `healthy` ✅
  - `/api/diagnostics/last-run` → `partial`, 5 articles, 3 sections
  - `/api/reports?report_type=lab` → id=2, `complete_auto_publish` ✅
  - `/api/reports?report_type=ai` → **空数组** ❌
- **分析**：`_run_all_reports()` 中 global 完成后调用了 AI pipeline，但数据未写入数据库。已在 `ai_rss_pipeline.py` 中添加诊断日志（`logger.info("AI RSS pipeline: ...")`），待推送后查看。
- **修复**：待诊断日志确认具体失败点
- **下一步**：推送代码 → Zeabur 重建 → 手动触发日报 → Agent 通过 API 读取诊断信息
