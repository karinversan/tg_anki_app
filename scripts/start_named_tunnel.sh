#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-5173}"
LOCAL_URL="${LOCAL_URL:-http://localhost:${PORT}}"
ENV_FILE="${ENV_FILE:-.env}"
TUNNEL_ID="${TUNNEL_ID:-}"
TUNNEL_CRED_FILE="${TUNNEL_CRED_FILE:-}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed. Install with: brew install cloudflared" >&2
  exit 1
fi

if [[ -z "${TUNNEL_ID}" || -z "${TUNNEL_CRED_FILE}" || -z "${TUNNEL_HOSTNAME}" ]]; then
  echo "Set TUNNEL_ID, TUNNEL_CRED_FILE, and TUNNEL_HOSTNAME env vars before running." >&2
  exit 1
fi

if [[ ! -f "${TUNNEL_CRED_FILE}" ]]; then
  echo "Tunnel credentials file not found: ${TUNNEL_CRED_FILE}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Run: cp .env.example .env" >&2
  exit 1
fi

CONFIG_FILE="$(mktemp)"
cat > "${CONFIG_FILE}" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${TUNNEL_CRED_FILE}
protocol: http2
ingress:
  - hostname: ${TUNNEL_HOSTNAME}
    service: ${LOCAL_URL}
  - service: http_status:404
EOF

if rg -q '^WEB_BASE_URL=' "${ENV_FILE}"; then
  sed -i.bak "s|^WEB_BASE_URL=.*|WEB_BASE_URL=https://${TUNNEL_HOSTNAME}|" "${ENV_FILE}"
else
  printf 'WEB_BASE_URL=https://%s\n' "${TUNNEL_HOSTNAME}" >> "${ENV_FILE}"
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose up -d --no-deps web bot >/dev/null 2>&1 || true
fi

echo "Starting named tunnel for https://${TUNNEL_HOSTNAME} -> ${LOCAL_URL}"
cloudflared tunnel --config "${CONFIG_FILE}" run
