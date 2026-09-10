.PHONY: sync test lint up down seed migrate

sync:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

up:
	docker compose up --build -d postgres api mcp worker

down:
	docker compose down

seed:
	docker compose --profile demo run --rm seed

migrate:
	uv run alembic upgrade head

