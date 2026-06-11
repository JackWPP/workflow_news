# workflow_news 问题清单与改造设计

> 编写时间：2026-06-11
> 基于：对 `main` 分支（最新提交 17ba7c6, 5/14）的完整代码审查 + 对《workflow_news 系统诊断与改造计划》（`workflow_news_diagnosis_plan.md`, 2026-05-27）的逐条核对
> 相关文档：`IMPROVEMENT_PLAN.md`（更早的初步方案，关注 token 成本；本文覆盖范围更全，以本文为准）
>
> 目标：
> 1. **每天都有稳定的日报产出**（编辑环节不受任何外部 API 抖动影响）
> 2. **搜索更智能**（保留 Agent 的发散搜索/思考/总结能力，同时具备 workflow 级的稳定性）

---

## 一、TL;DR

诊断文档（5/27）列出的 P0/P1 问题**截至本文撰写全部未修复**。在此之外，本次审查发现了若干诊断文档没有覆盖的、更深层的根因：

1. **种子对 Agent 完全不可见**——不是 prompt 没强调，而是种子 URL 从未出现在任何 LLM 可见文本中，Agent 在第一步除了 web_search 没有别的选择；
2. **Bocha 客户端不校验响应体 `code` 字段**——余额耗尽被当成"搜索无结果"处理，还会重置健康计数器，导致整套熔断机制对最常见的故障模式失明；
3. **多 provider 容灾是半成品**——`WebSearchTool` 接收 `zhipu_client` 参数后直接丢弃；`ScraperClient` 的 `browser_fallback` 参数没有任何实现；
4. **解析链路的超时预算是错的**——三层降级总耗时 60-90s，被外层 25s 一刀切，第二、三层降级几乎永远轮不到执行；
5. **搜索结果零缓存、查询产出零记录**——系统无法学习"哪些查询有产出"，这是搜索无法变聪明的根本原因之一。

总体判断：**架构骨架（多层降级、多 provider、Agent + Harness）大体正确，但关键接线断了、预算管理错误、该专业化的地方在用通用工具硬扛。改造方式是"补全 + 换掉两个明确的弱环节"，不是推倒重来。**

---

## 二、问题清单

### A 类：直接导致日报停产（对应目标 1）

#### A-1 种子对 Agent 完全不可见 ⭐ 最关键

- **位置**：`app/services/daily_report_agent.py:567-578`（种子灌入）、`daily_report_agent.py:1805`（task prompt 构建）、`app/services/working_memory.py:707`（to_context_summary）
- **机制**：
  - `_run_phases` 把种子写进 `memory.search_results`，但主路径的 `_build_task_prompt()` **从不枚举种子**，连"工作记忆里有种子"都没提（`fallback_candidates` 参数只在已废弃的 fallback 路径传入）；
  - `WorkingMemory.to_context_summary()` 的输出**没有任何一行涉及 `search_results`**，只有搜索次数、文章计数、阶段建议；
  - 该摘要还只在每 5 步注入一次——第一步 LLM 的上下文里种子信息为零。
- **后果**：Agent 第一步必然 web_search（它看不到任何 URL）。5/26 timeline 里"agent 没有读种子"不是模型不听话，是架构上不可能听话。Bocha 一挂，整个 run 全灭。
- **修复**：见 Phase 2 / EditorAgent——种子必须以编号列表形式出现在 task prompt 文本里。

#### A-2 Bocha 客户端不校验响应体 `code`，余额耗尽被判定为"健康"

- **位置**：`app/services/bocha_search.py:88-93`
- **机制**：拿到 HTTP 200 后直接取 `data["data"]["webPages"]`，从不检查 `data["code"]`（Bocha 余额不足时返回 HTTP 200 + body 内 `code: 403`）。更糟的是空结果分支执行 `self._consecutive_failures = 0`——余额耗尽反而把 provider 重置为 healthy。
- **后果**：`health_snapshot()` / `_should_disable_fallback_search()` 这套熔断机制**存在但传感器对最常见故障免疫**。5/26 的 30+ 次空搜索全程没有触发任何降级。
- **修复**：见 Phase 0-1。这比"加余额巡检 cron"更治本。

