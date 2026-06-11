# Phase 1 紧急修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 5 个崩溃级 BUG 和安全漏洞，提升系统稳定性和用户体验

**Architecture:** 针对前端样式、内存泄漏、SSE 错误处理、CI 配置和文档准确性进行修复，不涉及架构变更

**Tech Stack:** Vue 3, TypeScript, CSS, GitHub Actions, Markdown

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `frontend/src/views/LoginView.vue` | 修改 | 添加完整的 scoped styles |
| `frontend/src/lib/particles.ts` | 修改 | 修复内存泄漏 |
| `frontend/src/lib/api.ts` | 修改 | 修复 SSE error 处理 |
| `.github/workflows/ci.yml` | 修改 | 移除 `\|\| true` |
| `README.md` | 修改 | 更新数据库描述 |

---

### Task 1: 修复 LoginView 无样式

**Covers:** 前端崩溃级 BUG

**Files:**
- Modify: `frontend/src/views/LoginView.vue`

- [ ] **Step 1: 添加 scoped styles 到 LoginView.vue**

在 `</template>` 后添加完整的 scoped styles：

```vue
<style scoped>
.auth-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: var(--bg-primary);
}

.auth-card {
  max-width: 400px;
  width: 100%;
  padding: 2.5rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-glow);
  border-radius: 16px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px);
}

.eyebrow {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--accent-primary);
  margin-bottom: 0.5rem;
}

h2 {
  font-size: 1.75rem;
  font-weight: 700;
  color: #ffffff;
  margin-bottom: 0.75rem;
}

p {
  color: var(--text-secondary);
  font-size: 0.875rem;
  margin-bottom: 1.5rem;
  line-height: 1.5;
}

label {
  display: block;
  margin-bottom: 1rem;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
}

input {
  display: block;
  width: 100%;
  margin-top: 0.5rem;
  padding: 0.75rem 1rem;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.875rem;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

input:focus {
  outline: none;
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px rgba(100, 180, 255, 0.15);
}

input::placeholder {
  color: var(--text-muted);
}

.error-box {
  padding: 0.75rem 1rem;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 8px;
  color: var(--status-error);
  font-size: 0.875rem;
  margin-bottom: 1rem;
}

.primary-button {
  display: block;
  width: 100%;
  padding: 0.75rem 1.5rem;
  background: linear-gradient(135deg, var(--accent-primary), #4a9eff);
  color: #ffffff;
  font-weight: 600;
  font-size: 0.875rem;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  transition: opacity 0.2s ease, transform 0.2s ease;
  margin-bottom: 0.75rem;
}

.primary-button:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}

.primary-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ghost-button {
  display: block;
  width: 100%;
  padding: 0.75rem 1.5rem;
  background: transparent;
  color: var(--text-secondary);
  font-weight: 500;
  font-size: 0.875rem;
  border-radius: 8px;
  border: 1px solid var(--line);
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}

.ghost-button:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
</style>
```

- [ ] **Step 2: 验证样式生效**

运行前端开发服务器并检查登录页面：

```bash
cd frontend && npm run dev
```

访问 http://localhost:5173，检查登录页面是否正确显示：
- 居中布局
- 卡片容器有背景和边框
- 按钮有正确的样式
- 错误提示有红色背景

- [ ] **Step 3: 提交更改**

```bash
git add frontend/src/views/LoginView.vue
git commit -m "fix: add missing styles for LoginView

LoginView was using CSS classes (.auth-wrap, .auth-card, .panel, .eyebrow,
.error-box, .primary-button, .ghost-button) that were never defined, causing
the login page to render completely unstyled.

Added complete scoped styles matching the design system (dark theme,
glass-morphism cards, gradient buttons)."
```

---

### Task 2: 修复 ParticleSystem 内存泄漏

**Covers:** 内存泄漏 BUG

**Files:**
- Modify: `frontend/src/lib/particles.ts`

- [ ] **Step 1: 保存 bind 后的函数引用**

修改 `ParticleSystem` 类，保存 bind 后的函数引用：

