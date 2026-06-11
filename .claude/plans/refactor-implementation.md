# 实施计划：workflow_news 分阶段改造

> 基于 `REFACTOR_PLAN_2026-06-11.md` 的四阶段方案，细化到每个 agent 的具体任务、文件、依赖关系和执行顺序。

## 执行策略

- **Worktree 隔离**：每个并行任务在独立 worktree 中工作，互不冲突
- **分波合并**：同一波次的 agent 完成后逐个 merge 到 main，解决冲突后再启动下一波
- **波次依赖**：后一波依赖前一波的合并结果

---

## 波次 1：Phase 0 止血（6 个 agent 并行）

全部基于当前 `main`，互不冲突（已确认文件交叉最小化）。

### Agent 0-1：Bocha code 校验
- **文件**：`app/services/bocha_search.py`
- **改动**：
  1. `search()` 方法 L88-93：HTTP 200 后检查 `data.get("code")`，非 200 走失败分支
  2. 空结果分支不再执行 `self._consecutive_failures = 0`，改为单独记录 `self._consecutive_empty_queries: int`
  3. 连续 ≥5 个不同 query 全空时 `health_state` 返回 `"degraded"`
  4. `health_snapshot()` 增加 `consecutive_empty_queries` 和 `last_api_code` 字段
  5. `ai_search()` 方法同步做相同 code 校验

### Agent 0-2：定时任务 try/except
- **文件**：`main.py`
- **改动**：
  1. `scheduled_report_run()`（L156-163）全身包 try/except，失败 log.error + 预留 `_alert("report_failed", exc)`
  2. `scheduled_ai_report_run()`（L177-184）同上
  3. 两个函数末尾的 logger.info 保持不变

### Agent 0-3：polymer.cn 解禁
- **文件**：`app/services/harness.py`
- **改动**：
  1. `DEFAULT_BLOCKED_DOMAINS`（L118-125）删除"已知不可访问站点"段（`21cp.com` 到 `polymer.cn` 共 5 行）
  2. 保留 PR/B2B/财经/百科/台湾媒体段不动

### Agent 0-4：zhipu 接回 + include_domains 放宽
- **文件**：`app/services/tools.py`
- **改动**：
  1. `WebSearchTool.__init__`（L216-218）：存下 `self._zhipu = zhipu_client`
  2. `execute()` 方法：在 Bocha 返回空结果后，自动 fallback 到 `self._zhipu.search(query, count=...)`（如果 zhipu 已启用）
  3. `include_domains` 启发式（L265-287）：从 `academic_keywords` 列表移除 `"polymer"` 和 `"materials science"`（这两个太宽泛，会把几乎所有查询都限定到学术域名）；只在同时含明确学术意图词（"论文/journal/study/实验室/大学/university/research"）时才启用收窄

### Agent 0-5：diagnostics/api-health 端点
- **文件**：`main.py`
- **改动**：
  1. 新增 `GET /api/diagnostics/api-health` 端点
  2. 返回 `bocha`、`zhipu`、`jina`、`llm` 各 provider 的 health_snapshot
  3. 复用 `BochaSearchClient().health_snapshot()` / `ZhipuSearchClient().health_snapshot()`

### Agent 0-6：WebSearchTool 构造处传入 zhipu（适配 0-4）
- **文件**：`app/services/daily_report_agent.py`、`app/services/research_agent.py`、`app/services/ingester.py`
- **改动**：所有构造 `WebSearchTool(...)` 的地方，加上 `zhipu_client=ZhipuSearchClient()` 参数
- **依赖**：与 0-4 并行，但合并时需确认 0-4 已合入

---

## 波次 2：Phase 1 ArticlePool 正文化（3 个 agent，前 1 个必须先完成）