#### A-3 ArticlePool 种子无正文（诊断 P0-3，确认未修）

- **位置**：`app/services/ingester.py:198-231`（`_try_write_pool` 不调 scraper）
- **补充发现**：改造成本比诊断文档预估的小——`models.py:483-492` 中 `raw_content`、`quality_score`、`section`、`category`、`eval_metadata` 字段**已经存在**（微信路径已在用 `raw_content`），只缺 `fetch_status` / `fetch_attempts` / `last_fetch_at` / `image_url` / `consumed_report_ids` 几列。
- **修复**：见 Phase 1。

#### A-4 定时任务裸奔无兜底（诊断 P0-2，确认未修，范围比文档更大）

- **位置**：`main.py:177-184`（`scheduled_ai_report_run` 无 try/except）、`main.py:156-163`（`scheduled_report_run` 同样裸奔）
- **修正诊断文档的说法**：APScheduler 会隔离单次 job 失败，下次照常触发，不会"整个定时任务 crash"。真正的问题是**静默失败、无告警、无降级**。
- **修复**：见 Phase 0-2。

#### A-5 read_page 超时叠层，三层降级形同虚设（诊断 P1-3 的根因）

- **位置**：`app/services/harness.py:30`（read_page 限 25s）、`app/services/scraper.py:179`（trafilatura 层 httpx 20s）、`app/services/jina_reader.py:146-156`（Jina 层 2 次重试、每次用满 timeout）、`jina_reader.py:217`（direct_http 层 20s）
- **机制**：三层串行最坏 60-90s，外层 `asyncio.wait_for(25)` 一刀切——第二、三层降级在工具预算内几乎永远轮不到执行。同时 `runtime["scrape_timeout_seconds"]` 传给 ReadPageTool 的值可以超过 harness 的 25s，两套超时互相打架。
- **修复**：见 Phase 2-10（deadline 驱动的分层预算）。

### B 类：搜索链路的结构性问题（对应目标 2）

#### B-1 多 provider 容灾被一行代码废掉

- **位置**：`app/services/tools.py:216-217`——`WebSearchTool.__init__(self, bocha_client, zhipu_client=None)` 接收 zhipu 参数后**直接丢弃**（只存 `self._bocha`）。
- **现状**：`zhipu_search.py` 完整可用（标记 DEPRECATED），`ZHIPU_API_KEY` 在 config 中存在；git 历史（34150b5）有 Tavily 集成可捞回。搜索单点依赖 Bocha 不是设计如此，是接线断了。

#### B-2 SearchEngine 抽象被绕过，blocked-domain 逻辑散落四处

- **位置**：`search_engine.py:46`（设计良好的 source_order 路由，但只有 ingester 在用）；`WebSearchTool` 直连 Bocha 绕过它。
- **四份 blocklist 副本**：`harness.py:71`、`search_engine.py:13`、`ingester.py:54`、`daily_report_agent.py:196`。同一域名解禁要改四处。

#### B-3 搜索结果零缓存、查询产出零记录

- Ingester 每小时 21 个模板查询 + Agent 每天 30+ 次搜索，全部即用即弃。没有 query→results 缓存表；系统永远不知道哪些查询是死词。这是"搜索变聪明"的最大障碍。

#### B-4 硬编码 include_domains 启发式可能饿死搜索结果

- **位置**：`tools.py:265-280`——query 含 "polymer"、"研究" 等词时强制把 Bocha 限定在十来个学术域名内。"polymer" 是本系统几乎所有查询的核心词，大量本该开放的搜索被收窄。5/26 连 `polymer` 单词都搜不到，除了余额，这个过滤器有重大嫌疑。

#### B-5 Bocha freshness 日期区间格式未经验证

- **位置**：`tools.py:254-260`——24h 时效下自拼 `"YYYY-MM-DD..YYYY-MM-DD"` 格式。若 Bocha 实际只接受 `oneDay/oneWeek` 枚举，该参数要么被忽略要么导致请求失效。需要用真实 key 验证一次。

#### B-6 用通用 web search 干专业 API 的活

- 学术板块靠 Bocha 搜 "polymer composite research"，而 arXiv / Crossref / Semantic Scholar 有**免费、结构化、自带摘要和日期**的 API；政策板块（工信部等）页面结构固定，定向抓取比搜索可靠。