```typescript
export class ParticleSystem {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private particles: Array<{
    x: number;
    y: number;
    radius: number;
    vx: number;
    vy: number;
    alpha: number;
  }> = [];
  private animationId: number = 0;
  private width: number = 0;
  private height: number = 0;
  private boundResize: () => void;
  private boundAnimate: () => void;
  private isVisible: boolean = true;
  private observer: IntersectionObserver | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context not available');
    this.ctx = ctx;

    // 保存 bind 后的引用，确保 addEventListener/removeEventListener 使用同一个引用
    this.boundResize = this.resize.bind(this);
    this.boundAnimate = this.animate.bind(this);

    this.resize();
    window.addEventListener('resize', this.boundResize);
    this.initParticles();
    
    // 使用 IntersectionObserver 在 canvas 不可见时暂停动画
    this.observer = new IntersectionObserver(
      (entries) => {
        this.isVisible = entries[0].isIntersecting;
        if (this.isVisible && !this.animationId) {
          this.animate();
        }
      },
      { threshold: 0.1 }
    );
    this.observer.observe(canvas);
    
    this.animate();
  }

  private resize() {
    this.width = this.canvas.parentElement?.clientWidth || window.innerWidth;
    this.height = this.canvas.parentElement?.clientHeight || window.innerHeight;
    this.canvas.width = this.width;
    this.canvas.height = this.height;
  }

  private initParticles() {
    const particleCount = Math.floor((this.width * this.height) / 15000);
    this.particles = [];
    for (let i = 0; i < particleCount; i++) {
      this.particles.push({
        x: Math.random() * this.width,
        y: Math.random() * this.height,
        radius: Math.random() * 1.5 + 0.5,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        alpha: Math.random() * 0.5 + 0.1
      });
    }
  }

  private animate() {
    // 如果不可见，停止动画循环
    if (!this.isVisible) {
      this.animationId = 0;
      return;
    }

    this.ctx.clearRect(0, 0, this.width, this.height);
    
    // Draw particles
    this.particles.forEach(p => {
      p.x += p.vx;
      p.y += p.vy;

      // Wrap around edges
      if (p.x < 0) p.x = this.width;
      if (p.x > this.width) p.x = 0;
      if (p.y < 0) p.y = this.height;
      if (p.y > this.height) p.y = 0;

      this.ctx.beginPath();
      this.ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = `rgba(100, 180, 255, ${p.alpha})`;
      this.ctx.fill();
    });

    // Draw connections
    for (let i = 0; i < this.particles.length; i++) {
      for (let j = i + 1; j < this.particles.length; j++) {
        const dx = this.particles[i].x - this.particles[j].x;
        const dy = this.particles[i].y - this.particles[j].y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        if (distance < 100) {
          this.ctx.beginPath();
          this.ctx.strokeStyle = `rgba(100, 180, 255, ${0.15 * (1 - distance / 100)})`;
          this.ctx.lineWidth = 0.5;
          this.ctx.moveTo(this.particles[i].x, this.particles[i].y);
          this.ctx.lineTo(this.particles[j].x, this.particles[j].y);
          this.ctx.stroke();
        }
      }
    }

    this.animationId = requestAnimationFrame(this.boundAnimate);
  }

  public destroy() {
    // 停止动画循环
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = 0;
    }
    
    // 移除事件监听器（使用保存的引用）
    window.removeEventListener('resize', this.boundResize);
    
    // 断开 IntersectionObserver
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
  }
}
```

- [ ] **Step 2: 验证修复**

运行前端开发服务器并检查：
1. 粒子动画正常显示
2. 切换到其他页面（如 ChatView）时动画暂停
3. 切换回来时动画恢复
4. 没有内存泄漏（可以使用浏览器开发者工具的 Performance 面板检查）

```bash
cd frontend && npm run dev
```

- [ ] **Step 3: 提交更改**

```bash
git add frontend/src/lib/particles.ts
git commit -m "fix: resolve ParticleSystem memory leak and add pause/resume

- Stored bound function references for resize and animate handlers
- Added IntersectionObserver to pause animation when canvas is not visible
- Fixed destroy() to properly remove event listeners using stored references
- Added proper cleanup for IntersectionObserver in destroy()"
```

---

### Task 3: 修复 SSE error 处理

**Covers:** SSE 错误处理 BUG

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 修复 SSE error 事件处理**

修改 `streamProgress` 方法中的 error 事件处理：

