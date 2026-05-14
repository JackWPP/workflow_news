
# 高分子材料加工每日资讯平台 - 云平台部署计划

## 一、项目现状分析

### 当前架构
- **后端**: FastAPI + SQLAlchemy + SQLite(WAL)
- **前端**: Vue 3 + Vite + Pinia (SPA, 构建为静态文件)
- **任务调度**: APScheduler (定时任务: 每日日报 + 每小时 Ingester)
- **SSE**: 实时进度推送 (报告生成进度)
- **数据库迁移**: Alembic (已配置, 仅 1 个迁移版本)
- **API**: DeepSeek, Bocha, Jina, SiliconFlow 等

### 部署核心需求
1. **24/7 持续运行** — APScheduler 定时任务不能中断
2. **国内正常访问** — 用户是实验室师生 (10-30人), 在国内
3. **SSE 实时通信** — 报告生成进度推送
4. **稳定数据库** — 从 SQLite 迁移到 PostgreSQL
5. **低成本** — 月费控制在 ¥50 以内
6. **Git 级 CI/CD** — push 即部署, 自动化测试+迁移

---

## 二、平台选型分析 (重点: 国内可达性)

### 前端托管平台对比

| 平台 | 国内访问 | 免费额度 | 部署难度 | 备注 |
|------|---------|---------|---------|------|
| **Zeabur** | ⭐⭐⭐⭐ 快 | $5/月免费额度 | ⭐⭐ 简单 | 香港/台湾节点, `*.zeabur.app` 国内可直连, 内置 CI/CD |
| **腾讯云 EdgeOne Pages** | ⭐⭐⭐⭐⭐ 极快 | 公测期免费 | ⭐ 极简 | 国内CDN 2300+节点, 无内置 CI/CD 需配合 GitHub Actions |
| Vercel | ⭐⭐ 不稳定 | 100GB/月 | ⭐⭐ 简单 | 国内部分地区被墙/被限速 |
| Cloudflare Pages | ⭐⭐ 高延迟 | 无限带宽 | ⭐⭐⭐ 中等 | 国内回源美国, 延迟高 |
| GitHub Pages | ⭐ 经常不可达 | 永久免费 | ⭐⭐⭐ 中等 | 国内访问极不稳定 |

### 后端托管平台对比

| 平台 | 国内访问 | 免费额度 | CI/CD | 备注 |
|------|---------|---------|-------|------|
| **Zeabur** | ⭐⭐⭐⭐ 快 | $5/月免费 | ✅ 内置 git push 自动部署 | 香港/台湾节点, 自动识别 Python 项目 |
| Fly.io | ⭐⭐⭐ 一般 | 3台共享VM免费 | ❌ 需 flyctl 手动 | 东京/香港区域, 延迟高 |
| Railway | ⭐⭐ 不可达 | $5/月免费 | ✅ 内置 | `*.up.railway.app` 国内被墙 |
| Render | ⭐⭐ 不稳定 | 750小时/月 | ✅ 内置 | 国内访问不稳定 |

### 数据库平台对比

| 平台 | 国内访问 | 免费额度 | 备注 |
|------|---------|---------|------|
| **Zeabur PostgreSQL** | ⭐⭐⭐⭐ 快 | 与后端同项目部署 | 内网通信, 零延迟, 最简方案 |
| **Supabase** | ⭐⭐⭐ 可用 | 500MB+2项目 | 新加坡/日本节点, 直连可用 |
| 腾讯云 PostgreSQL | ⭐⭐⭐⭐⭐ 极快 | 需付费 ~¥30/月 | 国内节点, 延迟最低 |

---

## 三、推荐方案: Zeabur 全栈部署

### 为什么选择 Zeabur

1. **国内可达性** — `*.zeabur.app` 域名未被污染, 国内直连; 选择香港区域延迟 <50ms
2. **前后端一体** — 同一项目内部署后端(FastAPI)和前端(Vue SPA), 内网通信零延迟
3. **内置 PostgreSQL** — 可直接在项目内创建 PostgreSQL 服务, 内网直连
4. **自动识别** — 自动识别 Python/FastAPI 项目, 无需写 Dockerfile
5. **内置 CI/CD** — 绑定 GitHub 仓库后, **每次 `git push` 自动触发重新部署**
6. **SSE 支持** — 长连接/流式响应无问题
7. **成本友好** — 开发者方案 $12/月 (约 ¥85), 含 $5 资源额度