#### B-7 polymer.cn 被 block 且与 scraper 配置自相矛盾（诊断 P1-4 确认 + 补充）

- `harness.py:123` 把 polymer.cn 列入 `DEFAULT_BLOCKED_DOMAINS`（注释称"已知不可访问"），但 `scraper.py:59` 同时把它列在 `_JINA_FIRST_DOMAINS`（重点照顾名单）——一边当重点源、一边禁止访问。抓不动的站应靠 scraper 分层降级处理，不该 harness 一刀禁。

### C 类：解析链路的结构性问题

#### C-1 regex HTML→Markdown 兜底层是质量黑洞

- **位置**：`jina_reader.py:272-299`——十几个正则把整页 HTML 打碎，截断 8000 字符，导航/广告/推荐位全混入。产物被当正文喂给 `evaluate_article`，浪费 token 且污染判断，下游**没有任何标记**区分"trafilatura 干净正文"和"regex 渣滓"。

#### C-2 缺真正的浏览器层，`browser_fallback` 是断掉的接线

- **位置**：`scraper.py:93`——参数存在，全代码库无任何实现传入。中文产业站和微信文章大量依赖 JS 渲染/反爬，三层全是纯 HTTP，注定一批源永远抓不动，然后被"3 次失败 disable"机制永久杀掉——这是 RSS 源生态死亡（诊断 P1-1）的帮凶之一。

#### C-3 抓取策略写死，不会学习

- `_JINA_FIRST_DOMAINS`（`scraper.py:33`）手工维护。系统每天产生大量"某域名哪层成功"的数据，全部丢弃，每次按固定顺序重试失败层。

#### C-4 免费 Jina 只有 20 RPM

- 无 API key 时 Jina 免费档 20 RPM。Phase 1 改成"入池即抓"后每小时 100 条会直接撞限流，需要并发控制 + 限速意识。

#### C-5 每层新建 httpx client，无连接池复用

- `scraper.py:179`、`jina_reader.py:148/217`、`bocha_search.py:68` 每次请求都 `async with httpx.AsyncClient(...)`。应模块级共享连接池化的 client。

### D 类：种子消费与可观测性

#### D-1 种子无消费记录，跨天重复无人拦截

- `composer.py:36` 取 72h 窗口，ArticlePool 无 consumed 标记。同一篇文章可连续三天被灌给 Agent；当天有 MinHash 去重，跨报告没有。

#### D-2 诊断接口数据不准（诊断 P1-5 确认）

- `daily_report_agent.py:504-528` 异常路径只写 status 不写 totals；`_result_to_report` 的写回在 `daily_report_agent.py:2165-2171`，需核对所有出口都落库。`/api/diagnostics/last-run`（`main.py:1193`）已 JOIN AgentRun，问题在写入侧。

#### D-3 全系统无告警通道（诊断 P2 全部确认）

- 日报失败/降级、API 异常、RSS 源 disable、池子入量暴跌——全部只有 log，无推送。

---

## 三、目标架构

设计哲学（与诊断文档共识一致）：**记者（发现）与编辑（写作）异步解耦，通过 ArticlePool 通信。**

