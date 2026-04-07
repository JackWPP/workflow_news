# 前端重做计划：高分子加工全视界

## 关于图文并茂的回答

> **现在能做到图文并茂吗？**

**后端能力已经基本具备**，但有两个需要修复的断点：

| 能力 | 状态 | 说明 |
|------|------|------|
| Agent 主动搜图 | ✅ | `search_images` 工具，Agent 自主为文章找配图 |
| Agent 验证图片 | ✅ | `verify_image` 工具，过滤 logo/广告/无关图 |
| 数据模型 | ✅ | `ReportItem` 已有 `image_url`, `image_caption`, `has_verified_image` |
| 前端渲染 | ✅ | `ReportItemCard` 已有图片展示逻辑 |
| **图片元数据从 Agent Memory → ReportItem** | ⚠️ | 需要在 `daily_report_agent.py` 的 `_result_to_report()` 中从 memory 的 `image_candidates` 提取已验证图片并写入 ReportItem |
| **write_section 输出收集** | ⚠️ | Agent 调用 `write_section` 后内容在 message history 中，但没有被 `finish` 收集到 `sections_content` |

> 修完这两个断点后，每日日报就能真正图文并茂。

---

## 1. 设计方向

### 从"温暖编辑部"到"科技指挥中心"

| 维度 | 现在 | 重做后 |
|------|------|--------|
| **色调** | 暖黄色 `#f1ece2` | 深色科技 `#0a0e1a` + 霓虹蓝绿 |
| **字体** | IBM Plex Sans + 宋体 | Inter + Noto Sans SC |
| **风格** | 纸质杂志感 | Glassmorphism + 微光边框 + 粒子背景 |
| **动效** | 几乎没有 | 数字粒子背景、卡片入场动画、状态脉冲、count-up |
| **布局** | 单栏内容区 | 全屏暗色背景 + 浮动面板 + 侧边导航 |

### 视觉关键词

- **深色模式优先**（暗色背景 + 高对比内容区）
- **霓虹发光边框**（蓝/绿/紫三色 accent 对应三个板块）
- **Glassmorphism 面板**（`backdrop-filter: blur` + 半透明背景）
- **数据粒子动画**（首页背景 canvas 粒子流）
- **微交互**（hover 发光、点击涟漪、skeleton loading）
- **等宽数字**（数据仪表盘用 tabular-nums）

---

## 2. 页面结构（7 个视图）

### 2.1 🏠 首页：今日日报 Magazine

**路由**: `/`

