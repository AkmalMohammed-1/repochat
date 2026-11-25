# Needs GNU make (Linux/macOS/CI; on Windows use Git Bash + `choco install make`, or run the commands directly).
export PYTHONPATH := apps:.

.PHONY: install dev up down migrate api worker web test test-unit lint fmt eval eval-answers

install:            ## Python + web dependencies
	uv sync
	cd apps/web && npm ci

up:                 ## Whole stack in Docker
	docker compose up --build

down:
	docker compose down

dev:                ## Infra in Docker; run `make api`, `make worker`, `make web` in separate terminals
	docker compose up -d db localstack
	uv run python -m api.core.bootstrap

migrate:
	uv run python -m api.core.migrate

api:
	uv run uvicorn api.main:app --reload --port 8000

worker:
	uv run python -m worker.main

web:
	cd apps/web && npm run dev

test:               ## Unit + integration tests (integration needs `docker compose up -d db`)
	uv run pytest -q

test-unit:
	uv run pytest -q -m "not integration"

lint:
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run lint

fmt:
	uv run ruff check --fix .
	uv run ruff format .

eval:               ## Retrieval ablation on every dataset in eval/datasets
	uv run python eval/run_eval.py

eval-answers:       ## + answer quality (citation accuracy, "I don't know" rate); calls the LLM
	uv run python eval/run_eval.py --answers