### Agent 1-1：Alembic migration（必须先完成，后续依赖此 schema 变更）
- **文件**：新建 `alembic/versions/xxxx_add_pool_fetch_columns.py` + `app/models.py`
- **改动**：
  1. `models.py` ArticlePool 类新增 5 个字段：
     - `fetch_status: Mapped[str] = mapped_column(String(20), server_default='pending')`
     - `fetch_attempts: Mapped[int] = mapped_column(Integer, server_default=0)`
     - `last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime)`
     - `image_url: Mapped[str | None] = mapped_column(String(2048))`
     - `consumed_report_ids: Mapped[list] = mapped_column(JSON, default=list)`
  2. 新建 Alembic migration（`down_revision = 'd95861c536ae'`），用 `op.add_column()` 添加 5 列
  3. `requirements.txt` 添加 `alembic`（当前未列出）

### Agent 1-2：Ingester 入池即抓正文（依赖 1-1 完成）
- **文件**：`app/services/ingester.py`
- **改动**：
  1. `_try_write_pool()` 在 `session.add(article)` 之后，用 semaphore 限 5 并发调 `ScraperClient().scrape(url, timeout_seconds=20)`
  2. 写入 `raw_content`（截断 50000 字符）、`image_url`、`fetch_status='ok'`/`'empty'`/`'failed'`、`fetch_attempts=1`、`last_fetch_at`
  3. 抓取失败但 snippet > 200 字时标 `fetch_status='empty'` 不丢弃
  4. 抓取优先走 trafilatura（本地无限流），Jina 只兜底
  5. `_try_write_pool` 改为 `async`（已经是 async，确认内部可以 await scraper）

### Agent 1-3：Failed-Fetch Retrier（依赖 1-1 完成，可与 1-2 并行）
- **文件**：`app/services/ingester.py`（或新建 `app/services/pool_fetcher.py`）
- **改动**：
  1. 新增 `async def retry_failed_fetches() -> int`
  2. 查询 `fetch_status IN ('failed','pending') AND fetch_attempts < 3 AND ingested_at >= now()-3d`，limit 20
  3. 对每条调 scraper 重试，更新 `fetch_status`/`fetch_attempts`/`last_fetch_at`/`raw_content`
  4. 第 3 次失败标 `permanent_fail`
  5. `main.py` 的 `scheduled_ingester_run()` 末尾调用此函数

---

## 波次 3：Phase 2 + Phase 3 前半（4 个 agent 并行）

波次 2 合并完成后启动。此波次是核心改造，产出最大。

### Agent 2-1：ReadPoolArticleTool + EditorAgent 主体
- **文件**：`app/services/tools.py`（新增工具类）、`app/services/editor_agent.py`（新建）、`app/services/working_memory.py`
- **改动**：
  1. `tools.py` 新增 `ReadPoolArticleTool(Tool)`：从 ArticlePool 读 `raw_content`，零网络零超时；返回 title/domain/published_at/summary/full_content
  2. 新建 `editor_agent.py`：
     - 工具集：ReadPoolArticleTool + ReadPageTool（仅正文缺失时）+ EvaluateArticleTool + CompareSourcesTool + WriteSectionTool + SearchImagesTool + VerifyImageTool + CheckCoverageTool + FinishTool
     - **❌ 无 WebSearchTool**（Harness 硬限）
     - system prompt 强调"你没有搜索工具，只处理种子清单"
     - 种子选取降级阶梯：24h→48h→72h→7天高分→休刊+告警
     - **task prompt 显式枚举种子**（编号+标题+域名+snippet+正文就绪状态）
     - 成功发布后给种子 `consumed_report_ids` 打标
  3. `working_memory.py` `to_context_summary()` 增加种子列表展示（编号+标题+正文状态）

### Agent 2-2：ScoutAgent（ResearcherAgent，目标 2 的载体）
- **文件**：`app/services/scout_agent.py`（新建，避免与现有 `research_agent.py` 撞名）
- **改动**：
  1. 工具集：WebSearchTool + `BochaAiSearchTool`（新增，封装 `bocha.ai_search()`，返回 AI 摘要 + followup_questions）+ ReadPageTool + EvaluateArticleTool + `CheckPoolGapsTool`（新增，查池内各 section 过去 24h 数量）
  2. 任务流：check_pool_gaps → 缺哪个方向搜哪个方向 → evaluate 达标入池
  3. `ai_search` 返回的 `followup_questions` 注入工作记忆，agent 可顺着追问
  4. budget 小（max_steps=25, max_duration=300s）
  5. **查询产出落库**：新增 `SearchQueryLog` 模型（query, hit_count, run_at）或写入 `search_query_log` JSON 字段；下轮把"近 7 天零产出查询"注入 prompt
  6. `main.py` 新增 `scheduled_scout_run()` cron（每 6 小时）