```
┌─────────────────────────────────────────────────────────────┐
│ Actor 1: RSS Ingester（每小时，确定性，无 LLM）               │
│   拉 RSS → 相关性过滤 → 入池即抓正文（curl_cffi+trafilatura）│
│   → og:image 预提取 → 去重入 ArticlePool                     │
├─────────────────────────────────────────────────────────────┤
│ Actor 2: Failed-Fetch Retrier（每小时，确定性，无 LLM）       │
│   重试 fetch 失败种子，3 次后标 permanent_fail                │
├─────────────────────────────────────────────────────────────┤
│ Actor 3: ResearcherAgent（每 6 小时，LLM，"记者"）            │
│   check_pool_gaps → 缺哪个方向搜哪个方向                      │
│   工具：web_search(SearchRouter) + ai_search + read_page     │
│         + evaluate_article                                   │
│   发散能力：Bocha ai_search 的 followup_questions 顺藤摸瓜；  │
│   学术缺口走 arXiv/Crossref 专业 API                          │
│   evaluate 达标才入池；budget 小（25 步）；失败无所谓          │
├─────────────────────────────────────────────────────────────┤
│              ArticlePool（统一缓冲池）                        │
│   url/title/snippet/raw_content/image_url                   │
│   fetch_status/fetch_attempts/quality_score/section         │
│   consumed_report_ids                                       │
├─────────────────────────────────────────────────────────────┤
│ Actor 4: EditorAgent（每日 10:00，LLM，"编辑"）               │
│   工具：read_pool_article（读池内正文，零网络）+              │
│         evaluate/compare/write_section/search_images/finish  │
│   ❌ 无 web_search（Harness 硬限）                            │
│   种子以编号列表显式写进 task prompt                          │
│   降级阶梯：24h→48h→72h→7天高分→休刊+告警                     │
│   失败模式只剩"种子不够"，不受任何外部 API 影响                │
└─────────────────────────────────────────────────────────────┘

横切组件：
  SearchRouter   —— 多 provider 路由 + 健康度 + 缓存 + 唯一 blocklist
  ContentGateway —— 统一抓取入口 + deadline 分层预算 + per-domain 策略学习
  AlertChannel   —— webhook 推送（失败/降级/API 异常/源禁用）
```

保留 Agent 智能的位置：**ResearcherAgent 保有全套发散搜索/追问/评估能力**（目标 2），它的失败被架构吸收；**EditorAgent 保有思考/对比/总结/写作能力**，但运行在确定性环境里（目标 1）。Workflow 的稳定性来自"编辑只跟池子对话"，不来自砍掉 LLM 的自主性。

---

## 四、分阶段改造计划

### Phase 0 — 止血（约半天，全是小改动）

| # | 改动 | 文件 | 说明 |
|---|---|---|---|
| 0-1 | **Bocha `code` 校验** | `bocha_search.py` | `data.get("code") != 200` 走失败分支、累计 `_consecutive_failures`、`_last_error="api_code_{code}"`；空结果**不再重置**失败计数，单独记录"连续空结果"，连续 ≥5 个不同 query 全空也判 degraded；加 `health_check()` 方法 |
| 0-2 | **定时任务包 try/except** | `main.py:156, 177` | `scheduled_report_run` 和 `scheduled_ai_report_run` 全身包裹，失败 log + 预留告警钩子；`ai_rss_pipeline.py` 的 `fetch_feed_entries` 单独包裹返回空列表 |
| 0-3 | **解禁 polymer.cn / 21cp.com** | `harness.py:118-125` | 移除"已知不可访问"段（与 `_JINA_FIRST_DOMAINS` 的矛盾一并解决）；保留 PR/B2B/财经类 block |
| 0-4 | **zhipu 接回 WebSearchTool** | `tools.py:216` | 存下 `self._zhipu = zhipu_client`；Bocha 不健康或返回空时自动切 Zhipu；`daily_report_agent.py` / `research_agent.py` 构造工具时传入 ZhipuSearchClient |
| 0-5 | **放宽 include_domains 启发式** | `tools.py:265-280` | 学术域名过滤只在 query 同时含明确学术意图词（"论文/journal/study"）时启用；"polymer" 单词不触发收窄。同时验证 freshness 日期区间格式是否被 Bocha 接受（B-5） |
| 0-6 | **加 `/api/diagnostics/api-health` 端点** | `main.py` | 返回 bocha/zhipu/jina/llm 各 provider 的 health_snapshot，cron 每日调用一次，异常走告警钩子 |

### Phase 1 — ArticlePool 正文化（1-2 天）

**1-1 Alembic migration**（只加缺的列；`raw_content`/`quality_score`/`section`/`category`/`eval_metadata` 已存在）：

```python
op.add_column('article_pool', sa.Column('fetch_status', sa.String(20), server_default='pending'))
# 'pending' | 'ok' | 'empty' | 'failed' | 'permanent_fail'
op.add_column('article_pool', sa.Column('fetch_attempts', sa.Integer(), server_default='0'))
op.add_column('article_pool', sa.Column('last_fetch_at', sa.DateTime()))
op.add_column('article_pool', sa.Column('image_url', sa.String(2048)))
op.add_column('article_pool', sa.Column('consumed_report_ids', sa.JSON(), server_default='[]'))
```

