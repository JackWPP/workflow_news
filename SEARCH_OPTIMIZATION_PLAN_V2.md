# 搜索 V2 优化方案（数据驱动）

> **日期**：2026-06-19
> **修订**：2026-06-19（晚）—— oracle 独立审查发现 4 个阻塞 + 真实生产架构核实
> **依据**：`experiments/search_v2/` 4 大实验，294 次真实 API 调用
> **取代**：`SEARCH_OPTIMIZATION_PLAN.md`（v1）—— v1 假设 DDG/Brave/AnySearch，被实验证伪
> **原则**：每条改动必须有实验数据出处 + 可验证的成功标准

---

## 🔴 修订背景与本版关键修正

V2 初稿（早些时候完成）经独立审查发现 **4 个阻塞性 bug + 4 个警告 + 1 个根本性认知错误**。本版做了以下修正：

### 根本性认知错误（最严重）
**初稿假设主路径是 `daily_report_agent.py`，但生产真实主路径是 `DailyOrchestrator`**。
- `app/config.py:94` `multi_agent_mode = True`（默认）
- `main.py:204-205` 主管道 = `DailyOrchestrator`，DailyReportAgent 仅作 fallback
- `DailyOrchestrator` → 3 × `ExplorerAgent`（并行搜索）+ 3 × `SectionEditorAgent`（不搜索）+ `SummaryAgent`
- → **真正需要改造的搜索路径是 `ExplorerAgent._build_tools()`（explorer_agent.py:103-113），不是 daily_report_agent.py:322**

### 4 个阻塞性 bug（修订后已修复）

| # | 原 PLAN 问题 | 本版修复 |
|---|------------|---------|
| 1 | `_seeds_too_stale` 访问 `s.metadata.ingested_at` 但 `composer.gather_seeds()` 返回的 dict 没这个字段，导致 `needs_refresh` 永远 True | 在 `gather_seeds()` 返回值里加 `ingested_at` 字段 |
| 2 | Phase B 改 `daily_report_agent.py:322`，但那不是主路径；ExplorerAgent 才是 | 改 `explorer_agent.py:107`，给 SearchRouter 注入 zhipu_client |
| 3 | lazy_read 承诺减 60-70% read_page，但 98.6% Bocha summary 触顶导致几乎全要 read_page | 新决策表：智谱来源 ~95% 跳过，Bocha 来源仅 summary<600 字才跳过；预期减少 **30-45%**（仅 Phase B 后才生效） |
| 4 | 可观测性零设计 | 新增 Phase 0.5 "可观测性增强"——结构化日志字段 + `/api/diagnostics/last-run` 扩展 |

### 4 个警告（修订后已处理）

| # | 警告 | 修复方式 |
|---|------|---------|
| 5 | `_MAX_POOL_ARTICLES=200` 在 count=50 后丢弃 87% 候选 | Phase A 同步调到 1000 |
| 6 | AGENTS.md 38 天没更新，已与生产架构脱节（DailyOrchestrator 等） | 新增 Phase 0「前置确认」+ 收尾 Phase 包含 AGENTS.md 同步 |
| 7 | `daily_report_score` 基线值未定 | 前置确认环节先跑一次基线日报记录数字 |
| 8 | 双源对不同 query family 的成本/质量差异未讨论 | Phase B 上线后观察 1 周再评估"按 family 选择性双源" |

---

## 📋 各文件改动一览（修订版，按真实生产路径）

| 文件 | 修订前的 PLAN 描述 | 修订后的真实改动 |
|------|------------------|----------------|
| `main.py:395` | 删除 hourly_ingester | ✅ 不变 |
| `app/services/ingester.py` | 拆 `run() / run_rss_only()` | ✅ 不变 |
| `app/services/composer.py:69-76` | 不变 | 🆕 加 `ingested_at` 字段到返回 dict |
| `app/services/daily_report_agent.py:322` | 改 WebSearchTool 加 search_router | ⚠️ 改为可选（只是 fallback 路径），主改动转到 explorer_agent |
| **`app/services/explorer_agent.py:107`** | 未提及 | 🆕 **核心改动**：SearchRouter 加 zhipu_client（这才是双源主路径）|
| `app/services/research_agent.py:286` | 未提及 | 🆕 同 daily_report_agent，可选 |
| `app/services/bocha_search.py:127-148` | 加 summary_full 字段 | ✅ 不变 |
| `app/services/zhipu_search.py:67` | 加 content_size=high | ✅ 不变 |
| `app/services/scraper.py` | Jina → 智谱 reader 兜底 | ✅ 不变（C.2）|
| `app/services/composer.py:20` | 未提及 | 🆕 `_MAX_POOL_ARTICLES=200 → 1000` |

---



> **🔴 Phase 0 是本方案的核心，工作量最小，收益最大**

---

## 🟢 Phase -1：前置确认（上线前必跑，10 分钟）

> **修订背景**：oracle 审查发现 AGENTS.md 已 38 天没更新，期间 git 有 ~20 个新 commit（包括 DailyOrchestrator 多 agent 架构）。**不能依赖 AGENTS.md 描述的"基线"作为对比标准**。本 Phase 在动任何代码前先取生产真实基线。

### -1.1 目的
- 取得**当前真实日报基线数据**，作为 Phase 0/A/B/C 落地后的对比锚点
- 验证 PLAN_V2 假设的代码路径与真实运行路径一致

### -1.2 操作清单

```bash
# 1) 确认主管道是 DailyOrchestrator 不是 DailyReportAgent
grep -n "multi_agent_mode" app/config.py
# 期望: multi_agent_mode: bool = ... default=True

# 2) 取一份生产基线日报（不改任何代码）
curl -X POST http://localhost:8765/api/reports/run -H "Content-Type: application/json" -d '{"shadow_mode": true}'

# 3) 等待结束（约 10-15 分钟），记录关键指标
curl http://localhost:8765/api/diagnostics/last-run | jq '.publish_grade, .scores, .key_failures'
```

### -1.3 必须落档的基线数据

把以下数字写到 `experiments/search_v2/results/baseline_pre_v2.json`：

| 指标 | 当前值（必填）| 来源 |
|------|------|------|
| `publish_grade` |  | `last-run` 返回 |
| `scores.daily_report_score` |  | `last-run` 返回 |
| `scores.content_score` |  | `last-run` 返回 |
| `scores.image_score` |  | `last-run` 返回 |
| 文章数 |  | DailyOrchestrator 返回 `meta.total_cards` |
| 各板块文章数 |  | `meta.sections` |
| 总耗时（秒）|  | `meta.elapsed_seconds` |
| Bocha 调用次数（本次日报）|  | `last-run` 的 `search_health.bocha.request_count` |
| 主路径 |  | DailyOrchestrator / DailyReportAgent fallback |

### -1.4 通过标准（缺一不可）

- [ ] 确认 `multi_agent_mode=True`，主路径走 DailyOrchestrator
- [ ] 至少跑通 1 次完整日报，`publish_grade ∈ {complete, partial}`
- [ ] 基线数据已落档
- [ ] 如果当前生产**根本跑不出 complete 日报**，**先停下来修生产，不要进 Phase 0**

---

## 📊 Phase 0.5：可观测性增强（修复阻塞 4，30 分钟）

> **修订背景**：oracle 审查发现 PLAN_V2 各 Phase 的"验证"全靠 grep 文本日志，没法量化。本 Phase 把核心指标改成结构化 + 暴露给 `/api/diagnostics/last-run`。

### 0.5.1 结构化日志字段（写入 last-run 的 search_health）

**文件**：`app/services/search_router.py` 和 `app/services/bocha_search.py` 的 `health_snapshot()`

新增字段：
```python
{
    "provider": "bocha" | "zhipu_sogou",
    "request_count": int,
    "failure_count": int,
    "consecutive_failures": int,
    "consecutive_empty_queries": int,
    # —— 以下为 V2 新增 ——
    "avg_results_per_query": float,    # 平均召回数
    "p50_summary_chars": int,          # 摘要字数 p50（验证 count=50 是否生效）
    "p50_latency_ms": int,             # 延迟 p50
    "fresh_filter_in": int,            # Phase B 后：进入 freshness filter 的条数
    "fresh_filter_out": int,           # Phase B 后：被过滤掉的条数
    "lazy_read_skip_count": int,       # Phase C 后：被 lazy_read 跳过的次数
    "lazy_read_total": int,            # Phase C 后：lazy_read 总判断次数
}
```