### Agent 2-3：超时叠层修复
- **文件**：`app/services/scraper.py`、`app/services/jina_reader.py`、`app/services/harness.py`
- **改动**：
  1. `ScraperClient.scrape(url, deadline_seconds)` 新增总 deadline 参数
  2. 按剩余时间给各层分配：trafilatura 8s / Jina 10s / fallback 用剩余
  3. `JinaReaderClient._jina_scrape()` 内部重试循环检查 deadline，超时直接跳出
  4. `harness.py` `DEFAULT_TOOL_TIMEOUTS["read_page"]` 改为 `scrape_timeout_seconds + 5`（从 config 动态读取而非硬编码 25）

### Agent 3-1：SearchRouter 初版
- **文件**：`app/services/search_router.py`（新建）
- **改动**：
  1. `SearchRouter` 类：统一 `search()` 接口，内部按 `source_order` failover
  2. 中文 web/news: Bocha（主）→ Zhipu（备）
  3. 英文 web/news: Bocha（主）→ Zhipu（备）
  4. 学术: 新增 `ArxivSearchTool` / `CrossrefSearchTool`（免费、结构化 API）
  5. 唯一 blocklist（从 `harness.py` 提取，其他三处删除副本引用 SearchRouter）
  6. `search_cache` 表：Alembic migration 新增（query_hash TEXT, results JSON, provider TEXT, created_at TIMESTAMP, TTL 24h）
  7. 每 provider 健康度（含 Bocha code 校验，从 Agent 0-1 的产出接入）
  8. `WebSearchTool` / `SearchEngine` / ingester 模板搜索全部改走 SearchRouter（或渐进式：先让 ScoutAgent 用，EditorAgent 不受影响）

---

## 波次 4：Phase 2 后半 + Phase 3 后半（2 个 agent 并行）

波次 3 合并完成后启动。

### Agent 2-4：main.py 调度调整
- **文件**：`main.py`
- **改动**：
  1. `pipeline` 变量改为 EditorAgent（当 `agent_mode` 时）
  2. 新增 `scheduled_scout_run` job（`CronTrigger(hour="*/6")`）
  3. 所有 scheduler job 包 try/except + 告警钩子（与 0-2 合并确认）
  4. `/api/diagnostics/last-run` 修复异常路径写回（AgentRun totals 在所有出口落库）

### Agent 3-2：ContentGateway + curl_cffi 换件
- **文件**：`app/services/scraper.py`、`app/services/content_gateway.py`（新建）、`requirements.txt`
- **改动**：
  1. `requirements.txt` 添加 `curl_cffi`
  2. `scraper.py` `_trafilatura_scrape()` 的 httpx fetch 替换为 `curl_cffi.requests.AsyncSession`（Chrome TLS 指纹）
  3. `_fallback_scrape()` 同上
  4. 新建 `domain_scrape_stats` 表（domain TEXT, layer TEXT, success_count INT, failure_count INT, last_success_at TIMESTAMP）+ 写入逻辑
  5. scrape 时按 `domain_scrape_stats` 历史成功率排序尝试层级，替代硬编码 `_JINA_FIRST_DOMAINS`
  6. regex HTML→MD 产物标记 `extraction_quality=low`，下游 evaluate 只用 snippet

---

## 波次 5：Phase 4 运维（1 个 agent）

### Agent 4-1：告警通道 + 诊断修复 + README
- **文件**：`app/services/alert.py`（新建）、`main.py`、`README.md`
- **改动**：
  1. 新建 `alert.py`：`async def send_alert(event_type: str, message: str)`，webhook URL 从 config 读取（Server酱/钉钉，先实现钉钉）
  2. 接四类事件：日报失败或降级、API health 异常、RSS 源 disable、池子日入量低于阈值
  3. `/api/diagnostics/last-run` 修复：`daily_report_agent.py:504-528` 异常路径补写 `total_steps`/`total_tokens`
  4. README 更新：架构图、环境变量清单、部署说明