**1-2 `_try_write_pool` 入池即抓正文**（`ingester.py:198`）：

- semaphore 限 5 并发、单条 20s；写 `raw_content`（截断 50000 字符）+ `fetch_status` + `fetch_attempts=1` + `last_fetch_at`；
- scraper 返回的 `image_url` 顺手入池（= 配图分层 Layer 1，免费拿到）；
- 抓取失败但 RSS snippet 较长（>200 字）时标 `fetch_status='empty'` 不丢弃——snippet 足够 EditorAgent 做 evaluate；
- 注意 Jina 20 RPM 限流（C-4）：抓取优先走 trafilatura（本地、无限流），Jina 只兜底。

**1-3 Failed-Fetch Retrier**：挂在每小时 ingester 末尾。查 `fetch_status IN ('failed','pending') AND fetch_attempts < 3 AND ingested_at >= now-3d`，limit 20，重试后更新状态；第 3 次失败标 `permanent_fail`。

### Phase 2 — 编辑/记者解耦（2-3 天，核心改造）

**2-1 EditorAgent（每日 10:00）**

- 工具集（无 web_search）：

```python
editor_tools = [
    ReadPoolArticleTool(),       # 新增：直接读 ArticlePool.raw_content，零网络零超时
    ReadPageTool(...),           # 仅当池内正文缺失时补抓
    EvaluateArticleTool(...), CompareSourcesTool(...),
    WriteSectionTool(...), SearchImagesTool(...), VerifyImageTool(...),
    CheckCoverageTool(), FinishTool(...),
]
```

- **task prompt 显式枚举种子**（修复 A-1 的关键，种子必须出现在 prompt 文本里）：

```
你的种子清单（共 N 条，正文已预抓取）：
- [1] {title}（{domain}）正文✓ — {snippet[:100]}
- [2] {title}（{domain}）仅摘要 — {snippet[:100]}
...
第一步：用 read_pool_article 逐条读取种子正文并 evaluate_article。
全部种子处理完后再 compare_sources → write_section → finish。
你没有 web_search 工具。
```

- **种子选取降级阶梯**（替换 `composer.gather_seeds` 的固定 72h 窗口）：

```
24h 内 fetch_status='ok' 且未消费 ≥6 条 → 用之（label=fresh）
不足 → 48h（extended）→ 72h（archive）→ 7 天内 quality_score 最高 10 条（fallback）
→ 仍为空 → 休刊占位报告 + 告警（empty）
```

  每级把 `seed_window` 标签写入 debug_payload；成功发布后给种子 `consumed_report_ids` 打标（修复 D-1）。

**2-2 ResearcherAgent（新增每 6h cron，目标 2 的载体）**

- 工具：`web_search`（走 SearchRouter）+ `ai_search` + `read_page` + `evaluate_article` + 新增 `check_pool_gaps`（查池内各 section 过去 24h 数量）；
- 任务流：先看缺口 → 缺哪个方向搜哪个方向 → **接入现成但闲置的 `bocha.ai_search()`**（`bocha_search.py:123`，返回 AI 摘要 + `followup_questions`，agent 顺着追问继续挖，天然适合发散探索）→ evaluate 达标才入池（带 quality_score + section）；
- 学术缺口优先走 arXiv/Crossref API（见 Phase 3），不烧 Bocha 配额；
- budget 小（max_steps=25, max_duration=300s）；失败只损失增量，不影响 Editor；
- **查询产出落库**（修复 B-3 的学习闭环）：每轮把 query + 命中数写入 `search_query_log` 表；下轮运行时把"近 7 天搜过且零产出的查询"注入 prompt，避免反复搜死词。
- 注意命名冲突：现有 `research_agent.py` 是聊天问答用的 ResearchAgent，新 Actor 建议命名 `ScoutAgent` 或放 `researcher_agent.py` 以示区分。

**2-3 修超时叠层（A-5）**

