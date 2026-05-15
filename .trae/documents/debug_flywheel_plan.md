# 调试飞轮：部署后自主迭代方案

## 一、背景

项目已部署到 Zeabur（`https://buctyl.preview.aliyun-zeabur.cn/`），每次 `git push` 自动重建。
当前问题：手动触发日报后，**global + lab 日报正常生成，但 AI 日报始终缺失**，
且只能通过用户粘贴日志来排查，效率极低。

需要一个机制让 AI Agent **无需用户中转即可自主诊断、修复、验证**。

---

## 二、飞轮架构

```
┌─────────────────────────────────────────────────────────┐
│                    调试飞轮 (Debug Flywheel)             │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ Step 1   │    │ Step 2   │    │ Step 3   │          │
│  │ 读部署状态 │───→│ 分析问题  │───→│ 修改代码  │          │
│  │          │    │          │    │          │          │
│  │ /api/    │    │ 对比期望  │    │ 精确修复  │          │
│  │ version  │    │ vs 实际   │    │ + 诊断日志 │          │
│  │ health   │    │          │    │          │          │
│  │ last-run │    └──────────┘    └────┬─────┘          │
│  │ reports  │                         │                │
│  └──────────┘                         │ 用户 git push  │
│       ↑                               │ Zeabur 重建    │
│       │                               ↓                │
│       └────────── 循环 ───────────────┘                │
└─────────────────────────────────────────────────────────┘
```

### 2.1 Step 1 — 读部署状态（Agent 自主完成）

通过 HTTP API 读取生产环境信息，无需用户介入：

| 端点 | 用途 | 关键字段 |
|------|------|---------|
| `GET /api/version` | **新增**，确认代码版本是否已部署 | `git_sha`, `build_time`, `dirty` |
| `GET /api/diagnostics/health` | 检查 API/数据库连接 | `overall`, `components` |
| `GET /api/diagnostics/last-run` | 最近一次日报运行摘要 | `publish_grade`, `scores`, `key_failures`, `article_count` |
| `GET /api/reports?limit=3&report_type=ai` | 检查 AI 报告是否存在 | 空数组=AI 报告未生成 |
| `GET /api/reports?limit=3&report_type=lab` | 检查 Lab 报告是否存在 | 空数组=Lab 报告未生成 |
| `GET /api/reports/today` | 检查 combined 日报内容 | `items[].decision_trace.category` |
| `GET /api/reports?limit=5&view=combined` | 查看最近几天的 combined 日报 | |
| `GET /api/diagnostics/run/{id}/timeline` | 单次运行的时间线 | `steps[]`, `slowest_step`, `error_patterns` |
| `GET /api/diagnostics/llm-metrics` | LLM 调用错误统计 | `bad_request_count`, `model_fallbacks` |

### 2.2 Step 2 — 分析问题

对比 Step 1 获取的数据与预期，识别差异：

- `publish_grade != "complete"` → 看 `key_failures` 和 `scores`
- AI 报告数组为空 → AI pipeline 未执行或执行失败
- `health.overall != "healthy"` → 看具体哪个组件挂了
- `llm_errors.bad_request_count > 0` → LLM 请求格式问题

### 2.3 Step 3 — 修改代码

根据问题定位，精确修改代码。原则：
- 每次修改聚焦单一问题
- 在关键路径加诊断日志（`logger.info`/`logger.warning(exc_info=True)`）
- 修复后飞轮回到 Step 1

### 2.4 用户操作（最小化）

用户只需做一件事：**`git push`**。Agent 通过 API 自主确认部署是否生效。

---

## 三、需要实施的内容

### 3.1 新增 `/api/version` 端点

用于确认代码是否已部署到生产环境。返回 Git 信息：

```python
@app.get("/api/version")
async def api_version():
    return {
        "git_sha": get_git_sha(),
        "build_time": get_build_time(),
        "dirty": is_git_dirty(),
    }
```

在构建时将 git commit SHA 写入文件（`git rev-parse HEAD > git_sha.txt`），应用读取该文件。

或者在 `zbpack.json` 的 `build_command` 中加入 `git rev-parse HEAD > version.txt`。

### 3.2 更新 `AGENTS.md` — 新增"部署调试飞轮"章节

新增 **第 11 章：部署调试飞轮**，包含：

- 生产环境地址
- 诊断 API 速查表
- 标准调试流程（Step 1 → 2 → 3 循环）
- 常见问题诊断表
- 当前轮次状态记录

### 3.3 更新 `zbpack.json` — 构建时记录版本

```json
{
  "build_command": "git rev-parse HEAD > git_sha.txt && echo $(date -u +%Y-%m-%dT%H:%M:%SZ) > build_time.txt && cd frontend && npm ci && npm run build",
  ...
}
```

### 3.4 当前调试轮次：AI 日报缺失问题

这是飞轮的第一个实战用例。**当前状态（从线上读取）**：

| 指标 | 值 |
|------|-----|
| Health | `healthy` |
| Last run | `partial`, 5 articles, 3 sections |
| Lab 报告 | ✅ id=2, `complete_auto_publish` |
| **AI 报告** | ❌ **空数组，完全不存在** |
| LLM errors | 0 bad request, 0 fallback |

**分析**：AI pipeline 被 `_run_all_reports()` 调用了，但数据没有写入数据库。
代码中已经加了诊断日志（`logger.info("AI RSS pipeline: ...)`），但还未推送部署。

**下一步**：
1. 推送当前代码（含诊断日志）
2. 手动触发一次日报
3. Agent 读取 `/api/diagnostics/last-run` 确认部署
4. Agent 检查 `/api/reports?report_type=ai` 是否非空
5. 如果仍为空，读取日志中的 AI pipeline 诊断信息定位失败点

---

## 四、文件修改清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `main.py` | 新增 | `/api/version` 端点 |
| `zbpack.json` | 修改 | `build_command` 加入 `git rev-parse` |
| `AGENTS.md` | 修改 | 新增第 11 章：部署调试飞轮 |
| `app/config.py` | 修改 | 添加 `version_file` 相关配置 |

---

## 五、飞轮使用约定

### Agent 自主运行规则

1. **先读后写**：先请求 `/api/version` 确认当前部署版本，再分析问题
2. **单问题聚焦**：每次只修复一个明确的问题
3. **增量修改**：尽量通过添加诊断日志来定位，而非一次性大改
4. **状态追踪**：在 AGENTS.md 中记录当前飞轮轮次和状态
5. **用户最小化**：用户只需 `git push`，不需要手动查日志

### AGENTS.md 中飞轮记录格式

```markdown
## 十一、部署调试飞轮

### 当前轮次：Round N — 问题描述
- **目标**：xxx
- **部署版本**：`abc1234`
- **状态**：调试中 / 已修复 / 已验证
- **观察**：xxx
- **修复**：xxx
- **下一步**：xxx
```

---

## 六、实施步骤

1. [ ] 创建 `/api/version` 端点（读取 `git_sha.txt` + `build_time.txt`）
2. [ ] 更新 `zbpack.json` 的 `build_command` 写入版本文件
3. [ ] 更新 `AGENTS.md` 新增"部署调试飞轮"章节，记录当前 AI 报告轮次
4. [ ] 用户推送，等待 Zeabur 重建
5. [ ] Agent 读 `/api/version` 确认部署 → 进入飞轮