---

## 文件变更汇总

| 文件 | 涉及 Agent | 变更类型 |
|---|---|---|
| `app/services/bocha_search.py` | 0-1 | 修改 |
| `main.py` | 0-2, 0-5, 1-3, 2-4 | 修改 |
| `app/services/harness.py` | 0-3, 2-3 | 修改 |
| `app/services/tools.py` | 0-4, 2-1 | 修改 |
| `app/models.py` | 1-1 | 修改 |
| `alembic/versions/xxxx_*.py` | 1-1, 3-1 | 新建 |
| `app/services/ingester.py` | 0-6, 1-2, 1-3 | 修改 |
| `app/services/daily_report_agent.py` | 0-6 | 修改 |
| `app/services/research_agent.py` | 0-6 | 修改 |
| `app/services/scraper.py` | 2-3, 3-2 | 修改 |
| `app/services/jina_reader.py` | 2-3 | 修改 |
| `app/services/editor_agent.py` | 2-1 | **新建** |
| `app/services/scout_agent.py` | 2-2 | **新建** |
| `app/services/search_router.py` | 3-1 | **新建** |
| `app/services/content_gateway.py` | 3-2 | **新建** |
| `app/services/alert.py` | 4-1 | **新建** |
| `app/services/working_memory.py` | 2-1 | 修改 |
| `requirements.txt` | 1-1, 3-2 | 修改 |
| `README.md` | 4-1 | 修改 |

---

## 验收检查点

### 波次 1 完成后
- [ ] Bocha 返回 `code:403` 时 `_consecutive_failures` 正确累计，health_snapshot 返回 degraded
- [ ] `scheduled_report_run` 抛异常时服务不崩溃，日志有记录
- [ ] `harness.py` 不再 block polymer.cn
- [ ] `WebSearchTool` 能 failover 到 zhipu（当 bocha 返回空时）
- [ ] `/api/diagnostics/api-health` 端点可访问

### 波次 2 完成后
- [ ] `alembic upgrade head` 成功，article_pool 表有 5 个新列
- [ ] 入池文章的 `fetch_status` 为 `ok` 且 `raw_content` 非空
- [ ] 失败种子 3 次重试后标 `permanent_fail`

### 波次 3 完成后
- [ ] EditorAgent 无 web_search 工具，种子以编号列表出现在 task prompt
- [ ] ScoutAgent 能用 ai_search 的 followup_questions 做发散探索
- [ ] ScraperClient 三层降级在 25s deadline 内完成
- [ ] SearchRouter 能 failover（bocha→zhipu）且搜索结果有缓存

### 波次 4 完成后
- [ ] curl_cffi 替换 httpx 后抓取成功率提升（可通过 domain_scrape_stats 验证）
- [ ] EditorAgent 作为 daily pipeline 运行正常
- [ ] ScoutAgent 每 6 小时运行一次

### 波次 5 完成后
- [ ] 日报失败 10 分钟内有钉钉/Server酱推送
- [ ] `/api/diagnostics/last-run` 在 budget_exhausted 路径下 total_steps 不为 0

---

## 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| Zhipu API key 未配置 | 高 | .env 中 ZHIPU_API_KEY 为空；Agent 0-4 的 fallback 只在 enabled 时生效，不影响主力 |
| curl_cffi 安装在 Zeabur 容器失败 | 中 | 先在本地验证；curl_cffi 是纯 Python wheel，不需编译 |
| worktree 合并冲突 | 中 | 波次内按文件拆分最小化交叉；合并时逐个 agent 处理 |
| EditorAgent 无搜索后种子不足 | 中 | 降级阶梯 + 告警；Phase 1 的入池即抓确保种子有正文 |
| search_cache 表与 SQLite 兼容性 | 低 | JSON 字段 SQLite 原生支持 |
