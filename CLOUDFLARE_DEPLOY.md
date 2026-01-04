# Cloudflare 部署指引（FastAPI + Cloudflare Tunnel）

本项目是 FastAPI 应用，Cloudflare 无法直接托管 Python 运行时。推荐做法是：
在一台可联网的服务器上运行 FastAPI，再通过 **Cloudflare Tunnel** 暴露到公网。

> 适用场景：你希望应用“部署到 Cloudflare 上”并可公网访问，但仍保留 Python 运行环境。

---

## 1. 前置条件

- 一个可用的服务器（VPS/云主机/本地机器亦可，建议 Linux）
- 一个已绑定到 Cloudflare 的域名（示例：`wear.example.com`）
- 服务器上已安装 `git`、`python3`、`pip`

---

## 2. 在服务器上部署应用

```bash
# 1) 克隆代码
cd /opt
sudo git clone <你的仓库地址> wearfinder
cd wearfinder

# 2) 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3) 运行应用（先测试能启动）
uvicorn main:app --host 0.0.0.0 --port 8000
```

确认能通过 `http://服务器IP:8000` 访问后，继续下一步。

---

## 3. 使用 systemd 将 FastAPI 常驻后台

创建 systemd 服务：

```bash
sudo tee /etc/systemd/system/wearfinder.service > /dev/null <<'SERVICE'
[Unit]
Description=WearFinder FastAPI
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/wearfinder
Environment="PATH=/opt/wearfinder/.venv/bin"
ExecStart=/opt/wearfinder/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable --now wearfinder
sudo systemctl status wearfinder
```

---

## 4. 安装并配置 Cloudflare Tunnel

### 4.1 安装 cloudflared

```bash
# Debian/Ubuntu
sudo apt-get update
sudo apt-get install -y cloudflared

# 或者用官方安装脚本（若上面不可用）
# curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
# sudo dpkg -i cloudflared.deb
```

### 4.2 登录并创建 Tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create wearfinder
```

记下输出的 Tunnel ID（例如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）。

### 4.3 配置 Tunnel

创建配置文件：

```bash
sudo mkdir -p /etc/cloudflared
sudo tee /etc/cloudflared/config.yml > /dev/null <<'CFG'
tunnel: <你的TunnelID>
credentials-file: /etc/cloudflared/<你的TunnelID>.json

ingress:
  - hostname: wear.example.com
    service: http://localhost:8000
  - service: http_status:404
CFG
```

### 4.4 绑定域名 DNS

```bash
cloudflared tunnel route dns wearfinder wear.example.com
```

### 4.5 让 Tunnel 常驻后台

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
```

---

## 5. 初始化用户（可选）

应用提供了一个仅用于初始化的接口：

```bash
curl -X POST "https://wear.example.com/init_user?username=admin&password=YOUR_PASS"
```

> ⚠️ 建议初始化完成后移除/保护该接口，避免安全风险。

---

## 6. 访问与验证

- 访问登录页：`https://wear.example.com/login_page`
- 访问应用页：`https://wear.example.com/app`

---

## 7. 常见问题

1. **无法访问**：确认 `wearfinder` 服务和 `cloudflared` 均在运行。
2. **502/504**：通常是 FastAPI 没启动或端口不一致。
3. **数据库持久化**：SQLite `data.db` 位于项目根目录，请确保有写权限并做好备份。

---

## 8. 生产建议（可选）

- 使用反向代理（如 Nginx）和 HTTPS 头部增强
- 将 SQLite 迁移到正式数据库（如 Postgres）
- 禁用 `/init_user` 或添加管理员权限

---

完成后，你就可以通过 Cloudflare 提供的域名公网访问该项目了。
