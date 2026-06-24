# Nginx 反向代理 + Basic Auth + HTTPS 配置

## 安装

```bash
apt-get update && apt-get install -y nginx apache2-utils certbot python3-certbot-nginx
```

## 创建密码文件

```bash
htpasswd -bc /etc/nginx/.htpasswd <用户名> '<密码>'
```

## Nginx 配置（HTTP + HTTPS + WebSocket）

写入 `/etc/nginx/sites-available/hermes-dashboard`：

### 方案 A：仅 HTTP（开发/测试用）

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name <你的域名>;

    # REST API + 静态资源 — Basic Auth
    location / {
        auth_basic "Hermes Dashboard";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:9119;
        proxy_set_header Host 127.0.0.1:9119;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 86400;
    }

    # WebSocket 路径 — 不要 Nginx Basic Auth，隐藏 Origin 头，转发 token 到请求头
    location ~ ^/api/(pty|ws|events|pub)(/.*)?$ {
        auth_basic off;
        proxy_set_header Origin "";
        proxy_set_header Host 127.0.0.1:9119;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Hermes-Session-Token $arg_token;  # 关键！将 ?token= 转发为请求头

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 86400;
    }
}
```

### 方案 B：HTTPS + Let's Encrypt（生产推荐）

**步骤 1：** 先用 HTTP 配置启动 Nginx，确认能访问。

**步骤 2：** 运行 Certbot 自动配置 SSL：
```bash
certbot --nginx -d <你的域名> --non-interactive --agree-tos -m admin@<域名>
```

**步骤 3：** Certbot 会自动修改 Nginx 配置，添加 SSL 相关指令和 HTTP→HTTPS 重定向。

**⚠️ 坑点：Certbot 修改配置后，如果你手动覆盖整个配置文件，SSL 设置会丢失。** 应该用 `certbot --nginx` 重新运行，而不是手动重写。

### ⚠️ 关键 Nginx 配置要求

1. **WebSocket 必须分离 location** — 浏览器的 WebSocket API 不支持发送 Basic Auth 头，所以 WebSocket 路径（`/api/pty`、`/api/ws`、`/api/events`、`/api/pub`）必须 `auth_basic off`。Dashboard 自己的 ticket/token 机制处理 WS 认证。

2. **必须用 `proxy_set_header Origin ""` 而不是 `proxy_hide_header Origin`** — `proxy_hide_header` 只移除响应头，不移除请求头。WebSocket 升级请求中的 Origin 头需要用 `proxy_set_header Origin "";` 覆盖。

3. **⚠️ 必须转发 token 到请求头：`proxy_set_header X-Hermes-Session-Token $arg_token;`** — Dashboard 的 HTTP 中间件 `auth_middleware` 在 WebSocket 升级前拦截请求，检查 `X-Hermes-Session-Token` 请求头。浏览器通过 `?token=` 查询参数传递 token，不发送请求头。Nginx 的 `$arg_token` 变量自动提取查询参数并转发为请求头，使 auth_middleware 能正确识别身份。**缺少此配置会导致 WebSocket 返回 403。**

4. **使用 `$connection_upgrade` 变量** — 需要在 `nginx.conf` 的 `http` 块中添加：
   ```nginx
   map $http_upgrade $connection_upgrade {
       default upgrade;
       ''      close;
   }
   ```
   然后在 location 中用 `proxy_set_header Connection $connection_upgrade;`（不要硬编码 `"upgrade"`）。

4. **Host 头必须设为 127.0.0.1:9119** — `proxy_set_header Host 127.0.0.1:9119;`。Dashboard 的 `_is_accepted_host()` 会检查 Host 头是否匹配绑定地址。

## 启用配置

```bash
# 删除默认站点（会冲突）
rm -f /etc/nginx/sites-enabled/default

# 链接新配置
ln -sf /etc/nginx/sites-available/hermes-dashboard /etc/nginx/sites-enabled/

# 测试配置
nginx -t