### 架构图

```
┌─────────────────────────────────────────────────────┐
│                    开发者本地                          │
│  git push origin main                               │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               GitHub 仓库                             │
│  webhook → Zeabur 自动触发重新部署                     │
│  (可选) GitHub Actions: 测试 + 迁移检查                │
└──────────┬───────────────────────────┬───────────────┘
           │ push 触发                  │ (可选) Actions
┌──────────▼──────────┐  ┌─────────────▼───────────────┐
│  Zeabur 自动部署      │  │  GitHub Actions (可选增强)    │
│  pull → build → run  │  │  pytest + alembic check     │
└──────────┬──────────┘  └─────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────┐
│         Zeabur 项目 (香港区域)                        │
│                                                      │
│  ┌─────────────┐  ┌──────────────────────────┐      │
│  │  前端服务     │  │  后端服务 (FastAPI)        │      │
│  │  Vue 3 SPA   │  │  + APScheduler           │      │
│  │  (nginx 托管) │  │  + SSE                   │      │
│  └──────┬───────┘  └──────────┬───────────────┘      │
│         │                     │                      │
│  ┌──────▼─────────────────────▼───────────────────┐  │
│  │      PostgreSQL (Zeabur 内置)                   │  │
│  │      内网通信, 零延迟                            │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## 四、CI/CD 方案详解

### 4.1 Zeabur 内置 CI/CD (零配置, 开箱即用)

Zeabur 与 GitHub 集成后自动提供:

| 能力 | 说明 |
|------|------|
| **自动部署** | 每次 `git push` 到 main 分支, Zeabur 自动检测 → 构建 → 部署 |
| **构建失败回滚** | 新版本构建失败时, 自动保留上一个稳定版本运行 |
| **构建日志** | 控制台实时显示构建日志和错误信息 |
| **零配置** | 无需写 Dockerfile 或 CI 配置文件, Zeabur 自动识别项目类型 |

**工作流程:**
```
git push origin main
  → GitHub webhook 通知 Zeabur
  → Zeabur 拉取最新代码
  → 自动检测项目类型 (Python/FastAPI)
  → 安装依赖 (pip install -r requirements.txt)
  → 构建前端 (如配置)
  → 启动新容器
  → 健康检查通过 → 切换流量到新版本
  → 旧容器优雅关闭
```

### 4.2 GitHub Actions 增强 CI/CD (推荐)

在 Zeabur 内置 CI/CD 的基础上, 用 GitHub Actions 增加质量门禁:

**为什么需要 GitHub Actions?**
- Zeabur 内置 CI/CD 只有"构建+部署", 没有测试/迁移检查
- 如果推送了有 bug 的代码, Zeabur 会照常部署 (只要能构建成功)
- GitHub Actions 可以在部署前跑测试, **测试不通过则不部署**

**`.github/workflows/ci.yml` 设计:**

```yaml
name: CI

on:
  push:
    branches: [main, refactor]
  pull_request:
    branches: [main]

jobs:
  test-backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql://postgres:test@localhost:5432/test_db
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: alembic upgrade head
      - run: python -m pytest tests/ -v

  test-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - working-directory: frontend
        run: npm ci
      - working-directory: frontend
        run: npm run build

  alembic-check:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports: ['5432:5432']
    env:
      DATABASE_URL: postgresql://postgres:test@localhost:5432/test_db
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: alembic upgrade head
      - run: alembic check
```

**完整 CI/CD 流程:**

```
开发者 push 到 main
  │
  ├──→ [并行] GitHub Actions: pytest + alembic check + 前端构建
  │         │
  │         ├─ 通过 → 无操作 (Zeabur 自动部署已触发)
  │         └─ 失败 → 通知开发者, 但 Zeabur 可能已部署
  │
  └──→ [自动] Zeabur: 构建 + 部署
