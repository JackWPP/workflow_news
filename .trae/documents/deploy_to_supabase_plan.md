
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

---

## 二、平台选型分析 (重点: 国内可达性)

### 前端托管平台对比

| 平台 | 国内访问 | 免费额度 | 部署难度 | 备注 |
|------|---------|---------|---------|------|
| **腾讯云 EdgeOne Pages** | ⭐⭐⭐⭐⭐ 极快 | 公测期免费,几乎无限制 | ⭐ 极简 | 国内CDN 2300+节点, 首包<50ms |
| **Zeabur** | ⭐⭐⭐⭐ 快 | $5/月免费额度 | ⭐⭐ 简单 | 香港/台湾节点, `*.zeabur.app` 国内可直连 |
| Vercel | ⭐⭐ 不稳定 | 100GB/月 | ⭐⭐ 简单 | 国内部分地区被墙/被限速 |
| Cloudflare Pages | ⭐⭐ 高延迟 | 无限带宽 | ⭐⭐⭐ 中等 | 国内回源美国, 延迟高 |
| GitHub Pages | ⭐ 经常不可达 | 永久免费 | ⭐⭐⭐ 中等 | 国内访问极不稳定 |
| 腾讯云 CloudBase | ⭐⭐⭐⭐⭐ 极快 | 1GB存储+5GB流量/月 | ⭐⭐ 简单 | 国内节点, 需备案 |

### 后端托管平台对比

| 平台 | 国内访问 | 免费额度 | FastAPI支持 | 备注 |
|------|---------|---------|------------|------|
| **Zeabur** | ⭐⭐⭐⭐ 快 | $5/月免费 | ✅ 原生支持 | 香港/台湾节点, 自动识别Python项目 |
| Fly.io | ⭐⭐⭐ 一般 | 3台共享VM免费 | ✅ Docker部署 | 东京/香港区域, `*.fly.dev`国内可访问但延迟高 |
| Railway | ⭐⭐ 不可达 | $5/月免费 | ✅ 支持 | `*.up.railway.app` 国内被墙 |
| Render | ⭐⭐ 不稳定 | 750小时/月 | ✅ 支持 | 国内访问不稳定 |

### 数据库平台对比

| 平台 | 国内访问 | 免费额度 | 备注 |
|------|---------|---------|------|
| **Supabase** | ⭐⭐⭐ 可用 | 500MB存储+2个免费项目 | 新加坡/日本节点, 直连可用 |
| 腾讯云 PostgreSQL | ⭐⭐⭐⭐⭐ 极快 | 需付费 (~¥30/月起) | 国内节点, 延迟最低 |
| Zeabur PostgreSQL | ⭐⭐⭐⭐ 快 | 与后端同项目部署 | 内网通信, 零延迟 |

---

## 三、推荐方案: Zeabur 全栈 + Supabase 数据库

### 为什么选择 Zeabur

1. **国内可达性** — `*.zeabur.app` 域名未被污染, 国内直连; 选择香港区域延迟 <50ms
2. **前后端一体** — 同一项目内部署后端(FastAPI)和前端(Vue SPA), 内网通信零延迟
3. **内置 PostgreSQL** — 可直接在项目内创建 PostgreSQL 服务, 内网直连
4. **自动识别** — 自动识别 Python/FastAPI 项目, 无需写 Dockerfile
5. **成本友好** — 开发者方案 ¥35/月, 含 $5 资源额度 (对本项目足够)
6. **SSE 支持** — 长连接/流式响应无问题

### 备选方案: EdgeOne Pages(前端) + Zeabur(后端)

- 前端用 EdgeOne Pages 获得更快的国内 CDN 加速
- 后端仍用 Zeabur
- 适合对前端加载速度有极致要求的场景

### 架构图

```
┌─────────────────────────────────────────────┐
│              用户 (国内浏览器)                  │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│         Zeabur 项目 (香港区域)                │
│                                              │
│  ┌─────────────┐  ┌──────────────────────┐  │
│  │  前端服务     │  │  后端服务 (FastAPI)    │  │
│  │  Vue 3 SPA   │  │  + APScheduler       │  │
│  │  (nginx 托管) │  │  + SSE              │  │
│  └──────┬───────┘  └──────────┬───────────┘  │
│         │                     │              │
│  ┌──────▼─────────────────────▼───────────┐  │
│  │      PostgreSQL (Zeabur 内置)           │  │
│  │      内网通信, 零延迟                    │  │
│  └────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
                   │
         外部 API (出站请求):
         DeepSeek / Bocha / Jina / SiliconFlow
```

---

## 四、实施步骤

### 阶段 1: 数据库适配 (SQLite → PostgreSQL)

#### 1.1 安装 PostgreSQL 驱动
- 在 `requirements.txt` 中添加 `psycopg[binary]` (已有)
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
- 两种部署策略:
  - **策略 A (推荐)**: 前端作为 Zeabur 独立服务部署, 自动识别为 Node/Static 项目
  - **策略 B**: 前端构建产物由后端 FastAPI 的 StaticFiles 托管 (当前方案)

#### 2.3 后端入口适配
- 确认 `main.py` 的 uvicorn 启动命令兼容云平台
- Zeabur 默认检测 `main.py` 作为 FastAPI 入口, 无需额外配置
- 端口: Zeabur 默认使用 8080, 需添加 `PORT` 环境变量支持

### 阶段 3: Zeabur 部署

#### 3.1 创建 Zeabur 项目
1. 注册 Zeabur 账号 (GitHub 登录)
2. 创建项目, 选择 **香港区域** (AWS Hong Kong)
3. 升级到开发者方案 (¥35/月, 含 $5 资源额度)

