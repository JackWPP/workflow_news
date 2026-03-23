# 高分子材料加工每日资讯平台

这是一个基于 Coze 工作流的全栈资讯聚合平台，专为高分子材料加工领域设计。它能够每日自动从互联网搜集最新科研进展和新闻，整理成 Markdown 报告并展示。

## ✨ 核心功能

- **自动化采集**：集成 APScheduler，每日上午 10:00 自动触发 Coze 工作流抓取最新资讯。
- **即时生成**：支持手动触发“重新生成”，实时调用 Coze API 获取最新内容。
- **历史存档**：自动将每日资讯持久化存储至 SQLite 数据库，支持按日期回溯查看。
- **Markdown 渲染**：前端内置 Markdown 解析器，完美呈现标题、列表、链接等格式。
- **轻量级架构**：后端采用 FastAPI，数据库使用 SQLite，前端为纯 HTML/Vue3，部署极其简单。

## 🛠 技术栈

- **后端**：Python 3.12+, FastAPI, Uvicorn, APScheduler, HTTPX
- **数据库**：SQLite
- **前端**：HTML5, Vue 3, Tailwind CSS, Marked.js (全部通过 CDN 引入，无需构建)
- **AI 服务**：Coze Workflow API (SSE 流式响应)

## 🚀 快速开始

### 1. 环境准备

确保你的系统已安装 Python 3.8 或更高版本。

### 2. 安装依赖

在项目根目录下运行：

```bash
pip install -r requirements.txt
```

### 3. 配置 Coze 信息

项目会优先从项目根目录下的 `.env` 文件或系统环境变量读取 Coze 配置。可先复制一份示例文件：

```bash
cp .env.example .env
```

然后填写以下变量：

```dotenv
COZE_ACCESS_TOKEN=你的 Coze Token
COZE_WORKFLOW_ID=你的 Workflow ID
COZE_API_URL=https://api.coze.cn/v1/workflow/stream_run
```

如果未显式设置 `COZE_WORKFLOW_ID` 和 `COZE_API_URL`，代码会分别回退到当前仓库内置的工作流 ID 和中国区 workflow 接口地址。

### 4. 启动服务

```bash
# 默认运行在 8000 端口
python main.py

# 或者指定端口运行
python -m uvicorn main:app --host 0.0.0.0 --port 8001
```

### 5. 访问应用

打开浏览器访问：http://localhost:8000 (或你指定的端口)

## 📂 目录结构

```
d:/workflow_news/
├── coze.py                 # Coze API 调用与 SSE 解析逻辑
├── database.py             # SQLite 数据库操作封装
├── main.py                 # FastAPI 应用入口、路由与定时任务
├── requirements.txt        # Python 依赖列表
├── .env.example            # 环境变量示例
├── news.db                 # SQLite 数据库文件（自动生成）
├── static/                 # 前端静态资源
│   └── index.html          # 单页应用入口
├── DEPLOY.md               # 部署指南
└── README.md               # 项目说明文档
```

## 📝 API 接口说明

| 方法 | 路径                  | 描述                                 |
| ---- | --------------------- | ------------------------------------ |
| GET  | `/api/news/today`   | 获取当日最新资讯                     |
| GET  | `/api/news/history` | 获取所有有数据的历史日期列表         |
| GET  | `/api/news/{date}`  | 获取指定日期的资讯详情               |
| POST | `/api/regenerate`   | 手动触发 Coze 工作流重新生成当日资讯 |

## ⚠️ 注意事项

- **首次运行**：数据库为空，页面会提示“暂无资讯”。请点击页面上的“立即生成”按钮或等待次日定时任务执行。
- **端口占用**：如果启动失败提示端口被占用，请修改 `main.py` 中的端口号或使用命令行参数指定新端口。
- **Coze 调用失败**：优先检查 `.env` 中的 `COZE_ACCESS_TOKEN` 是否有效，以及对应 workflow 内部依赖的搜索/插件工具是否可用。