### 0.5.2 `/api/diagnostics/last-run` 扩展字段

**文件**：`app/services/agent_observability.py` 或 `main.py` 的 diagnostics 端点

新增顶层字段：

```python
{
    # 既有字段保留
    "publish_grade": "complete",
    "scores": {...},
    # —— V2 新增 ——
    "search_health": {
        "bocha": {...},  # 上文 health_snapshot
        "zhipu_sogou": {...},  # Phase B 后出现
    },
    "ingest_decision": {
        # 由改动 E 的 _ensure_pool_fresh_before_report 写入
        "fresh_count_at_check": int,
        "ingester_triggered": bool,
        "trigger_reason": str,  # "fresh<15" | "all_stale_24h" | "pool_healthy"
    },
    "lazy_read_stats": {  # Phase C 后
        "total_candidates": int,
        "skipped_by_lazy": int,
        "skip_rate": float,
        "by_provider": {"bocha": {...}, "zhipu_sogou": {...}},
    },
}
```

### 0.5.3 验证标准

- [ ] `/api/diagnostics/last-run` 返回的 JSON 比改造前多了 `ingest_decision` 字段
- [ ] Phase B 上线后，`search_health` 同时含 `bocha` 和 `zhipu_sogou` 两个键
- [ ] Phase C 上线后，`lazy_read_stats` 字段非空
- [ ] **每个 Phase 验证清单都用 last-run 返回的数字**，不是文本日志

### 0.5.4 回滚

可观测性只增字段不动逻辑，零回滚成本。如果某字段实现有 bug 导致序列化失败，删除该字段即可。

---

## 🚨 Phase 0：调度频率削减（最高优先级，10 分钟）

### 0.1 问题背景

**当前现状**（`main.py:395`）：
```python
scheduler.add_job(scheduled_ingester_run, CronTrigger(hour="*"), ...)
```

**每小时**触发一次 `ContinuousIngester.run()`，每次跑 41 个 Bocha web-search 模板查询。

**实际使用**：
- 日报每天只跑 1 次（10:00）
- 研究助手按需触发（不依赖池子新鲜度）
- → 每天 23 次 hourly 跑出来的内容**根本没人用**

**真实成本**：
- 调用次数：41 × 24 × 30 = **29,520 次/月**
- 单价：¥0.036/次（Bocha web-search 官方定价）
- **月成本：~¥1,063**（仅 ingester template_search 部分；加上 ai-search 和 daily_report ≈ ¥1,400/月）

**浪费比例**：71/72 ≈ **99% 的搜索调用是浪费的**（每天 72 次刷新里只有 1 次会进日报）。

### 0.2 目标方案：取消 hourly，按需触发

**关键观察**：项目已有"池子空就触发 ingester"的兜底逻辑（`daily_report_agent.py:268-275`），只是被 hourly 刷新掩盖了。删掉 hourly 后这个兜底自动生效。

**新调度逻辑**：

```
RSS（免费）：保持 hourly 持续产出（不耗 Bocha）
Bocha 模板搜索：取消 hourly，仅在以下情况触发：
  1. 日报触发（10:00）→ composer 检查池子 → 不够新就跑一次 ingester template_search
  2. 手动触发 /api/reports/run → 同上
  3. 研究助手用 web_search 工具按需搜（已有能力）
```

**预期成本**（Bocha web-search 部分）：
- 调用：41 × 1 × 30 = **1,230 次/月**
- 月成本：**~¥44/月**
- 相比当前 ~¥1,063 → **节省 96%**

### 0.3 改动清单（4 处）

#### 改动 A：删除 hourly_ingester 调度

**文件**：`main.py:395`

```diff
- scheduler.add_job(scheduled_ingester_run, CronTrigger(hour="*"),
-                   id="hourly_ingester", replace_existing=True)
+ # Hourly Bocha template search disabled (Phase 0).
+ # Bocha is expensive; rely on daily-report-time on-demand trigger instead.
+ # RSS-only ingest moved to a lighter hourly job.
+ scheduler.add_job(scheduled_rss_ingester_run, CronTrigger(hour="*"),
+                   id="hourly_rss_ingester", replace_existing=True)
```

#### 改动 B：拆分 ingester.run()，让 RSS 独立可调

**文件**：`app/services/ingester.py:119-125`

```python
class ContinuousIngester:
    async def run(self) -> int:
        """完整跑：RSS + 模板搜索 + arXiv. 仅在日报触发或手动触发时调用."""
        total = 0
        total += await self._ingest_rss()
        total += await self._ingest_template_searches()  # Bocha 大头
        total += await self._ingest_arxiv_api()
        return total

    async def run_rss_only(self) -> int:
        """轻量跑：只拉 RSS + arXiv，不烧 Bocha 钱. 用于 hourly 调度."""
        total = 0
        total += await self._ingest_rss()
        total += await self._ingest_arxiv_api()
        return total
```

#### 改动 C：新增 scheduled_rss_ingester_run

**文件**：`main.py`（在 `scheduled_ingester_run` 附近）

```python
async def scheduled_rss_ingester_run():
    """每小时跑 RSS（免费），不调用 Bocha."""
    from app.services.ingester import ContinuousIngester
    logger.info("Starting hourly RSS-only ingester run.")
    try:
        ingester = ContinuousIngester()
        count = await ingester.run_rss_only()
        logger.info("Hourly RSS ingester finished: %d new articles.", count)
    except Exception as exc:
        logger.error("Hourly RSS ingester failed: %s", exc, exc_info=True)
```

`scheduled_ingester_run`（旧的全量版本）保留，但仅由日报触发调用，不再挂 scheduler。

#### 改动 D：composer 返回 dict 加 `ingested_at` 字段

**修复阻塞 1**：原 PLAN 写的 `_seeds_too_stale` 函数访问 `s.metadata.ingested_at`，但 `composer.gather_seeds()` 返回的 dict 里**根本没这个字段**，`metadata` 来自 `a.eval_metadata` 也不含 `ingested_at`。这是字段不存在的访问，会让 `fresh_count` 永远为 0、`needs_refresh` 永远为 True。

**文件**：`app/services/composer.py:69-76`

```diff
  for a in final[:15]:  # max 15 per language
      results.append({
          "url": a.url, "title": a.title, "domain": a.domain,
          "snippet": a.summary or "", "published_at": a.published_at,
          "language": a.language, "source_type": a.source_type,
          "section": a.section, "category": a.category,
          "metadata": a.eval_metadata or {},
+         "ingested_at": a.ingested_at,  # 新增：用于 staleness 判断
      })
```

#### 改动 E：陈旧度检查逻辑加在两条路径上

**修复"主路径不是 daily_report_agent.py"的认知错误**。

生产环境主管道是 `DailyOrchestrator`（`main.py:204-205`，`MULTI_AGENT_MODE=True` 默认）。但 `DailyOrchestrator` 不读 `ArticlePool`——它直接让 3 个 ExplorerAgent 去搜。所以"池子陈旧度检查"应该在 **DailyOrchestrator 启动前**触发。

**文件 1**：`main.py`（在 `scheduled_report_run` 或 DailyOrchestrator 启动逻辑前加预热）

```python
async def _ensure_pool_fresh_before_report():
    """日报启动前确认池子健康。陈旧或不足则补一次 ingester。"""
    from datetime import datetime, timezone, timedelta
    from app.database import session_scope
    from app.models import ArticlePool
    from sqlalchemy import select, func

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with session_scope() as session:
        # 直接 SQL 数过去 24 小时入池数量，不经 composer
        fresh_count = session.scalar(
            select(func.count(ArticlePool.id)).where(ArticlePool.ingested_at >= cutoff)
        ) or 0

    if fresh_count < 15:
        logger.info(
            "[Pool prewarm] fresh count=%d < 15, triggering full ingester",
            fresh_count,
        )
        from app.services.ingester import ContinuousIngester
        await ContinuousIngester().run()
    else:
        logger.info("[Pool prewarm] fresh count=%d, skipping ingester", fresh_count)
```

调用点：`main.py` 的 `scheduled_report_run()` 开头，DailyOrchestrator 启动前。