#### 3.2 部署后端
1. 添加 Git 服务 → 选择 GitHub 仓库
2. Zeabur 自动识别为 Python/FastAPI 项目
3. 配置环境变量 (所有 API Key, DATABASE_URL 等)
4. 设置 `PORT=8765` (或 Zeabur 默认 8080, 需调整)
5. 部署完成, 获取 `*.zeabur.app` 域名

#### 3.3 部署 PostgreSQL
1. 在同一项目中添加 Marketplace 服务 → PostgreSQL
2. Zeabur 自动生成 `POSTGRES_URL` 内网地址
3. 将 `DATABASE_URL` 环境变量指向 Zeabur 生成的 PostgreSQL URL
4. 运行 Alembic 迁移: `alembic upgrade head`
5. (可选) 从 SQLite 导出数据并导入

#### 3.4 部署前端 (如策略 A)
1. 添加另一个 Git 服务 → 同一仓库, 子目录 `frontend/`
2. 配置构建命令: `npm run build`
3. 配置输出目录: `dist`
4. 设置环境变量: `VITE_API_BASE_URL=https://后端服务.zeabur.app`

#### 3.5 绑定自定义域名 (可选)
- 在 Zeabur 控制台添加自定义域名
- Zeabur 自动配置 SSL 证书
- 如需国内备案域名, 可结合 EdgeOne CDN 加速

### 阶段 4: 验证与调优

#### 4.1 功能验证
- 访问前端页面, 确认加载正常
- 登录系统, 确认数据库连接正常
- 手动触发日报生成, 确认 APScheduler + SSE 工作正常
- 检查 Ingester 每小时运行

#### 4.2 性能调优
- PostgreSQL 连接池: 根据负载调整 pool_size
- Zeabur 资源: 根据实际消耗调整 CPU/内存配置
- APScheduler: 确认单实例运行, 避免多副本重复执行

---

## 五、需要修改的文件清单

### 必须修改

| 文件 | 修改内容 |
|------|---------|
| [app/database.py](file:///d:/workflow_news/app/database.py) | 移除 SQLite 专用逻辑, 添加 PostgreSQL 连接池 |
| [app/bootstrap.py](file:///d:/workflow_news/app/bootstrap.py) | 跳过 `_ensure_sqlite_schema()`, 全走 Alembic |
| [app/config.py](file:///d:/workflow_news/app/config.py) | 添加 `PORT` 环境变量, 确认 DATABASE_URL 兼容 |
| [requirements.txt](file:///d:/workflow_news/requirements.txt) | 确认 `psycopg[binary]` 已在 |
| [main.py](file:///d:/workflow_news/main.py) | uvicorn 端口支持 PORT 环境变量 |
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
| `zbpack.json` | Zeabur 构建配置 (Python 入口, 构建命令等) |
| `.env.production` | 生产环境变量模板 |

---

## 六、环境变量清单

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

## 七、风险和注意事项

### 7.1 国内访问风险
- `*.zeabur.app` 域名目前国内可直连, 但未来可能变化
- 缓解: 绑定自定义域名 + Cloudflare DNS, 或使用 EdgeOne CDN 加速
- 最坏情况: 切换到国内 VPS (腾讯云轻量 ~¥50/月)

### 7.2 APScheduler 多实例问题
- Zeabur 可能自动扩展为多个实例
- 必须: 设置 `min_replicas=1, max_replicas=1` 确保单实例
- 否则定时任务会重复执行

### 7.3 PostgreSQL 连接
- Zeabur PostgreSQL 有连接数限制 (免费版约 20 个)
- 当前代码 `session_scope()` 每次请求创建新 session, 需确认连接池配置合理
- Supabase 免费版连接数限制更严格 (约 60 个), Zeabur 内置 PostgreSQL 更宽松

### 7.4 SSE 连接超时
- 确认 Zeabur 反向代理不限制长连接时间
- 当前 SSE 心跳 120s, 一般没问题

### 7.5 成本预估

| 项目 | 月费 |
|------|------|
| Zeabur 开发者方案 | ¥35 |
| Zeabur 资源消耗 (估计 $3-5) | ¥0 (含在方案内) |
| PostgreSQL (Zeabur 内置) | ¥0 |
| 域名 (可选) | ¥50/年 |
| **合计** | **~¥35/月** |

---

## 八、备选方案

### 备选 A: EdgeOne Pages (前端) + Zeabur (后端+DB)
- 前端获得国内 CDN 极速访问
- 多一个平台要管理
- 前后端分离部署, CORS 配置稍复杂

### 备选 B: 腾讯云轻量应用服务器 (全栈自建)
- 最稳定的国内访问方案
- ~¥50/月 (2C4G)
- 需要自行维护: Nginx, SSL, 进程管理, 备份
- 适合长期稳定运营后切换

### 备选 C: Supabase (DB) + Zeabur (后端) + EdgeOne Pages (前端)
- 数据库用 Supabase 免费 PostgreSQL
- 前后端各用最优平台
- 三平台管理复杂度较高

---

## 九、后续优化

1. 自定义域名 + SSL: 绑定如 `news.your-lab.cn`
2. EdgeOne CDN 加速: 如需更快国内访问
3. 日志聚合: Zeabur 内置日志查看, 或接入第三方
4. 自动备份: PostgreSQL 定时备份到 S3/COS
5. CI/CD: GitHub Actions 推送到 main 分支自动部署
6. 监控告警: 健康检查失败时通知
