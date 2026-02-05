#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-5173}"
LOCAL_URL="${LOCAL_URL:-http://localhost:${PORT}}"
ENV_FILE="${ENV_FILE:-.env}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed. Install with: brew install cloudflared" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Run: cp .env.example .env" >&2
  exit 1
fi

update_env() {
  local url="$1"
  if rg -q '^WEB_BASE_URL=' "${ENV_FILE}"; then
    sed -i.bak "s|^WEB_BASE_URL=.*|WEB_BASE_URL=${url}|" "${ENV_FILE}"
  else
    printf 'WEB_BASE_URL=%s\n' "${url}" >> "${ENV_FILE}"
  fi
}

restart_services() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose up -d --no-deps web bot >/dev/null 2>&1 || true
  fi
}

echo "Starting Cloudflare Tunnel for ${LOCAL_URL}..."
echo "Press Ctrl+C to stop the tunnel."

web_url_set=0
while IFS= read -r line; do
  echo "${line}"
  if [[ ${web_url_set} -eq 0 ]]; then
    url="$(printf '%s\n' "${line}" | sed -nE 's/.*(https:\/\/[A-Za-z0-9.-]+\.trycloudflare\.com).*/\1/p')"
    if [[ -n "${url}" ]]; then
      update_env "${url}"
      web_url_set=1
      echo "WEB_BASE_URL updated in ${ENV_FILE}: ${url}"
      restart_services
    fi
  fi
done < <(cloudflared tunnel --protocol http2 --url "${LOCAL_URL}" 2>&1)