**文件 2**：`app/services/daily_report_agent.py:267-275`（fallback 路径，仍然要修，因为 DailyOrchestrator 失败时 fallback 到这条）

把现有的"池子完全空才补"改成"陈旧或不足才补"，用改动 D 添加的 `ingested_at` 字段：

```python
seeds = await composer.gather_seeds(target_date)

# 池子健康度检查：数量 + 新鲜度
needs_refresh = (
    len(seeds) < 15  # 候选不足
    or _seeds_too_stale(seeds, max_age_hours=24)  # 全部超过 24h
)

if needs_refresh:
    logger.info(
        "[DailyReportAgent] Pool needs refresh (count=%d, stale=%s), triggering ingester",
        len(seeds), needs_refresh,
    )
    await ContinuousIngester().run()
    seeds = await composer.gather_seeds(target_date)
```

`_seeds_too_stale` 实现（修复阻塞 1：用改动 D 加的 `ingested_at` 字段）：

```python
def _seeds_too_stale(seeds: list[dict], max_age_hours: int = 24) -> bool:
    """所有种子都超过 max_age_hours 则视为陈旧。

    依赖 composer.gather_seeds() 返回的 dict 必须包含 'ingested_at' 字段
    （见改动 D）。
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    fresh_count = 0
    for s in seeds:
        ingested_at = s.get("ingested_at")  # 直接取顶层字段，不走 metadata
        if not ingested_at:
            continue
        if isinstance(ingested_at, str):
            try:
                ts = datetime.fromisoformat(ingested_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
        else:
            ts = ingested_at  # 已经是 datetime
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            fresh_count += 1
    return fresh_count == 0
```

### 0.4 验证标准

**结构性检查（部署当天）**：
- [ ] `/api/diagnostics/health` 显示 scheduler 中只有 `hourly_rss_ingester`，没有 `hourly_ingester`
- [ ] 日报触发时（10:00）`/api/diagnostics/last-run.ingest_decision` 字段显示 `trigger_reason`（来自 Phase 0.5 加的字段）
- [ ] `last-run.ingest_decision.fresh_count_at_check` 数值合理（>0 即可）

**质量回归检查（与 Phase -1 基线对比，必填）**：
- [ ] `last-run.publish_grade` ≥ Phase -1 基线
- [ ] `last-run.scores.daily_report_score` 不下降超过 5 分
- [ ] 文章数 / 板块数与基线持平
- [ ] `eval_runner.evaluate_report()` 跑出来的 `weighted_total` ≥ 基线 - 0.3

**成本检查（24 小时后）**：
- [ ] Bocha 调用日志：每天调用次数从 ~984 次（41×24）下降到 ~41-82 次
- [ ] 月度账单（博查后台）从 ~¥1,000 降到 ~¥40-80（取决于陈旧度触发频率）

### 0.5 回滚

恢复 `main.py:395` 的 `hourly_ingester` 调度即可，其他文件改动可保留（向后兼容）。

### 0.6 后续可选优化

- **模板分级触发**：把 41 个模板按 query_family 分成 daily/2-day/weekly 三档（policy/lab_watch 这种低频内容不需要每天搜）。预计再省 30-50% Bocha 调用。
- **基于池子缺口的定向搜索**：如果某个 section（policy/academic/industry）池子薄，只搜对应 family 的模板，而不是全 41 个。

这些等 Phase 0 + Phase A 的数据回来再决定，不强求一次到位。

---


## 〇、为什么换 PLAN

V1 计划基于"实验代码 + 大致猜想"，但实测后发现：
- ❌ AnySearch 端点 `v1/search` 文档不存在（只有 MCP 协议）
- ❌ DDG `html.duckduckgo.com` 中国大陆 IP 直接 403（SearXNG 文档明文记录）
- ❌ Brave HTML 抓取违反 ToS，应该用付费 API
- ❌ JSON-LD 提取 trafilatura 已内置，重复造轮子
- ✅ 但 V1 的 CircuitBreaker / SearchRouter 并行 / 双兜底 这些**架构思路**是对的

V2 核心不同：**搜索源换成 Bocha + 智谱 sogou 双源（实测 0% 重叠互补），不再引入海外搜索源**。

---

## 一、关键实验结论速查

> 完整分析见 `experiments/search_v2/reports/exp{1,2,4,4_ext}_report.md`

| 编号 | 结论 | 数据出处 |
|----|------|---------|
| F1 | Bocha `count=10→50` 同价同延迟拿 4× 结果 | exp1 Q1：9.8→38.2 召回，278→244ms |
| F2 | Bocha summary 已在用，但被塞进 `snippet` 字段 | bocha_search.py:140 |
| F3 | Bocha summary 是 800 字硬截断（98.6% 触顶） | exp1 Q6 + exp2 Q6 |
| F4 | Bocha freshness 过滤几乎不起作用 | exp1 Q3 |
| F5 | 但 Bocha 100% 结果带 `datePublished` | exp1 Q3 |
| F6 | 智谱 search_std/pro 中文 link 100% 损坏 | exp4 Q1：14/14 中文都空链 |
| F7 | 智谱 search_pro_sogou link 100% 完整 | exp4_ext：300/300 完整 |
| F8 | 智谱 freshness 完全失效 | exp4_ext Q2：4 档召回数一致 |
| F9 | 智谱 sogou × Bocha = 0% URL 重叠 + 0% 域名级 Jaccard | exp4_ext Q3 |
| F10 | 智谱 sogou content_size=high 平均 7000 字 | exp2 Q2 |
| F11 | 智谱 search content ≈ 智谱 reader（仅 1.1×） | exp2 Q7 |
| F12 | 智谱 sogou URL 真文章率 97% | exp2 Q5 |
| F13 | Trafilatura + 智谱 reader 双兜底 = 100% 命中 | exp2 Q1 |
| F14 | 两个搜索 API 都 100% 返回 published_at | exp2 Q4 |
| F15 | Bocha rerank 待充值才能验证（暂搁置） | exp3 |

---

## 二、改造前的依赖注入图

```
ingester.py:106  ContinuousIngester.search_engine
  └── BochaSearchClient() + SearchRouter（串行：Bocha → Zhipu fallback）

daily_report_agent.py:247  _run_phases()
  ├── JinaReaderClient()
  ├── ScraperClient(jina_client=jina)
  ├── BochaSearchClient()
  ├── ZhipuSearchClient()       # 标 DEPRECATED 但仍在用
  └── WebSearchTool(bocha=, zhipu=)  # 串行 fallback

bocha_search.py:42  search()
  默认 count=10, summary=true
  返回时 summary 塞进 "snippet" 字段（line 140）

zhipu_search.py
  默认 search_engine=search_pro_sogou (config.py:45) ✅
  但 content_size 未传 → 默认 medium → content 缩水
```

---

## 三、Phase A：纯配置 + 字段语义梳理（30 分钟）

### A.1 Bocha 默认 count 调到 50

**依据**：F1
**文件**：`.env:12`、`app/config.py:42`

```diff
- BOCHA_SEARCH_COUNT=10
+ BOCHA_SEARCH_COUNT=50
```

```diff
# app/config.py:42
- bocha_search_count: int = int(os.getenv("BOCHA_SEARCH_COUNT", "10"))
+ bocha_search_count: int = int(os.getenv("BOCHA_SEARCH_COUNT", "50"))
```

**验证**：
- 触发日报，看 `BochaSearchClient.search()` 日志：`'<query>' → 38-50 results`
- 总 Bocha 调用次数不变（不是更多次，是每次更多）
- 延迟变化 < 50ms

**回滚**：env 改回 10。

---

### A.2 Bocha 客户端字段语义分离（向后兼容版本）

**依据**：F2 + F3
**文件**：`app/services/bocha_search.py:127-148`

**问题**：
```python
# 当前 line 140
"snippet": ai_summary if ai_summary else snippet,  # 800字塞进 snippet
```

下游所有用 `row["snippet"]` 的地方实际拿到 800 字，但代码注释里以为是短 snippet。

**推荐改法（不破坏下游）**：

