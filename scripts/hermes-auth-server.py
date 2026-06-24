#!/usr/bin/env python3
"""Hermes Dashboard Cookie Auth Service.

A lightweight authentication proxy for Nginx auth_request.
- GET  /auth/verify  → check session cookie, return 200/401
- POST /auth/login   → validate credentials, set session cookie
- POST /auth/logout  → clear session cookie
- GET  /auth/login   → serve login page
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# --- Config ---
SECRET_KEY = os.environ.get("AUTH_SECRET_KEY", "")
if not SECRET_KEY:
    # Generate and persist a secret key. Override path with AUTH_SECRET_KEY_FILE.
    key_file = Path(os.environ.get("AUTH_SECRET_KEY_FILE", "/etc/hermes-auth/secret.key"))
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        SECRET_KEY = key_file.read_text().strip()
    else:
        SECRET_KEY = secrets.token_hex(32)
        key_file.write_text(SECRET_KEY)
        os.chmod(key_file, 0o600)

COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "hermes_session")
COOKIE_MAX_AGE = int(os.environ.get("AUTH_COOKIE_MAX_AGE", str(86400 * 7)))
HTPASSWD_FILE = os.environ.get("AUTH_HTPASSWD_FILE", "/etc/nginx/.htpasswd")

# --- htpasswd parser ---
def verify_htpasswd(username: str, password: str) -> bool:
    """Verify username:password against Apache htpasswd file."""
    try:
        with open(HTPASSWD_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line:
                    continue
                stored_user, stored_hash = line.split(":", 1)
                if stored_user != username:
                    continue
                # Apache $apr1$ MD5 hash
                if stored_hash.startswith("$apr1$"):
                    import crypt
                    return crypt.crypt(password, stored_hash) == stored_hash
                # bcrypt
                if stored_hash.startswith("$2"):
                    import crypt
                    return crypt.crypt(password, stored_hash) == stored_hash
                # SHA-512 ($6$)
                if stored_hash.startswith("$6$"):
                    import crypt
                    return crypt.crypt(password, stored_hash) == stored_hash
                # SHA-256 ($5$)
                if stored_hash.startswith("$5$"):
                    import crypt
                    return crypt.crypt(password, stored_hash) == stored_hash
                # SHA1
                if stored_hash.startswith("{SHA}"):
                    import base64
                    h = hashlib.sha1(password.encode()).digest()
                    return base64.b64encode(h).decode() == stored_hash[5:]
        return False
    except FileNotFoundError:
        return False


# --- Session cookie helpers ---
def make_session_token(username: str) -> str:
    """Create a signed session token."""
    payload = json.dumps({"user": username, "ts": int(time.time())})
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    import base64
    return base64.urlsafe_b64encode(payload.encode()).decode() + "." + sig


def verify_session_token(token: str) -> dict | None:
    """Verify and decode a session token. Returns payload or None."""
    try:
        import base64
        parts = token.rsplit(".", 1)
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        payload = base64.urlsafe_b64decode(payload_b64).decode()
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        data = json.loads(payload)
        # Check expiry
        if time.time() - data.get("ts", 0) > COOKIE_MAX_AGE:
            return None
        return data
    except Exception:
        return None


# --- Login page HTML ---
LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hermes Dashboard - 登录</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    color: #e0e0e0;
  }
  .login-card {
    background: rgba(255,255,255,0.05);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px;
    padding: 48px 40px;
    width: 400px;
    max-width: 90vw;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  .logo {
    text-align: center;
    margin-bottom: 32px;
  }
  .logo h1 {
    font-size: 28px;
    font-weight: 700;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .logo p {
    color: #888;
    font-size: 14px;
  }
  .form-group {
    margin-bottom: 20px;
  }
  label {
    display: block;
    font-size: 13px;
    color: #aaa;
    margin-bottom: 6px;
    font-weight: 500;
  }
  input[type="text"], input[type="password"] {
    width: 100%;
    padding: 12px 16px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 8px;
    color: #fff;
    font-size: 15px;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus {
    border-color: #667eea;
  }
  .btn {
    width: 100%;
    padding: 13px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border: none;
    border-radius: 8px;
    color: #fff;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
    margin-top: 8px;
  }
  .btn:hover { opacity: 0.9; }
  .btn:active { transform: scale(0.98); }
  .error {
    background: rgba(255,59,48,0.15);
    border: 1px solid rgba(255,59,48,0.3);
    color: #ff6b6b;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
    margin-bottom: 16px;
    display: none;
  }
  .footer {
    text-align: center;
    margin-top: 24px;
    color: #555;
    font-size: 12px;
  }
</style>
</head>
<body>
<div class="login-card">
  <div class="logo">
    <h1>🤖 Hermes Agent</h1>
    <p>Web Dashboard</p>
  </div>
  <div class="error" id="error">%ERROR_MSG%</div>
  <form method="POST" action="/auth/login">
    <div class="form-group">
      <label>用户名</label>
      <input type="text" name="username" autocomplete="username" required autofocus>
    </div>
    <div class="form-group">
      <label>密码</label>
      <input type="password" name="password" autocomplete="current-password" required>
    </div>
    <button type="submit" class="btn">登 录</button>
  </form>
  <div class="footer">Hermes Agent v0.15.1 · Nous Research</div>
</div>
</body>
</html>"""


class AuthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_GET(self):
        if self.path == "/auth/verify":
            self.handle_verify()
        elif self.path == "/auth/login":
            self.handle_login_page()
        elif self.path == "/auth/logout":
            self.handle_logout()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/auth/login":
            self.handle_login_post()
        elif self.path == "/auth/logout":
            self.handle_logout()
        else:
            self.send_error(404)

    def handle_verify(self):
        """Nginx calls this to check if the request has a valid session cookie."""
        cookie_header = self.headers.get("Cookie", "")
        cookies = {}
        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()

        token = cookies.get(COOKIE_NAME, "")
        if token and verify_session_token(token):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized")

    def handle_login_page(self):
        """Serve the login page."""
        content = LOGIN_HTML.replace("%ERROR_MSG%", "").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def handle_login_post(self):
        """Validate credentials and set session cookie."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        params = urllib.parse.parse_qs(body)
        username = params.get("username", [""])[0]
        password = params.get("password", [""])[0]

        if verify_htpasswd(username, password):
            token = make_session_token(username)
            # Set session cookie
            cookie = (
                f"{COOKIE_NAME}={token}; "
                f"Path=/; "
                f"Max-Age={COOKIE_MAX_AGE}; "
                f"HttpOnly; "
                f"SameSite=Lax; "
                f"Secure"
            )
            # Redirect to dashboard root
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
        else:
            content = b"Invalid credentials"
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    def handle_logout(self):
        """Clear session cookie."""
        content = b"Logged out"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(content)))
        cookie = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure"
        self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 9120), AuthHandler)
    print("Auth service running on 127.0.0.1:9120")
    server.serve_forever()