```

> **注意**: Zeabur 的自动部署和 GitHub Actions 是并行触发的, 不存在"Actions 通过后才部署"的阻塞关系。如需严格的"测试通过才部署", 需要在 Zeabur 中关闭自动部署, 改用 GitHub Actions 调用 Zeabur API 或 CLI 手动触发部署。

### 4.3 严格 CI/CD 方案 (可选, 测试通过才部署)

如果需要"测试不通过绝不部署"的严格模式:

```yaml
name: CI/CD (Strict)

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    # ... (同上测试步骤)

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Deploy to Zeabur
        uses: zeabur/deploy-action@v1
        with:
          token: ${{ secrets.ZEABUR_TOKEN }}
          project: your-project-id
          service: your-service-id
```

**配置变更:**
- 在 Zeabur 服务设置中关闭 "Auto Redeploy" (自动重新部署)
- 改用 GitHub Actions 的 `zeabur/deploy-action` 手动触发
- 这样只有测试全部通过后才会触发部署

### 4.4 数据库迁移的 CI/CD 集成

| 场景 | 处理方式 |
|------|---------|
| 有新 Alembic 迁移 | GitHub Actions 中 `alembic check` 检测到未应用迁移, CI 失败提醒 |
| 部署时自动迁移 | 在 FastAPI startup 事件中执行 `alembic upgrade head` |
| 迁移冲突 | CI 中 `alembic check` 失败, 阻止部署 |
| 回滚 | Alembic `alembic downgrade -1` 手动执行 |

**推荐**: 在 FastAPI 应用启动时自动运行迁移:

```python
# main.py — 启动时自动迁移
@app.on_event("startup")
async def run_migrations():
    from alembic.config import Config
    from alembic import command
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.upgrade(alembic_cfg, "head")
```

---

## 五、实施步骤

### 阶段 1: 数据库适配 (SQLite → PostgreSQL)

#### 1.1 安装 PostgreSQL 驱动
- 在 `requirements.txt` 中确认 `psycopg[binary]` 存在
- 确认 SQLAlchemy 兼容 PostgreSQL 语法差异

#### 1.2 修改数据库配置
- [app/database.py](file:///d:/workflow_news/app/database.py) — 移除 SQLite 专用配置 (WAL PRAGMA, NullPool, check_same_thread)
- 为 PostgreSQL 添加连接池配置 (pool_size=5, max_overflow=10)
- [app/config.py](file:///d:/workflow_news/app/config.py) — 确保 `DATABASE_URL` 支持 PostgreSQL 格式

#### 1.3 修改 Bootstrap 逻辑
- [app/bootstrap.py](file:///d:/workflow_news/app/bootstrap.py) — 跳过 SQLite 专用 schema 迁移 (`_ensure_sqlite_schema`)
- PostgreSQL 全部走 Alembic 迁移

#### 1.4 更新 Alembic 迁移
- 确认现有迁移脚本兼容 PostgreSQL
- 如有需要, 生成新的 PostgreSQL 兼容迁移

#### 1.5 修复代码中的 SQLite 假设
- 全局搜索 `sqlite` 相关代码: `session_scope` 中的 "database is locked" 重试逻辑
- `time.sleep()` 改为 PostgreSQL 适用的错误处理
- 检查所有 `PRAGMA` 调用

### 阶段 2: 前后端部署适配

#### 2.1 前端 API 地址配置
- [frontend/src/lib/api.ts](file:///d:/workflow_news/frontend/src/lib/api.ts) — 添加环境变量 `VITE_API_BASE_URL`
- 前端构建时通过环境变量注入后端地址
- 同域部署时 API 前缀为 `/api`, 无需跨域

#### 2.2 前端构建产物
- `cd frontend && npm run build` → `frontend/dist/`
- **策略 A (推荐)**: 前端作为 Zeabur 独立服务部署
- **策略 B**: 前端构建产物由后端 FastAPI 的 StaticFiles 托管 (当前方案)

#### 2.3 后端入口适配
- [main.py](file:///d:/workflow_news/main.py) — uvicorn 端口支持 `PORT` 环境变量
- 添加应用启动时自动 Alembic 迁移
- Zeabur 默认检测 `main.py` 作为 FastAPI 入口

### 阶段 3: GitHub Actions CI/CD 配置

#### 3.1 创建 CI 工作流
- 创建 `.github/workflows/ci.yml`
- 配置 PostgreSQL 测试服务
- 配置后端测试 + 前端构建 + Alembic 迁移检查

#### 3.2 (可选) 严格部署模式
- 创建 `.github/workflows/deploy.yml`
- 在 Zeabur 中关闭自动部署
- 配置 `zeabur/deploy-action` 仅在测试通过后触发

### 阶段 4: Zeabur 部署

#### 4.1 创建 Zeabur 项目
1. 注册 Zeabur 账号 (GitHub 登录)
2. 创建项目, 选择 **香港区域**
3. 安装 Zeabur GitHub App, 授权仓库访问

#### 4.2 部署后端
1. 添加 Git 服务 → 选择 GitHub 仓库
2. Zeabur 自动识别为 Python/FastAPI 项目
3. 配置环境变量 (所有 API Key, DATABASE_URL 等)
4. 配置 `PORT=8765`
5. 设置 `max_replicas=1` (单实例, 避免定时任务重复)
6. 部署完成, 获取 `*.zeabur.app` 域名

#### 4.3 部署 PostgreSQL
1. 在同一项目中添加 Marketplace 服务 → PostgreSQL
2. Zeabur 自动生成 `POSTGRES_URL` 内网地址
3. 将 `DATABASE_URL` 环境变量指向 Zeabur 生成的 PostgreSQL URL
4. 应用启动时自动运行 Alembic 迁移

#### 4.4 部署前端 (如策略 A)
1. 添加另一个 Git 服务 → 同一仓库, 子目录 `frontend/`
2. 配置构建命令: `npm run build`
3. 配置输出目录: `dist`
4. 设置环境变量: `VITE_API_BASE_URL=https://后端服务.zeabur.app`

