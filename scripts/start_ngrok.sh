#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-5173}"
ENV_FILE="${ENV_FILE:-.env}"

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok is not installed. Install with: brew install ngrok" >&2
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

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to read the ngrok local API." >&2
  exit 1
fi

echo "Starting ngrok on port ${PORT}..."
echo "Press Ctrl+C to stop the tunnel."

ngrok http "${PORT}" >/tmp/ngrok.out 2>&1 &
ngrok_pid=$!

cleanup() {
  kill "${ngrok_pid}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

web_url=""
for _ in $(seq 1 20); do
  sleep 0.5
  web_url="$(curl -fsS http://127.0.0.1:4040/api/tunnels | sed -nE 's/.*"public_url":"(https:[^"]+)".*/\1/p' | head -n 1)"
  if [[ -n "${web_url}" ]]; then
    break
  fi
done

if [[ -z "${web_url}" ]]; then
  echo "Failed to read ngrok public URL from http://127.0.0.1:4040/api/tunnels" >&2
  exit 1
fi

update_env "${web_url}"
restart_services
echo "WEB_BASE_URL updated in ${ENV_FILE}: ${web_url}"

wait "${ngrok_pid}"
