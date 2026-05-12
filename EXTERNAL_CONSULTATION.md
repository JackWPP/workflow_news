# 项目外部咨询文档

> **用途**：本文档用于向导师、有经验的开发者、或其他外部咨询对象展示项目的完整情况，寻求有针对性的建议。  
> **最后更新**：2026-05-08

---

## 一、项目概述

### 1.1 我们做什么

**高分子材料加工领域每日资讯平台** —— 一个 AI 驱动的垂直领域研究情报系统。

核心功能：每天自动检索全球范围内的高分子材料加工相关新闻、政策、学术成果，经过质量评估和去重后，生成结构化的中文日报，并提供交互式研究助手对话。

### 1.2 技术栈

| 层 | 技术 |
|---|------|
| 后端 | Python 3.10+, FastAPI, SQLAlchemy (SQLite), APScheduler |
| 前端 | Vue 3, TypeScript, Vite, Pinia |
| LLM | OpenRouter (主要), Kimi/Moonshot (备用), DeepSeek V3 |
| 搜索 | Brave Search, 智谱搜索, Tavily, RSS |
| 内容提取 | Firecrawl, Jina Reader, Trafilatura (三层 fallback) |
| Agent 系统 | 自研 Agent Loop（非 LangChain），含 Harness 安全约束 + WorkingMemory 工作记忆 |

### 1.3 项目规模

- 后端约 8,000 行 Python（服务层）
- 前端约 3,000 行 Vue/TypeScript
- 18 个数据库模型
- 10 个 Agent 工具
- 3 个测试文件（约 160KB）
- 开发周期：2025 年 12 月至今（约 5 个月）

---

## 二、架构现状

### 2.1 整体流程

```
定时任务（每日 10:00）
  │
  ▼
Phase 1: 搜索发现
  AgentCore（LLM 自主决策搜索策略）
  → web_search × 12+ 轮 → 结果存入 WorkingMemory
  │
  ▼
Phase 2: 并发文章处理
  N × ArticleAgent（每篇文章：read → evaluate → search_images → verify_image）
  │
  ▼
Phase 2.5A: Supervisor Loop（覆盖不足时再搜一轮）
Phase 2.5B: Link Validation（检查所有链接有效性）
  │
  ▼
Phase 3: 综合撰写
  AgentCore（LLM 去重 + 写章节 + 完成日报）
  │
  ▼
Phase C: 持久化到数据库 → 前端展示
```

### 2.2 架构演进历史

| 时期 | 范式 | 核心方式 |
|------|------|---------|
| 2025.12（Coze 时代） | 外包黑盒 | Coze 工作流 API 处理一切 |
| 2026.03（Pipeline 时代） | 确定性流水线 | 代码决定每一步，LLM 仅用于评估和生成 |
| 2026.04（Agent 时代） | LLM 自主循环 | LLM 决定搜索什么、何时评估、何时完成 |

---

## 三、当前面临的核心问题

### 3.1 Token 消耗过高

**问题描述**：每次日报生成消耗大量 LLM token（日均估算 50,000-100,000 tokens），主要浪费在 Phase 1 的搜索循环中。

**根因分析**：
- Phase 1 使用 LLM Agent 循环决定搜索策略——这是一个需要"广度"而非"推理"的操作
- LLM 每轮搜索前要"思考"该搜什么 → 调用 web_search → 看结果 → 再"思考"下一步
- 这个过程至少 12 轮 LLM 调用，每轮携带完整 message history + tool definitions + working memory context
- 类似于"雇博士翻黄页"——每一步都在烧钱但产出并不比脚本更好

**Token 消耗估算**（单次日报生成）：

| 环节 | Token 消耗 | 是否必要 |
|------|-----------|---------|
| Phase 1 AgentCore 搜索循环 | ~40,000 | ❌ 可用确定性模板替代 |
| kimi reasoning_content 开销 | ~15,000 | ❌ 仅搜索阶段携带 |
| Working memory context 注入 | ~10,000 | 部分必要 |
| Phase 2 逐篇 LLM 评估 | ~15,000 | 可批量化减少 |
| Phase 2.5A Supervisor Loop | ~20,000 | ❌ 覆盖不足时的额外浪费 |
| Phase 3 综合撰写 | ~10,000 | ✅ 真正需要 LLM 推理 |

### 3.2 结果不稳定

**问题描述**：同样的配置和 prompt，每天产出的日报质量波动大。有时能搜到 8-10 篇好文章，有时只有 2-3 篇。

**根因分析**：
- LLM 自主决定搜索策略 → 不可复现
- 今天搜了 12 个好 query，明天可能搜了 8 个就提前 finish
- 搜索结果的随机性 + LLM 决策的随机性 = 双重不稳定

### 3.3 Agent 分工边界模糊

**问题描述**：
- DailyReportAgent 承担了过多职责（搜索策略 + 协调 + 合成）
- ArticleAgent 名义上是 agent，实际是确定性 pipeline（read → evaluate → search_images → verify_image）
- Supervisor Loop 和 Fallback 模式增加了多条代码路径，难以测试和调试

### 3.4 导师反馈的新需求