#### 4.5 绑定自定义域名 (可选)
- 在 Zeabur 控制台添加自定义域名
- Zeabur 自动配置 SSL 证书

### 阶段 5: 验证与调优

#### 5.1 CI/CD 验证
- 推送代码到 main 分支, 确认:
  - GitHub Actions 自动运行测试
  - Zeabur 自动触发重新部署
  - 部署成功后应用正常运行

#### 5.2 功能验证
- 访问前端页面, 确认加载正常
- 登录系统, 确认数据库连接正常
- 手动触发日报生成, 确认 APScheduler + SSE 工作正常

---

## 六、需要修改/创建的文件清单

### 必须修改

| 文件 | 修改内容 |
|------|---------|
| [app/database.py](file:///d:/workflow_news/app/database.py) | 移除 SQLite 专用逻辑, 添加 PostgreSQL 连接池 |
| [app/bootstrap.py](file:///d:/workflow_news/app/bootstrap.py) | 跳过 `_ensure_sqlite_schema()`, 全走 Alembic |
| [app/config.py](file:///d:/workflow_news/app/config.py) | 添加 `PORT` 环境变量, 确认 DATABASE_URL 兼容 PostgreSQL |
| [main.py](file:///d:/workflow_news/main.py) | uvicorn 端口支持 PORT 环境变量, 启动时自动 Alembic 迁移 |
| [requirements.txt](file:///d:/workflow_news/requirements.txt) | 确认 `psycopg[binary]` 已在 |
| [frontend/src/lib/api.ts](file:///d:/workflow_news/frontend/src/lib/api.ts) | 添加 `VITE_API_BASE_URL` 支持 |

### 可能需要修改

| 文件 | 修改内容 |
|------|---------|
| [alembic/env.py](file:///d:/workflow_news/alembic/env.py) | 确认 DATABASE_URL 从环境变量读取 |
| [alembic/versions/d95861c536ae_initial_schema.py](file:///d:/workflow_news/alembic/versions/d95861c536ae_initial_schema.py) | 确认 PostgreSQL 兼容 |
| [app/services/ingester.py](file:///d:/workflow_news/app/services/ingester.py) | 检查 SQLite 依赖 |
| [app/services/daily_report_agent.py](file:///d:/workflow_news/app/services/daily_report_agent.py) | 检查 session_scope 用法 |
| [frontend/vite.config.ts](file:///d:/workflow_news/frontend/vite.config.ts) | 开发环境代理配置 |

### 新增文件

| 文件 | 内容 |
|------|------|
| `.github/workflows/ci.yml` | GitHub Actions CI 工作流 (测试+迁移检查+前端构建) |
| `.github/workflows/deploy.yml` | (可选) 严格部署模式, 测试通过后才触发 Zeabur 部署 |
| `zbpack.json` | Zeabur 构建配置 (Python 入口, 端口等) |

---

## 七、环境变量清单

```env
# 数据库 (Zeabur PostgreSQL 内网地址)
DATABASE_URL=postgresql://user:pass@postgres.zeabur.internal:5432/dbname

# 服务端口
PORT=8765

# 时区
APP_TIMEZONE=Asia/Shanghai

# API Keys
BOCHA_API_KEY=xxx
DEEPSEEK_API_KEY=xxx
JINA_API_KEY=xxx
SILICONFLOW_API_KEY=xxx
OPENROUTER_API_KEY=xxx
KIMI_API_KEY=xxx
ZHIPU_API_KEY=xxx

# 模型配置
REPORT_PRIMARY_MODEL=deepseek-v4-flash
REPORT_FALLBACK_MODEL=deepseek-v4-flash

# 任务调度
REPORT_HOUR=10
REPORT_MINUTE=0
SHADOW_MODE=false
AGENT_MODE=true

# 管理员
ADMIN_EMAIL=admin@your-lab.com
ADMIN_PASSWORD=your_secure_password

# 日志
LOG_LEVEL=INFO
LOG_FORMAT=json

# 前端 (仅前端服务需要)
VITE_API_BASE_URL=https://workflow-news-api.zeabur.app
```

---

## 八、风险和注意事项

### 8.1 国内访问风险
- `*.zeabur.app` 域名目前国内可直连, 但未来可能变化
- 缓解: 绑定自定义域名 + Cloudflare DNS, 或使用 EdgeOne CDN 加速
- 最坏情况: 切换到国内 VPS (腾讯云轻量 ~¥50/月)

### 8.2 APScheduler 多实例问题
- Zeabur 可能自动扩展为多个实例
- 必须: 设置 `max_replicas=1` 确保单实例
- 否则定时任务会重复执行

### 8.3 PostgreSQL 连接
- Zeabur PostgreSQL 有连接数限制
- 当前代码 `session_scope()` 每次请求创建新 session, 需确认连接池配置合理

### 8.4 CI/CD 注意事项
- Zeabur 内置 CI/CD 和 GitHub Actions 是**并行触发**的, 不是串行
- 如需严格"测试通过才部署", 必须关闭 Zeabur 自动部署, 改用 Actions 手动触发
- 数据库迁移建议在应用启动时自动运行, 而非依赖 CI 步骤

### 8.5 成本预估

| 项目 | 月费 |
|------|------|
| Zeabur 开发者方案 | $12 (~¥85) |
| Zeabur 资源消耗 (估计 $3-5) | 含在方案内 |
| PostgreSQL (Zeabur 内置) | 含在方案内 |
| GitHub Actions | 免费 (公开仓库无限, 私有仓库 2000 分钟/月) |
| 域名 (可选) | ¥50/年 |
| **合计** | **~¥85/月** |

---

## 九、备选方案

### 备选 A: EdgeOne Pages (前端) + Zeabur (后端+DB)
- 前端获得国内 CDN 极速访问
- 多一个平台要管理
- 前后端分离部署, CORS 配置稍复杂

### 备选 B: 腾讯云轻量应用服务器 (全栈自建)
- 最稳定的国内访问方案
- ~¥50/月 (2C4G)
- 需要自行维护: Nginx, SSL, 进程管理, 备份
- CI/CD 需完全自建 (GitHub Actions + SSH deploy)
- 适合长期稳定运营后切换

### 备选 C: Supabase (DB) + Zeabur (后端) + EdgeOne Pages (前端)
- 数据库用 Supabase 免费 PostgreSQL
- 前后端各用最优平台
- 三平台管理复杂度较高

---

## 十、后续优化

1. 自定义域名 + SSL: 绑定如 `news.your-lab.cn`
2. EdgeOne CDN 加速: 如需更快国内访问
3. 日志聚合: Zeabur 内置日志查看, 或接入第三方
4. 自动备份: PostgreSQL 定时备份到 S3/COS
5. 监控告警: 健康检查失败时通知
6. Staging 环境: Zeabur 支持多环境, 推到 develop 分支自动部署到 staging
