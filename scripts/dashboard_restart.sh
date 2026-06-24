#!/bin/bash
systemctl restart hermes-dashboard.service && echo "✅ Web Dashboard 已重启（$(date '+%Y-%m-%d %H:%M:%S')）" || echo "❌ 重启失败"