- UI 需去除英文，统一使用中文
- 需区分"全球日报"和"实验室日报"两个独立板块
- 内容需按"高材制造"、"清洁能源"、"AI"三个方向分类
- 需同时检索全球文献和本实验室文章
- 中英文文献需分开展示

---

## 四、我们设想的改进方向

### 方向 A：从 "Agent-First" 转向 "Workflow + Agent 混合"

**核心思想**：确定性操作用 workflow（模板搜索、规则过滤、批量评估），判断性操作用 agent（去重分析、报告撰写）。

**具体改动**：

1. **搜索去 Agent 化**（省 50-70% Phase 1 token）
   - 预定义搜索模板（6 维度 × 中英文 × 多关键词 = 30-40 个固定 query）
   - 直接批量调用搜索 API，零 LLM 调用
   - RSS 种子源继续工作

2. **评估批量化**（省 40-50% Phase 2 token）
   - 规则预筛（域名质量、关键词命中）→ 通过的文章打包一次 LLM 调用批量评估

3. **Agent 只保留在合成阶段**
   - 去重分析 + 报告撰写 = 这才是 LLM 真正发挥价值的地方

**预期效果**：

| 指标 | 现状 | 目标 |
|------|------|------|
| 单次日报 LLM 调用次数 | 30-50 次 | 5-10 次 |
| 单次日报 Token 消耗 | 50k-100k | 15k-25k |
| 结果稳定性 | 波动大 | 可复现 |
| 月运营成本 | ~545 CNY | ~200-300 CNY |

### 方向 B：继续走 Agent 路线但加固 Harness

**核心思想**：保持 Agent 自主决策的架构，但通过更强的约束和引导让 Agent 行为更可控。

**具体改动**：
- 更强的 system prompt 引导（分阶段、分节奏）
- 更细粒度的 harness 预算管理
- 更好的 working memory context 注入
- 搜索结果缓存和复用

**风险**：不解决根本矛盾（让 LLM 做确定性操作），token 消耗改善有限。

### 方向 C：回归确定性 Pipeline

**核心思想**：放弃 Agent 架构，回到 Pipeline 时代的确定性流程。

**优势**：可复现、可调试、token 消耗最低  
**风险**：灵活性差，难以适应新的搜索需求

---

## 五、我们希望获得的外部建议

### 5.1 架构方向

1. **方向 A（Workflow + Agent 混合）是否是更好的选择？** 您在实践中看到成功的 AI 资讯/情报类项目采用了什么架构模式？

2. **Agent 的合理使用边界在哪里？** 我们目前的判断是"搜索不应用 agent，合成应该用 agent"——这个判断是否合理？

3. **是否有我们没考虑到的其他架构模式？** 例如 event-driven 的信息采集、流式处理等。

### 5.2 成本控制

4. **对于日均生成一次的资讯类应用，怎样的 token 消耗是合理的？** 我们的目标 15k-25k tokens/次 是否仍然偏高？

5. **批量评估 vs 逐篇评估的 trade-off 是什么？** 批量评估是否会降低评估质量？

### 5.3 内容质量

6. **如何衡量 AI 生成日报的质量？** 我们目前有 quality feedback 系统，但缺乏系统的评估框架。

7. **中英文混合搜索的策略建议？** 如何平衡中文和英文信息源的质量和覆盖？

### 5.4 工程实践

8. **对于自研 Agent 框架（非 LangChain），有哪些常见陷阱？** 我们已经在消息历史管理、超时保护、异常分类等方面做了规划，是否还有遗漏？

9. **SQLite 用于生产环境的经验？** 当前数据量不大（日均 10-20 条记录），但长期来看是否需要迁移到 PostgreSQL？

### 5.5 类似项目参考

10. **您是否知道类似的 AI 资讯/情报聚合项目？** 无论是开源项目还是商业产品，我们希望了解别人是怎么解决这类问题的。

---

## 六、附录

### A. 核心文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `app/services/daily_report_agent.py` | ~1000+ | 主编排器（三阶段 + supervisor + fallback） |
| `app/services/agent_core.py` | ~430 | Agent 循环引擎 |
| `app/services/tools.py` | ~1100 | 10 个工具实现 |
| `app/services/working_memory.py` | ~410 | Agent 工作记忆 |
| `app/services/harness.py` | ~250 | 安全约束 |
| `app/services/llm_client.py` | ~360 | 统一 LLM 客户端 |
| `app/services/article_agent.py` | ~230 | 文章处理子 Agent |
| `app/services/pipeline.py` | ~2100 | 旧版确定性 Pipeline（保留作为 fallback） |
| `New_PLAN.md` | ~500 | 已规划的 5 阶段改进方案 |

### B. 成本估算

| 项目 | 费用 |
|------|------|
| 开发成本（一次性） | ~2,000 CNY |
| 月运营成本（API 调用） | ~545 CNY |
| 主要 API 消耗来源 | OpenRouter LLM (~70%), Brave Search (~15%), Firecrawl (~10%) |

### C. 运行环境

- 操作系统：Windows 11 Pro / Linux (Ubuntu)
- 数据库：SQLite (WAL mode)
- 部署：pm2 + nginx 反向代理
- 定时任务：APScheduler (cron: 10:00 daily)
