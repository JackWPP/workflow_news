# 完整实施路线图 — 从 Phase 0 到最终交付

> 最后更新：2026-05-09
> 当前进度：Phase 0 ✅ → Phase 1 进行中

---

## 总体进度

```
Phase -1  [✅] 脚手架搭建 (CLAUDE.md, 调研, 咨询)
Phase 0   [✅] 评估体系 (EvaluationRun, eval_rubric, eval_runner, 评测集)
Phase 1   [  ] 采集解耦 (ArticlePool, ContinuousIngester)
Phase 2   [  ] 语义去重 + 批量评估 (SemanticDedup, BatchEvaluator)
Phase 3   [  ] 合成强化 (DailyComposer, 结构化输出)
Phase 4   [  ] 前端 + 导师需求 (UI中文化, 双日报, 三方向分类)
Phase 5   [  ] 基础设施加固 (Alembic, 备份, 可观测性)
```

---

## Phase 1: 采集解耦 (当前)

### 后端改动

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 1.1 | ArticlePool 模型 | `app/models.py` | 新表，含 content_hash + embedding + source_type + language |
| 1.2 | DB migration | `app/bootstrap.py` | SQLite migration for article_pool |
| 1.3 | ContinuousIngester | `app/services/ingester.py` (新) | RSS + 模板搜索 + 学术 API → ArticlePool，每小时运行 |
| 1.4 | SearchEngine 统一接口 | `app/services/search_engine.py` (新) | 从 tools.py 拆出搜索逻辑，统一 Brave/Zhipu/Tavily 调用 |
| 1.5 | 迁移 seed 逻辑 | `app/services/ingester.py` + `daily_report_agent.py` | 将 `_seed_trusted_source_candidates()` 移入 Ingester |
| 1.6 | 日报改为从 ArticlePool 拉取 | `daily_report_agent.py` | Phase 1 不再实时搜索，直接 SELECT from article_pool |
| 1.7 | 移除 Supervisor Loop | `daily_report_agent.py` | Phase 2.5A 删除 |
| 1.8 | 调度 ingester | `main.py` | APScheduler 增加每小时一次的 ingester job |

### 验证标准
- 连续 3 天 ArticlePool 日均新增 ≥ 30 条
- 日报生成 token 消耗下降 ≥ 30%

---

## Phase 2: 语义去重 + 批量评估

### 后端改动

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 2.1 | SemanticDedup 服务 | `app/services/semantic_dedup.py` (新) | URL → MinHash → BGE-M3 embedding 三级去重 |
| 2.2 | BatchEvaluator | `app/services/batch_evaluator.py` (新) | Map-Reduce: 小模型 map → 强模型 reduce |
| 2.3 | ContentExtractor 重构 | `app/services/content_extractor.py` (新) | 从 tools.py ReadPageTool 拆出，三层 fallback |
| 2.4 | 中英文 pipeline 分离 | `daily_report_agent.py` | 独立评估阈值，分开写入 |
| 2.5 | ArticleAgent 确定性化 | `article_agent.py` | 去掉 agent 包装，改为纯 pipeline |
| 2.6 | Embedding 服务部署 | 部署脚本 | BGE-M3 via Ollama 或 sentence-transformers |

### 验证标准
- 去重后候选集大小减少 ≥ 30%
- 单次日报 token ≤ 15k

---

## Phase 3: 合成强化

### 后端改动

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 3.1 | DailyComposer | `app/services/composer.py` (新) | 拉取 → 去重 → 评估 → 合成 统一编排 |
| 3.2 | 强模型 structured output | `llm_client.py` | JSON schema 强制，去掉自由文本解析 |
| 3.3 | 三方向分类 tag | `batch_evaluator.py` + `composer.py` | 评估阶段打 tag，不做最后分类 |
| 3.4 | Prompt 模板化 | `config/prompts/` | 所有 LLM prompt 移到独立模板文件 |
| 3.5 | 全球/实验室双 pipeline | `composer.py` | 不同搜索源、不同阈值、不同 prompt |

### 验证标准
- precision@10 ≥ 0.85
- recall@10 ≥ 0.75

---

## Phase 4: 前端 + 导师需求

### 前端改动

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 4.1 | UI 全中文化 | 所有 `.vue` 文件 | 去除所有英文文案 |
| 4.2 | 全球日报/实验室日报切换 | `DashboardView.vue` | Tab 切换，独立数据源 |
| 4.3 | 三方向分类 Tab | `DashboardView.vue` + 新组件 | 高材制造/清洁能源/AI 三个 tab |
| 4.4 | 中英文文献分组展示 | `ReportItemCard.vue` + `SectionDivider.vue` | 按语言分组，分开展示 |
| 4.5 | 评估看板 | 新 `EvaluationDashboard.vue` | 五维度评分趋势图 |
| 4.6 | 类型定义更新 | `types.ts` | 新增 report_type, categories, language 字段 |

### 后端改动

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 4.7 | API: global/lab 切换 | `main.py` | report_type 参数过滤 |
| 4.8 | 实验室文章源接入 | `ingester.py` | arXiv, 知网, 本地数据库 connector |

### 验证标准
- 用户可以独立查看全球日报和实验室日报
- 三方向 Tab 正常切换
- 中英文内容分开展示

---

## Phase 5: 基础设施加固

### 后端改动

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 5.1 | Alembic 集成 | `alembic/` | Schema 版本管理 |
| 5.2 | 清理 bootstrap | `app/bootstrap.py` | 移除 `_ensure_sqlite_schema()` |
| 5.3 | SQLite 自动备份 | `scripts/backup.py` | 每日 cron 备份 |
| 5.4 | Agent replay 能力 | `agent_core.py` | LLM 响应缓存 |
| 5.5 | Token 监控仪表板 | `main.py` + 前端 | 展示 token 消耗趋势 |

---

## 当前执行：Phase 1

按依赖关系排序：
1. ArticlePool 模型 + migration (立即开始)
2. SearchEngine 统一接口 (独立，并行)
3. ContinuousIngester (依赖 1, 2)
4. 迁移 seed 逻辑 (依赖 3)
5. 日报改为从 ArticlePool 拉取 (依赖 1, 3)
6. 移除 Supervisor Loop (依赖 5)
7. 调度 ingester (依赖 3)
