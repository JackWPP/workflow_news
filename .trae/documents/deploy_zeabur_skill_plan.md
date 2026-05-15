# 创建 Zeabur 部署 Skill 计划

## 目标

将"把一个 Python+前端项目部署到 Zeabur"的能力沉淀为一个可复用的 skill，覆盖从代码适配到部署上线的全流程，包含所有踩过的坑和解决方案。

## Skill 概要

- **名称**: `deploy-zeabur`
- **描述**: 将 Python (FastAPI/Django/Flask) + 前端 (Vue/React) 全栈项目部署到 Zeabur 平台。包含 SQLite→PostgreSQL 适配、前端构建集成、CI/CD 配置、环境变量管理等。
- **触发条件**: 用户要求部署项目到 Zeabur、或提及"部署到云平台"、"上线"、"持续运营"等

## Skill 内容结构

SKILL.md 将包含以下章节，每个章节都是从实际部署中提炼的 **可操作步骤 + 踩坑记录**：

### 1. 项目评估 Checklist
- 识别后端框架 (FastAPI/Django/Flask)
- 识别前端框架 (Vue/React/无前端)
- 识别数据库 (SQLite/PostgreSQL/MySQL)
- 识别定时任务/后台任务
- 识别 SSE/WebSocket 长连接

### 2. 数据库适配 (SQLite → PostgreSQL)
- `_normalize_db_url()` 函数 — `postgresql://` → `postgresql+psycopg://`
- database.py 双兼容架构 (SQLite 本地 + PostgreSQL 生产)
- bootstrap.py 条件分支 (`_is_sqlite` 守卫)
- Alembic 迁移脚本完整性 (所有表+外键依赖顺序)
- `alembic/env.py` URL normalize
- 应用启动时自动 `alembic upgrade head`

### 3. 前端构建集成
- 根目录 `package.json` (让 Zeabur 安装 Node.js)
- `zbpack.json` 的 `build_command`
- 前端 API 地址 `VITE_API_BASE_URL` 环境变量
- StaticFiles 挂载 fallback

### 4. 端口和环境变量适配
- `PORT` 环境变量 (Zeabur 自动注入，默认 8080)
- `requirements.txt` 完整性 (所有直接 import 的第三方库)
- zbpack.json 配置 (`build_command`, `start_command`, `python_version`)

### 5. GitHub Actions CI/CD
- ci.yml 模板 (PostgreSQL 服务 + 测试 + 前端构建 + alembic check)
- `postgresql+psycopg://` URL 格式
- `python -m alembic` 替代 `alembic`

### 6. Zeabur 控制台配置
- 创建项目 (香港区域)
- 部署后端 + PostgreSQL
- 环境变量配置
- `max_replicas=1` (定时任务防重复)
- 自定义域名

### 7. 踩坑清单 (Troubleshooting)
- `ModuleNotFoundError: No module named 'psycopg2'` → URL 驱动映射
- `UndefinedTable: relation "reports" does not exist` → 迁移脚本不完整
- `alembic: command not found` → 用 `python -m alembic`
- `numpy` 不在 requirements.txt → 扫描所有 import
- 前端不显示 → 缺少 `package.json` 和 `build_command`
- 端口不是 8765 → Zeabur 注入 PORT 环境变量

## 实施步骤

1. 创建 `.trae/skills/deploy-zeabur/` 目录
2. 创建 `SKILL.md`，包含上述完整内容
3. 验证 skill 结构正确

## 需要创建的文件

| 文件 | 内容 |
|------|------|
| `.trae/skills/deploy-zeabur/SKILL.md` | 完整 skill 文档 |
