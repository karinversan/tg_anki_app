# Telegram Anki MVP

Production-ready monorepo for a Telegram bot + Mini App for anki cards generation.

## Services
- `bot/` — aiogram bot
- `api/` — FastAPI backend
- `worker/` — Celery worker
- `web/` — React Mini App
- `infra/` — Docker, migrations, scripts

## Quick start
```bash
cp .env.example .env
# fill required secrets in .env (BOT_TOKEN, JWT_SECRET, ENCRYPTION_KEY_BASE64)
# set GEMINI_API_KEY for generation and WEB_BASE_URL for Telegram WebApp

docker compose up --build
```

## URLs
- API: http://localhost:8000
- Web: http://localhost:5173
