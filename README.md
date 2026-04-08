# 高分子材料加工每日资讯平台

这是一个面向高分子材料加工领域的研究情报台。当前版本已经升级为“原生检索后端 + OpenRouter 混合式日报编排 + 正式 Vue 前端 + 基础账号体系”的产品化形态。

## 当前能力

- 原生日报流水线：Brave 负责发现，Firecrawl 负责抽取，OpenRouter 负责检索规划、候选评分与日报成稿。
- 结构化数据：除 Markdown 外，同时保存 retrieval runs、articles、report items。
- 正式前端：`frontend/` 为 Vue 3 + Vite + TypeScript 工程，包含今日日报、历史日报、研究助手、后台。
- 账号体系：邮箱注册/登录、服务端 session、单管理员后台。
- 聊天能力：优先使用本地日报库回答，命中不足时可走外部检索与 OpenAI 兼容模型。

## 技术栈

- 后端：FastAPI、APScheduler、SQLAlchemy、HTTPX
- 数据库：SQLite
- 前端：Vue 3、Vite、TypeScript、Vue Router、Pinia
- 检索：Brave Search API、Firecrawl API
- 模型接口：OpenRouter（OpenAI-compatible）

## 快速开始

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

最少建议配置：

```dotenv
DATABASE_URL=sqlite:///./news.db
BRAVE_API_KEY=你的 Brave API Key
FIRECRAWL_API_KEY=你的 Firecrawl API Key
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=admin123456
REPORT_HOUR=10
REPORT_MINUTE=0
SHADOW_MODE=true
OPENROUTER_API_KEY=你的 OpenRouter API Key
REPORT_PRIMARY_MODEL=google/gemini-3-flash-preview
REPORT_FALLBACK_MODEL=minimax/minimax-m2.7
SCRAPE_TIMEOUT_SECONDS=20
SCRAPE_CONCURRENCY=3
```

### 3. 安装并构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

后端会优先托管 `frontend/dist`；如果没有构建产物，则回退到旧版 `static/` 页面。

### 4. 启动服务

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000`。

## 默认管理员

- 邮箱：`.env` 中的 `ADMIN_EMAIL`
- 密码：`.env` 中的 `ADMIN_PASSWORD`

首次启动会自动创建管理员账号；SQLite 旧库会自动补齐当前版本所需的 `sources` 扩展字段。

## 主要接口

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/me`
- `GET /api/reports`
- `GET /api/reports/today`
- `POST /api/reports/run`
- `GET /api/conversations`
- `POST /api/conversations`
- `POST /api/chat/stream`
- `GET /api/retrieval-runs`
- `GET /api/admin/source-rules`
- `PUT /api/admin/source-rules`
- `GET /api/admin/report-settings`
- `PUT /api/admin/report-settings`

兼容旧接口仍保留：

- `GET /api/news/today`
- `GET /api/news/history`
- `GET /api/news/{date}`
- `POST /api/regenerate`

## 开发说明

- 后端测试：`python -m unittest discover -s tests -v`
- 前端构建：`cd frontend && npm run build`
- 本轮优先完成“完全脱离 Coze 的日报链路”；聊天仍维持本地优先的轻量能力。
- 如果原生日报为空，优先检查 `BRAVE_API_KEY`、`FIRECRAWL_API_KEY`、`OPENROUTER_API_KEY`、来源规则和后台调度配置。
