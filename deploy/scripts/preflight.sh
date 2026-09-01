#!/usr/bin/env sh
set -eu

echo "telegram-secretary preflight"
echo "This script is read-only: it does not modify nginx, TLS, Docker, or systemd."
echo

echo "Docker:"
if command -v docker >/dev/null 2>&1; then
  docker --version
else
  echo "docker: not found"
fi

echo
echo "Docker Compose:"
if docker compose version >/dev/null 2>&1; then
  docker compose version
else
  echo "docker compose: not available"
fi

echo
echo "Reserved local health port:"
if command -v ss >/dev/null 2>&1; then
  ss -ltn | grep ':18097 ' || echo "127.0.0.1:18097 appears unused"
else
  echo "ss: not found; check port 18097 manually"
fi

echo
echo "Expected next step: dry-run health only. Do not enable nginx/TLS/webhooks until DNS is confirmed."

