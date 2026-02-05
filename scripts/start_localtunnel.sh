#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-5173}"
ENV_FILE="${ENV_FILE:-.env}"
SUBDOMAIN="${LOCAL_TUNNEL_SUBDOMAIN:-}"

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is not available. Install Node.js first." >&2
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

echo "Starting Localtunnel on port ${PORT}..."
echo "Press Ctrl+C to stop the tunnel."

cmd=(npx localtunnel --port "${PORT}")
if [[ -n "${SUBDOMAIN}" ]]; then
  cmd+=(--subdomain "${SUBDOMAIN}")
fi

web_url_set=0
while IFS= read -r line; do
  echo "${line}"
  if [[ ${web_url_set} -eq 0 ]]; then
    url="$(printf '%s\n' "${line}" | sed -nE 's/.*(https:\/\/[A-Za-z0-9.-]+\.loca\.lt).*/\1/p')"
    if [[ -n "${url}" ]]; then
      update_env "${url}"
      web_url_set=1
      echo "WEB_BASE_URL updated in ${ENV_FILE}: ${url}"
      restart_services
    fi
  fi
done < <("${cmd[@]}" 2>&1)
