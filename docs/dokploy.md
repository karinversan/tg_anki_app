# Deploy In Dokploy (Telegram Mini App + HTTPS)

## 1. What to deploy

Use `docker-compose.yml` from repo root.  
It is already production-oriented for Dokploy:
- no local bind mounts from your laptop;
- persistent Docker volumes for Postgres and app data;
- web runs as built Vite app (`vite preview` on port `4173`);
- API runs migrations on startup.

For local development you do not need another deploy flow:
- `docker-compose.override.yml` is applied automatically by Docker Compose on your machine;
- it switches web/api to dev ports and mounts source code.

## 2. Create app in Dokploy

1. `Create Project` -> `Docker Compose`.
2. Source: GitHub repository `karinversan/tg_anki_app`.
3. Branch: `main`.
4. Compose path: `docker-compose.yml`.
5. Save.

## 3. Required environment variables

Add variables from `.env.example`, then override at least these:

```env
APP_ENV=production

POSTGRES_USER=postgres
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=tg_anki
DATABASE_URL=postgresql+asyncpg://postgres:<strong-password>@postgres:5432/tg_anki

REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

BOT_TOKEN=<telegram-bot-token>
JWT_SECRET=<long-random-secret>
ENCRYPTION_KEY_BASE64=<32-byte-base64-key>

WEB_BASE_URL=https://app.example.com
API_BASE_URL=https://api.example.com
VITE_API_BASE_URL=https://api.example.com
CORS_ORIGINS=https://app.example.com
```

Key generation example:

```bash
openssl rand -base64 32
```

If Ollama is not on the same host, use remote provider (`LLM_PROVIDER=gemini`) or point `OLLAMA_BASE_URL` to reachable endpoint.

## 4. Domains and HTTPS in Dokploy

Recommended: separate subdomains (single Dokploy deploy).

- `app.example.com` -> service `web`, port `4173`
- `api.example.com` -> service `api`, port `8000`

For each domain in Dokploy:
1. Open service -> `Domains`.
2. Add domain.
3. Select `HTTPS` + `Let's Encrypt`.
4. Save and wait for certificate status `Issued`.
5. Redeploy Docker Compose app (for Docker Compose, domain changes apply on redeploy).

DNS records must exist before issuing certs:
- `A app.example.com -> <your-server-ip>`
- `A api.example.com -> <your-server-ip>`

Notes:
- `*.traefik.me` free domains in Dokploy are HTTP-only by default, so they are not suitable for Telegram Mini App production URL.
- For Mini App use your own domain + valid certificate.

## 5. Telegram Mini App HTTPS requirements

Mini App URL must be public HTTPS with valid certificate (self-signed is not accepted).

After Dokploy cert is issued:
1. In BotFather run `/setmenubutton`.
2. Select your bot.
3. Set URL exactly to `https://app.example.com`.
4. Restart `bot` service in Dokploy.

Your bot already validates this in code: if `WEB_BASE_URL` is not `https://...`, Mini App button falls back and warns user.

## 6. Verify after deploy

1. Open `https://app.example.com` in browser (no certificate warning).
2. Check API health:

```bash
curl -i https://api.example.com/
```

Expected: `200 OK` and `{"status":"ok"}`.

3. Open bot in Telegram and tap `Open Mini App`.
4. Ensure auth and API calls work inside Telegram WebView.

## 7. Common issues

- Cert not issued:
  - DNS still not propagated;
  - wrong domain record;
  - port `80/443` blocked by firewall.
- Mini App opens blank:
  - wrong `VITE_API_BASE_URL` or CORS not including `app.example.com`.
- Bot button is not Web App:
  - `WEB_BASE_URL` is not `https://...`;
  - BotFather menu button still points to old URL.

## 8. Which ports to use in Dokploy

Use these target ports in Dokploy `Domains`:
- `web` service -> `4173`
- `api` service -> `8000`

No public domains needed for:
- `postgres` (`5432`)
- `redis` (`6379`)
- `clamav` (`3310`)
- `worker`
- `bot`
