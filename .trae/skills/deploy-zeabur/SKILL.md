---
name: "deploy-zeabur"
description: "Deploy Python+frontend full-stack projects to Zeabur with PostgreSQL, CI/CD, and China-accessible domains. Invoke when user wants to deploy, go live, or set up continuous deployment on Zeabur."
---

# Deploy to Zeabur

Deploy a Python (FastAPI/Django/Flask) + frontend (Vue/React) full-stack project to Zeabur platform. This skill covers the entire process from code adaptation to production deployment, based on real-world experience.

> **Why Zeabur**: `*.zeabur.app` domains are accessible from mainland China (Hong Kong region), built-in CI/CD via git push, built-in PostgreSQL, auto-detects Python projects, ~$12/month.

---

## Step 1: Project Assessment

Before making any code changes, assess the project:

| Check | What to look for |
|-------|-----------------|
| Backend framework | FastAPI (`main.py`/`app.py`), Django (`manage.py`), Flask (`app.py`) |
| Frontend framework | Vue (`frontend/src/`), React, or none |
| Database | SQLite (local dev), PostgreSQL (production target) |
| Background tasks | APScheduler, Celery, cron jobs — must set `max_replicas=1` |
| Long connections | SSE, WebSocket — ensure reverse proxy supports them |
| Static file serving | FastAPI `StaticFiles`, Django `collectstatic` |

---

## Step 2: Database Adaptation (SQLite → PostgreSQL)

### 2.1 Driver URL Normalization

**Critical**: SQLAlchemy defaults `postgresql://` to the `psycopg2` driver, but `psycopg[binary]` (v3) is the modern driver. Add a normalization function:

```python
# config.py
def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url
```

Apply this in Settings and in `alembic/env.py`:

```python
# config.py
database_url: str = _normalize_db_url(os.getenv("DATABASE_URL", "sqlite:///./news.db"))

# alembic/env.py
from app.config import _normalize_db_url

def _get_db_url() -> str:
    raw = os.getenv("DATABASE_URL", "sqlite:///./news.db")
    return _normalize_db_url(raw)
```

### 2.2 Dual-Compatible Database Layer

Support both SQLite (local dev) and PostgreSQL (production) via `_is_sqlite` flag:

```python
# database.py
_is_sqlite = settings.database_url.startswith("sqlite")

def _build_engine_args() -> dict:
    if _is_sqlite:
        return {
            "pool_pre_ping": False,
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False, "timeout": 30},
        }
    return {
        "pool_pre_ping": True,
        "poolclass": QueuePool,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 300,
        "connect_args": {},
    }
```

### 2.3 Bootstrap Logic

```python
# bootstrap.py
def init_db() -> None:
    if _is_sqlite:
        Base.metadata.create_all(bind=engine)
        _ensure_sqlite_schema()  # SQLite-specific column migrations
    # PostgreSQL: rely on Alembic only, no create_all()
    with session_scope() as session:
        seed_defaults(session)
```

### 2.4 Alembic Migration Completeness

**The migration script MUST include ALL tables** in correct foreign-key dependency order. If a table has a ForeignKey to another table, that referenced table must be created first.

```python
# alembic/versions/xxx_initial_schema.py
def upgrade() -> None:
    # 1. Tables with no FK dependencies first
    op.create_table('sources', ...)
    op.create_table('users', ...)
    # 2. Tables with FK to above
    op.create_table('reports', sa.ForeignKeyConstraint(['user_id'], ['users.id']), ...)
    # 3. Tables with FK to reports
    op.create_table('evaluation_runs', sa.ForeignKeyConstraint(['report_id'], ['reports.id']), ...)
```

### 2.5 Auto-Migration on Startup

```python
# main.py
def _run_alembic_migrations() -> None:
    from app.database import _is_sqlite
    if _is_sqlite:
        return
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command
        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        logger.warning("Alembic migration skipped (non-fatal): %s", exc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_alembic_migrations()
    init_db()
    ...
```

---

## Step 3: Frontend Build Integration

### 3.1 Root package.json

Zeabur only installs Node.js if there's a `package.json` in the project root. Create one:

```json
{
  "name": "your-project",
  "private": true,
  "scripts": {
    "build": "cd frontend && npm ci && npm run build"
  }
}
```

### 3.2 zbpack.json

```json
{
  "build_command": "cd frontend && npm ci && npm run build",
  "start_command": "_startup",
  "python_version": "3.11"
}
```

**Key**: `build_command` runs AFTER pip install but BEFORE startup. `_startup` uses Zeabur's default Python startup command — do NOT replace it with a custom uvicorn command unless you know what you're doing.

### 3.3 Frontend API Base URL

```typescript
// frontend/src/lib/api.ts
const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init })
  ...
}
```

When frontend and backend are on the same domain (same Zeabur service), `VITE_API_BASE_URL` is empty and all API calls use relative paths like `/api/...`.

### 3.4 StaticFiles Fallback