```typescript
streamProgress(runId: number, handlers: {
  onStep?: (data: any) => void
  onPhase?: (data: any) => void
  onComplete?: (data: any) => void
  onError?: (data: any) => void
}) {
  const es = new EventSource(`${API_BASE}/api/reports/run/${runId}/stream`)
  es.addEventListener('step', (e) => handlers.onStep?.(JSON.parse(e.data)))
  es.addEventListener('phase', (e) => handlers.onPhase?.(JSON.parse(e.data)))
  es.addEventListener('complete', (e) => {
    handlers.onComplete?.(JSON.parse(e.data))
    es.close()
  })
  es.addEventListener('error', (e) => {
    // EventSource 的 error 事件是 Event 类型，不是 MessageEvent
    // 尝试从 EventSource 获取错误信息
    const errorData = {
      type: 'connection_error',
      message: es.readyState === EventSource.CLOSED 
        ? 'Connection closed' 
        : es.readyState === EventSource.CONNECTING
          ? 'Reconnecting...'
          : 'Unknown error',
      readyState: es.readyState
    }
    handlers.onError?.(errorData)
    
    // 如果连接已关闭，不再尝试重连
    if (es.readyState === EventSource.CLOSED) {
      es.close()
    }
  })
  es.addEventListener('done', () => es.close())
  return es
}
```

- [ ] **Step 2: 验证修复**

运行前端开发服务器并测试 SSE 连接：
1. 触发日报生成
2. 检查 SSE 连接是否正常工作
3. 模拟网络错误，检查错误处理是否正确

```bash
cd frontend && npm run dev
```

- [ ] **Step 3: 提交更改**

```bash
git add frontend/src/lib/api.ts
git commit -m "fix: correct SSE error event handling in streamProgress

EventSource's error event is of type Event, not MessageEvent. The previous
check `e instanceof MessageEvent` always returned false, so onError callback
was never called.

- Removed incorrect instanceof check
- Added proper error data extraction from EventSource state
- Added readyState information to error data
- Only close connection when readyState is CLOSED"
```

---

### Task 4: 移除 CI 测试 `|| true`

**Covers:** CI 配置问题

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: 移除 `|| true` 并添加测试环境变量**

修改 `.github/workflows/ci.yml` 中的测试命令：

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
      BOCHA_API_KEY: ""
      DEEPSEEK_API_KEY: ""
      ADMIN_PASSWORD: "test-password-123"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: alembic upgrade head
      - name: Run backend tests
        run: python -m pytest tests/ -v --timeout=60 --tb=short
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/test_db

  test-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
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
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: alembic upgrade head
      - run: alembic check
```

- [ ] **Step 2: 提交更改**

```bash
git add .github/workflows/ci.yml
git commit -m "fix: remove || true from CI to catch test failures

The `|| true` at the end of pytest command made CI always pass regardless
of test results, effectively disabling the test gate.

- Removed `|| true` from pytest command
- Added --tb=short for more concise failure output
- Added ADMIN_PASSWORD env var for test environment
- Added explicit step name for better readability"
```

---

### Task 5: 更新 README.md 数据库描述

**Covers:** 文档准确性

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新数据库描述**

修改 README.md 中的数据库描述：

```markdown
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
│  │  SQLite (WAL) 或 PostgreSQL — ArticlePool │ Reports │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```
```

- [ ] **Step 2: 提交更改**

```bash
git add README.md
git commit -m "docs: update database description in README

Updated the system architecture diagram to reflect that the project
supports both SQLite (WAL) and PostgreSQL databases, not just SQLite."
```

---

## 验证清单

完成所有任务后，运行以下验证：

- [ ] 前端构建成功：`cd frontend && npm run build`
- [ ] 后端测试通过：`python -m pytest tests/ -v --timeout=60`
- [ ] 登录页面样式正确
- [ ] 粒子动画正常工作且无内存泄漏
- [ ] SSE 错误处理正确
- [ ] CI 配置正确（测试失败会阻断流水线）

---

## 回滚计划

如果出现问题，可以按以下顺序回滚：

1. 回滚 Task 5（README.md）- 无影响
2. 回滚 Task 4（CI 配置）- 恢复 `|| true`
3. 回滚 Task 3（SSE 处理）- 恢复原 error 处理
4. 回滚 Task 2（粒子系统）- 恢复原代码
5. 回滚 Task 1（LoginView）- 移除 styles
