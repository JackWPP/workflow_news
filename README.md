<div align="center">

# 高分子材料加工每日资讯平台

**AI Agent 驱动的垂直领域研究情报平台**

自动检索 · 智能筛选 · 结构化日报 · 研究助手

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Vue 3](https://img.shields.io/badge/Vue-3.5-4FC08D?style=flat-square&logo=vue.js&logoColor=white)](https://vuejs.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://typescriptlang.org)

</div>

---

## 平台简介

面向**高分子材料加工**领域的智能研究情报平台。通过 Agent 自主搜索 + Bocha 搜索引擎 + RSS 订阅，每天自动生成高质量中文日报，覆盖高材制造、清洁能源、AI 三个方向。

### 核心特性

| 能力 | 说明 |
|------|------|
| **Agent 自主日报生成** | AgentCore 自主搜索→阅读→评估→写作，4 个检查点确保进度 |
| **Bocha AI 搜索** | 中文语义搜索，支持域名限定、时间范围、ai-search 端点 |
| **RSS 多源订阅** | 14 条中英文 RSS（Nature/ACS/ScienceDirect/北化大学报/Feeddd） |
| **三层内容提取** | Jina Reader → Trafilatura → direct HTTP，自动适配网页结构 |
| **图片智能评分** | 内容图片优先，logo/模板图片硬拒绝，AI 生成分类兜底图 |
| **三分类日报** | 高材制造 / 清洁能源 / AI，按产业/政策/学术分板块 |
| **现代化前端** | Vue 3 + TypeScript，SSE 实时推送生成进度，中英文分组展示 |
| **诊断 API** | health / last-run / timeline / llm-metrics，支持 Agent 自主迭代 |

### 稳定产出（实测）

| 指标 | 值 |
|------|-----|
| 文章数 | 6 篇/天 |
| 板块 | industry + academic + policy |
| 图片 | 3-4 张（真实图 + AI 兜底图）|
| 等级 | complete_auto_publish |
| LLM API 成本 | ~¥0.25/次 (~¥7.5/月) |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    Vue 3 Frontend                        │
│   全球日报 / 实验室日报 / 高材制造·清洁能源·AI 分类       │
├─────────────────────────────────────────────────────────┤
│                     FastAPI Backend                       │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────────────────┐ │
│  │ ContinuousIngester│  │      DailyReportAgent        │ │
│  │ (每小时)          │  │  gather_seeds → AgentCore    │ │
│  │ RSS + Bocha搜索   │  │  4 检查点确保进度             │ │
│  │ → ArticlePool     │  │  → Report + ReportItems      │ │
│  └────────┬─────────┘  └─────────────┬────────────────┘ │
│           │                          │                   │
│  ┌────────▼──────────────────────────▼────────────────┐ │
│  │              Shared Services                        │ │
│  │  BochaSearch │ Scraper/Jina │ SemanticDedup       │ │
│  │  LLMClient(DeepSeek) │ SiliconFlow Embedding       │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │     SQLite (WAL) — ArticlePool │ Reports │ Traces  │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## 技术栈

### 后端

| 组件 | 用途 |
|------|------|
| **FastAPI + Uvicorn** | 异步 Web 框架 |
| **SQLAlchemy + SQLite (WAL)** | ORM + 数据库 |
| **APScheduler** | 定时任务（每日 10:00 日报，每小时采集） |
| **DeepSeek V4 Flash** | 主力 LLM（直连 api.deepseek.com） |
| **Bocha AI Search** | 中文语义搜索（web-search + ai-search） |
| **SiliconFlow BGE-M3** | Embedding 语义去重（API，可选） |
| **Trafilatura + Jina Reader** | 三层内容提取 |
| **Feedparser + HTTPX** | RSS 订阅 + HTTP 客户端 |

### 前端

| 组件 | 用途 |
|------|------|
| **Vue 3 + TypeScript** | Composition API + 类型安全 |
| **Vite** | 构建工具 |
| **Pinia + Vue Router** | 状态管理 + 路由 |
| **Lucide Vue** | 图标 |
| **Marked + DOMPurify** | Markdown 渲染 |

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（前端构建）

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入 API Key：

```dotenv
DEEPSEEK_API_KEY=your_deepseek_key
BOCHA_API_KEY=your_bocha_key
SILICONFLOW_API_KEY=your_siliconflow_key  # 可选，用于embedding去重

# 管理员账号
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=admin123456

# 日报调度
REPORT_HOUR=10
REPORT_MINUTE=0
```

### 3. 构建前端

```bash
cd frontend && npm install && npm run build && cd ..
```

### 4. 启动

```bash
python main.py
# 或
python -m uvicorn main:app --host 0.0.0.0 --port 8765
```

访问 **http://localhost:8765**

---

## 项目结构

```
workflow_news/
├── main.py                    # FastAPI 入口
├── app/
│   ├── config.py              # 配置（Pydantic Settings）
│   ├── models.py              # SQLAlchemy 数据模型
│   ├── bootstrap.py           # DB 初始化 + 列迁移
│   ├── seed.py                # 种子数据（RSS源等）
│   └── services/
│       ├── daily_report_agent.py   # 主编排器
│       ├── agent_core.py           # Agent 循环 + 4 检查点
│       ├── composer.py             # 种子提供器（仅去重）
│       ├── ingester.py             # 持续采集（RSS + 搜索）
│       ├── harness.py              # 安全约束（步数+时间+域名）
│       ├── tools.py                # Agent 工具集（9 个工具）
│       ├── working_memory.py       # Agent 工作记忆
│       ├── llm_client.py           # LLM 客户端（DeepSeek）
│       ├── bocha_search.py         # Bocha API 客户端
│       ├── semantic_dedup.py       # URL+MinHash+Embedding 去重
│       ├── jina_reader.py          # Jina + HTTP fallback（含图片评分）
│       ├── scraper.py              # 三层抓取
│       ├── rss.py                  # RSS feed 解析
│       └── agent_observability.py  # Agent trace 查询
├── frontend/                  # Vue 3 前端
│   └── src/
│       ├── views/             # DashboardView 等
│       ├── components/        # ReportItemCard, HeroSection 等
│       └── assets/            # AI 生成分类兜底图
├── pics/                      # AI 生成分类图片源文件
├── tests/
└── requirements.txt
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/reports/today` | 今日日报 |
| `GET` | `/api/reports` | 历史日报列表 |
| `POST` | `/api/reports/run` | 手动触发生成 |
| `GET` | `/api/reports/run/{id}/stream` | SSE 实时进度 |
| `GET` | `/api/diagnostics/health` | 系统健康检查 |
| `GET` | `/api/diagnostics/last-run` | 最近运行摘要 |
| `GET` | `/api/diagnostics/run/{id}/timeline` | Agent 步骤时间线 |
| `GET` | `/api/diagnostics/llm-metrics` | LLM 调用指标 |
| `GET` | `/api/chat/stream` | 研究助手（SSE） |

---

## 部署

```bash
# Systemd 服务
sudo tee /etc/systemd/system/workflow_news.service << 'EOF'
[Unit]
Description=Workflow News Platform
After=network.target

[Service]
User=root
WorkingDirectory=/opt/workflow_news
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now workflow_news
```

---

## 开发

```bash
# 后端测试
python -m pytest tests/ -v

# 前端开发
cd frontend && npm run dev

# 手动触发日报
curl -X POST http://localhost:8765/api/reports/run \
  -H "Content-Type: application/json" \
  -d '{"shadow_mode": false}'

# 查看诊断
curl http://localhost:8765/api/diagnostics/last-run
```

---

## License

MIT