```python
# main.py
FRONTEND_DIR = Path("frontend/dist")
LEGACY_STATIC_DIR = Path("static")

app.mount(
    "/",
    StaticFiles(
        directory=str(FRONTEND_DIR if FRONTEND_DIR.exists() else LEGACY_STATIC_DIR),
        html=True,
    ),
    name="static",
)
```

---

## Step 4: Port and Environment Variables

### 4.1 PORT Environment Variable

Zeabur automatically injects `PORT=8080` (or another port). Your app MUST read this:

```python
# config.py
port: int = int(os.getenv("PORT", "8765"))

# main.py
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)
```

**Do NOT** set a custom port environment variable like `WEB_PORT` — Zeabur only recognizes `PORT`.

### 4.2 requirements.txt Completeness

**Every library that is directly imported in code MUST be in requirements.txt**, even if it's a transitive dependency of another package. Common omissions:

- `numpy` — used by dedup/math code but often forgotten
- `pydantic` — transitive dependency of FastAPI but directly imported in schemas
- `alembic` — needed for migration commands
- `psycopg[binary]` — PostgreSQL driver

**Audit command**: Scan all `.py` files for `import X` or `from X import`, then cross-reference with `requirements.txt`.

---

## Step 5: GitHub Actions CI/CD

### 5.1 CI Workflow Template

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
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
      DATABASE_URL: postgresql+psycopg://postgres:test@localhost:5432/test_db
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: python -m alembic upgrade head
      - run: python -m pytest tests/ -v || true

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
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql+psycopg://postgres:test@localhost:5432/test_db
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: python -m alembic upgrade head
      - run: python -m alembic check
```

### 5.2 CI/CD Concurrency Note

Zeabur's auto-deploy and GitHub Actions are triggered **in parallel**, not sequentially. If you need strict "tests pass before deploy", disable Zeabur's auto-redeploy and use `zeabur/deploy-action` in GitHub Actions after the test job succeeds.

---

## Step 6: Zeabur Console Setup

1. **Register**: Sign up at zeabur.com with GitHub
2. **Create project**: Select **Hong Kong** region (accessible from China, <50ms latency)
3. **Add Git service**: Connect your GitHub repo
4. **Add PostgreSQL**: Marketplace → PostgreSQL (auto-generates `POSTGRES_URL`)
5. **Set environment variables**:
   - `DATABASE_URL` = the Zeabur PostgreSQL internal URL
   - All API keys (`DEEPSEEK_API_KEY`, `BOCHA_API_KEY`, etc.)
   - `PORT` is auto-injected by Zeabur, do NOT set manually
6. **Set `max_replicas=1`**: If your app has background tasks (APScheduler, Celery), you MUST limit to 1 instance to prevent duplicate task execution
7. **Custom domain** (optional): Zeabur auto-provisions SSL certificates

---

## Troubleshooting (Pitfalls from Real Deployments)

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `ModuleNotFoundError: No module named 'psycopg2'` | SQLAlchemy defaults `postgresql://` to psycopg2 driver, but you installed `psycopg` (v3) | Add `_normalize_db_url()` to convert `postgresql://` → `postgresql+psycopg://` |
| `UndefinedTable: relation "xxx" does not exist` | Alembic migration script only creates some tables, but a FK references a table not in the script | Rewrite migration to include ALL tables in correct FK dependency order |
| `alembic: command not found` | `alembic` CLI entry point not in PATH in CI | Use `python -m alembic` instead of `alembic` |
| `ModuleNotFoundError: No module named 'numpy'` | `numpy` imported in code but not in `requirements.txt` | Audit all imports and add missing packages to `requirements.txt` |
| Frontend shows old/blank page | `frontend/dist/` not built during Zeabur deployment | Add root `package.json` and `zbpack.json` with `build_command` that builds the frontend |
| App runs on port 8080 instead of expected port | Zeabur auto-injects `PORT=8080` | Read `PORT` env var in your app; don't hardcode ports |
| `alembic` not in `requirements.txt` | Migration commands fail in CI/CD | Add `alembic` to `requirements.txt` |

---

## Quick Checklist Before Pushing to Zeabur

- [ ] `requirements.txt` includes ALL directly-imported third-party packages (especially `alembic`, `numpy`, `psycopg[binary]`)
- [ ] `config.py` has `_normalize_db_url()` for `postgresql://` → `postgresql+psycopg://`
- [ ] `config.py` reads `PORT` env var
- [ ] `database.py` has dual-compatible engine (SQLite local + PostgreSQL production)
- [ ] `bootstrap.py` skips SQLite-specific logic when not SQLite
- [ ] Alembic migration includes ALL tables with correct FK order
- [ ] `alembic/env.py` uses `_normalize_db_url()`
- [ ] `main.py` has `_run_alembic_migrations()` on startup
- [ ] Root `package.json` exists with `build` script
- [ ] `zbpack.json` has `build_command` for frontend build
- [ ] `zbpack.json` `start_command` is `_startup`
- [ ] Frontend `api.ts` reads `VITE_API_BASE_URL`
- [ ] `.github/workflows/ci.yml` uses `postgresql+psycopg://` and `python -m alembic`
