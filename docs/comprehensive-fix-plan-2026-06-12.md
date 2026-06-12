# 综合修复方案 — 2026-06-12

> 基于 Phase 1-3 架构优化 + 端到端诊断 + RSS 调研的全面修复

## 问题全景

### 已修复（Phase 1-3）
- LoginView 样式、ParticleSystem 内存泄漏、SSE 错误处理、CI 配置
- DailyReportAgent 拆分、Alembic 统一、核心索引、Tailwind CSS、conftest.py
- 异步 session_scope、Dockerfile、HTTPS + Rate Limiting
- CancelledError 崩溃、409 卡住、alembic env.py 丢失、CORS 白名单

### 待修复（本次）

#### A. 流水线问题

| # | 问题 | 优先级 | 修复方案 |
|---|------|--------|----------|
| A1 | Finish tool JSON 解析失败 | P0 | 重写 finish 工具的 description，明确 JSON 格式要求；在 _extract_finish_result 中添加 JSON 修复逻辑 |
| A2 | SSL 证书过期 | P1 | scraper 中添加 ssl_verify=False 选项（仅对已知问题站点） |
| A3 | 404 处理 | P1 | read_page 对 404 返回明确错误，不重试 |
| A4 | LLM-as-Judge 不可用 | P2 | 检查 EvalRunner 的 LLM 客户端初始化 |

#### B. RSS 源优化

| # | 问题 | 优先级 | 修复方案 |
|---|------|--------|----------|
| B1 | 10 个失效 RSS 源 | P0 | 从 seed.py 中删除，消除 warning 噪音 |
| B2 | 缺少 arXiv 源 | P1 | 新增 4 个 arXiv 分类 RSS |
| B3 | Bocha 搜索模板不足 | P1 | 扩充中文到 20+、英文到 15+ 查询 |
| B4 | Bocha 429 限流 | P2 | 在 bocha_search.py 中添加并发控制 |

#### C. 代码质量

| # | 问题 | 优先级 | 修复方案 |
|---|------|--------|----------|
| C1 | ArticleSummary 缺少必填字段 | P1 | 给 published_at/summary 添加默认值 |
| C2 | AgentResult 属性名 | P2 | 统一命名，添加别名属性 |
| C3 | 测试失败 | P2 | 修复 12 个预先存在的测试失败 |

## 执行计划

### Phase 4A：流水线修复（并行）

**任务 1：修复 Finish Tool JSON 解析**
- 修改 `app/services/tools.py` 中 FinishTool 的 description
- 在 `_extract_finish_result` 中添加 JSON 修复逻辑（处理未转义引号）
- 添加 JSON 解析的 fallback 机制

**任务 2：修复 Scraper SSL/404 处理**
- 修改 `app/services/scraper.py`，添加 SSL 跳过选项
- 修改 `app/services/jina_reader.py`，404 不重试
- 添加更详细的错误日志

**任务 3：修复 LLM-as-Judge**
- 检查 `app/services/eval_runner.py` 的 LLM 客户端初始化
- 确保评估系统可以正常调用

### Phase 4B：RSS 源优化（并行）

**任务 4：清理失效 RSS 源 + 新增 arXiv**
- 修改 `app/seed.py`，删除 10 个失效源
- 新增 4 个 arXiv 分类 RSS
- 新增 Nature/ACS/ScienceDirect 期刊 RSS
- 验证所有 RSS 源可用性

**任务 5：扩充 Bocha 搜索模板**
- 修改 `app/services/ingester.py` 中的搜索模板
- 中文扩充到 20+ 查询（新能源/生物降解/3D打印等）
- 英文扩充到 15+ 查询
- 添加并发控制避免 429

### Phase 4C：代码质量（并行）

**任务 6：修复 ArticleSummary 和 AgentResult**
- 修改 `app/services/working_memory.py`，给 ArticleSummary 添加默认值
- 修改 `app/services/agent_core.py`，统一 AgentResult 属性名
- 更新所有引用

## 验证标准

1. 完整流水线测试通过（status=complete_auto_publish）
2. 所有 RSS 源可用（无 404/403 warning）
3. Finish tool JSON 解析成功率 > 95%
4. LLM-as-Judge 正常工作
5. 所有测试通过