```python
# bocha_search.py:127-148
results.append({
    "url": url,
    "title": item.get("name") or "",
    "snippet": ai_summary if ai_summary else snippet,  # 维持现状
    "summary_full": ai_summary,                         # 新增：800 字 AI 摘要
    "snippet_raw": snippet,                             # 新增：原始 100 字
    "image_url": image_url,
    "published_at": published_at,
    "domain": domain,
    "search_type": "news",
    "result_type": "news",
    "provider": "bocha",
    "metadata": item,
})
```

下游迁移分阶段：
- 短期：旧 `row["snippet"]` 行为不变
- 长期：`composer.py` MinHash 用 `summary_full`，关键词匹配用 `snippet_raw`

**验证**：
- 日志里能看到 `summary_full` 字段
- DB ArticlePool.snippet 字段长度分布不变

**回滚**：删除新增字段。

---

### A.3 智谱客户端必传 content_size=high

**依据**：F10
**文件**：`app/services/zhipu_search.py:67`

当前 payload 没传 `content_size`，默认是 `medium`，content 缩水到几百字。

```python
# zhipu_search.py 内部 build payload 处
payload = {
    "search_engine": settings.zhipu_search_engine,
    "search_query": query,
    "count": count,
    "search_recency_filter": recency,
    "content_size": "high",  # ✅ 必传
}
```

**验证**：
- 实测一次智谱搜索，每条 `content` 字段平均 ≥3000 字（中文到 7000+）

**回滚**：删除字段。

---

### A.4 智谱字段映射加 summary_full

**依据**：F7、F11
**文件**：`app/services/zhipu_search.py:120-145`

让智谱响应字段与 Bocha 对齐，下游统一处理：

```python
# zhipu_search.py 返回结果处
results.append({
    "url": item.get("link") or "",
    "title": item.get("title") or "",
    "snippet": (item.get("content") or "")[:200],   # 截短做 snippet
    "summary_full": item.get("content") or "",       # 完整 content
    "snippet_raw": (item.get("content") or "")[:200],
    "image_url": item.get("icon") or "",             # 注：是站点 icon 不是文章图
    "published_at": _parse_date(item.get("publish_date")),
    "domain": extract_domain(item.get("link") or ""),
    "search_type": "news",
    "result_type": "news",
    "provider": "zhipu_sogou",
    "metadata": item,
})
```

**验证**：
- 智谱搜索结果字段名跟 Bocha 一致
- `summary_full` 字段平均 7000 字（中文）/ 3500 字（英文）

---

### A.5 强制智谱 search_engine=search_pro_sogou

**依据**：F6、F7
**文件**：`app/config.py:45`

**当前已正确**：

```python
zhipu_search_engine: str = os.getenv("ZHIPU_SEARCH_ENGINE", "search_pro_sogou")
```

但要加**防御性检查**：如果环境变量被错改成 `search_std` / `search_pro`，应该 warning。

```python
# zhipu_search.py 顶部初始化检查
_VALID_ZHIPU_ENGINES = {"search_pro_sogou", "search_pro_quark"}
# 注：search_std/search_pro 在中文 query 上 link 字段 100% 损坏，禁用
if settings.zhipu_search_engine not in _VALID_ZHIPU_ENGINES:
    logger.warning(
        "ZHIPU_SEARCH_ENGINE='%s' may have empty link field on Chinese queries. "
        "Recommended: search_pro_sogou. See exp4 report.",
        settings.zhipu_search_engine,
    )
```

**验证**：日志启动时不出 warning。

---

### A.6 composer `_MAX_POOL_ARTICLES` 提到 1000（修复警告 5）

> **修订背景**：oracle 警告 5 指出 count=50 后单次 ingester 写入 ~1500 条，但 `composer.gather_seeds()` 的 `LIMIT 200` 会丢弃 87% 候选。Phase A 调 count 必须同步调 LIMIT。

**依据**：警告 5

**文件**：`app/services/composer.py:20`

```diff
- _MAX_POOL_ARTICLES = 200
+ _MAX_POOL_ARTICLES = 1000
```

**为什么是 1000**：
- count=50 × 41 模板 ≈ 2050 候选/ingester run，URL 去重后 ~1500 条
- 双源（Phase B 后）= ~3000 条/ingester run
- LIMIT 1000 留出余量但不爆 MinHash O(n²) 计算
- 1000 条 × 平均 800 字 summary ≈ 800KB 内存，可接受

**风险评估**：
- MinHash 去重 O(n²)：n=200 时 4 万次比对（~10ms），n=1000 时 100 万次比对（~250ms）。可接受。
- 内存：800KB，零风险。
- 数据库 SELECT：`ORDER BY ingested_at DESC LIMIT 1000` 走索引，不影响性能。

**验证**：
- `gather_seeds()` 返回的候选数从 ~30 提到 ~50-80（受 per-language 15 条限制约束）
- 上限提到 1000 后，per-language 15 条限制可能成为新瓶颈——观察后再调

**回滚**：改回 `_MAX_POOL_ARTICLES = 200`。

---

### Phase A 验收清单

**结构性检查**：
- [ ] env 变量 `BOCHA_SEARCH_COUNT=50`
- [ ] config.py 默认值同步
- [ ] bocha_search.py 新增 `summary_full` + `snippet_raw` 字段
- [ ] zhipu_search.py 必传 `content_size=high`
- [ ] zhipu_search.py 新增 `summary_full` 字段映射
- [ ] zhipu_search.py 加 engine 校验 warning
- [ ] composer.py `_MAX_POOL_ARTICLES = 1000`

**质量回归检查（与 Phase -1 基线对比，统一标准）**：
- [ ] 触发一次 shadow_mode 日报
- [ ] `last-run.publish_grade` ≥ 基线
- [ ] `last-run.scores.daily_report_score` 不下降超过 5 分
- [ ] 文章数 / 板块数 ≥ 基线
- [ ] `eval_runner.evaluate_report()` 的 `weighted_total` ≥ 基线 - 0.3

**指标增益检查**：
- [ ] Bocha 调用次数与基线相同
- [ ] Bocha 单次返回数：10 → 38-50
- [ ] 智谱 content 字段长度 p50：100-500 → 3000-7000
- [ ] composer.gather_seeds 返回的候选数：~30 → ~50-80

**Phase A 工作量**：30 分钟
**Phase A 风险**：极低（向后兼容，只新增字段不删字段）

---

## 四、Phase B：客户端时效过滤 + 双源并行（1-2 小时）

### B.1 客户端按 published_at 硬过滤新鲜度

**依据**：F4 + F5 + F8（两个搜索源的 freshness 都不可靠，但都 100% 返回 published_at）
**文件**：`app/services/composer.py`、`app/services/ingester.py`，新增 `app/services/freshness_filter.py`

**新增工具函数**：

```python
"""按 published_at 客户端硬过滤新鲜度.

Bocha freshness 参数过滤很弱 (oneDay 实际给一个月内的内容)
智谱 search_recency_filter 完全失效 (4 档召回数一致)
所以必须客户端硬过滤.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any


def filter_by_freshness(
    rows: list[dict[str, Any]],
    max_age_hours: int = 168,
    require_published: bool = False,
) -> list[dict[str, Any]]:
    """
    按 published_at 字段过滤.

    Args:
        rows: 搜索结果列表，期望有 'published_at' 字段
        max_age_hours: 最大年龄，超过则过滤掉，默认 168 小时（7 天）
        require_published: True=没有 published_at 的也过滤；False=保留

    Returns:
        过滤后的列表
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    kept = []
    for row in rows:
        pub = row.get("published_at")
        if pub is None:
            if not require_published:
                kept.append(row)
            continue
        if isinstance(pub, str):
            try:
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                if not require_published:
                    kept.append(row)
                continue
        else:
            pub_dt = pub
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        if pub_dt >= cutoff:
            kept.append(row)
    return kept
```

**调用点**（在 ingester 把搜索结果汇总后立即过滤）：

```python
# ingester.py 的 _ingest_template_searches，在 search_engine.batch_search 之后:
from app.services.freshness_filter import filter_by_freshness
results = filter_by_freshness(results, max_age_hours=168)
```

**验证**：
- 日志：`filter_by_freshness: 47 → 38 (filtered 9 stale)`
- ArticlePool 表里没有 `ingested_at - published_at > 7 days` 的记录

**回滚**：移除 import 和调用。

---

### B.2 SearchRouter 升级：Bocha + 智谱并行

**依据**：F9 + F12
**文件**：`app/services/search_router.py:106-127`

