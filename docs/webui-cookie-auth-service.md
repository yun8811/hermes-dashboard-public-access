# Cookie 认证服务

## 概述

一个轻量 Python HTTP 服务，通过 Nginx `auth_request` 实现 Cookie-based 会话认证。登录一次后浏览器自动携带 Cookie，刷新页面无需重复输入密码。

## 架构

```
浏览器 → Nginx (443) → auth_request → 认证服务 (9120)
                   ↓ (通过)         ↓ (验证 Cookie)
                Dashboard (9119)   200/401
```

## 安装

```bash
# 创建配置目录
mkdir -p /etc/hermes-auth

# 认证服务脚本位于 /usr/local/bin/hermes-auth-server.py
# 已在部署时创建，如需重新部署：
# 见下方 "认证服务脚本" 章节
```

## systemd 服务

```ini
# /etc/systemd/system/hermes-auth.service
[Unit]
Description=Hermes Dashboard Auth Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/hermes-auth-server.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now hermes-auth
```

## Nginx 配置（Cookie 认证版）

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name <域名>;

    ssl_certificate /etc/letsencrypt/live/<域名>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<域名>/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    set $auth_service http://127.0.0.1:9120;

    # 静态资源 — 不需要认证
    location /assets/ {
        proxy_pass http://127.0.0.1:9119;
        proxy_set_header Host 127.0.0.1:9119;
        expires 1h;
        add_header Cache-Control "public, immutable";
    }

    location ~* \.(ico|css|js|woff2|woff|ttf|svg|png|jpg|jpeg|gif|webp)$ {
        proxy_pass http://127.0.0.1:9119;
        proxy_set_header Host 127.0.0.1:9119;
        expires 1h;
    }

    # 认证服务端点 — 直接访问（不需要 Cookie 验证）
    location /auth/ {
        proxy_pass http://127.0.0.1:9120;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket 路径 — 不需要 Cookie 认证
    location ~ ^/api/(pty|ws|events|pub)(/.*)?$ {
        proxy_pass http://127.0.0.1:9119;
        proxy_set_header Host 127.0.0.1:9119;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_hide_header Origin;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 86400;
    }

    # 所有其他请求 — Cookie 认证
    location / {
        auth_request /auth-verify;
        error_page 401 = /login-redirect;

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

    # auth_request 内部验证端点
    location = /auth-verify {
        internal;
        proxy_pass http://127.0.0.1:9120/auth/verify;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
    }

    # 未认证时重定向到登录页
    location = /login-redirect {
        return 302 /auth/login;
    }
}
```

## 认证流程

1. 用户访问 `https://domain.com/` → Nginx `auth_request` 检查 Cookie → 无有效 Cookie → 302 到 `/auth/login`
2. 用户在登录页输入用户名密码 → POST `/auth/login` → 验证通过 → 设置 `hermes_session` Cookie → 重定向回首页
3. 后续请求自动携带 Cookie → Nginx `auth_request` 验证通过 → 正常访问 Dashboard
4. 登出：POST `/auth/logout` → 清除 Cookie

## 认证服务 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/auth/login` | GET | 返回登录页面 HTML |
| `/auth/login` | POST | 验证凭据，设置 Cookie |
| `/auth/verify` | GET | Nginx auth_request 调用，验证 Cookie |
| `/auth/logout` | POST | 清除 Cookie |

## ⚠️ 坑点

### `crypt.crypt` 无法验证 Apache `$apr1$` 哈希

Python 的 `crypt.crypt()` 对 Apache `$apr1$` MD5 哈希的验证会失败（返回 `*0`）。这是因为 `crypt` 模块的 `$apr1$` 实现有兼容性问题。

**根本修复：** 在认证服务的 `verify_htpasswd` 函数中，添加对 `$6$`（SHA-512）和 `$5$`（SHA-256）格式的显式支持，然后用 Python 重新生成 htpasswd 文件：

```bash
# 用 Python 生成 SHA-512 格式的 htpasswd（crypt.crypt 原生支持）
python3 -c "
import crypt
password = 'your-password'
salt = crypt.mksalt(crypt.METHOD_SHA512)
hashed = crypt.crypt(password, salt)
print(f'username:{hashed}')
" > /etc/nginx/hermes-passwd
```

认证服务代码中需要添加（在 `$apr1$` 和 `$2*` 检查之后）：
```python
# SHA-512 ($6$)
if stored_hash.startswith("$6$"):
    import crypt
    return crypt.crypt(password, stored_hash) == stored_hash
# SHA-256 ($5$)
if stored_hash.startswith("$5$"):
    import crypt
    return crypt.crypt(password, stored_hash) == stored_hash
```

**注意：** Python 3.13+ 中 `crypt` 模块已被弃用。如遇此警告可忽略（功能仍可用），或迁移到 `passlib` 库。

### htpasswd 文件路径不匹配

认证服务代码中硬编码的 htpasswd 路径可能与实际文件路径不一致（如代码读 `/etc/nginx/.htpasswd`，但文件实际在 `/etc/nginx/hermes-passwd`）。

**诊断：** 用 `curl` 测试登录返回 401，但手动 `crypt.crypt()` 验证密码正确 → 路径问题。

**修复：** 创建符号链接：
```bash
ln -sf /etc/nginx/hermes-passwd /etc/nginx/.htpasswd
```

或直接修改认证服务代码中的 `HTPASSWD_FILE` 路径。

### 登录页 HTML 模板

登录页使用暗色渐变背景，与 Hermes 品牌风格一致。完整 HTML 见认证服务脚本中的 `LOGIN_HTML` 变量。关键特性：
- 响应式设计，移动端友好
- 暗色主题（`#0f0c29` → `#302b63` → `#24243e` 渐变）
- 毛玻璃效果卡片（`backdrop-filter: blur`）
- 错误消息显示区域

## Cookie 安全配置

- **HttpOnly**: 防止 JavaScript 读取
- **Secure**: 仅 HTTPS 传输
- **SameSite=Lax**: 防止 CSRF
- **Max-Age**: 7 天（604800 秒）
- **签名**: HMAC-SHA256 + base64url 编码

## 验证修复

部署后用以下命令验证登录功能：
```bash
# 测试登录（应返回 200）
curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:9120/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=<用户名>&password=<密码>"

# 测试验证端点（无 Cookie 应返回 401）
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9120/auth/verify
```

如果登录返回 401 但手动验证密码正确，检查：
1. htpasswd 文件路径是否匹配代码中的 `HTPASSWD_FILE`
2. 哈希格式是否为 `$6$` 或 `$5$`（Python `crypt` 原生支持）

## 监控

```bash
# 检查服务状态
systemctl status hermes-auth

# 查看日志
journalctl -u hermes-auth -f

# 测试验证端点
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9120/auth/verify
# 无 Cookie → 401
# 有有效 Cookie → 200
```
