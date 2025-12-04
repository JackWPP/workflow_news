# 高分子材料加工每日资讯平台部署指南

本指南将指导你在 Linux (Ubuntu/CentOS) 或 Windows 服务器上部署本项目。

## 📋 前置要求

1. **服务器**：一台可以访问公网的服务器（Linux 或 Windows）。
2. **环境**：Python 3.8 或更高版本。
3. **网络**：确保服务器可以访问 `api.coze.cn`。

---

## 🐧 Linux 部署 (推荐)

### 1. 传输文件

将项目文件夹上传到服务器，例如上传到 `/opt/workflow_news`。

### 2. 安装依赖

```bash
cd /opt/workflow_news
pip3 install -r requirements.txt
```

### 3. 后台运行 (使用 Nohup)

这是最简单的后台运行方式：

```bash
# 后台运行并将日志输出到 server.log
nohup python3 main.py > server.log 2>&1 &

# 检查是否运行成功
ps aux | grep python
```

### 4. 生产级部署 (使用 Systemd - 推荐)

创建一个 systemd 服务文件，以便开机自启和崩溃重启。

1. 创建服务文件：
   ```bash
   sudo nano /etc/systemd/system/workflow_news.service
   ```

2. 写入以下内容（请修改 `User` 和 `WorkingDirectory`）：
   ```ini
   [Unit]
   Description=Workflow News Platform
   After=network.target

   [Service]
   User=root
   WorkingDirectory=/opt/workflow_news
   ExecStart=/usr/bin/python3 /opt/workflow_news/main.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. 启动服务：
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start workflow_news
   sudo systemctl enable workflow_news
   ```

4. 查看状态：
   ```bash
   sudo systemctl status workflow_news
   ```

---

## 🪟 Windows 部署

### 1. 安装 Python

下载并安装 Python 3.x，确保勾选 "Add Python to PATH"。

### 2. 安装依赖

打开 PowerShell 或 CMD，进入项目目录：

```powershell
cd D:\workflow_news
pip install -r requirements.txt
```

### 3. 启动服务

直接运行：

```powershell
python main.py
```

### 4. 后台运行 (可选)

可以使用 `pythonw` 来无窗口运行：

```powershell
# 启动无窗口进程
start pythonw main.py
```

如果要停止服务，需要在任务管理器中结束 `python` 进程。

---

## 🌐 反向代理配置 (Nginx - 可选)

如果你想通过域名访问（例如 `http://news.example.com`）而不是 `IP:8000`，建议配置 Nginx。

### Nginx 配置示例

```nginx
server {
    listen 80;
    server_name news.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

配置完成后重启 Nginx：
```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## ❓ 常见问题

**Q: 启动后页面显示“暂无资讯”？**
A: 这是正常的。因为数据库初始为空。请点击页面中间的“立即生成”按钮，等待约 30 秒即可看到内容。

**Q: 报错 `Address already in use`？**
A: 端口 8000 被占用。
- **Linux**: `lsof -i :8000` 查看占用进程，`kill -9 <PID>` 杀掉进程。
- **Windows**: 修改 `main.py` 中的端口号，或使用 `python -m uvicorn main:app --port 8001` 启动。

**Q: 生成资讯失败？**
A: 检查 `server.log` 日志。通常是因为 Coze Token 过期或网络无法连接到 Coze API。