**当前**（串行 fallback）：

```python
# search_router.py:106-127 (当前)
if self._bocha and self._bocha.enabled:
    bocha_results = await self._bocha.search(...)
    if bocha_results: results = bocha_results

if not results and self._zhipu and self._zhipu.enabled:
    zhipu_results = await self._zhipu.search(...)
    if zhipu_results: results = zhipu_results
```

**改造为并行 + URL 合并**：

```python
async def search(self, query, language="zh", max_results=50,
                 freshness="oneWeek", include_domains=None, **kwargs):
    cache_key = self._cache_key(query, language, freshness, include_domains)
    if cached := self._cache_get(cache_key):
        return cached

    import asyncio
    tasks = []
    if self._bocha and self._bocha.enabled:
        tasks.append(("bocha", self._bocha.search(
            query, count=max_results, freshness=freshness,
            include_domains=include_domains,
        )))
    if self._zhipu and self._zhipu.enabled:
        tasks.append(("zhipu", self._zhipu.search(
            query, count=max_results, recency=freshness,
        )))

    # 并行，单源失败不阻塞
    results_per_source = {}
    for name, coro in tasks:
        try:
            r = await asyncio.wait_for(coro, timeout=20)
            if r:
                results_per_source[name] = r
        except Exception as exc:
            logger.warning("SearchRouter: %s failed for '%s': %s",
                           name, query[:50], exc)

    # 合并：Bocha 优先，URL 去重
    merged, seen = [], set()
    for source in ("bocha", "zhipu"):
        for row in results_per_source.get(source, []):
            url = row.get("url") or ""
            if url and url not in seen:
                merged.append(row)
                seen.add(url)

    # blocklist 过滤（保持现状）
    merged = [r for r in merged if not _is_blocked_domain(
        r.get("domain") or extract_domain(r.get("url", ""))
    )]

    self._cache_put(cache_key, merged)
    logger.info("SearchRouter '%s' (%s) → %d (bocha=%d, zhipu=%d)",
                query[:50], language, len(merged),
                len(results_per_source.get("bocha", [])),
                len(results_per_source.get("zhipu", [])))
    return merged
```

**关键点**：
- 并行不串行（实测 Bocha ~250ms + 智谱 ~1500ms，并行只要 ~1500ms）
- URL 去重（实验 0% 重叠，基本不会冲突，但保险起见还是做）
- 单源失败不影响另一源

**验证**：
- 日志：`SearchRouter '注塑机' (zh) → 86 (bocha=42, zhipu=44)`
- 候选池总数 ~翻倍
- 单调用延迟 < 2.5s

**回滚**：恢复原 search_router.py。

---

### B.3 给所有搜索调用点注入 zhipu_client（修复阻塞 2）

> **修订背景**：原 PLAN 只改了 ingester 的 search_engine。但生产真正主路径是 `DailyOrchestrator` → `ExplorerAgent`，那里有自己的 SearchRouter 实例，**ingester 改了对 Agent 实时搜索零影响**。本节列出所有需要注入 zhipu_client 的位置。

**依据**：F9（0% URL 重叠 + 0% 域名级 Jaccard）

**4 个核心注入点**（按优先级排序）：

#### B.3.1 [P0] ExplorerAgent — 主路径
**文件**：`app/services/explorer_agent.py:103-113`

当前：
```python
def _build_tools(self) -> list:
    bocha = BochaSearchClient()
    scraper = ScraperClient()
    from app.services.search_router import SearchRouter
    router = SearchRouter(bocha_client=bocha)  # ❌ 单源
    return [
        WebSearchTool(bocha_client=bocha, search_router=router),
        ...
    ]
```

改成：
```python
def _build_tools(self) -> list:
    from app.services.zhipu_search import ZhipuSearchClient
    bocha = BochaSearchClient()
    zhipu = ZhipuSearchClient()  # 新增
    scraper = ScraperClient()
    from app.services.search_router import SearchRouter
    router = SearchRouter(bocha_client=bocha, zhipu_client=zhipu)  # ✅ 双源
    return [
        WebSearchTool(bocha_client=bocha, search_router=router),
        ...
    ]
```

**这是双源生效的核心改动**——DailyOrchestrator 跑日报时 3 个 ExplorerAgent 都走这条路径。

#### B.3.2 [P0] ingester search_engine 注入 zhipu_client
**文件**：`app/services/ingester.py:106-117`

```python
@property
def search_engine(self):
    if self._search_engine is None:
        from app.services.bocha_search import BochaSearchClient
        from app.services.zhipu_search import ZhipuSearchClient
        from app.services.search_engine import SearchEngine
        from app.services.search_router import SearchRouter
        bocha_client = BochaSearchClient()
        zhipu_client = ZhipuSearchClient()  # 新增
        self._search_engine = SearchEngine(
            bocha_client=bocha_client,
            search_router=SearchRouter(
                bocha_client=bocha_client,
                zhipu_client=zhipu_client,  # 新增
            ),
        )
    return self._search_engine
```

#### B.3.3 [P1] DailyReportAgent fallback 路径
**文件**：`app/services/daily_report_agent.py:307-322`

DailyReportAgent 是 DailyOrchestrator 失败时的 fallback。同样需要双源以保持降级路径质量。

```python
zhipu = ZhipuSearchClient()  # 已存在
from app.services.search_router import SearchRouter
router = SearchRouter(bocha_client=bocha, zhipu_client=zhipu)  # 新增
agent_tools = [
    WebSearchTool(bocha_client=bocha, search_router=router),  # 改：用 router 不传 zhipu_client
    ...
]
```

#### B.3.4 [P1] research_agent
**文件**：`app/services/research_agent.py:277-286`

研究助手对外暴露给用户用。改造后用户搜索也享受到双源。

```python
from app.services.bocha_search import BochaSearchClient
from app.services.zhipu_search import ZhipuSearchClient
from app.services.search_router import SearchRouter

bocha = BochaSearchClient()
zhipu = ZhipuSearchClient()
router = SearchRouter(bocha_client=bocha, zhipu_client=zhipu)
tools = [
    WebSearchTool(bocha_client=bocha, search_router=router),  # 改：用 router
    ...
]
```

#### B.3.5 [P2] scout_agent (可选)
**文件**：`app/services/scout_agent.py:76-86`

scout_agent 用 ai-search（Bocha 独有），不强求双源。可保持现状。

**验证（覆盖所有 4 个注入点）**：

- [ ] `last-run.search_health` 同时含 `bocha` 和 `zhipu_sogou` 两个键
- [ ] DailyOrchestrator 跑日报时，3 个 ExplorerAgent 的日志都出现 `SearchRouter '...' → N (bocha=X, zhipu=Y)`
- [ ] ingester run 日志同上
- [ ] ArticlePool 表 `provider` 字段同时出现 `bocha` 和 `zhipu_sogou`（提示：当前 BochaSearchClient 写 `"bocha"`，ZhipuSearchClient 写 `"zhipu"`，需要核实统一）

---

### B.4 缩减搜索模板触发频率（警告 8 处理）

> **修订背景**：oracle 警告 8 提到双源对不同 query family 成本差异未讨论。本节给出**初期保守策略**：上线后观察 1 周再决定是否做"按 family 选择性双源"。

**初期策略**：所有 41 模板都双源，观察实际数据后再优化。

**1 周观察期后的决策点**：
- 如果某 family 的智谱召回**平均字数 < 1500 字**（远低于 7000 字基准），考虑该 family 单源
- 如果某 family 的智谱+Bocha 重叠率 > 30%（远高于实验 0%），考虑该 family 单源
- 否则维持双源

观察期数据来自 Phase 0.5 加的 `search_health.zhipu_sogou.p50_summary_chars` 字段。

---

### Phase B 验收清单

**结构性检查（4 个搜索注入点都改完）**：
- [ ] `freshness_filter.py` 新文件 + 单元测试
- [ ] ingester.py 调用 filter_by_freshness
- [ ] search_router.py 改并行 + URL 去重
- [ ] **ExplorerAgent (`explorer_agent.py:107`) 注入 zhipu_client** ⭐主路径
- [ ] **ingester search_engine (`ingester.py:113`) 注入 zhipu_client**
- [ ] DailyReportAgent fallback (`daily_report_agent.py:322`) 改用 search_router
- [ ] research_agent (`research_agent.py:286`) 改用 search_router