- `ScraperClient.scrape(url, deadline)` 接收总 deadline，按剩余时间给各层分配预算（trafilatura 8s / Jina 10s / fallback 用剩余）；Jina 内部重试受 deadline 约束；
- harness 的 read_page 超时与 `scrape_timeout_seconds` 统一为单一来源（harness timeout = scrape budget + 3s 裕量）。

**2-4 main.py 调度调整**

- `daily_native_report` → EditorAgent；新增 researcher cron（`CronTrigger(hour="*/6")`）；全部 job 包 try/except + 告警钩子。

### Phase 3 — 搜索/解析工具链重构（2-3 天，可与 Phase 2 并行）

**3-1 SearchRouter（重构，不换主力件）**

```
SearchRouter
 ├─ 中文 web/news:  Bocha（主）→ Zhipu search_pro（备）
 ├─ 英文 web/news:  Bocha（主）→ Tavily（备，从 git 34150b5 捞回）
 ├─ 学术:           arXiv API + Crossref + Semantic Scholar（免费、结构化）
 └─ 横切:           唯一 blocklist（B-2 四份副本合并于此）
                    + search_cache 表（query_hash → results JSON，TTL 24h）
                    + 每 provider 健康度（含 code 校验）
                    + search_query_log（query → 命中数，供 Researcher 学习）
```

- `WebSearchTool` / `SearchEngine` / ingester 模板搜索全部改走 SearchRouter；
- Exa（语义搜索）可作为 Researcher 发散探索的可选增强，优先级低于 Zhipu 接回。
- 选型结论：**Bocha 对中文新闻仍是少数能打的选择，保留主力地位；问题从来不是它不好，是没有备胎 + 故障不可见。**

**3-2 解析链路（ContentGateway）**

| 层 | 现状 | 改造 |
|---|---|---|
| fetch | 裸 httpx（大量 403） | **换 `curl_cffi`**（Chrome TLS 指纹模拟，API 与 httpx 几乎一致，只动 fetch 部分，trafilatura 提取逻辑不动）⭐ 性价比最高的换件 |
| 提取 | trafilatura | **保留**（新闻正文提取的最佳开源选择，不要换） |
| 第 2 层 | Jina Reader | 保留为付费逃生通道；重试受 deadline 约束 |
| 第 3 层 | regex HTML→MD | 保留但产物标 `extraction_quality=low`，下游评估只用 snippet 不用全文（修复 C-1） |
| 第 4 层 | 无 | **Playwright / 自部署 crawl4ai**，仅供"前三层全败 + 高价值源"，放最后做（见执行顺序说明） |

- **per-domain 策略表**（修复 C-3）：`domain_scrape_stats` 记录每域名各层成功率，scrape 按历史成功率排序尝试；`_JINA_FIRST_DOMAINS` 从硬编码变数据驱动；
- 模块级共享连接池化 AsyncClient（修复 C-5）。

**3-3 RSS 源生态**（持续，照诊断文档 Phase 3 的推荐源清单执行）

- 源池从 ~5 个可用扩到 15-20 个（学术期刊 RSS / arXiv / 中文行业 RSSHub 适配 / 政策镜像）；
- RSSHub 自部署（Docker），优先适配 2-3 个高价值中文行业站；
- 源健康监控：每天统计各源 7 天入池条数；源被自动 disable 时**必须**走告警。

### Phase 4 — 运维与体验（1 天）

- **4-1 AlertChannel**：一个 webhook 函数（Server酱/钉钉，几十行），接四类事件——日报失败或降级发布、API health 异常、RSS 源 disable、池子日入量低于阈值；
- **4-2 修诊断写回**（D-2）：`daily_report_agent.py:504-528` 异常路径补写 `total_steps`/`total_tokens`；核对 `_result_to_report` 所有出口；
- **4-3 配图分层**：Layer 1 = 池内预提取 og:image（Phase 1 已带）；Layer 2 = Pexels/Unsplash 关键词图（可选）；Layer 3 = 现有 AI 分类大图兜底；
- **4-4 README 更新**：PostgreSQL 实况、四 Actor 架构图、环境变量清单。

---

## 五、执行顺序与优先级

