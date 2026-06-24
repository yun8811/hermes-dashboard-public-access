# Hermes Dashboard Public Access

A small deployment recipe for exposing the Hermes Web Dashboard safely over HTTPS using Nginx, a cookie-auth gate, and local-only dashboard binding.

## What this repo contains

- `docs/` — implementation notes for Nginx proxying, cookie authentication, and WebSocket debugging.
- `examples/nginx-hermes-dashboard.conf.example` — sanitized Nginx reverse proxy example.
- `scripts/hermes-auth-server.py` — lightweight cookie-auth helper service.
- `scripts/dashboard_restart.sh` — optional restart helper for the Dashboard service.

## Architecture

- Hermes Dashboard listens on `127.0.0.1:9119` only.
- Cookie auth helper listens on `127.0.0.1:9120` only.
- Nginx terminates HTTPS and proxies authenticated traffic to the local Dashboard.
- WebSocket requests forward `token` via `$arg_token` to preserve Dashboard chat connectivity.

## Deployment checklist

1. Install Hermes Agent and enable the Web Dashboard on `127.0.0.1:9119`.
2. Create an htpasswd file, for example `/etc/nginx/.htpasswd`.
3. Install `scripts/hermes-auth-server.py` as a local service on `127.0.0.1:9120`.
4. Copy `examples/nginx-hermes-dashboard.conf.example` into your Nginx sites directory and replace `dashboard.example.com`.
5. Issue TLS certificates with Certbot or your preferred ACME client.
6. Run `nginx -t`, reload Nginx, then verify login, Dashboard loading, and WebSocket chat.

The WebSocket location must include `proxy_set_header X-Hermes-Session-Token $arg_token;`; without it, Hermes Dashboard can reject browser WebSocket upgrades with `401/403` even when the URL token is present.

## Security notes

This repository intentionally excludes live secrets, tokens, htpasswd files, and machine-specific Nginx configs. Use the example config as a template and keep production credentials outside Git.
