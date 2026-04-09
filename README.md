<div align="center">

# 高分子材料加工每日资讯平台

**AI Agent 驱动的垂直领域研究情报平台**

自动检索 · 智能筛选 · 结构化日报 · 研究助手

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Vue 3](https://img.shields.io/badge/Vue-3.5-4FC08D?style=flat-square&logo=vue.js&logoColor=white)](https://vuejs.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://typescriptlang.org)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

</div>

---

## 平台简介

本平台是面向**高分子材料加工**领域的智能研究情报系统，通过多 Agent 协作实现从信息发现、内容抽取、质量评估到日报撰写的全链路自动化，并提供交互式研究助手对话能力。

### 核心特性

| 能力 | 说明 |
|------|------|
| **多 Agent 日报流水线** | DailyReportAgent 编排 SearchAgent / ArticleAgent / ResearchAgent，自动完成检索→抽取→评估→成稿 |
| **多源检索引擎** | Brave Search + RSS + 智谱搜索，覆盖学术、产业、政策等多维度信息源 |
| **智能内容抽取** | Firecrawl + Jina Reader + Trafilatura 三路抽取，自动适配不同网页结构 |
| **LLM 质量评估** | 多维度评分（时效性、相关性、来源可信度、研究价值），自动聚类去重 |
| **结构化数据存储** | 完整保存检索记录、候选文章、评分详情、报告条目，支持全链路溯源 |
| **交互式研究助手** | 基于本地日报库的 RAG 问答，命中不足时自动调用外部检索增强 |
| **现代化前端** | Vue 3 + TypeScript + Pinia，SSE 实时推送生成进度 |
| **账号与权限体系** | 邮箱注册登录、服务端 Session、管理员后台 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    Vue 3 Frontend                        │
│         (Vite + TypeScript + Pinia + SSE)                │
├─────────────────────────────────────────────────────────┤
│                     FastAPI Backend                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ Auth     │  │ Report   │  │ Chat     │  │ Admin   │ │
│  │ Service  │  │ API      │  │ Service  │  │ API     │ │
│  └──────────┘  └────┬─────┘  └────┬─────┘  └─────────┘ │
│                     │              │                     │
│  ┌──────────────────▼──────────────▼──────────────────┐ │
│  │              Agent Orchestration Layer               │ │
│  │  ┌───────────────────────────────────────────────┐  │ │
│  │  │  DailyReportAgent (主编排)                      │  │ │
│  │  │   ├─ AgentCore (LLM 循环引擎)                  │  │ │
│  │  │   ├─ WorkingMemory (工作记忆)                  │  │ │
│  │  │   ├─ Harness (安全约束)                        │  │ │
│  │  │   └─ Tools (搜索/阅读/评估/撰写)               │  │ │
│  │  └───────────────────────────────────────────────┘  │ │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────┐     │ │
│  │  │ Article  │  │ Research │  │ Native        │     │ │
│  │  │ Agent    │  │ Agent    │  │ Pipeline      │     │ │
│  │  └──────────┘  └──────────┘  └───────────────┘     │ │
│  └─────────────────────────────────────────────────────┘ │
│                     │              │                     │
│  ┌──────────────────▼──────────────▼──────────────────┐ │
│  │               External Services                     │ │
│  │  Brave Search │ Firecrawl │ Jina │ OpenRouter LLM  │ │
│  └─────────────────────────────────────────────────────┘ │
│                     │                                    │
│  ┌──────────────────▼──────────────────────────────────┐ │
│  │     SQLite / PostgreSQL (SQLAlchemy ORM)            │ │
│  │     Sources │ Articles │ Reports │ Users │ Sessions │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## 技术栈

### 后端

| 框架/库 | 用途 |
|---------|------|
| **FastAPI** | 高性能异步 Web 框架 |
| **Uvicorn** | ASGI 服务器 |
| **SQLAlchemy** | ORM 与数据库迁移 |
| **APScheduler** | 定时任务调度（每日自动生成日报） |
| **HTTPX** | 异步 HTTP 客户端 |
| **Feedparser** | RSS 订阅源解析 |
| **Trafilatura** | 网页正文抽取 |

### 前端

| 框架/库 | 用途 |
|---------|------|
| **Vue 3** | 渐进式前端框架（Composition API） |
| **TypeScript** | 类型安全 |
| **Vite** | 构建工具 |
| **Pinia** | 状态管理 |
| **Vue Router** | 前端路由（Hash 模式） |
| **Lucide Vue** | 图标库 |
| **Marked + DOMPurify** | Markdown 渲染与安全过滤 |

### 外部服务

| 服务 | 用途 |
|------|------|
| **Brave Search API** | 网络检索发现 |
| **Firecrawl API** | 智能网页抽取 |
| **Jina Reader API** | 备用内容抽取 |
| **OpenRouter API** | LLM 统一接入（支持 Gemini / MiniMax 等） |

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（仅前端构建时需要）

### 1. 克隆项目

```bash
git clone <repository-url>
cd workflow_news
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入必要的 API Key：

```dotenv
# 数据库（默认 SQLite，也支持 PostgreSQL）
DATABASE_URL=sqlite:///./news.db

# 检索服务
BRAVE_API_KEY=your_brave_api_key
FIRECRAWL_API_KEY=your_firecrawl_api_key

# LLM 服务（OpenRouter）
OPENROUTER_API_KEY=your_openrouter_api_key
REPORT_PRIMARY_MODEL=google/gemini-3-flash-preview
REPORT_FALLBACK_MODEL=minimax/minimax-m2.7

# 管理员账号（首次启动自动创建）
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=admin123456

# 调度配置
REPORT_HOUR=10
REPORT_MINUTE=0
SHADOW_MODE=true
```

> 完整配置项请参考 [.env.example](.env.example)

### 4. 构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

> 后端自动托管 `frontend/dist`，无需额外配置静态文件服务。若未构建前端，回退至旧版 `static/` 页面。

### 5. 启动服务

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

开发模式（热重载）：

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

访问 **http://localhost:8000** 即可使用。

---

## 项目结构

```
workflow_news/
├── main.py                    # FastAPI 应用入口
├── app/
│   ├── config.py              # 配置管理（Pydantic Settings）
│   ├── database.py            # 数据库连接
│   ├── models.py              # SQLAlchemy 数据模型
│   ├── schemas.py             # Pydantic 请求/响应模式
│   ├── bootstrap.py           # 数据库初始化与迁移
│   └── services/
│       ├── daily_report_agent.py   # 主 Agent 编排器
│       ├── agent_core.py           # Agent 循环引擎
│       ├── article_agent.py        # 文章处理 Agent
│       ├── research_agent.py       # 深度研究 Agent
│       ├── working_memory.py       # Agent 工作记忆
│       ├── tools.py                # Agent 工具集
│       ├── harness.py              # Agent 安全约束
│       ├── pipeline.py            # 原生日报流水线
│       ├── llm.py                  # LLM 服务封装
│       ├── llm_client.py          # 统一 LLM 客户端
│       ├── brave.py                # Brave Search 客户端
│       ├── firecrawl.py            # Firecrawl 客户端
│       ├── jina_reader.py         # Jina Reader 客户端
│       ├── scraper.py             # 通用网页抓取
│       ├── rss.py                  # RSS 订阅解析
│       ├── chat.py                 # 聊天服务
│       ├── auth.py                 # 认证服务
│       ├── evaluation.py          # 质量评估
│       └── repository.py          # 数据访问层
├── frontend/                  # Vue 3 前端工程
│   ├── src/
│   │   ├── views/             # 页面组件
│   │   ├── components/        # 通用组件
│   │   ├── stores/            # Pinia 状态仓库
│   │   └── router/            # 路由配置
│   └── package.json
├── tests/                     # 单元测试
├── static/                    # 旧版静态页面（已弃用）
├── requirements.txt
├── .env.example
├── DEPLOY.md                  # 部署指南
└── README.md
```

---

## API 接口

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/register` | 邮箱注册 |
| `POST` | `/api/auth/login` | 登录 |
| `POST` | `/api/auth/logout` | 登出 |
| `GET` | `/api/me` | 获取当前用户信息 |

### 日报

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/reports/today` | 获取今日日报 |
| `GET` | `/api/reports` | 获取历史日报列表 |
| `POST` | `/api/reports/run` | 手动触发生成日报 |

### 研究助手

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/conversations` | 获取对话列表 |
| `POST` | `/api/conversations` | 创建新对话 |
| `POST` | `/api/chat/stream` | 流式聊天（SSE） |

### 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/admin/source-rules` | 获取来源规则 |
| `PUT` | `/api/admin/source-rules` | 更新来源规则 |
| `GET` | `/api/admin/report-settings` | 获取日报设置 |
| `PUT` | `/api/admin/report-settings` | 更新日报设置 |
| `GET` | `/api/retrieval-runs` | 获取检索运行记录 |

<details>
<summary>兼容旧接口</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/news/today` | 今日资讯 |
| `GET` | `/api/news/history` | 历史资讯列表 |
| `GET` | `/api/news/{date}` | 按日期获取资讯 |
| `POST` | `/api/regenerate` | 重新生成 |

</details>

---

## 部署

详细的 Linux (Systemd) / Windows / Nginx 反向代理部署指南请参阅 [**DEPLOY.md**](DEPLOY.md)。

### Systemd 快速部署

```bash
# 创建服务文件
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

# 启动并设置开机自启
sudo systemctl daemon-reload
sudo systemctl enable --now workflow_news
```

---

## 开发

### 运行测试

```bash
python -m unittest discover -s tests -v
```

### 前端开发

```bash
cd frontend
npm run dev       # 开发服务器（热重载）
npm run build     # 生产构建
```

---

## 常见问题

<details>
<summary><strong>启动后页面显示"暂无资讯"？</strong></summary>

这是正常现象——数据库初始为空。点击页面中的"立即生成"按钮，等待约 30 秒即可看到内容。
</details>

<details>
<summary><strong>生成日报失败？</strong></summary>

请按以下顺序排查：
1. 检查 `BRAVE_API_KEY`、`FIRECRAWL_API_KEY`、`OPENROUTER_API_KEY` 是否正确配置
2. 查看后台日志中的具体错误信息
3. 确认网络可以访问 `api.search.brave.com`、`api.firecrawl.dev`、`openrouter.ai`
4. 检查来源规则和调度配置是否合理
</details>

<details>
<summary><strong>端口 8000 被占用？</strong></summary>

```bash
# Linux: 查找并终止占用进程
lsof -i :8000
kill -9 <PID>

# 或者使用其他端口启动
python -m uvicorn main:app --port 8001
```
</details>

---

## License

MIT
