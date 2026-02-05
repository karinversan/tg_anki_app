.PHONY: dev lint test migrate tunnel dev-with-tunnel named-tunnel tunnel-local tunnel-ssh tunnel-ngrok

dev:
	docker compose up --build

tunnel:
	bash scripts/start_tunnel.sh

dev-with-tunnel:
	docker compose up --build -d
	bash scripts/start_tunnel.sh

named-tunnel:
	bash scripts/start_named_tunnel.sh

tunnel-local:
	bash scripts/start_localtunnel.sh

tunnel-ssh:
	bash scripts/start_localhostrun.sh

tunnel-ngrok:
	bash scripts/start_ngrok.sh

lint:
	cd api && python -m ruff .
	cd web && npm run lint

test:
	cd api && pytest

migrate:
	alembic -c infra/alembic.ini upgrade head
