# Dashboard WebSocket 调试指南

## 问题诊断流程

当 Chat 标签页报错 "events feed disconnected" 时，按以下步骤排查：

### 1. 确认 Dashboard 进程状态

```bash
ps aux | grep "hermes dashboard" | grep -v grep
ss -tlnp | grep 9119
```

### 2. 测试直接连接（绕过 Nginx）

```bash
# 获取 session token
TOKEN=$(curl -s http://127.0.0.1:9119/ | awk -F'HERMES_SESSION_TOKEN__=...' '{print $1}' | awk -F'\"' '{print $1}')

# 测试 PTY WebSocket（最简单的端点）
timeout 5 curl -s -o /dev/null -w "pty: %{http_code}\n" \
  -H "Upgrade: websocket" -H "Connection: Upgrade" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "http://127.0.0.1:9119/api/pty?token=$TOKEN"

# 测试 events WebSocket（需要 channel 参数）
timeout 5 curl -s -o /dev/null -w "events: %{http_code}\n" \
  -H "Upgrade: websocket" -H "Connection: Upgrade" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "http://127.0.0.1:9119/api/events?token=$TOKEN&channel=test"
```

### 3. 测试通过 Nginx

```bash
# 带 Basic Auth（REST API 用）
curl -s -o /dev/null -w "nginx rest: %{http_code}\n" \
  -u <user>:<pass> https://<domain>/api/status

# WebSocket 不带 auth（Nginx 层应跳过 Basic Auth）
timeout 5 curl -s -o /dev/null -w "nginx ws: %{http_code}\n" \
  -H "Upgrade: websocket" -H "Connection: Upgrade" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "https://<domain>/api/pty"
```

## HTTP 状态码含义

| 码 | 来源 | 含义 |
|----|------|------|
| 101 | Dashboard | WebSocket 升级成功 ✅ |
| 400 | Dashboard | 缺少必要参数（罕见，通常表现为 403） |
| 401 | Dashboard/Nginx | 认证失败（token 错误或 Basic Auth 拒绝） |
| 403 | Dashboard | Host/Origin 检查失败、未启用 embedded_chat、或缺少必要参数（如 events 缺 channel） |

## 常见 403 原因

1. **`_DASHBOARD_EMBEDDED_CHAT_ENABLED = False`** — 启动时没加 `--tui` 参数
2. **⚠️ auth_middleware 拦截（最常见的隐藏原因）** — Dashboard 的 HTTP 中间件 `auth_middleware` 在 WebSocket 升级请求到达 WS 处理程序**之前**拦截请求，检查 `X-Hermes-Session-Token` **请求头**。但浏览器通过 `?token=` 查询参数传递 token，不发送请求头。修复：Nginx 添加 `proxy_set_header X-Hermes-Session-Token $arg_token;` 将查询参数转发为请求头。
3. **Origin 头不匹配** — 浏览器发送域名 origin，Dashboard 绑定 loopback。修复：Nginx `proxy_set_header Origin "";`
4. **Host 头不匹配** — Nginx 没设置 `proxy_set_header Host 127.0.0.1:9119`
5. **channel 参数缺失** — `/api/events` 需要 `?channel=<id>`，`/api/pty` 不需要

## ⚠️ WebSocket 403 根本原因分析（auth_middleware 拦截）

**现象：** Dashboard 绑定 `0.0.0.0` + Nginx 代理时，WebSocket 连接仍然返回 403。直接连接 `ws://127.0.0.1:9119/api/events?token=<valid_token>` 也返回 403。

**根本原因：** Dashboard 的 HTTP 中间件 `auth_middleware`（`hermes_cli/web_server.py`）在 WebSocket 升级请求到达 WebSocket 处理程序**之前**拦截请求。该中间件检查 `X-Hermes-Session-Token` 请求头和 `Authorization: Bearer` 头，但浏览器通过 URL 查询参数 `?token=` 传递 token，不发送请求头。中间件返回 401，导致 WebSocket 升级失败（客户端看到 403）。

**关键代码路径：**
```
HTTP 请求 → auth_middleware（检查 X-Hermes-Session-Token 头）→ WebSocket Handler → _ws_auth_ok（检查 query_params token）
                                                    ↑
                                            这里拦截了 WS 升级请求
```

**修复：** Nginx 将查询参数的 token 转发为请求头：
```nginx
proxy_set_header X-Hermes-Session-Token $arg_token;
```

**已验证的测试：**
- 直接连接 `ws://127.0.0.1:9119/api/events?token=<valid_token>` → 403（无 X-Hermes-Session-Token 头）
- HTTP API（如 `/api/sessions`）带 `X-Hermes-Session-Token` 头 → 200 ✅
- HTTP API 不带头 → 401

**✅ 推荐方案：绑定 0.0.0.0 + Nginx token 头转发 + iptables 防火墙**

```bash
# Dashboard 启动
hermes dashboard --host 0.0.0.0 --port 9119 --no-open --insecure --tui

# iptables 规则：只允许本地访问
iptables -A INPUT -s 127.0.0.1 -p tcp --dport 9119 -j ACCEPT
iptables -A INPUT -p tcp --dport 9119 -j DROP
```

## Dashboard WS 认证源码位置

- `_ws_auth_ok()` — `hermes_cli/web_server.py` ~line 3427
- `_ws_request_is_allowed()` — `hermes_cli/web_server.py` ~line 3422
- `_ws_host_origin_is_allowed()` — `hermes_cli/web_server.py` ~line 3393
- `_ws_client_is_allowed()` — `hermes_cli/web_server.py` ~line 3371
- `/api/events` handler — `hermes_cli/web_server.py` ~line 3758
- `/api/pty` handler — `hermes_cli/web_server.py` ~line 3580
