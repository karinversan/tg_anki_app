# Deploy In Dokploy (Telegram Mini App + HTTPS)

## 1. What to deploy

Use `docker-compose.yml` from repo root.  
It is already production-oriented for Dokploy:
- no local bind mounts from your laptop;
- no host `ports` bindings (no port conflicts with other projects);
- persistent Docker volumes for Postgres and app data;
- web is built in image build stage and served by nginx on port `4173`;
- API runs migrations on startup.

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
STACK_NAME=tg_anki_prod

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

Use remote provider for generation:
- `LLM_PROVIDER=openrouter`
- `OPENROUTER_API_KEY=<api-key>`
- `OPENROUTER_MODEL=qwen/qwen3-8b:free`
or:
- `LLM_PROVIDER=gemini`
- `GEMINI_API_KEY=<api-key>`

`STACK_NAME` must be unique per project on one server, so Docker object names (`network`, `volumes`, containers) do not collide.

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