**质量回归检查（与 Phase -1 基线对比）**：
- [ ] `last-run.publish_grade` ≥ 基线
- [ ] `last-run.scores.daily_report_score` 不下降超过 5 分
- [ ] 文章数 / 板块数 ≥ 基线
- [ ] `eval_runner.evaluate_report()` 的 `weighted_total` ≥ 基线 - 0.3

**指标增益检查（用 Phase 0.5 加的字段）**：
- [ ] `last-run.search_health` 同时含 `bocha` 和 `zhipu_sogou` 两个键
- [ ] DailyOrchestrator 的 3 个 ExplorerAgent 都触发双源（看 explorer 日志的 `(bocha=X, zhipu=Y)` 记录）
- [ ] 候选池总数从 ~50-80 提到 **~80-150**（双源 0% 重叠 → 接近翻倍）
- [ ] ArticlePool 表 `provider` 字段同时出现 `bocha` 和 `zhipu_sogou`
- [ ] 单次 SearchRouter.search 延迟 < 2.5s（智谱较慢源决定）
- [ ] freshness_filter 过滤率 >= 0%（即过滤逻辑生效，不一定每次都过滤）

**Phase B 工作量**：1.5-2.5 小时（4 个注入点比原 PLAN 多 3 个）
**Phase B 风险**：中-高（涉及 ExplorerAgent 主路径行为变化，必须 shadow_mode 至少 1 天验证）


---

## 五、Phase C：read_page 改造 + 智谱 reader 兜底（半天）

### C.1 lazy read_page —— 按价值惰性触发（修复阻塞 3）

> **修订背景**：原 PLAN 承诺减 60-70% read_page 调用，但实测数据**完全不支持这个数字**：
> - exp1 Q6：**98.6% Bocha summary 触顶 800 字**
> - 原 lazy_read 决策表里 Bocha + summary≥790 → "需要 read_page"
> - → Bocha 来源**几乎全部仍要 read_page**，lazy_read 对 Bocha 几乎零跳过
> - 真正受益的只有智谱来源，且要等 Phase B 后才进系统
> 原 PLAN 把 exp2 Q6（"summary 信息充分性 41%"）和 exp1 Q6（"字数触顶 98.6%"）两个不同概念混用了。本节修订决策表，给出**真实可达**的减少率预期。

**依据**：exp1 Q6 + exp2 Q6 + exp2 Q7（原始数据，重新解读）

**文件**：新增 `app/services/lazy_read.py`，调用点在 `composer.py` 的 gather_seeds 后

#### 真实数据画像（重新整理）

| 来源 | summary 字数特征 | 与全文比 | 决策 |
|------|----------------|---------|------|
| 智谱 sogou + content_size=high | 平均 7000 字（中文）/ 3500 字（英文）| 1.1× （仅多 10%）| **跳过 read_page** |
| Bocha + summary 触顶 800 字（98.6% 样本）| 800 字硬截断 | 平均全文是 summary 的 3.2× | **必须 read_page** |
| Bocha + summary 未触顶（1.4% 样本，<600 字）| 短文章，summary 接近完整 | ~1× | **跳过 read_page** |
| RSS 来源 | snippet 字段不稳定（不同源结构差异大）| 无法判断 | **保守 read_page** |

#### 重新设计的决策函数

```python
"""判断候选文章是否需要 read_page 抓全文 (修订版)."""
from typing import Any

# Bocha summary 触顶字数（exp1 Q6 实测 98.6% 触顶 790-800）
BOCHA_TRUNCATION_BOUNDARY = 600  # 保守阈值: <600 字视为非截断
# 智谱 sogou + content_size=high 在中文 query 下 p50 ~3000 字（exp4_ext Q1）
ZHIPU_CONTENT_FULL_THRESHOLD = 3000


def needs_full_content(candidate: dict[str, Any]) -> tuple[bool, str]:
    """决定候选是否需要 read_page 抓全文。

    Returns:
        (needs_read_page, reason)
    """
    summary = candidate.get("summary_full") or candidate.get("snippet") or ""
    provider = candidate.get("provider") or ""
    n_chars = len(summary)

    # 智谱 sogou: content 已接近全文
    if provider == "zhipu_sogou" and n_chars >= ZHIPU_CONTENT_FULL_THRESHOLD:
        return False, f"zhipu_content_sufficient ({n_chars}c)"

    # Bocha: 仅当 summary 明显未触顶才跳过
    if provider == "bocha" and 0 < n_chars < BOCHA_TRUNCATION_BOUNDARY:
        return False, f"bocha_short_summary ({n_chars}c, likely complete)"

    # 其他情况（含 Bocha 长 summary、RSS、未知 provider）: 必须 read_page
    return True, f"need_full ({provider}, {n_chars}c)"
```

#### 真实可达的减少率预期

**前提**：Phase B 上线后，智谱来源占总候选 ~50%（双源 0% 重叠 + 各 50 条 = 智谱占一半）。

| 来源 | 占比 | lazy_read 跳过率 | 加权跳过率 |
|------|-----:|----------------:|----------:|
| 智谱 sogou | 50% | ~95%（中文 content 几乎都 ≥3000 字）| 47.5% |
| Bocha 触顶 long summary | 49.3% | 0% | 0% |
| Bocha 短 summary | 0.7% | 100% | 0.7% |
| **加权总计** | 100% | — | **~48%** |

**修订后承诺**：
- Phase B 上线 + Phase C lazy_read 完成后：read_page 调用减少 **30-50%**（不是 60-70%）
- 仅 Phase C 不上 Phase B（即仍是单 Bocha 源）：read_page 调用减少 **<5%**（基本无效）

**调用点**：在 `composer.gather_seeds()` 后给每条候选标记 `_needs_read_page` 字段。
Agent 在 evaluate_article 阶段读取该字段，决定是否调用 read_page 工具。Agent 仍可手动覆盖（保持自主性）。

#### 关键约束

- 这是**优化提示**而不是硬限制
- 改造范围限定在 composer / Agent 启动前，不改 Agent 工具签名
- **必须依赖 Phase B 完成**（如果还是单 Bocha 源，几乎没有跳过收益）

#### 验证（用 Phase 0.5 的结构化指标）

- [ ] `last-run.lazy_read_stats.skip_rate` ≥ 30%（Phase B+C 都上线后）
- [ ] `lazy_read_stats.by_provider.zhipu_sogou.skip_rate` ≥ 90%
- [ ] `lazy_read_stats.by_provider.bocha.skip_rate` < 5%（符合 Bocha summary 触顶预期）
- [ ] 文章数 / 板块数 / daily_report_score 不下降（与 Phase -1 基线对比）

#### 回滚

`needs_full_content()` 永远返回 `(True, "rollback")`，恢复全量 read_page。


---

### C.2 ScraperClient：智谱 reader 上位为主兜底，Jina 降为次兜底

**依据**：F11 + F13
**文件**：新增 `app/services/zhipu_reader.py`，修改 `app/services/scraper.py`

**为什么换**：
- Jina 是境外 API，国内可能受 GFW 干扰
- 当前 Jina 没有 API key（`.env` 没设），实际靠 trafilatura fallback
- 智谱 reader 国内可达，与智谱 search 共用 API key（同一余额）
- exp2 实测：Trafilatura + 智谱 reader 双兜底 = 100% 命中（60/60）

**新增** `app/services/zhipu_reader.py`：