```
第 1 优先：Phase 0（全部）          —— 半天，止血
第 2 优先：Phase 1 → Phase 2-1/2-3 —— 编辑环节先稳下来（目标 1）
第 3 优先：Phase 2-2 + Phase 3-1   —— 记者 + SearchRouter（目标 2）
第 4 优先：Phase 3-2 curl_cffi     —— 1-2 小时改动，抓取成功率立竿见影，
                                      还能救活一批被误杀的 RSS 源
持续进行：Phase 3-3 源池扩充
最后做：  Playwright/crawl4ai 浏览器层 —— 解决的是长尾 10-15%，且在 Zeabur
          容器引入浏览器依赖有部署成本；等前面改完、用 domain_scrape_stats
          数据确认还剩多少抓不动的高价值域名再决定上不上
```

如果只能做三件事：**① SearchRouter 的健康度+备胎+缓存；② curl_cffi 换 fetch 层；③ 学术/政策走专业 API。**

---

## 六、验收标准

| 目标 | 验收方法 |
|---|---|
| 稳定产出 | 拔掉 Bocha API key 跑一次 EditorAgent，日报照常产出（用池内种子）；连续 7 天无人工干预有日报 |
| 编辑零外部依赖 | EditorAgent 全程无 web_search 调用；read_page 调用次数 ≤ 种子中正文缺失数 |
| 搜索智能 | ResearcherAgent 入池文章占日报引用的比例可在 dashboard 看到（证明发散搜索在贡献增量）；`search_query_log` 中重复死词率逐周下降 |
| 故障可见 | 任何失败/降级 10 分钟内有推送；`/api/diagnostics/api-health` 准确反映 Bocha 余额耗尽（用 code 校验验证） |
| 抓取成功率 | `domain_scrape_stats` 中整体 fetch_status='ok' 比例 ≥ 70%（curl_cffi 改造后复测） |
| 诊断准确 | `/api/diagnostics/last-run` 的 total_steps/total_tokens 在 budget_exhausted 路径下不再为 0 |

---

## 附录：问题 → 修复 → 位置 速查表

| 编号 | 问题 | 修复 | 位置 |
|---|---|---|---|
| A-1 | 种子对 Agent 不可见 | 种子编号列表进 task prompt + ReadPoolArticleTool | `daily_report_agent.py:1805` |
| A-2 | Bocha 不校验 code，余额耗尽显示健康 | code 校验 + 空结果不重置失败计数 | `bocha_search.py:88-93` |
| A-3 | 种子无正文 | 入池即抓（字段大多已存在） | `ingester.py:198` |
| A-4 | 定时任务裸奔 | try/except + 告警钩子 | `main.py:156,177` |
| A-5 | read_page 超时叠层 | deadline 分层预算 | `scraper.py` `jina_reader.py` `harness.py:30` |
| B-1 | zhipu 参数被丢弃 | 接回 + failover | `tools.py:216` |
| B-2 | blocklist 四份副本 | 合并进 SearchRouter | 4 个文件 |
| B-3 | 搜索零缓存零学习 | search_cache + search_query_log | 新增 |
| B-4 | include_domains 饿死结果 | 收紧触发条件 | `tools.py:265-280` |
| B-5 | freshness 格式存疑 | 真实 key 验证 | `tools.py:254-260` |
| B-6 | 学术用 web search 硬扛 | arXiv/Crossref/S2 API | SearchRouter |
| B-7 | polymer.cn 矛盾配置 | 解禁，交给 scraper 分层 | `harness.py:123` vs `scraper.py:59` |
| C-1 | regex 兜底污染评估 | extraction_quality 标记 | `jina_reader.py:272-299` |
| C-2 | 无浏览器层 | Playwright/crawl4ai（最后做） | `scraper.py:93` |
| C-3 | 抓取策略不学习 | domain_scrape_stats 表 | `scraper.py:33` |
| C-4 | Jina 20 RPM 限流 | trafilatura 优先 + 并发控制 | Phase 1 |
| C-5 | httpx client 不复用 | 模块级共享连接池 | 多处 |
| D-1 | 种子跨天重复 | consumed_report_ids | `composer.py:36` |
| D-2 | 诊断数据不准 | 异常路径补写 totals | `daily_report_agent.py:504-528` |
| D-3 | 无告警 | AlertChannel webhook | Phase 4-1 |