# 重启
systemctl restart nginx
```

**⚠️ 坑点：必须删除 `/etc/nginx/sites-enabled/default`，否则默认的 80 端口 server 块会冲突。**

## 启动 Dashboard

**🚨 安全规则：Dashboard 绑定到 `0.0.0.0` 时必须用防火墙阻止公网访问！**

绑定到 `0.0.0.0` 会将端口直接暴露到公网，任何人可以绕过 Nginx、HTTPS 和所有认证直接访问 Dashboard。

### ✅ 推荐方案：绑定 0.0.0.0 + iptables 阻止公网访问

```bash
# Dashboard 启动（绑定 0.0.0.0，WebSocket 正常工作）
hermes dashboard --host 0.0.0.0 --port 9119 --no-open --insecure --tui

# iptables 规则：只允许本地访问
iptables -A INPUT -s 127.0.0.1 -p tcp --dport 9119 -j ACCEPT
iptables -A INPUT -p tcp --dport 9119 -j DROP
```

这样 WebSocket 从 Nginx（本地连接）可以正常工作，同时公网无法直接访问端口 9119。

### ✅ 推荐方案：绑定 0.0.0.0 + Nginx token 头转发 + iptables 防火墙

```bash
# Dashboard 启动（绑定 0.0.0.0，WebSocket 正常工作）
hermes dashboard --host 0.0.0.0 --port 9119 --no-open --insecure --tui

# iptables 规则：只允许本地访问
iptables -A INPUT -s 127.0.0.1 -p tcp --dport 9119 -j ACCEPT
iptables -A INPUT -p tcp --dport 9119 -j DROP
```

这样 WebSocket 从 Nginx（本地连接）可以正常工作，同时公网无法直接访问端口 9119。

**关键：** Nginx WebSocket location 中必须包含 `proxy_set_header X-Hermes-Session-Token $arg_token;`，否则 auth_middleware 会拦截 WebSocket 升级请求导致 403。

**关键：** Nginx WebSocket location 中必须包含 `proxy_set_header X-Hermes-Session-Token $arg_token;`，否则 auth_middleware 会拦截 WebSocket 升级请求导致 403。

## 验证

```bash
# 无密码访问 → 应返回 401
curl -s -o /dev/null -w "无密码: HTTP %{http_code}\n" http://127.0.0.1/

# 有密码访问 → 应返回 200
curl -s -o /dev/null -w "有密码: HTTP %{http_code}\n" \
  -u <用户名>:'<密码>' http://127.0.0.1/

# Dashboard 直连 → 应只有本地能访问
curl -s -o /dev/null -w "直连: HTTP %{http_code}\n" http://127.0.0.1:9119/
```

## 修改密码

```bash
# 重新生成（覆盖）
htpasswd -bc /etc/nginx/.htpasswd <新用户名> '<新密码>'

# 或追加用户
htpasswd -b /etc/nginx/.htpasswd <另一个用户名> '<密码>'

# 重启 Nginx 使生效
systemctl restart nginx
```

**⚠️ 坑点：Python `crypt.crypt()` 无法验证 Apache `$apr1$` 哈希。**

Cookie 认证服务如果使用 Python `crypt` 模块验证密码，会对 Apache `$apr1$` 格式的哈希返回 `*0`（验证失败）。解决方案：

```bash
# 方案 1：用 SHA 格式生成密码（crypt 兼容）
htpasswd -bc -s /etc/nginx/.htpasswd <用户名> '<密码>'

# 方案 2：在认证服务中用 passlib 代替 crypt
pip install passlib
# from passlib.hash import apr_md5; apr_md5.verify(password, stored_hash)
```

### 后台运行 Dashboard

Dashboard 进程需要持久化。两种方式：

### 方式 1：tmux（简单）
```bash
tmux new-session -d -s dashboard \
  "hermes dashboard --host 0.0.0.0 --port 9119 --no-open --insecure --tui"
```

### 方式 2：systemd（持久化）
```ini
# /etc/systemd/system/hermes-dashboard.service
[Unit]
Description=Hermes Agent Web Dashboard
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/hermes dashboard --host 0.0.0.0 --port 9119 --no-open --insecure --tui
Restart=always
RestartSec=5
Environment=HOME=/root

[Install]
WantedBy=default.target
```

```bash
systemctl daemon-reload
systemctl enable --now hermes-dashboard
```

## 与其他端口服务共存

如果 80 端口已被占用（如已有网站），可以：
- 改 Nginx 监听其他端口（如 8443）
- 或用 `server_name` 做基于域名的虚拟主机
- Dashboard 默认端口 9119 也可以直接开放（配合防火墙限制 IP）