```python
"""智谱 Web Reader API 客户端.

POST https://open.bigmodel.cn/api/paas/v4/reader
input: {url, timeout, return_format, retain_images}
output: reader_result.content (markdown), reader_result.title

注意:
- 单 URL 接口（无批量）
- 不返回 publish_date / author（需上游搜索 API 提供）
- 部分 URL 会 500 + code=1234，需 fallback
"""
from __future__ import annotations
import logging
import re
from typing import Any

import httpx
from app.config import settings

logger = logging.getLogger(__name__)
_READER_URL = "https://open.bigmodel.cn/api/paas/v4/reader"


class ZhipuReaderClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.zhipu_api_key
        self._request_count = 0
        self._failure_count = 0
        self._consecutive_failures = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def scrape(self, url: str, timeout: float = 25.0) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "error", "url": url, "error": "no_key"}

        payload = {
            "url": url,
            "timeout": min(int(timeout), 20),
            "return_format": "markdown",
            "retain_images": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}

        self._request_count += 1
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_READER_URL, json=payload, headers=headers)
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            return {"status": "error", "url": url, "error": str(exc)[:200]}

        if resp.status_code != 200:
            self._failure_count += 1
            self._consecutive_failures += 1
            return {"status": "error", "url": url,
                    "error": f"http_{resp.status_code}"}

        data = resp.json()
        rr = data.get("reader_result") or {}
        content = rr.get("content") or ""
        if not content.strip():
            self._failure_count += 1
            self._consecutive_failures += 1
            return {"status": "error", "url": url, "error": "empty_content"}

        image_url = ""
        m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", content[:3000])
        if m:
            image_url = m.group(1)

        self._consecutive_failures = 0
        return {
            "status": "ok",
            "url": url,
            "markdown": content,
            "title": rr.get("title") or "",
            "image_url": image_url,
            "published_at": None,
            "links": [],
        }
```

**修改 scraper.py 的 fallback 链**：

```python
class ScraperClient:
    def __init__(self, jina_client=None, zhipu_reader=None):
        self._jina = jina_client            # 仅境外站
        self._zhipu_reader = zhipu_reader   # 主兜底

    async def scrape(self, url, timeout=25):
        # 第一层: Trafilatura 直抓（国内站快）
        result = await self._trafilatura_scrape(url, timeout)
        if result["status"] == "ok" and len(result.get("markdown", "")) >= 300:
            return result

        # 第二层: 智谱 reader（exp2 验证 100% 命中）
        if self._zhipu_reader and self._zhipu_reader.enabled:
            zr_result = await self._zhipu_reader.scrape(url, timeout=timeout)
            if zr_result["status"] == "ok":
                return zr_result

        # 第三层: Jina（仅境外站可能更稳）
        if self._jina and self._jina.enabled:
            return await self._jina.scrape(url, timeout=timeout)

        return result
```

**验证**：
- fallback 路径分布：trafilatura ~93%，zhipu_reader ~5%，jina ~2%
- Jina API key 不再必需
- 综合命中率 100%

**回滚**：恢复原 scraper.py，删除 zhipu_reader.py。

---

### Phase C 验收清单

**结构性检查**：
- [ ] `lazy_read.py` 新文件 + 单元测试
- [ ] composer.gather_seeds() 给每条候选标记 `_needs_read_page`
- [ ] `zhipu_reader.py` 新文件 + 单元测试
- [ ] scraper.py 加 zhipu_reader 兜底分支

**质量回归检查（与 Phase -1 基线对比）**：
- [ ] `last-run.publish_grade` ≥ 基线
- [ ] `last-run.scores.daily_report_score` 不下降超过 5 分
- [ ] 文章数 / 板块数 ≥ 基线
- [ ] `eval_runner.evaluate_report()` 的 `weighted_total` ≥ 基线 - 0.3

**指标增益检查（用 Phase 0.5 加的字段）**：
- [ ] `last-run.lazy_read_stats.skip_rate` ∈ [30%, 50%]（修订后真实预期）
- [ ] `lazy_read_stats.by_provider.zhipu_sogou.skip_rate` ≥ 90%
- [ ] `lazy_read_stats.by_provider.bocha.skip_rate` < 5%（与 Bocha summary 触顶率一致）
- [ ] read_page 总抓取成功率 ≥ 现状
- [ ] Jina 调用次数：下降到 <5%（zhipu_reader 上位）
- [ ] 单次日报总耗时：下降 3-7 分钟（read_page 是大头）

**Phase C 工作量**：半天
**Phase C 风险**：中高（需 shadow_mode 至少 3 天验证稳定）


---

## 六、Phase D：可选增强（视情况）

### D.1 Bocha rerank 接入（待充值）

**依据**：F15
**前提**：Bocha 后台给 rerank 服务单独充值（¥0.005/次）

**改造**：候选池 > 30 条时，按 query 调一次 rerank 取 top-30。
**预期收益**：节省后续 LLM 评估成本 + 提升 top-K 质量。
**待 exp3 实测后才能给出明确收益数据**。

### D.2 query rewrite（待 exp7 结论）

只在 exp7 证明"加领域词 / 中英对译"明显提升召回质量时才做。
当前数据看，双源 0% 重叠 + count=50 后候选池已非常充足，**优先级最低**。

---

## 七、整体路线图与顺序（修订版）

```
Phase -1: 前置确认 (10 min, 必跑)
   │
   ├─→ 跑 1 次基线日报，落档真实数字
   │   ↓
   ↓
Phase 0.5: 可观测性增强 (30 min)
   │
   ├─→ 加结构化日志 + last-run 字段扩展
   │   ↓
   ↓
Phase 0: 调度频率削减 (10 min) ⭐核心，省 96% 成本
   │
   ├─→ 取消 hourly_ingester
   ├─→ composer 加 ingested_at 字段
   ├─→ DailyOrchestrator 启动前加池子健康度检查
   │   ↓
   ↓ [验证 1 天: 看 last-run.ingest_decision 字段]
   │
Phase A: 配置 + 字段语义梳理 (30 min)
   │
   ├─→ count=10→50, _MAX_POOL_ARTICLES=200→1000
   ├─→ bocha/zhipu 字段语义分离
   │   ↓
   ↓ [验证 1 天: eval_runner.weighted_total ≥ 基线-0.3]
   │
Phase B: 双源并行 (1.5-2.5h) ⭐关键，4 个搜索调用点全改
   │
   ├─→ search_router 改并行
   ├─→ ExplorerAgent (主路径) 注入 zhipu_client
   ├─→ ingester / DailyReportAgent fallback / research_agent 也注入
   ├─→ 客户端 published_at 硬过滤
   │   ↓
   ↓ [验证 1 天: search_health 含 zhipu_sogou]
   │
Phase C: lazy_read + zhipu_reader 兜底 (半天)
   │
   ├─→ lazy_read 给候选打 _needs_read_page 标签
   ├─→ scraper.py: trafilatura → zhipu_reader → jina 三层
   │   ↓
   ↓ [验证 3 天: lazy_read_stats.skip_rate ∈ 30-50%]
   │
Phase X: 收尾 (30 min)
   │
   ├─→ AGENTS.md 同步
   ├─→ 1 周观察期 → 决定是否做 Phase D
   │
   ↓
Phase D: 可选增强（rerank / query rewrite）
```

**关键节奏**：
- 每个 Phase 完成后**必须跑 eval_runner.evaluate_report 对比 Phase -1 基线**
- 每个 Phase 之间至少留 1 天 shadow_mode 观察
- 不要一口气全改

**总工作量预算**：
- 强制必做（Phase -1 / 0.5 / 0 / A / B）：~3.5 小时改代码 + 4 天观察期
- 可选（Phase C）：~半天改代码 + 3 天观察期
- 收尾（Phase X）：30 分钟

---

## 八、整体验收标准（V2 完成后）

### 8.1 调用量与成本（最关键）

| 指标 | 改造前（当前） | Phase 0 后 | Phase A 后 | Phase B 后（双源全开）|
|------|------:|------:|------:|------:|
| Bocha web-search 调用/月 | 29,520 | **1,230** | 1,230 | 1,230 |
| Bocha 月成本 | ~¥1,063 | **~¥44** | ~¥44 | ~¥44 |
| 智谱调用/月 | 0 | 0 | 0 | ~1,230 |
| 智谱月成本 | 0 | 0 | 0 | ~¥62（按 ¥0.05/次）|
| **总月成本** | **~¥1,400**（含 ai-search、agent 调用）| **~¥80** | ~¥80 | **~¥150** |
| 节省比例 | — | **94%** | 94% | 89% |

> 注：Phase 0 单独完成就能省 94%（¥1,400 → ¥80）。Phase B 引入双源后仍比当前便宜 89%，但内容量翻 9 倍以上。
> 这是"实实在在省钱 + 大幅提升质量"的方案，不是"省一点钱换更多质量"。

### 8.2 内容质量