**布局**:
```
┌─────────────────────────────────────────────────────┐
│  ← 侧边栏折叠   高分子加工全视界              🔔 👤 │
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌─── Hero Banner ───────────────────────────┐     │
│   │                                           │     │
│   │  "高分子加工全视界" 日报  2026-04-01       │     │
│   │                                           │     │
│   │  [主图 image]          摘要文字            │     │
│   │                        Agent 状态脉冲      │     │
│   │                                           │     │
│   └───────────────────────────────────────────┘     │
│                                                     │
│   ── 🔬 学术前沿 ─────────────────────────────────  │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│   │ Card 1   │  │ Card 2   │  │ Card 3   │         │
│   │ [图片]   │  │ [图片]   │  │ [图片]   │         │
│   │ 标题     │  │ 标题     │  │ 标题     │         │
│   │ 摘要     │  │ 摘要     │  │ 摘要     │         │
│   └──────────┘  └──────────┘  └──────────┘         │
│                                                     │
│   ── 🏭 产业动态 ─────────────────────────────────  │
│   (同上)                                            │
│                                                     │
│   ── 📢 政策标准 ─────────────────────────────────  │
│   (同上)                                            │
│                                                     │
│   ── 📊 覆盖度仪表 ──────────────────────────────   │
│   [学术 ██████░ 2]  [产业 ████████ 3]  [政策 ██ 1] │
│   [图片覆盖率 72%]  [来源多样性 85%]                 │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**交互**:
- Hero 区域的主图支持视差滚动
- 板块卡片 hover 发光（学术=蓝、产业=绿、政策=紫）
- 点击卡片展开详情（slide-over panel）
- 卡片视图 ⇄ Markdown 视图切换
- "手动生成"按钮触发后显示 Agent 实时进度

**数据源**: `GET /api/reports/today`

---

### 2.2 💬 研究助手 Chat

**路由**: `/chat`

**布局**:
```
┌──── 左栏 ────┬──── 右栏（主聊天区）──────────────┐
│              │                                    │
│ 对话列表      │  ┌─ 消息流 ────────────────────┐  │
│              │  │                              │  │
│ ▸ 新对话      │  │  [user] 注塑机最新进展？      │  │
│ ▸ 对话1 ★    │  │                              │  │
│ ▸ 对话2      │  │  [agent] 正在研究...          │  │
│ ▸ ...        │  │   ├── search_local ✓          │  │
│              │  │   ├── web_search ✓            │  │
│              │  │   ├── read_page ✓             │  │
│              │  │   └── finish ✓                │  │
│              │  │                              │  │
│              │  │  [agent] 根据最新资料...       │  │
│              │  │  引用: [1] [2] [3]            │  │
│              │  │                              │  │
│              │  └──────────────────────────────┘  │
│              │                                    │
│              │  ┌─ 输入框 ─────────────────────┐  │
│              │  │ 请输入你的问题...      [发送] │  │
│              │  └──────────────────────────────┘  │
└──────────────┴────────────────────────────────────┘
```

**新增特性**:
- Agent 多步研究可视化（实时显示 Agent 正在做什么）
- 引用卡片（点击展开来源详情）
- Markdown 渲染优化（代码高亮、表格美化）
- 收藏对话、对话标题自动生成

**数据源**: `POST /api/chat/stream`, `GET /api/conversations`

---

### 2.3 📅 历史日报

**路由**: `/history`

**布局**:
```
┌──── 日期侧栏 ──┬──── 报告详情 ────────────────┐
│                │                                │
│ 2026-04-01 ✓   │  ┌─ 日报标题 + Hero ───────┐  │
│ 2026-03-31     │  │ [主图]                   │  │
│ 2026-03-30     │  │ 标题 + 状态              │  │
│ 2026-03-29     │  └──────────────────────────┘  │
│ ...            │                                │
│                │  卡片/Markdown 切换视图         │
│ [日历选择]     │                                │
│                │                                │
└────────────────┴────────────────────────────────┘
```

**新增特性**:
- 日历热力图（显示每天的报告质量色块）
- 条目数趋势折线图（sparkline）
- 报告对比（选择两天并排查看差异）

**数据源**: `GET /api/news/history`, `GET /api/news/{date}`, `GET /api/reports`

---

### 2.4 🧠 Agent Trace 回放（全新页面）

**路由**: `/agent-trace`

**布局**:
```
┌────────────────────────────────────────────────────────┐
│  Agent 运行列表                                         │
│  ┌──────┬──────────┬────────┬──────┬─────────┐         │
│  │ ID   │ 类型     │ 状态   │ 步数 │ 时间     │         │
│  ├──────┼──────────┼────────┼──────┼─────────┤         │
│  │ #12  │ daily    │ ✓ done │ 17   │ 3min    │  →      │
│  │ #11  │ research │ ⚠ part │ 8    │ 1min    │  →      │
│  └──────┴──────────┴────────┴──────┴─────────┘         │
│                                                         │
│  ── 运行详情 #12 ──────────────────────────────────── │
│                                                         │
│  ┌─ 时间线（每步可交互） ────────────────────────┐     │
│  │                                                │     │
│  │  Step 1: 💭 "先了解今天有什么值得关注的..."       │     │
│  │         🔧 web_search("高分子 注塑 最新") ✓      │     │
│  │         📦 找到 8 条结果                          │     │
│  │                                                │     │
│  │  Step 2: 💭 "第3条关于注塑机扩产很有价值..."      │     │
│  │         🔧 read_page("https://...") ✓            │     │
│  │         📦 获得 2000 字正文 + 3 张图片             │     │
│  │                                                │     │
│  │  Step 5: 💭 "尝试搜索 bilibili..."               │     │
│  │         🛑 Harness BLOCKED: bilibili.com          │     │
│  │                                                │     │
│  │  Step 17: 🏁 finish → Report("高分子加工日报")    │     │
│  │                                                │     │
│  └────────────────────────────────────────────────┘     │
│                                                         │
│  ┌─ WorkingMemory 面板 ──────────────────────────┐     │
│  │ 已搜索: 6 queries  已阅读: 4 pages              │     │
│  │ 已收集: 5 articles  已验证图片: 3                 │     │
│  │ 覆盖: [industry ✓] [policy ✓] [academic ✓]     │     │
│  └────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────┘
```

**核心特性**:
- 每步展示 Agent 的**思考** → **工具选择** → **执行结果**
- Harness 拦截用红色高亮
- WorkingMemory 状态面板随步骤演变
- 可折叠/展开每步详情

**数据源**: `GET /api/agent-runs`, `GET /api/agent-runs/{id}`, `GET /api/agent-runs/{id}/steps/{step_id}`

---

### 2.5 ⚙️ 管理后台

**路由**: `/admin`

**子面板**:

| 面板 | 功能 | 数据源 |
|------|------|--------|
| 报告设置 | 调度时间/模型/参数 | `GET/PUT /api/admin/report-settings` |
| 信源管理 | 信源规则编辑器 | `GET/PUT /api/admin/source-rules` |
| 质量反馈 | 标记好/差条目 | `GET/POST /api/admin/quality-feedback` |
| 质量仪表盘 | 趋势图/域名热力图 | `GET /api/admin/quality-overview` |
| 评估报告 | 综合评分/基准测试 | `GET /api/admin/evaluation-summary` |
| Retrieval Runs | 旧 pipeline 运行记录 | `GET /api/retrieval-runs` |

**设计**:
- Tab 式子面板导航
- 统计卡片用霓虹色数字
- 图表用 Chart.js 或纯 SVG（不引入重型图表库）

---

### 2.6 🔐 登录/注册

**路由**: `/login`

**设计**:
- 全屏暗色背景 + 居中玻璃卡片
- 粒子动画背景
- 邮箱密码表单 + 切换注册/登录

---

### 2.7 📱 移动端适配

- 侧边栏改为 hamburger 底部导航
- 卡片改为单列
- Hero 图片改为全宽
- Chat 全屏模式

---

## 3. 技术栈

| 项 | 选择 | 理由 |
|----|------|------|
| 框架 | **Vue 3** (保留) | 已有基础，无需迁移 |
| 构建 | **Vite** (保留) | 快 |
| 样式 | **Vanilla CSS** (重写) | 全量重做，不需要 Tailwind |
| 字体 | **Inter + Noto Sans SC** | 现代 + 中文支持，Google Fonts CDN |
| 图标 | **Lucide Icons** (SVG) | 轻量、科技感、Tree-shakeable |
| 图表 | **Chart.js 4** (新增) | 轻量，管理后台趋势图用 |
| Markdown | **marked + DOMPurify** (保留) | 已有 |
| 动画 | **CSS @keyframes + requestAnimationFrame** | 粒子背景 canvas 自绘，其余纯 CSS |
| 状态 | **Pinia** (保留) | 已有 |
| 路由 | **vue-router** (保留) | 已有 |

---

## 4. 色彩系统

```css
:root {
  /* 基础 */
  --bg-primary:    #0a0e1a;         /* 深蓝黑背景 */
  --bg-surface:    rgba(15, 20, 40, 0.85);  /* 面板背景（玻璃） */
  --bg-card:       rgba(20, 28, 55, 0.7);   /* 卡片背景 */
  --bg-hover:      rgba(30, 40, 70, 0.9);   /* hover 态 */
  --border-glow:   rgba(100, 180, 255, 0.15); /* 边框发光 */

  /* 文字 */
  --text-primary:  #e8ecf4;
  --text-secondary: #8892a8;
  --text-muted:    #5c647a;

  /* 板块 Accent（三色霓虹） */
  --accent-academic:  #6cb4ff;  /* 学术：冰蓝 */
  --accent-industry:  #4ade80;  /* 产业：翠绿 */
  --accent-policy:    #a78bfa;  /* 政策：薰紫 */

  /* 状态 */
  --status-ok:     #34d399;
  --status-warn:   #fbbf24;
  --status-error:  #f87171;
  --status-info:   #60a5fa;

  /* 发光效果 */
  --glow-blue:   0 0 20px rgba(100, 180, 255, 0.3);
  --glow-green:  0 0 20px rgba(74, 222, 128, 0.3);
  --glow-purple: 0 0 20px rgba(167, 139, 250, 0.3);
}
```

---

## 5. 文件结构（重做后）

```
frontend/src/
├── main.ts
├── App.vue
├── router.ts
├── style.css                    # 全局设计系统（暗色主题）
├── types.ts                     # 扩展 AgentRun/AgentStep 类型
│
├── lib/
│   ├── api.ts                   # API 客户端（扩展 agent-runs）
│   └── particles.ts             # 粒子背景 canvas 引擎
│
├── stores/
│   └── session.ts               # 用户会话（保留）
│
├── components/
│   ├── AppShell.vue             # 侧边导航 + 顶栏
│   ├── ReportItemCard.vue       # 日报条目卡片（重做：暗色+发光）
│   ├── StatusPill.vue           # 状态标签（重做：发光）
│   ├── MarkdownPanel.vue        # Markdown 渲染（重做：暗色代码块）
│   ├── CoverageGauge.vue        # 覆盖度仪表 [NEW]
│   ├── AgentStepTimeline.vue    # Agent 步骤时间线 [NEW]
│   ├── MemoryPanel.vue          # WorkingMemory 状态 [NEW]
│   ├── HeroSection.vue          # 首页大图 Hero [NEW]
│   ├── SectionDivider.vue       # 板块分割线 + 图标 [NEW]
│   ├── ChatBubble.vue           # 对话气泡（重做）[NEW]
│   ├── AgentThinking.vue        # Agent 思考中动画 [NEW]
│   ├── CalendarHeatmap.vue      # 日历热力图 [NEW]
│   └── StatsCard.vue            # 统计数字卡片 [NEW]
│
├── views/
│   ├── DashboardView.vue        # 今日日报（重做）
│   ├── ChatView.vue             # 研究助手（重做）
│   ├── HistoryView.vue          # 历史日报（重做）
│   ├── AgentTraceView.vue       # Agent 回放 [NEW]
│   ├── AdminView.vue            # 管理后台（重做）
│   └── LoginView.vue            # 登录（重做）
```

---

## 6. 实施步骤

### Phase 1: 设计系统（1 天）
- [ ] 重写 `style.css`（暗色主题 + 色彩变量 + 动效关键帧）
- [ ] 引入 Inter + Noto Sans SC 字体
- [ ] 重做 `AppShell.vue`（侧边导航 + 暗色顶栏）
- [ ] 粒子背景 `particles.ts`

### Phase 2: 首页日报（1 天）
- [ ] 重做 `HeroSection.vue` + `DashboardView.vue`
- [ ] 重做 `ReportItemCard.vue`（暗色发光卡片）
- [ ] 新增 `CoverageGauge.vue`（覆盖度进度环）
- [ ] 新增 `SectionDivider.vue`（板块分隔 + 霓虹图标）

### Phase 3: Chat 研究助手（1 天）
- [ ] 重做 `ChatView.vue`（暗色对话界面）
- [ ] 新增 `ChatBubble.vue` + `AgentThinking.vue`
- [ ] Agent 多步研究可视化（实时步骤展示）

### Phase 4: Agent Trace 回放（1 天）
- [ ] 新增 `AgentTraceView.vue`
- [ ] 新增 `AgentStepTimeline.vue`（思考→工具→结果时间线）
- [ ] 新增 `MemoryPanel.vue`（WorkingMemory 状态面板）
- [ ] API 接入 `agent-runs` endpoints

### Phase 5: 历史 + 管理后台（1 天）
- [ ] 重做 `HistoryView.vue`（日历热力图 + 详情面板）
- [ ] 新增 `CalendarHeatmap.vue`
- [ ] 重做 `AdminView.vue`（Tab 式子面板）
- [ ] 新增 `StatsCard.vue`（统计数字 + 趋势线）
- [ ] Chart.js 集成（质量趋势图）

### Phase 6: 登录 + 打磨（0.5 天）
- [ ] 重做 `LoginView.vue`（玻璃卡片 + 粒子背景）
- [ ] 移动端适配
- [ ] 过渡动画打磨
- [ ] `npm run build` 生产构建

---

## 7. 验证计划

### 浏览器测试
```bash
cd frontend && npm run dev
# 访问 http://localhost:5173
```
逐页检查：首页、Chat、历史、Agent Trace、管理后台、登录

### 生产构建
```bash
cd frontend && npm run build
# 启动后端
cd .. && python -m uvicorn main:app
# 访问 http://localhost:8000
```

### 手动检查项
- [ ] 暗色主题整体一致性
- [ ] 卡片 hover 发光效果
- [ ] 粒子背景性能（FPS > 50）
- [ ] 移动端响应式（Chrome DevTools 切换设备）
- [ ] Agent Trace 时间线可交互
- [ ] Markdown 渲染正确（代码块、表格、链接）
