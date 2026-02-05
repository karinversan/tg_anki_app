#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-5173}"
ENV_FILE="${ENV_FILE:-.env}"
SUBDOMAIN="${LOCALHOSTRUN_SUBDOMAIN:-}"

if ! command -v ssh >/dev/null 2>&1; then
  echo "ssh is not available." >&2
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

echo "Starting localhost.run tunnel on port ${PORT}..."
echo "Press Ctrl+C to stop the tunnel."

subdomain_flag=()
if [[ -n "${SUBDOMAIN}" ]]; then
  subdomain_flag=(-o "SetEnv=LOCALHOSTRUN_SUBDOMAIN=${SUBDOMAIN}")
fi

web_url_set=0
while IFS= read -r line; do
  echo "${line}"
  if [[ ${web_url_set} -eq 0 ]]; then
    url="$(printf '%s\n' "${line}" | sed -nE 's/.*(https:\/\/[A-Za-z0-9.-]+\.lhr\.life).*/\1/p')"
    if [[ -n "${url}" ]]; then
      update_env "${url}"
      web_url_set=1
      echo "WEB_BASE_URL updated in ${ENV_FILE}: ${url}"
      restart_services
    fi
  fi
done < <(
  ssh -o StrictHostKeyChecking=no \
      -o ServerAliveInterval=30 \
      -o ExitOnForwardFailure=yes \
      -R 80:localhost:${PORT} \
      "${subdomain_flag[@]}" \
      ssh.localhost.run 2>&1
)
