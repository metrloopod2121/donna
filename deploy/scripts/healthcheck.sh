#!/usr/bin/env sh
set -eu

SECRETARY_HEALTH_URL="${SECRETARY_HEALTH_URL:-http://127.0.0.1:18097/healthz}"

if command -v curl >/dev/null 2>&1; then
  exec curl -fsS "$SECRETARY_HEALTH_URL"
fi

python3 - "$SECRETARY_HEALTH_URL" <<'PY'
import sys
from urllib.request import Request, urlopen

url = sys.argv[1]
request = Request(url, headers={"User-Agent": "telegram-secretary-healthcheck/0.1"})
with urlopen(request, timeout=3) as response:
    if not 200 <= response.status < 300:
        raise SystemExit(f"unhealthy status: {response.status}")
    print(response.read().decode("utf-8"))
PY

