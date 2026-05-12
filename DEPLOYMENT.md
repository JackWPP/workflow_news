# 高分子材料加工每日资讯平台 - 部署指南

## 环境要求

- Python 3.10+
- Node.js 18+
- npm
- pm2 (进程管理)
- nginx

---

## 部署步骤

### 1. 环境准备

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Node.js 依赖 (前端构建)
cd frontend && npm install && cd ..
```

### 2. 创建 `.env` 配置文件

在项目根目录创建 `.env` 文件：

```bash
cat > .env << 'EOF'
DATABASE_URL=sqlite:///./news.db
APP_TIMEZONE=Asia/Hong_Kong

# API Keys (填入你的密钥)
BRAVE_API_KEY=your_brave_api_key
FIRECRAWL_API_KEY=your_firecrawl_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
ZHIPU_API_KEY=your_zhipu_api_key
JINA_API_KEY=your_jina_api_key

# LLM 模型配置
REPORT_PRIMARY_MODEL=kimi-k2.5
REPORT_FALLBACK_MODEL=minimax/minimax-m2.7

# 部署配置
REPORT_HOUR=10
REPORT_MINUTE=0
SHADOW_MODE=false
AGENT_MODE=true

# 管理员账号
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=your_secure_password
EOF
```

### 3. 构建前端

```bash
cd frontend
npm run build
# 构建产物在 frontend/dist/
```

### 4. 后端启动 (端口 6789)

使用 **pm2** 管理进程：

```bash
# 安装 pm2
npm install -g pm2

# 启动后端 (端口 6789)
pm2 start python --name "workflow-news-api" -- \
  -m uvicorn main:app \
  --host 0.0.0.0 \
  --port 6789

pm2 save
pm2 startup
```

### 5. 前端部署 (端口 6130)

前端是纯静态文件，使用 **nginx** 托管：

```nginx
# /etc/nginx/sites-available/workflow-news
server {
    listen 6130;
    server_name _;

    root /path/to/workflow_news/frontend/dist;
    index index.html;

    # 前端路由 (SPA)
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理到后端
    location /api {
        proxy_pass http://127.0.0.1:6789;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # SSE 支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/workflow-news /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 目录结构

```
/path/to/workflow_news/
├── main.py                 # FastAPI 入口
├── .env                    # 环境配置
├── requirements.txt        # Python 依赖
├── news.db                 # SQLite 数据库 (自动创建)
└── frontend/
    └── dist/               # 构建产物 (nginx 托管于 6130)
```

---

## 验证部署

```bash
# 后端健康检查
curl http://localhost:6789/api/me

# 前端访问
curl http://localhost:6130
```

---

## 常用运维命令

```bash
# 查看后端日志
pm2 logs workflow-news-api

# 重启后端
pm2 restart workflow-news-api

# 重载 nginx
sudo systemctl reload nginx

# 查看 nginx 日志
sudo tail -f /var/log/nginx/access.log
```