| 指标 | 改造前 | V2 完整目标 |
|------|--------|--------|
| 单次搜索结果数（每模板）| 10 条（count=10） | **38-50 条**（count=50） |
| 候选池单条平均字数 | ~100（snippet 字段名误导）| 800（Bocha）/ 7000（智谱） |
| 单源候选池/小时 | ~410 条 | 约 1500-2000 条（Phase A 后）|
| 双源候选池（Phase B 后） | 不适用 | **~3000-4000 条**（0% 重叠互补）|
| read_page 调用次数 | 每候选 1 次 | 下降 60-70% |

### 8.3 可观测指标

| 指标 | 改造前 | V2 目标 |
|------|--------|--------|
| 单日报生成耗时 | ~15 分钟 | 10-12 分钟 |
| 单日报文章数 | 6（complete） | ≥ 6（不下降）|
| daily_report_score | 基线 | ≥ 基线 |
| publish_grade | complete | complete 或更好 |
| Jina 调用占比 | ~0%（无 key）| 仍 ~0%（zhipu_reader 上位）|

---

## 九、回滚预案

每个 Phase 独立可回滚：

| Phase | 回滚方式 | 影响 |
|-------|---------|------|
| **0** | **恢复 `main.py:395` 的 `hourly_ingester` 调度即可** | **零影响（数据不丢，调度恢复）**|
| A | env 改回 + 删除新增字段 | 零影响（向后兼容） |
| B | 恢复 search_router.py + ingester.py 原状 | ingester 回到单 Bocha |
| C | 恢复 scraper.py + 删除 lazy_read/zhipu_reader | read_page 全量触发 |
| D | 不接入即可 | 无 |

> **Phase 0 是最安全的回滚点**：万一发现"取消 hourly 后日报触发时池子空了"，立刻恢复 hourly 即可，整个改动只动了 `main.py:395` 一行。

---

## 十、与 V1 PLAN 的关键差异

| 维度 | V1（旧） | V2（新） | 原因 |
|------|---------|---------|------|
| **核心痛点** | "Bocha 用得不对" | **"hourly 调度浪费 99%"**（V1 没意识到）| V2 算清成本后发现 hourly 才是大头 |
| 第二搜索源 | DDG + Brave + AnySearch | 智谱 search_pro_sogou | DDG 中国 IP 403、Brave HTML 违 ToS、AnySearch 端点不存在 |
| JSON-LD 抽取 | 自写函数 | 复用 trafilatura 内置 | trafilatura 已支持 |
| Jina 改造 | 加 CircuitBreaker | 降为次兜底，主用 zhipu_reader | 国内可达性更好 |
| count 默认 | 未明确 | **count=50** | exp1 实测免费午餐 |
| summary 字段 | 未提及 | 字段语义梳理 | 发现已被错塞进 snippet |
| 时效过滤 | 依赖 freshness 参数 | 客户端按 published_at 硬过滤 | 实测两个 API 都不可靠 |
| rerank | 未提 | Phase D 可选 | 待充值后实测 |
| **成本目标** | 月成本 ¥7.5 → ¥0.25（**与现实脱节**）| 月成本 ¥1,400 → ¥80 → ¥150 | V2 算的是真实账，V1 数字虚低 |

---

## 十一、数据文件索引

```
experiments/search_v2/
├── fixtures/queries.py              # 6+12 真实生产 query 样本
├── exp1_bocha_params.py             # Bocha 参数扫荡脚本
├── exp2_summary_value.py            # 内容获取四方对比脚本
├── exp3_bocha_rerank.py             # Bocha rerank 脚本（待充值后跑）
├── exp4_zhipu_variants.py           # 智谱三引擎脚本
├── exp4_ext_zhipu_sogou.py          # 智谱 sogou 全参数扫荡脚本
├── analyze_*.py                     # 分析脚本
├── results/
│   ├── exp1_raw.json (~5.5MB)       # 144 次 Bocha 调用
│   ├── exp2_raw.json (~2MB)         # 60 URL 抓取结果
│   ├── exp4_ext_raw.json (~21MB)    # 72 次智谱调用
│   └── *_summary.csv                # 透视表
└── reports/
    ├── exp1_report.md
    ├── exp2_report.md
    ├── exp4_report.md
    └── exp4_ext_report.md
```



---

## 十二、Phase X：收尾（V2 上线后必做，30 分钟）

> **修订背景**：oracle 警告 6 提到 AGENTS.md 38 天没更新，已经过期严重。Phase 0/A/B/C 全部上线后，必须把架构文档同步到真实状态。

### X.1 AGENTS.md 同步任务清单

需要更新的章节：

| AGENTS.md 章节 | 当前过期内容 | 更新为 |
|---------------|------------|------|
| 二、架构原则 | "Agent 自主 + 轻量检查点" | 加上"DailyOrchestrator 多 agent 模式（默认）+ DailyReportAgent fallback"，二选一描述 |
| 三、3.2 核心模块 | 没有 DailyOrchestrator/ExplorerAgent/SectionEditorAgent | 新增 |
| 三、3.3 数据流 | "ContinuousIngester（每小时）→ ..." | 改成"ContinuousIngester（按需触发，由日报启动前的池子健康度检查触发）；hourly_rss_ingester 仅拉 RSS" |
| 四、4.0 成本实测 | "单次日报 token ~250K，月 LLM 成本 ~¥7.5" | 修正为：旧基线 ~¥1,400/月（含 ingester 大头），V2 完成后 ~¥80-150/月 |
| 四、4.1 当前技术栈 | "Bocha Web Search ✅" | 加上"智谱 search_pro_sogou ✅（Phase B）" |
| 五、5.1 已完成 / 5.2 待完成 | 旧的 Phase 1/2/3 等 | 替换为本次 V2 的 Phase 状态 |
| 八、8.6 诊断 API | 没有 ingest_decision / lazy_read_stats 字段说明 | 加上 Phase 0.5 引入的新字段 |

### X.2 实验数据归档

- [ ] 把 `experiments/search_v2/` 整个目录在 `AGENTS.md` 九、关键参考资料中加链接
- [ ] 在 PLAN_V2 顶部标注"已落地，归档时间 YYYY-MM-DD"
- [ ] 旧的 SEARCH_OPTIMIZATION_PLAN.md (v1) 改名为 `SEARCH_OPTIMIZATION_PLAN_v1_deprecated.md`

### X.3 1 周观察期任务（基于 Phase 0.5 加的指标）

- [ ] 跑 7 天，每天看 `last-run.search_health.zhipu_sogou.p50_summary_chars`
- [ ] 看每个 query family 的智谱召回质量（来自 ingester 日志）
- [ ] 决定是否做"按 family 选择性双源"（Phase B.4 的 1 周观察期决策点）
- [ ] 决定是否做 Phase D（rerank / query rewrite）

### X.4 Phase X 验收

- [ ] AGENTS.md 同步完成，关键链接指向最新代码
- [ ] PLAN_V2.md 文末标注"已落地"
- [ ] 1 周观察期数据落档为新报告 `experiments/search_v2/reports/post_v2_observation.md`

---

## 十三、本次修订相比初稿的差异

| 修订点 | 初稿问题 | 本版修复 |
|-------|--------|--------|
| 主路径 | 假设 daily_report_agent.py 是主路径 | 修正为 DailyOrchestrator → ExplorerAgent |
| 阻塞 1 | `_seeds_too_stale` 访问不存在的字段 | 在 composer.gather_seeds() 加 `ingested_at` 字段 |
| 阻塞 2 | Phase B 漏改 ExplorerAgent 等 3 个搜索调用点 | B.3 列出 4+1 个注入点 |
| 阻塞 3 | lazy_read 60-70% 减少率虚高 | 修正为 30-50%（仅 Phase B 后才生效） |
| 阻塞 4 | 可观测性零设计 | 新增 Phase 0.5（结构化日志 + last-run 字段扩展）|
| 警告 5 | `_MAX_POOL_ARTICLES=200` 与 count=50 不匹配 | A.6 改为 1000 |
| 警告 6 | AGENTS.md 过期 | 新增 Phase -1 取生产基线 + Phase X 同步文档 |
| 警告 7 | daily_report_score 无基线 | Phase -1 落档基线 + 各 Phase 验收用 eval_runner 对比 |
| 警告 8 | 双源策略未讨论 query family 差异 | B.4 加 1 周观察期决策点 |

**修订工作量**：从初稿"PLAN_V2 已完整"到本版"PLAN_V2 真正完备"，约 60 分钟。

